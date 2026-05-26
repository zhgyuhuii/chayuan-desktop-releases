"""``chayuan-server local_index`` ↔ ``chayuan_registry.ModelRepository`` 桥。

为什么要桥？
==============

* ``chayuan_gateway`` 的 9 类路由全部通过 ``Depends(get_repo)`` 拿到一个
  ``ModelRepository``（背后是 SQLAlchemy + ``models`` 表）；
* ``chayuan-server`` 早就有了一份功能等价的 **本地磁盘模型索引**
  （``chayuan/server/model_registry/local_index.py``），数据落 JSON 而不是 SQL；
* 为了不引入两个并行的"模型库"（数据双写、UI 看到的不一致），我们让网关
  路由直接用本桥提供的"鸭子类型 ModelRepository"，背后读 chayuan-server 的
  local_index。

设计要点
========

* 鸭子类型：实现 ``list(category=...)``、``get(public_id)``、``get_by_path``、
  ``set_enabled``、``hard_remove`` 等 chayuan_gateway 实际用到的方法；
* ``Model`` 也用鸭子类型对象 :class:`LocalModel` 替代 ORM；它提供 ``.public_id``、
  ``.category``、``.runtime``、``.format``、``.path``、``.enabled``、``.to_public()``，
  让 ``pick_adapter(model)`` + ``AdapterRequest(model=...)`` 不改一行也能跑；
* capability 词表对齐：local_index 里是 ``text-embedding`` / ``image-embedding`` /
  ``text-to-image`` / ``text-to-video`` / ``text-to-speech`` / ``asr`` / ``image-to-text``，
  这里映射到 chayuan_runtime 期望的 ``embedding`` / ``clip`` / ``t2i`` / ``t2v`` /
  ``tts`` / ``asr`` / ``ocr``；
* runtime（适配器）推断：根据 ``family`` + ``format`` 给一个合理默认值，
  让 ``pick_adapter`` 能拿到 adapter；
* 线程安全：底层 LocalModelIndex 已经有 RLock；本类只读快照不持有锁。

公开 API：
* :class:`LocalModel`
* :class:`LocalIndexRepository`
* :func:`local_repo_factory` —— 给 FastAPI ``dependency_overrides`` 用
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional

logger = logging.getLogger("chayuan.ai_platform.repo_bridge")

if TYPE_CHECKING:  # pragma: no cover
    from chayuan.server.model_registry.local_index import LocalModelEntry


# capability(local_index) → category(chayuan_runtime)
_CAP_TO_CATEGORY: Dict[str, str] = {
    "chat": "chat",
    "text-embedding": "embedding",
    "image-embedding": "clip",
    "rerank": "rerank",
    "text-to-image": "t2i",
    "text-to-video": "t2v",
    "text-to-speech": "tts",
    "text-to-audio": "tts",
    "asr": "asr",
    "image-to-text": "ocr",
    "ocr": "ocr",
}

# (category, format) → 推荐 runtime（adapter 名）
_RUNTIME_DEFAULTS: Dict[str, str] = {
    "chat": "ollama",          # 默认 ollama；gguf 也可走 llama.cpp
    "embedding": "infinity",
    "clip": "infinity",
    "rerank": "infinity",
    "t2i": "comfyui",
    "t2v": "comfyui",
    "tts": "piper",
    "asr": "whispercpp",
    "ocr": "rapidocr",
}


def _runtime_from(category: str, fmt: str, family: str) -> str:
    """根据 category + format + family 选一个 adapter 名。

    优先级：
    1. ``family`` 指定（``llama.cpp`` / ``ollama`` / ``vllm``...）
    2. ``format`` 推断（``gguf`` → llamacpp，``safetensors`` → vllm）
    3. 默认表 ``_RUNTIME_DEFAULTS``
    """
    f = (family or "").lower()
    if "llama.cpp" in f or "llama-cpp" in f or "llamacpp" in f:
        return "llamacpp"
    if "vllm" in f:
        return "vllm"
    if "ollama" in f:
        return "ollama"
    if "comfyui" in f:
        return "comfyui"
    if "whisper" in f and "cpp" in f:
        return "whispercpp"
    if "funasr" in f:
        return "funasr"
    if "piper" in f:
        return "piper"
    if "cosyvoice" in f:
        return "cosyvoice"
    if "rapidocr" in f:
        return "rapidocr"
    if "paddleocr" in f:
        return "paddleocr"
    if (fmt or "").lower() == "gguf" and category == "chat":
        return "llamacpp"   # 单独跑 llama.cpp；ollama 适配器需要 ollama daemon 在
    return _RUNTIME_DEFAULTS.get(category, "auto")


@dataclass
class LocalModel:
    """``chayuan_registry.Model`` 的鸭子类型替代物（无 SQL 依赖）。

    与 ORM Model 字段同名同义；新增 ``capability`` 保留 local_index 原语。
    """

    repo: str
    name: str
    category: str
    runtime: str = "auto"
    format: str = "unknown"
    quantization: str = ""
    path: str = ""
    size_bytes: int = 0
    sha256: str = ""
    status: str = "ready"
    enabled: bool = True
    is_default: bool = False
    capabilities: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)
    source: str = "discovered"
    file_mtime: float = 0.0
    capability: str = ""           # local_index 原始 capability 字符串
    aliases: List[Any] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def public_id(self) -> str:
        """OpenAI 协议里的 model 字段值。"""
        return self.repo

    def to_public(self) -> Dict[str, Any]:
        # 把"模型文件落在哪儿"暴露给前端：
        #   * ``path`` —— 主权重 / manifest 路径（local_index 直接给的）
        #   * ``dir``  —— 父目录，"打开文件夹"按钮用
        #   * ``exists`` —— 让前端在文件被人手删时也能立刻看到
        from pathlib import Path as _P
        try:
            p = _P(self.path) if self.path else None
            exists = bool(p and p.exists())
            parent = str(p.parent) if p else None
        except Exception:
            exists = False
            parent = None

        return {
            "id": self.public_id,
            "object": "model",
            "owned_by": "chayuan",
            "category": self.category,
            "capability": self.capability or self.category,
            "runtime": self.runtime,
            "format": self.format,
            "quantization": self.quantization,
            "status": self.status,
            "enabled": self.enabled,
            "is_default": self.is_default,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256 or None,
            "path": self.path or None,
            "dir": parent,
            "exists": exists,
            "capabilities": dict(self.capabilities),
            "aliases": [getattr(a, "alias", str(a)) for a in self.aliases],
            "created": int(self.created_at.timestamp()),
            "source_tag": self.source,
        }


def _entry_to_model(e: "LocalModelEntry") -> LocalModel:
    """LocalModelEntry → LocalModel。"""
    cap = (e.capability or "").lower()
    category = _CAP_TO_CATEGORY.get(cap, cap or "chat")
    runtime = _runtime_from(category, e.format or "", e.family or "")
    return LocalModel(
        repo=e.model_id,
        name=e.model_id.split("/")[-1] if "/" in e.model_id else e.model_id,
        category=category,
        runtime=runtime,
        format=(e.format or "unknown").lower(),
        path=e.path,
        size_bytes=int(e.size_bytes or 0),
        capabilities={category: True},
        extra=dict(e.meta or {}),
        source=e.source_tag or "discovered",
        file_mtime=float(e.mtime or 0.0),
        capability=cap,
        created_at=(datetime.fromtimestamp(float(e.mtime), tz=timezone.utc)
                    if e.mtime else datetime.now(timezone.utc)),
        updated_at=datetime.now(timezone.utc),
    )


class LocalIndexRepository:
    """``chayuan_registry.ModelRepository`` 的鸭子类型替代。

    chayuan_gateway 用到的方法：``list(category=, enabled=)`` / ``get(public_id)``
    / ``get_by_path`` / ``set_enabled`` / ``hard_remove`` / ``set_default``。
    其它方法（如 alias / upsert）网关不调，本类不实现。
    """

    def __init__(self) -> None:
        # 延迟导入避免在测试或没有 chayuan-server 时崩
        from chayuan.server.model_registry.local_index import get_local_index
        self._index = get_local_index()
        # 软"开关"集合：在内存维护 enabled=False 的 model_id（local_index 不持
        # 有 enabled 字段；网关层用本集合做拒绝路由）。重启后 enabled 默认为 True。
        self._disabled: set[str] = set()
        # is_default[(category)] = model_id
        self._defaults: Dict[str, str] = {}

    # ---- queries -------------------------------------------------------

    def list(
        self,
        *,
        category: Optional[str] = None,
        status=None,
        enabled: Optional[bool] = None,
        include_removed: bool = False,
    ) -> List[LocalModel]:
        out: List[LocalModel] = []
        for e in self._index.list_entries():
            m = _entry_to_model(e)
            self._apply_local_state(m)
            if category is not None and m.category != category:
                continue
            if enabled is not None and m.enabled != enabled:
                continue
            out.append(m)
        return out

    def get(self, public_id: str) -> Optional[LocalModel]:
        e = self._index.get(public_id)
        if e is None:
            return None
        m = _entry_to_model(e)
        self._apply_local_state(m)
        return m

    def get_by_path(self, path: str) -> Optional[LocalModel]:
        for e in self._index.list_entries():
            if e.path == path:
                m = _entry_to_model(e)
                self._apply_local_state(m)
                return m
        return None

    # ---- writes --------------------------------------------------------

    def set_enabled(self, public_id: str, enabled: bool) -> bool:
        if self._index.get(public_id) is None:
            return False
        if enabled:
            self._disabled.discard(public_id)
        else:
            self._disabled.add(public_id)
        return True

    def set_default(self, category: str, public_id: str) -> bool:
        m = self.get(public_id)
        if m is None or m.category != category:
            return False
        self._defaults[category] = public_id
        return True

    def hard_remove(self, public_id: str) -> bool:
        """从本地索引中移除（不删磁盘文件）。

        chayuan-server 的 LocalModelIndex 没有公开的"按 model_id 删"接口；
        我们暂时把它加入 _disabled + 标记成 source=removed。真正的物理删
        建议走 chayuan-server 既有的 ``/runtime/models/<id>`` admin API。
        """
        if self._index.get(public_id) is None:
            return False
        self._disabled.add(public_id)
        # 删除其作为 default 的引用
        self._defaults = {k: v for k, v in self._defaults.items() if v != public_id}
        return True

    def add_alias(self, public_id: str, alias: str) -> bool:
        return False  # 不支持

    # ---- helpers -------------------------------------------------------

    def _apply_local_state(self, m: LocalModel) -> None:
        if m.repo in self._disabled:
            m.enabled = False
        if self._defaults.get(m.category) == m.repo:
            m.is_default = True


# 供 FastAPI dependency_overrides 用：
def local_repo_factory() -> Iterator[LocalIndexRepository]:
    """与 ``chayuan_gateway.deps.get_repo`` 同形：``yield`` 一个 repo。"""
    yield LocalIndexRepository()


__all__ = [
    "LocalModel",
    "LocalIndexRepository",
    "local_repo_factory",
]
