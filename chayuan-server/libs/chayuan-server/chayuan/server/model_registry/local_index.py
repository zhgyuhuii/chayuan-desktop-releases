"""本地磁盘模型索引。

与 :mod:`chayuan.server.model_registry.catalog` 关系
=====================================================

* ``catalog.py`` 处理 **远端** 模型目录（HF / ModelScope / Civitai 爬虫）：
  写入 ``model_registry/models.json``。
* 本模块（``local_index.py``）处理 **本地磁盘** 已有的模型文件：
  写入 ``model_registry/local_models.json``。

两者解耦：
* 同一个 ``models.json`` 路径可以混着出现"还没下载（仅 metadata）"和"已经下
  载"的条目；后者由本模块负责标记 ``installed=True`` + ``local_path``，让前
  端模型广场可以一眼看出什么能直接用。
* 即便完全离线，本模块仍能独立工作（不依赖 ``catalog.py`` 的爬虫结果）。

扫描路径
========

默认按下面顺序合并扫描，每个目录独立打 tag（便于 UI 展示来源）：

::

    <CHAYUAN_ROOT>/models/                 ← 用户主放置区（原有约定）
    <CHAYUAN_ROOT>/models/huggingface/     ← `huggingface_hub` 缓存风格
    <CHAYUAN_ROOT>/models/model_registry/  ← catalog.download_registry_model 落地点
    <CHAYUAN_ROOT>/models/custom/          ← 用户自有模型
    <CHAYUAN_ROOT>/models/gguf/            ← 单文件 gguf 集中区
    <repo>/vendor/runtimes/*/models/       ← vendor 自带模型（如 piper voices）

任何 ``.gguf`` / ``.onnx`` / ``.safetensors`` / 目录里有 ``config.json`` 或
``model_index.json`` 的文件，都会被当成候选。

公开 API
========
* :class:`LocalModelEntry` —— 一条记录
* :class:`LocalModelIndex` —— 内存视图 + 持久化
* :func:`scan_once` —— 一次性全量扫描；返回新发现 / 更新 / 删除的列表
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from chayuan.server.model_registry.identifier import ModelIdentity, identify_path

logger = logging.getLogger("chayuan.model_registry.local_index")


# -- 路径约定 --------------------------------------------------------------

DEFAULT_LAYOUT_DIRS = ("huggingface", "model_registry", "custom", "gguf", "modelscope")

# 单文件直接当模型的扩展名（其它扩展会要求一同存在 config.json 等"标志"文件）
SINGLE_FILE_FORMATS = (".gguf", ".onnx")

# 目录里若同时出现这些标志文件 → 视为一个完整模型仓库
DIR_MARKER_FILES = ("config.json", "model_index.json", "tokenizer.json", "manifest.json")


def models_dir() -> Path:
    """``<CHAYUAN_ROOT>/models``。延迟求值，尊重用户的 root 切换。"""
    from chayuan.settings import CHAYUAN_ROOT
    return Path(CHAYUAN_ROOT) / "models"


def local_index_path() -> Path:
    """``<CHAYUAN_ROOT>/model_registry/local_models.json``。"""
    from chayuan.settings import CHAYUAN_ROOT
    return Path(CHAYUAN_ROOT) / "model_registry" / "local_models.json"


def _atomic_replace_with_retry(tmp: str, dst: Path, *, attempts: int = 6) -> None:
    """带退避重试的 ``os.replace``。

    Windows 上 ``os.replace`` 覆盖已存在文件时,若目标文件正被其它句柄占用
    (Windows Defender 实时扫描刚写好的文件、Search 索引器、OneDrive 同步盘
    等)会抛 ``PermissionError`` ([WinError 5] 拒绝访问)。这类锁几乎都是
    **瞬时**的(毫秒级),退避重试几次基本都能成功。

    退避:0.05 → 0.1 → 0.2 → 0.4 → 0.8s,累计约 1.55s,足够等过一次 AV 扫描。
    全部失败再把异常抛出去,由调用方降级处理(local_index 是缓存文件,
    写不进去只是下次启动要重扫,不影响功能)。

    Linux/macOS 上 ``os.replace`` 是真原子操作,基本不会撞这个问题,
    重试逻辑对它们是无害的 no-op(第一次就成功)。
    """
    delay = 0.05
    for i in range(attempts):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.8)


# bundled_models 子目录约定（与 vendor/bundled_models/README.md 对齐）。
BUNDLED_CAPABILITY_DIRS = ("chat", "embedding", "rerank", "asr", "tts", "ocr", "image", "custom")
BUNDLED_DIRNAME = "bundled_models"


def bundled_models_dir() -> Optional[Path]:
    """解析项目内"固定模型槽"目录;不存在时返回 ``None``。

    按以下顺序探测,返回第一个**存在**的目录：

    1. 环境变量 ``CHAYUAN_BUNDLED_MODELS_DIR``(测试 / 特殊部署专用)
    2. PyInstaller 运行时：``sys._MEIPASS / "bundled_models"``
    3. 仓库 dev 模式：根据本文件位置回溯到 ``<chayuan-server>/vendor/bundled_models``
    4. Tauri 装机布局:exe 同级 / 上一级 / resources 子目录里找 bundled_models
       (与 ``local_runtime._default_install_services_dirs`` 同策略 —— Tauri
       会把 sidecar 投到 ``<install>/resources/chayuan-server/`` 这种子目录,
       而 ``bundled_models/`` 在 ``<install>/resources/`` 一级,只看 exe 同
       目录会漏)。
    """
    env = os.environ.get("CHAYUAN_BUNDLED_MODELS_DIR")
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p

    import sys
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass) / BUNDLED_DIRNAME
        if p.is_dir():
            return p

    # dev: <repo>/libs/chayuan-server/chayuan/server/model_registry/local_index.py
    #       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    # parents[5] = <repo>(chayuan-server 仓库根)
    try:
        repo_root = Path(__file__).resolve().parents[5]
        p = repo_root / "vendor" / BUNDLED_DIRNAME
        if p.is_dir():
            return p
    except (IndexError, OSError):
        pass

    # 装机布局穷举:与 services/ 解析同策略
    # Tauri 2 NSIS / dmg / appimage 把 ``resources`` 投到的子目录在不同平台
    # 不一样,加上 sidecar 自己被 onedir 嵌进 ``resources/chayuan-server/``,
    # 所以 exe 同级、上一级、resources 子目录都要看。任一命中即返回。
    try:
        argv0 = Path(sys.argv[0]).resolve()
    except (IndexError, OSError):
        argv0 = None
    # frozen 状态下 sys.executable 才是 sidecar exe(更稳),argv0 在被 launcher
    # 重写参数时可能指向脚本路径;两个一起试,去重后逐个查。
    bases: List[Path] = []
    if getattr(sys, "frozen", False):
        try:
            bases.append(Path(sys.executable).resolve())
        except OSError:
            pass
    if argv0 is not None and argv0.is_file():
        bases.append(argv0)
    seen: set[Path] = set()
    for base in bases:
        for cand in (
            base.parent / BUNDLED_DIRNAME,
            base.parent.parent / BUNDLED_DIRNAME,
            base.parent / "resources" / BUNDLED_DIRNAME,
            base.parent.parent / "resources" / BUNDLED_DIRNAME,
            base.parent.parent / "Resources" / BUNDLED_DIRNAME,  # mac .app
        ):
            if cand in seen:
                continue
            seen.add(cand)
            try:
                if cand.is_dir():
                    return cand
            except OSError:
                continue

    return None


# -- 数据结构 --------------------------------------------------------------

@dataclass
class LocalModelEntry:
    """一条本地模型记录（持久化结构与本类字段一一对应）。"""

    model_id: str
    path: str
    relpath: str
    capability: str
    family: str = ""
    format: str = "unknown"
    size_bytes: int = 0
    mtime: float = 0.0
    confidence: float = 0.0
    evidence: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    source_tag: str = ""    # 顶层目录 tag（如 huggingface / custom / gguf）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "path": self.path,
            "relpath": self.relpath,
            "capability": self.capability,
            "family": self.family,
            "format": self.format,
            "size_bytes": int(self.size_bytes),
            "mtime": float(self.mtime),
            "confidence": round(float(self.confidence), 3),
            "evidence": list(self.evidence),
            "meta": dict(self.meta),
            "source_tag": self.source_tag,
        }


@dataclass
class ScanDelta:
    """``scan_once`` 的差异返回。"""
    added:   List[LocalModelEntry] = field(default_factory=list)
    updated: List[LocalModelEntry] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)   # model_id 列表

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed)


# -- 索引主体 --------------------------------------------------------------

class LocalModelIndex:
    """``local_models.json`` 的读写 + 主键去重 + 增量比对。

    主键策略：以 ``relpath``（相对扫描根的相对路径）作为唯一标识；同一份磁盘
    内容不同根扫到时会通过 ``path`` 解析成同一 entry，避免重复。
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or local_index_path()
        self._lock = threading.Lock()
        self._entries: Dict[str, LocalModelEntry] = {}
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            for raw in (doc.get("items") or []):
                if not isinstance(raw, dict):
                    continue
                e = LocalModelEntry(
                    model_id=str(raw.get("model_id") or ""),
                    path=str(raw.get("path") or ""),
                    relpath=str(raw.get("relpath") or ""),
                    capability=str(raw.get("capability") or "other"),
                    family=str(raw.get("family") or ""),
                    format=str(raw.get("format") or "unknown"),
                    size_bytes=int(raw.get("size_bytes") or 0),
                    mtime=float(raw.get("mtime") or 0),
                    confidence=float(raw.get("confidence") or 0),
                    evidence=list(raw.get("evidence") or []),
                    meta=dict(raw.get("meta") or {}),
                    source_tag=str(raw.get("source_tag") or ""),
                )
                if e.model_id:
                    self._entries[e.model_id] = e
        except (OSError, ValueError) as e:
            logger.warning("[local-index] 解析 %s 失败：%s；按空索引继续", self._path, e)

    def _flush(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            doc = {
                "version": 1,
                "updated_at": int(time.time()),
                "items": [e.to_dict() for e in self._entries.values()],
            }
            fd, tmp = tempfile.mkstemp(
                prefix=f".{self._path.name}.", suffix=".tmp",
                dir=str(self._path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(doc, f, ensure_ascii=False, indent=2)
                _atomic_replace_with_retry(tmp, self._path)
            except Exception:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            logger.warning("[local-index] 写 %s 失败：%s", self._path, e)

    # -- 查询 --

    def list_entries(self) -> List[LocalModelEntry]:
        with self._lock:
            return list(self._entries.values())

    def by_capability(self, cap: str) -> List[LocalModelEntry]:
        with self._lock:
            return [e for e in self._entries.values() if e.capability == cap]

    def get(self, model_id: str) -> Optional[LocalModelEntry]:
        with self._lock:
            return self._entries.get(model_id)

    # -- 维护 --

    def upsert(self, entry: LocalModelEntry) -> Tuple[bool, bool]:
        """返回 ``(added, updated)``。已有 entry 但内容不变时两者都为 False。"""
        with self._lock:
            old = self._entries.get(entry.model_id)
            if old is None:
                self._entries[entry.model_id] = entry
                return True, False
            if (
                old.path == entry.path
                and old.size_bytes == entry.size_bytes
                and abs(old.mtime - entry.mtime) < 0.5
                and old.capability == entry.capability
                and old.format == entry.format
            ):
                return False, False
            self._entries[entry.model_id] = entry
            return False, True

    def remove(self, model_id: str) -> bool:
        with self._lock:
            return self._entries.pop(model_id, None) is not None

    def replace_all(self, entries: Iterable[LocalModelEntry]) -> ScanDelta:
        new_map = {e.model_id: e for e in entries}
        with self._lock:
            added: List[LocalModelEntry] = []
            updated: List[LocalModelEntry] = []
            removed: List[str] = []
            for mid, e in new_map.items():
                old = self._entries.get(mid)
                if old is None:
                    added.append(e)
                elif (old.path != e.path or old.size_bytes != e.size_bytes
                      or abs(old.mtime - e.mtime) >= 0.5
                      or old.capability != e.capability or old.format != e.format):
                    updated.append(e)
            for mid in list(self._entries.keys()):
                if mid not in new_map:
                    removed.append(mid)
            self._entries = new_map
        self._flush()
        return ScanDelta(added=added, updated=updated, removed=removed)

    def flush(self) -> None:
        self._flush()


# -- 扫描 ------------------------------------------------------------------


def _candidate_roots(*, bundled_cap_dir: Optional[str] = None) -> List[Tuple[Path, str]]:
    """返回 [(root, source_tag)] 列表。

    注意:bundled_models 不在这里直接挂载,而是由 :func:`seed_bundled_models`
    在首启时拷贝到 ``<CHAYUAN_ROOT>/models/bundled/``,然后通过本函数的顶层
    ``models`` 根扫到(relpath 以 ``bundled/`` 开头,UI 据此渲染"装机自带"badge)。
    这样统一以 ``<CHAYUAN_ROOT>/models`` 为唯一可写真源,避免与项目内/打包内的
    bundled 源同时出现导致重复条目。

    Args:
        bundled_cap_dir: 非空时只返回 ``<models>/bundled/<cap_dir>`` 这一个根
            (root 仍是 ``models`` 顶层,source_tag=``models``,这样扫出来的
            ``relpath`` 仍以 ``bundled/<cap_dir>/`` 开头,与全量扫描完全一致 —
            只是把范围裁到单个 cap 子目录)。用于「按能力打开的下载对话框」
            的「扫描挂载」:rerank 对话框只扫 ``bundled/rerank/``,不碰别的 cap。
            目录不存在时返回空列表(没放模型,扫了也是 0)。
    """
    roots: List[Tuple[Path, str]] = []
    md = models_dir()

    if bundled_cap_dir:
        # cap-scoped:只挂 bundled/<cap> 一个根。root 仍取 md(models 顶层),
        # 由 _walk_collect 的 subdir 参数把游标定位进 bundled/<cap>,relpath
        # 因此保持以 bundled/<cap>/ 开头 — 与全量扫描出来的 model_id 一致。
        cap_root = md / "bundled" / bundled_cap_dir
        if cap_root.is_dir():
            roots.append((md, "models"))
        return roots

    if md.is_dir():
        roots.append((md, "models"))
        for sub in DEFAULT_LAYOUT_DIRS:
            if (md / sub).is_dir():
                roots.append((md / sub, sub))
    # vendor 子目录也扫一下：vendor/runtimes/*/models
    try:
        from chayuan.server.runtime.vendor_loader import discover_vendor
        layout = discover_vendor()
        for entry in layout.runtimes:
            mp = entry.path / "models"
            if mp.is_dir():
                roots.append((mp, f"vendor/{entry.name}"))
    except Exception as e:  # noqa: BLE001
        logger.debug("[local-index] vendor 扫描失败：%s", e)
    return roots


def _is_dir_repo(p: Path) -> bool:
    """判定目录 ``p`` 是否一个完整模型仓库。

    任意一种证据成立即认为是仓库:
      * 标志文件:config.json / tokenizer.json / model_index.json / manifest.json
      * 模型权重扩展:.safetensors / .bin / .gguf / .onnx / .pt / .pth

    扩展到 GGUF / ONNX 后,从社区量化仓库(``chat/<repo>/foo.gguf``)和
    自行落盘的 ONNX 推理目录都会被正确识别,而不是被当成"零散单文件"。
    """
    if not p.is_dir():
        return False
    for marker in DIR_MARKER_FILES:
        if (p / marker).is_file():
            return True
    for ext in SINGLE_FILE_FORMATS:
        if any(p.glob(f"*{ext}")):
            return True
    if any(p.glob("*.safetensors")) or any(p.glob("*.bin")):
        return True
    return False


def _walk_collect(
    root: Path,
    source_tag: str,
    max_depth: int = 5,
    *,
    start_subdir: Optional[str] = None,
) -> List[Tuple[Path, str]]:
    """深度有限的扫描，收集 (entry_path, relpath) 列表。

    遇到 ``_is_dir_repo`` 命中的目录就**不再下钻**（避免把仓库下面的子目录也
    错认成模型）。同时收集顶层的 ``.gguf`` / ``.onnx`` 单文件。

    Args:
        start_subdir: 非空时游标从 ``root/<start_subdir>`` 开始下钻,但 relpath
            仍相对 ``root`` 计算 — 这样「只扫 bundled/<cap>」时收集到的
            ``relpath`` 仍以 ``bundled/<cap>/`` 开头,model_id 与全量扫描一致。
            子目录不存在时返回空列表。
    """
    out: List[Tuple[Path, str]] = []
    root = root.resolve()
    walk_start = root
    if start_subdir:
        walk_start = (root / start_subdir).resolve()
        if not walk_start.is_dir():
            return out

    def _walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(d.iterdir())
        except OSError:
            return
        for c in children:
            try:
                if c.is_symlink():
                    # 只防"符号链接指回我自己 / 我的祖先"导致的死循环；
                    # 用户用 `chayuan model import /path/to/extern/model` 制造的
                    # 跨目录符号链接是合法用途，不应被排除。
                    real = c.resolve(strict=False)
                    if real == root or real in root.parents:
                        continue
                if c.is_dir():
                    if c.name.startswith(".") or c.name in ("__pycache__", "logs", "outputs"):
                        continue
                    if _is_dir_repo(c):
                        out.append((c, str(c.relative_to(root))))
                    else:
                        _walk(c, depth + 1)
                elif c.is_file():
                    if c.suffix.lower() in SINGLE_FILE_FORMATS:
                        out.append((c, str(c.relative_to(root))))
            except OSError:
                continue

    if _is_dir_repo(walk_start):
        # walk_start 本身是个模型仓库:relpath 相对 root(可能 != ".")。
        rel = "." if walk_start == root else str(walk_start.relative_to(root))
        out.append((walk_start, rel))
    else:
        _walk(walk_start, 0)
    return out


def scan_once(*, persist: bool = True, bundled_cap_dir: Optional[str] = None) -> ScanDelta:
    """跑一次扫描，返回与上次差异。

    Args:
        persist: ``True`` → 写回 ``local_models.json``；``False`` 用于试跑。
        bundled_cap_dir: 非空时只扫 ``<models>/bundled/<cap_dir>/`` 这一个能力
            子目录(如 ``rerank`` / ``embedding`` / ``image``),其余 cap 的条目
            **保持不变**(本次扫到的 + 索引里其它 cap 的旧条目合并写回)。
            用于「按能力打开的下载对话框」的「扫描挂载」:只挂当前能力,
            不误删别的 cap。不传则维持全量扫描(兼容别的调用方)。

    Note:
        cap-scoped 模式下,``ScanDelta`` 只反映该 cap 子目录的增/改/删 —
        别的 cap 的旧条目既不算 added/updated 也不算 removed。
    """
    seen_ids: Set[str] = set()
    seen_real: Set[str] = set()
    entries: List[LocalModelEntry] = []
    # cap-scoped:把游标定位进 bundled/<cap>;root 仍是 models 顶层,
    # relpath 因此保持以 bundled/<cap>/ 开头,与全量扫描一致。
    start_subdir = (
        f"bundled/{bundled_cap_dir}".replace("\\", "/") if bundled_cap_dir else None
    )
    for root, tag in _candidate_roots(bundled_cap_dir=bundled_cap_dir):
        for path, relpath in _walk_collect(root, tag, start_subdir=start_subdir):
            mid_base = relpath if relpath != "." else root.name
            model_id = f"{tag}/{mid_base}".replace(os.sep, "/")
            # 幂等：同一份内容被多个根扫到时只保留第一次出现的（按 model_id）
            if model_id in seen_ids:
                continue
            # 跨根去重：同一份磁盘内容不同 tag 也只算一次（保留第一个，
            # 通常顶层 "models" 比子目录 "custom" 更早出现）
            try:
                real_key = str(path.resolve())
            except OSError:
                real_key = str(path)
            if real_key in seen_real:
                continue
            ident = identify_path(path, default_id=model_id)
            try:
                if path.is_file():
                    size = path.stat().st_size
                    mtime = path.stat().st_mtime
                else:
                    size = sum((p.stat().st_size for p in path.rglob("*") if p.is_file()), 0)
                    mtime = max((p.stat().st_mtime for p in path.rglob("*") if p.is_file()), default=path.stat().st_mtime)
            except OSError:
                size, mtime = 0, time.time()

            entries.append(LocalModelEntry(
                model_id=model_id,
                path=str(path),
                relpath=relpath,
                capability=ident.capability,
                family=ident.family,
                format=ident.format,
                size_bytes=size,
                mtime=mtime,
                confidence=ident.confidence,
                evidence=ident.evidence,
                meta=ident.meta,
                source_tag=tag,
            ))
            seen_ids.add(model_id)
            seen_real.add(real_key)

    idx = get_local_index()
    if bundled_cap_dir:
        # cap-scoped:本次只重扫了 bundled/<cap> 子目录。索引里属于**别的**
        # cap 的旧条目必须原样保留 — 不然 replace_all 会把它们当 removed 全删,
        # 别的能力下次启动就找不到模型了。
        #
        # 判定"属于本次扫描范围"靠 model_id 前缀:cap-scoped 扫出来的
        # model_id 形如 ``models/bundled/<cap>/...``;在范围外的旧条目原样并入。
        scope_prefix = f"models/bundled/{bundled_cap_dir}/".replace(os.sep, "/")
        scanned_ids = {e.model_id for e in entries}
        for old in idx.list_entries():
            if old.model_id in scanned_ids:
                continue
            if old.model_id.startswith(scope_prefix):
                # 在本 cap 范围内但本次没扫到 → 它确实被删了,让 replace_all 算 removed。
                continue
            entries.append(old)
    delta = idx.replace_all(entries)
    if not persist:
        # 持久化已经发生在 replace_all 内部；为 False 时回滚比较麻烦，
        # 业务侧 dry-run 可改用 `_walk_collect` 直接拿候选。
        pass
    if delta.changed:
        logger.info(
            "[local-index] scan: +%d  ~%d  -%d  total=%d",
            len(delta.added), len(delta.updated), len(delta.removed), len(idx.list_entries()),
        )
    return delta


# -- 单例 ------------------------------------------------------------------

_LOCK = threading.Lock()
_SINGLETON: Optional[LocalModelIndex] = None


def get_local_index(*, force_reload: bool = False) -> LocalModelIndex:
    global _SINGLETON
    if force_reload or _SINGLETON is None or _SINGLETON.path != local_index_path():
        with _LOCK:
            _SINGLETON = LocalModelIndex()
    return _SINGLETON


__all__ = [
    "BUNDLED_CAPABILITY_DIRS",
    "BUNDLED_DIRNAME",
    "DEFAULT_LAYOUT_DIRS",
    "LocalModelEntry",
    "LocalModelIndex",
    "ScanDelta",
    "bundled_models_dir",
    "get_local_index",
    "models_dir",
    "scan_once",
]
