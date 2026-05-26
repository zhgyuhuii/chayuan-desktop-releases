"""``vendor/`` 目录的扫描与可用性判断。

为什么要 vendor 目录？
======================

很多开发 / 离线部署场景（内网医院、政企机房）希望"开箱即用"：

* 不允许容器里再去 dockerhub 拉镜像；
* 不允许 pip 装重型 wheels（torch / paddle）；
* 不允许 ollama / vllm / minio 服务通过外网下载。

约定：把这些**第三方可执行文件 / 容器目录 / 模型权重**预先放进
``<repo>/vendor/`` 或 ``<CHAYUAN_ROOT>/vendor/``，本模块负责扫描判定。

目录约定
========

::

    vendor/
    ├── README.md                          ← 总览（本仓库已写）
    ├── services/
    │   ├── postgres/
    │   │   ├── bin/postgres               ← 离线二进制（可选）
    │   │   ├── docker-compose.yml         ← 或 docker 启动方式
    │   │   └── README.md                  ← 本服务的下载链接 / 端口约定
    │   ├── redis/
    │   ├── minio/
    │   └── milvus/
    └── runtimes/
        ├── ollama/bin/ollama
        ├── llama-cpp/bin/llama-server
        ├── vllm/                          ← venv / wheels
        ├── whisper-cpp/bin/whisper-server
        ├── piper/bin/piper
        ├── rapidocr/                      ← onnx 权重
        └── comfyui/                       ← 完整 ComfyUI 仓库

"可执行文件"判定
================

* POSIX：``os.access(path, X_OK)`` 且文件存在；
* Windows：在常见后缀（``.exe`` / ``.bat`` / ``.cmd`` / ``.ps1``）里探一下；
* Docker：存在 ``docker-compose.yml`` 即视为可用（但 ``ready`` = compose 起得来与否，
  由调用方真正起服务时再检测）。

公开 API：
* :class:`VendorEntry` —— 单个 vendor 子目录的扫描结果
* :class:`VendorLayout` —— 整个 vendor 目录的扫描结果
* :func:`discover_vendor` —— 主入口
"""
from __future__ import annotations

import logging
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger("chayuan.runtime.vendor_loader")

VENDOR_DIRNAME = "vendor"

_IS_WIN = (os.name == "nt")
_WIN_EXEC_SUFFIX = (".exe", ".bat", ".cmd", ".ps1")


# 已知 vendor 子目录 → 用途说明（前端 + CLI 渲染时给出友好文案）。
# 注意：这里只列**官方支持**的；新增 vendor 可在 README.md 里说明，扫描器会把
# 任何子目录也当 unknown 收录。
_KNOWN_SERVICES: Dict[str, Dict[str, str]] = {
    "postgres":   {"label": "PostgreSQL",    "kind": "postgres",     "default_port": "35432"},
    "redis":      {"label": "Redis",         "kind": "redis",        "default_port": "36379"},
    "minio":      {"label": "MinIO",         "kind": "minio",        "default_port": "39000"},
    "milvus":     {"label": "Milvus",        "kind": "milvus",       "default_port": "39530"},
    "elastic":    {"label": "Elasticsearch", "kind": "elasticsearch","default_port": "39200"},
}

