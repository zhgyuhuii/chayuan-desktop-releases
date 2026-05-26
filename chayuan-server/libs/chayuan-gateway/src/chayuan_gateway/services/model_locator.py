"""模型文件定位器：给一个 model id，返回它在本机磁盘上的真实路径。

为什么要这一层？
================

* ``Model.path`` 字段对**自己拉的**模型（``chayuan model pull``）是准确的；
* 但**运行时托管**的模型（Ollama / Infinity / ComfyUI）我们 DB 里只有
  ``model_id``，文件落在哪个目录、blob 怎么拼，得问 runtime 自己。

设计
====

* :class:`ModelLocation` —— 统一返回类型；总有 ``runtime``、``model_id`` 两条；
  根据 runtime 不同可能再带 ``path`` / ``dir`` / ``blobs`` / ``cache_kind``。
* :func:`locate` —— 入口；按 ``runtime`` 分发到不同 strategy。
* 每个 strategy 都是 best-effort：找不到就返回 ``found=False``，不抛异常，
  让前端"未知"也能渲染。

支持矩阵
========

* ``ollama``    —— 解析 ``~/.ollama/models/manifests/...`` + ``blobs/sha256-...``
* ``infinity``  —— HF 缓存（``~/.cache/huggingface/hub/models--<owner>--<name>``）
* ``comfyui``   —— ``~/ComfyUI/models/<subfolder>/<file>``
* ``llamacpp``  —— DB 里 ``path`` 直接是 .gguf
* ``vllm``      —— DB 里 ``path`` 或 HF 缓存（同 infinity）
* ``rapidocr`` / ``paddleocr`` / ``cosyvoice`` / ``piper`` / ``funasr``
                —— 都走 DB ``path`` 兜底
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ModelLocation:
    """一条 model id → 文件位置 的查询结果。"""

    model_id: str
    runtime: str
    found: bool = False
    path: str | None = None             # 主权重 / manifest 文件
    dir: str | None = None              # 父目录（前端 "打开文件夹"）
    size_bytes: int = 0
    cache_kind: str | None = None       # "ollama-blobs" | "hf-cache" | "comfyui" | "local"
    blobs: list[dict[str, Any]] = field(default_factory=list)
    notes: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "blobs": list(self.blobs), "extra": dict(self.extra)}


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def _ollama_root() -> Path:
    """``OLLAMA_MODELS`` env > ``$HOME/.ollama/models`` 默认。"""
    env = os.environ.get("OLLAMA_MODELS")
    if env:
        return Path(env)
    return Path.home() / ".ollama" / "models"


def _locate_ollama(model_id: str) -> ModelLocation:
    """``ollama show`` HTTP 也能拿到 modelfile，但这里用文件系统遍历，离线也能查。

    Ollama tag 形如 ``qwen2:0.5b``。manifest 路径：
        $OLLAMA_MODELS/manifests/registry.ollama.ai/library/<name>/<tag>
    manifest 是 JSON，layers[].digest 引用 ``blobs/sha256-<...>``。
    """
    if ":" in model_id:
        name, tag = model_id.split(":", 1)
    else:
        name, tag = model_id, "latest"

    root = _ollama_root()
    manifest_candidates = [
        root / "manifests" / "registry.ollama.ai" / "library" / name / tag,
        root / "manifests" / "ollama.com" / "library" / name / tag,
        root / "manifests" / "registry.ollama.ai" / name / tag,
    ]
    manifest = next((p for p in manifest_candidates if p.is_file()), None)
    if manifest is None:
        return ModelLocation(
            model_id=model_id, runtime="ollama", found=False,
            cache_kind="ollama-blobs",
            notes=f"未找到 manifest；查过：{[str(p) for p in manifest_candidates]}",
        )

    try:
        meta = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return ModelLocation(
            model_id=model_id, runtime="ollama", found=False, path=str(manifest),
            cache_kind="ollama-blobs", notes=f"manifest 解析失败：{e}",
        )

    blobs: list[dict[str, Any]] = []
    total = 0
    weight_path: str | None = None
    weight_size = 0
    for layer in meta.get("layers", []) or []:
        digest = layer.get("digest") or ""
        if not digest:
            continue
        # ``sha256:abcd…`` → ``blobs/sha256-abcd…``
        blob_name = digest.replace(":", "-", 1)
        blob = root / "blobs" / blob_name
        size = int(layer.get("size") or 0)
        total += size
        blobs.append({
            "digest": digest, "size_bytes": size,
            "media_type": layer.get("mediaType"),
            "path": str(blob), "exists": blob.exists(),
        })
        # 找最大的 application/vnd.ollama.image.model 当 "主权重"
        mt = layer.get("mediaType") or ""
        if "image.model" in mt and size >= weight_size:
            weight_path = str(blob)
            weight_size = size

    primary = weight_path or (blobs[0]["path"] if blobs else str(manifest))
    return ModelLocation(
        model_id=model_id, runtime="ollama", found=True,
        path=primary, dir=str(Path(primary).parent),
        size_bytes=weight_size or total,
        cache_kind="ollama-blobs", blobs=blobs,
        extra={"manifest": str(manifest)},
    )


# ---------------------------------------------------------------------------
# HuggingFace 缓存（Infinity / vLLM 默认行为）
# ---------------------------------------------------------------------------

def _hf_cache_root() -> Path:
    """HF cache 目录。优先 ``HF_HOME`` 然后 ``$XDG_CACHE_HOME/huggingface``。"""
    if env := os.environ.get("HF_HOME"):
        return Path(env) / "hub"
    if env := os.environ.get("HUGGINGFACE_HUB_CACHE"):
        return Path(env)
    return Path.home() / ".cache" / "huggingface" / "hub"


def _locate_hf_cache(model_id: str, runtime: str) -> ModelLocation:
    """``BAAI/bge-m3`` → ``<cache>/models--BAAI--bge-m3/snapshots/<rev>/...``"""
    repo = model_id.replace("/", "--")
    snap_root = _hf_cache_root() / f"models--{repo}" / "snapshots"
    if not snap_root.is_dir():
        return ModelLocation(
            model_id=model_id, runtime=runtime, found=False,
            cache_kind="hf-cache",
            notes=f"未在 HF 缓存找到：{snap_root}",
        )
    # 取最新（按 mtime）的 snapshot
    snaps = sorted(
        (p for p in snap_root.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not snaps:
        return ModelLocation(
            model_id=model_id, runtime=runtime, found=False,
            cache_kind="hf-cache",
            notes=f"snapshots/ 为空：{snap_root}",
        )
    snap = snaps[0]
    files = [p for p in snap.rglob("*") if p.is_file() and not p.is_symlink()]
    # 真正的字节通常在 blobs/；snapshots 里是软链
    primary = next(
        (p for p in (snap / "model.safetensors", snap / "pytorch_model.bin")
         if p.exists()),
        files[0] if files else None,
    )
    return ModelLocation(
        model_id=model_id, runtime=runtime, found=True,
        path=str(primary) if primary else str(snap),
        dir=str(snap),
        size_bytes=sum(p.stat().st_size for p in files),
        cache_kind="hf-cache",
        extra={"snapshot": str(snap)},
    )


# ---------------------------------------------------------------------------
# ComfyUI
# ---------------------------------------------------------------------------

def _comfyui_root() -> Path:
    """``COMFYUI_MODELS`` env > ``~/ComfyUI/models``。"""
    if env := os.environ.get("COMFYUI_MODELS"):
        return Path(env)
    return Path.home() / "ComfyUI" / "models"


_COMFY_SUBDIRS = {
    "checkpoint": "checkpoints",
    "checkpoints": "checkpoints",
    "lora": "loras",
    "vae": "vae",
    "safetensors": "checkpoints",
    "svd": "checkpoints",
}


def _locate_comfyui(model_id: str, fmt: str | None) -> ModelLocation:
    """``sd_v15.safetensors`` 之类的 model_id 直接是 ComfyUI 目录下的文件名。"""
    root = _comfyui_root()
    sub = _COMFY_SUBDIRS.get((fmt or "").lower(), "checkpoints")
    target = root / sub / model_id
    if target.is_file():
        return ModelLocation(
            model_id=model_id, runtime="comfyui", found=True,
            path=str(target), dir=str(target.parent),
            size_bytes=target.stat().st_size,
            cache_kind="comfyui",
        )
    # 兜底：在所有子目录下找同名
    for cand in root.rglob(model_id):
        if cand.is_file():
            return ModelLocation(
                model_id=model_id, runtime="comfyui", found=True,
                path=str(cand), dir=str(cand.parent),
                size_bytes=cand.stat().st_size,
                cache_kind="comfyui",
            )
    return ModelLocation(
        model_id=model_id, runtime="comfyui", found=False,
        cache_kind="comfyui",
        notes=f"未在 {root} 下找到 {model_id}；可能还没拷进去。",
    )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

_HF_CACHE_RUNTIMES = {"infinity", "vllm", "transformers", "sentence-transformers"}


def locate(
    model_id: str,
    *,
    runtime: str | None,
    db_path: str | None = None,
    fmt: str | None = None,
) -> ModelLocation:
    """统一入口。

    优先级：
        1. DB 里有 ``path`` 且文件存在 → 直接用（含 llama.cpp、cosyvoice、piper、
           rapidocr、paddleocr、funasr、whispercpp、自建 vLLM ``--model`` 路径）
        2. runtime=ollama → ollama strategy
        3. runtime=comfyui → comfyui strategy
        4. runtime ∈ HF 家族 → hf-cache strategy
        5. 兜底：返回 ``found=False`` + 原 path（如果有）
    """
    rt = (runtime or "").lower()

    if db_path:
        p = Path(db_path)
        if p.exists():
            return ModelLocation(
                model_id=model_id, runtime=rt or "local", found=True,
                path=str(p), dir=str(p.parent),
                size_bytes=p.stat().st_size if p.is_file() else sum(
                    f.stat().st_size for f in p.rglob("*") if f.is_file()
                ),
                cache_kind="local",
            )

    if rt == "ollama":
        return _locate_ollama(model_id)
    if rt == "comfyui":
        return _locate_comfyui(model_id, fmt)
    if rt in _HF_CACHE_RUNTIMES:
        return _locate_hf_cache(model_id, rt)

    # 兜底
    if db_path:
        return ModelLocation(
            model_id=model_id, runtime=rt or "unknown", found=False,
            path=db_path, dir=str(Path(db_path).parent),
            cache_kind="local",
            notes="DB 中记录路径，但磁盘上不存在；可能模型已被删除。",
        )
    return ModelLocation(
        model_id=model_id, runtime=rt or "unknown", found=False,
        notes="无法定位：DB 无 path、runtime 不在已知列表。",
    )