_KNOWN_RUNTIMES: Dict[str, Dict[str, str]] = {
    "ollama":     {"label": "Ollama",        "kind": "llm",     "binary": "ollama",      "default_port": "31434"},
    "llama-cpp":  {"label": "llama.cpp",     "kind": "llm",     "binary": "llama-server","default_port": "38080"},
    "vllm":       {"label": "vLLM",          "kind": "llm",     "binary": "vllm",        "default_port": "38000"},
    "whisper-cpp":{"label": "whisper.cpp",   "kind": "asr",     "binary": "whisper-server","default_port": "38010"},
    "funasr":     {"label": "FunASR",        "kind": "asr",     "binary": "funasr",      "default_port": "38020"},
    "piper":      {"label": "Piper TTS",     "kind": "tts",     "binary": "piper",       "default_port": "38030"},
    "cosyvoice":  {"label": "CosyVoice",     "kind": "tts",     "binary": "cosyvoice",   "default_port": "38040"},
    "rapidocr":   {"label": "RapidOCR",      "kind": "ocr",     "binary": "",            "default_port": "38050"},
    "paddleocr":  {"label": "PaddleOCR",     "kind": "ocr",     "binary": "",            "default_port": "38060"},
    "comfyui":    {"label": "ComfyUI",       "kind": "image",   "binary": "main.py",     "default_port": "38188"},
    "infinity":   {"label": "Infinity",      "kind": "embed",   "binary": "infinity_emb","default_port": "37997"},
}


@dataclass
class VendorEntry:
    """单个 vendor/<group>/<name>/ 子目录的扫描结果。"""

    group: str            # "services" / "runtimes"
    name: str             # 子目录名
    label: str            # 友好显示名
    kind: str             # "postgres" / "redis" / "llm" / "asr" / ...
    path: Path            # 实际目录绝对路径
    binary: str = ""      # 探到的可执行文件相对路径（空 = 没探到）
    docker_compose: str = ""  # 若存在 docker-compose.yml 的相对路径
    readme: str = ""      # 子目录 README.md 相对路径（如有）
    default_port: str = ""
    available: bool = False     # 至少一项（binary / compose）可用
    issues: List[str] = field(default_factory=list)  # 不可用时的解释

    def to_dict(self) -> Dict[str, object]:
        return {
            "group": self.group, "name": self.name, "label": self.label,
            "kind": self.kind, "path": str(self.path),
            "binary": self.binary, "docker_compose": self.docker_compose,
            "readme": self.readme, "default_port": self.default_port,
            "available": self.available, "issues": list(self.issues),
        }


@dataclass
class VendorLayout:
    """整个 vendor 根目录的扫描结果。"""

    root: Path
    services: List[VendorEntry] = field(default_factory=list)
    runtimes: List[VendorEntry] = field(default_factory=list)
    unknown: List[VendorEntry] = field(default_factory=list)

    def all(self) -> List[VendorEntry]:
        return [*self.services, *self.runtimes, *self.unknown]

    def to_dict(self) -> Dict[str, object]:
        return {
            "root": str(self.root),
            "services": [e.to_dict() for e in self.services],
            "runtimes": [e.to_dict() for e in self.runtimes],
            "unknown":  [e.to_dict() for e in self.unknown],
        }


def _is_executable(p: Path) -> bool:
    if not p.is_file():
        return False
    if _IS_WIN:
        return p.suffix.lower() in _WIN_EXEC_SUFFIX
    return os.access(str(p), os.X_OK)


def _find_binary(dir_: Path, hint: str) -> str:
    """在 ``<dir>/bin`` 里寻找 ``hint`` / ``hint.exe`` / 任何可执行文件。

    返回相对 ``dir_`` 的路径；找不到返回空串。
    """
    bin_dir = dir_ / "bin"
    if not bin_dir.is_dir():
        return ""
    candidates: List[Path] = []
    if hint:
        candidates.append(bin_dir / hint)
        if _IS_WIN:
            candidates.append(bin_dir / f"{hint}.exe")
    for p in candidates:
        if _is_executable(p):
            try:
                return str(p.relative_to(dir_))
            except ValueError:
                return str(p)
    # 回退：bin/ 下任意可执行
    for p in sorted(bin_dir.iterdir()):
        if _is_executable(p):
            try:
                return str(p.relative_to(dir_))
            except ValueError:
                return str(p)
    return ""


def _scan_group(
    root: Path, group: str, known: Dict[str, Dict[str, str]],
) -> List[VendorEntry]:
    out: List[VendorEntry] = []
    base = root / group
    if not base.is_dir():
        return out
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        meta = known.get(name) or {}
        binary_hint = str(meta.get("binary") or "")
        binary = _find_binary(child, binary_hint) if group == "runtimes" else ""
        compose = ""
        for fn in ("docker-compose.yml", "docker-compose.yaml", "compose.yml"):
            if (child / fn).is_file():
                compose = fn
                break
        readme = "README.md" if (child / "README.md").is_file() else ""

        entry = VendorEntry(
            group=group,
            name=name,
            label=str(meta.get("label") or name),
            kind=str(meta.get("kind") or ("service" if group == "services" else "runtime")),
            path=child,
            binary=binary,
            docker_compose=compose,
            readme=readme,
            default_port=str(meta.get("default_port") or ""),
        )
        # 可用性判定
        if binary or compose:
            entry.available = True
        else:
            entry.issues.append(
                "未发现可执行文件（请放到 bin/）也未发现 docker-compose.yml；"
                "可参考子目录下 README.md 或 vendor/README.md 的下载指引"
            )
        out.append(entry)
    return out


def _scan_unknown(root: Path, known_groups: Iterable[str]) -> List[VendorEntry]:
    """``vendor/`` 一级目录里既不属于 services 也不属于 runtimes 的"""
    out: List[VendorEntry] = []
    if not root.is_dir():
        return out
    known_set = {*known_groups, "README.md", "readme.md", ".gitkeep"}
    for child in sorted(root.iterdir()):
        if child.name in known_set:
            continue
        if child.is_dir():
            out.append(VendorEntry(
                group="unknown",
                name=child.name,
                label=child.name,
                kind="unknown",
                path=child,
                available=False,
                issues=["未识别的 vendor 子目录；请放到 vendor/services/ 或 vendor/runtimes/ 下"],
            ))
    return out


def discover_vendor(*search_paths: Path) -> VendorLayout:
    """扫描 vendor 目录。

    Args:
        search_paths: 候选根目录；按顺序找第一个**存在的** ``vendor/``。
            未传时按 ``<CHAYUAN_ROOT>/vendor`` → ``<repo>/vendor``（CWD 兜底）顺序探。

    Returns:
        合并后的 :class:`VendorLayout`。如果两个路径都不存在，仍返回空对象
        （``services=[]``、``root=`` 第一个候选），方便前端区分"目录不存在"。
    """
    candidates: List[Path] = list(search_paths) if search_paths else []
    if not candidates:
        try:
            from chayuan.settings import CHAYUAN_ROOT
            candidates.append(Path(CHAYUAN_ROOT) / VENDOR_DIRNAME)
        except Exception:  # noqa: BLE001
            pass
        candidates.append(Path.cwd() / VENDOR_DIRNAME)

    chosen: Optional[Path] = None
    for c in candidates:
        if c and Path(c).is_dir():
            chosen = Path(c)
            break

    layout = VendorLayout(root=chosen or candidates[0])
    if chosen is None:
        return layout

    layout.services = _scan_group(chosen, "services", _KNOWN_SERVICES)
    layout.runtimes = _scan_group(chosen, "runtimes", _KNOWN_RUNTIMES)
    layout.unknown = _scan_unknown(chosen, ("services", "runtimes"))

    logger.info(
        "[vendor] 扫描 %s：services=%d / runtimes=%d / unknown=%d / available=%d",
        chosen,
        len(layout.services), len(layout.runtimes), len(layout.unknown),
        sum(1 for e in layout.all() if e.available),
    )
    return layout


def known_services() -> Dict[str, Dict[str, str]]:
    return dict(_KNOWN_SERVICES)


def known_runtimes() -> Dict[str, Dict[str, str]]:
    return dict(_KNOWN_RUNTIMES)


__all__ = [
    "VENDOR_DIRNAME",
    "VendorEntry",
    "VendorLayout",
    "discover_vendor",
    "known_services",
    "known_runtimes",
]
