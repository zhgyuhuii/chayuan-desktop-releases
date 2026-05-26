"""推理引擎子进程启动参数解析。

为什么需要
==========

``supervisor.yaml`` 里的 ``llamacpp`` / ``infinity`` / ``ollama`` 进程被启动
时,**默认没有绑定具体模型** —— ``llama-server`` 不带 ``--model``,启动起来
也不能服务请求;``infinity`` 不带 ``--model-name-or-path``,起来等于空跑。

本模块给出"按 capability defaults + local_index 解析出该进程应该用哪个模型
文件"的纯逻辑层。supervisor 在拉起子进程前调一次:

* :func:`resolve_llamacpp_args` → 额外 args ``["--model", "<gguf path>", ...]``
* :func:`resolve_infinity_args` → 额外 args ``["--model-id", "<repo or path>"]``
* :func:`resolve_ollama_env`    → 环境变量 ``{"OLLAMA_MODELS": "<dir>"}``

解析顺序
========

1. 读 :mod:`config_panel` 的 capability defaults(用户在 GUI 选的);
2. 用 :mod:`local_index` 把 model_id 反查成磁盘路径;
3. 若解析失败:返回空 args / 空 env;子进程仍能启动,但需要前端 GUI 引导
   用户选模型(:func:`resolve_*` 同时返回 :class:`Resolution` 报告字段
   ``reason``,便于排错)。

本模块**纯函数化**:不写盘、不启动进程、不调 supervisor;留给上层调用方做
集成 —— 这样 B2.5 阶段可以独立单测,B3 / B5 / B6 都能复用同一份解析。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from chayuan.server.model_registry.local_index import (
    LocalModelEntry,
    get_local_index,
)

logger = logging.getLogger("chayuan.model_registry.process_args")


@dataclass
class Resolution:
    """单个 process 的参数解析结果。"""

    process: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    resolved_models: Dict[str, str] = field(default_factory=dict)   # cap -> model_id
    missing: List[str] = field(default_factory=list)                 # 没解析出来的 cap
    reason: str = ""                                                 # 人类可读说明

    @property
    def ok(self) -> bool:
        """``True`` 时 args / env 已经准备好;``False`` 时调用方应该 fallback。"""
        return not self.missing and (bool(self.args) or bool(self.env))

    def to_dict(self) -> dict:
        return {
            "process": self.process,
            "args": list(self.args),
            "env": dict(self.env),
            "resolved_models": dict(self.resolved_models),
            "missing": list(self.missing),
            "reason": self.reason,
            "ok": self.ok,
        }


# ───────────────────────── 公共工具 ─────────────────────────


def _load_defaults() -> Dict[str, str]:
    """读 ``model_settings.yaml`` 的 capability defaults。失败时返回空 dict,
    不抛 —— 解析阶段必须容错。"""
    try:
        from chayuan.server.config_panel.runtime_framework_panel import (
            _load_capability_defaults,
        )
        return dict(_load_capability_defaults() or {})
    except Exception as e:  # noqa: BLE001
        logger.debug("[process_args] _load_capability_defaults 失败: %r", e)
        return {}


def _lookup_local(model_id: str) -> Optional[LocalModelEntry]:
    """按 model_id 反查 :mod:`local_index`。"""
    if not model_id:
        return None
    try:
        return get_local_index().get(model_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("[process_args] local_index.get 失败: %r", e)
        return None


def _pick_by_capability_local_only(
    local_cap: str,
    *,
    prefer_format: Optional[str] = None,
) -> Optional[LocalModelEntry]:
    """从 local_index 按 capability 找一个候选(default 缺失时的兜底)。

    Args:
        local_cap: identifier 用的 capability 字符串。
        prefer_format: 若指定,优先返回该 ``format`` 的条目(例如 llama.cpp
            必须 ``gguf``;infinity 偏好 ``hf_transformers``)。
    """
    try:
        cands = get_local_index().by_capability(local_cap)
    except Exception as e:  # noqa: BLE001
        logger.debug("[process_args] by_capability 失败: %r", e)
        return None
    if not cands:
        return None
    if prefer_format:
        for c in cands:
            if c.format == prefer_format:
                return c
    return cands[0]


def _resolve(panel_cap: str, *, prefer_format: Optional[str] = None,
             local_cap: Optional[str] = None) -> Tuple[Optional[LocalModelEntry], str]:
    """通用解析:default(panel_cap) → local_index 反查;失败时按 capability
    兜底选第一个本地候选。

    Args:
        panel_cap: ``CAPABILITY_LABELS`` 短名(``chat`` / ``embedding`` / ``rerank``)。
        prefer_format: 兜底匹配时偏好的 ``format`` 字段。
        local_cap: 兜底时使用的 local_index capability 字符串;不传则由
            :data:`candidates_bridge.LOCAL_TO_PANEL_CAP` 反查。

    Returns:
        ``(entry, reason)``;``entry`` 可能为 None。
    """
    defaults = _load_defaults()
    mid = defaults.get(panel_cap) or ""

    if mid:
        e = _lookup_local(mid)
        if e is not None:
            return e, f"resolved by default model_id={mid!r}"

    # 兜底:按 capability 在 local_index 里挑第一条
    if not local_cap:
        from chayuan.server.model_registry.candidates_bridge import (
            LOCAL_TO_PANEL_CAP,
        )
        for k, v in LOCAL_TO_PANEL_CAP.items():
            if v == panel_cap:
                local_cap = k
                break
    if not local_cap:
        return None, f"no local capability mapped for panel cap {panel_cap!r}"

    e = _pick_by_capability_local_only(local_cap, prefer_format=prefer_format)
    if e is not None:
        return e, (
            f"fallback by capability {local_cap!r}"
            + (f" prefer_format={prefer_format!r}" if prefer_format else "")
        )
    return None, f"no local candidate for {local_cap!r}"


# ───────────────────────── llamacpp ─────────────────────────

_LLAMACPP_CAPABILITIES = ("chat", "embedding", "rerank")
_LLAMACPP_LOCAL_CAP_MAP = {
    "chat": "chat",
    "embedding": "text-embedding",
    "rerank": "rerank",
}

# embedding 物理批 / context 大小。
# BERT 类编码器(bge-m3 等)做 embedding 时,llama.cpp 要求**整段输入落在
# 同一个物理批(ubatch)** 里 —— 一段超过 ubatch 的文本会被 llama-server
# 直接拒绝,报 500 "input (N tokens) is too large to process. increase the
# physical batch size"。llama-server 默认 ubatch 仅 512,而知识库切块
# (settings.CHUNK_SIZE 默认 750 字)叠加中文 token 密度、递归切分器在
# 长段落无分隔符时的过切,经常切出 600~1200+ token 的块,于是上传文档时
# 偶发向量化 500。
# 这里把 embedding llama-server 的 ctx / batch / ubatch 一起抬到 4096,
# 覆盖任何正常切块(约 3000+ 中文字),满足 ubatch ≤ batch ≤ ctx 约束;
# bge-m3 这类编码器在该尺寸下 compute buffer 约 1GB,办公单机可承受。
_EMBEDDING_N_CTX = 4096


def _expand_to_model_file(
    entry: LocalModelEntry,
    r: "Resolution",
    capability: str,
    *,
    suffix: str,
    label: str,
) -> Optional[Path]:
    """把 entry.path(可能是目录)展开成一个具体权重文件。

    scanner 的 _walk_collect 把 _is_dir_repo 命中的目录(只要里面有 .gguf / .bin
    就算)登记成单 entry,entry.path = 目录。llama-server / whisper-server 的
    --model 都要文件路径,Windows 上 open(dir) → EACCES,llama 报 "Permission denied"。

    Args:
        entry: 由 _resolve 返回的 LocalModelEntry。
        r: 当前 Resolution(失败时往里写 missing/reason)。
        capability: 用于错误信息。
        suffix: ``.gguf`` 或 ``.bin``,按引擎选。
        label: 错误信息里的人类标签(``gguf`` / ``ggml/.bin``)。

    Returns:
        权重文件 Path;失败返回 None,并往 r 写错。
    """
    p = Path(entry.path)
    if p.is_file():
        return p
    if not p.is_dir():
        r.missing.append(capability)
        r.reason = f"{capability} entry {entry.model_id!r} 路径既不是文件也不是目录:{p}"
        return None
    # rglob 兼容 HF snapshot 偶尔落子目录的布局
    candidates = [c for c in p.rglob(f"*{suffix}") if c.is_file()]
    if not candidates:
        r.missing.append(capability)
        r.reason = (
            f"{capability} entry {entry.model_id!r} 是目录 {p} 但里面没找到 {label} 文件"
        )
        return None

    # 多权重文件时的选择策略:
    #   1. 优先"文件名跟目录名公共前缀最长"的 —— 用户偶尔把别的模型权重
    #      误丢进某个模型文件夹(线上诊断:rerank/bge-reranker-v2-m3/ 里
    #      塞了 gte-...gguf)。文件名跟目录名对得上 = 这文件夹本来要装的
    #      模型;对不上 = 误入的,排后。
    #   2. 同前缀(典型:同一仓库的 Q3/Q4/Q8 多量化)取最小 —— 小量化更稳,
    #      大文件还可能撞 Windows installer 2GB 上限。
    def _norm(s: str) -> str:
        return "".join(ch for ch in s.lower() if ch.isalnum())

    def _common_prefix_len(a: str, b: str) -> int:
        n = 0
        for x, y in zip(a, b):
            if x != y:
                break
            n += 1
        return n

    dir_norm = _norm(p.name)
    candidates.sort(
        key=lambda c: (-_common_prefix_len(dir_norm, _norm(c.stem)), c.stat().st_size)
    )
    return candidates[0]


def _expand_to_gguf_file(
    entry: LocalModelEntry, r: "Resolution", capability: str
) -> Optional[Path]:
    return _expand_to_model_file(entry, r, capability, suffix=".gguf", label=".gguf")


def _expand_to_ggml_bin(
    entry: LocalModelEntry, r: "Resolution", capability: str
) -> Optional[Path]:
    return _expand_to_model_file(entry, r, capability, suffix=".bin", label="ggml .bin")


def resolve_llamacpp_args(
    *,
    capability: str = "chat",
    n_threads: Optional[int] = None,
    n_gpu_layers: Optional[int] = None,
    n_ctx: int = 4096,
) -> Resolution:
    """``llama-server`` 启动时需要的额外 args(在 supervisor.yaml 原有 host/port
    之外追加)。

    capability:
      * ``chat``      → chat default + GGUF + --ctx-size
      * ``embedding`` → text-embedding default + GGUF + --embedding --pooling cls
      * ``rerank``    → rerank default + GGUF + --reranking
    """
    if capability not in _LLAMACPP_CAPABILITIES:
        raise ValueError(f"Unknown capability for llamacpp: {capability!r}")

    local_cap = _LLAMACPP_LOCAL_CAP_MAP[capability]
    r = Resolution(process="llamacpp")
    entry, reason = _resolve(capability, prefer_format="gguf", local_cap=local_cap)
    if entry is None or entry.format != "gguf":
        r.missing.append(capability)
        r.reason = reason if entry is None else (
            f"{capability} model {entry.model_id!r} format={entry.format!r} 不是 gguf"
        )
        return r

    # scanner 把含 .gguf 的目录登记成 entry,entry.path 可能是目录。llama-server
    # --model 期望具体 .gguf 文件路径,直接传目录会在 gguf_init_from_file 阶段
    # 拿到 EACCES / Permission denied(Windows 上 open(dir) 即返此错)。
    # 此处展开:目录 → rglob *.gguf 选一个文件(用 rglob 兼容 HF snapshot 偶尔
    # 把权重落在子目录的布局)。多个候选(同一仓库不同 quantization)时取最小,
    # 因为大文件可能撞 Windows installer 2GB 上限,小量化更稳。
    model_path = _expand_to_gguf_file(entry, r, capability)
    if model_path is None:
        return r
    r.args.extend(["--model", str(model_path)])
    if capability == "chat":
        r.args.extend(["--ctx-size", str(int(n_ctx))])
    elif capability == "embedding":
        # --batch-size / --ubatch-size 必须 ≥ 单段文本 token 数,否则
        # llama-server 对超长块返 500(详见 _EMBEDDING_N_CTX 注释)。
        # ubatch ≤ batch ≤ ctx,三者取同值最简单且都满足约束。
        r.args.extend([
            "--embedding", "--pooling", "cls",
            "--ctx-size", str(_EMBEDDING_N_CTX),
            "--batch-size", str(_EMBEDDING_N_CTX),
            "--ubatch-size", str(_EMBEDDING_N_CTX),
        ])
    elif capability == "rerank":
        r.args.extend(["--reranking"])

    if n_threads is not None:
        r.args.extend(["--threads", str(int(n_threads))])
    if n_gpu_layers is not None:
        r.args.extend(["--n-gpu-layers", str(int(n_gpu_layers))])
    r.resolved_models[capability] = entry.model_id
    r.reason = reason
    return r


# ───────────────────────── infinity ─────────────────────────

_INFINITY_CAPABILITIES = ("image-embedding",)
# scanner identifier 现在(identifier.py:_CAPABILITY_BY_MODELTYPE)把 CLIP / ViT 标
# 'image-embedding',把 BLIP / LLaVA / qwen2_vl 标 'image-to-text'。两者**不同 cap**。
# 历史 bug:这里曾经写 'image-to-text',导致 OCR(PP-OCRv3/PP-OCRv4 也是 image-to-text
# +onnx)被当成 image-embedding 候选,然后 _resolve 返回 OCR → 走到 format='onnx'
# 拒绝分支报"是 OCR ONNX,不是 CLIP-like 模型"。
# 修复:直接查 'image-embedding',让 CLIP 命中、OCR 自然排除。
_INFINITY_LOCAL_CAP_MAP = {
    "image-embedding": "image-embedding",
}


def resolve_image_embedding_args(
    *,
    capability: str = "image-embedding",
    n_threads: Optional[int] = None,
) -> Resolution:
    """``chayuan.server.image_source.infinity_server`` (Python sidecar) 启动 args。

    Plan 3D 起新加:跟 Plan 1 老 :func:`resolve_infinity_args` 不一样 —
    本函数返 `-m chayuan.server.image_source.infinity_server --model <id>` 这种
    Python `-m module` 启动的 args(供 SidecarRuntimeManager engine='infinity' 用);
    Plan 1 的 :func:`resolve_infinity_args` 是给 michaelf34/infinity binary 用的
    `--model-id <id>` args(两个函数并存,不互相替代)。

    PyInstaller frozen 模式:`sys.executable` 是 chayuan-server.exe 不是 python.exe,
    不能 `-m`;改返 `--sidecar-mode=image-embedding` args 让 chayuan-server.exe
    自我转化(主入口分支实现留给后续 plan;本 plan 期间 frozen 模式 sidecar 起不来
    时 facade 自动 fallback in-process)。

    capability:
      * ``image-embedding`` → image default + transformers/safetensors 模型 + --model
    """
    import sys

    if capability not in _INFINITY_CAPABILITIES:
        raise ValueError(f"Unknown capability for infinity: {capability!r}")

    local_cap = _INFINITY_LOCAL_CAP_MAP[capability]
    r = Resolution(process="infinity")
    # prefer_format='hf_transformers' 让 _pick_by_capability_local_only 把
    # CLIP(hf_transformers + safetensors)排到 OCR(onnx)前面。
    entry, reason = _resolve(capability, prefer_format="hf_transformers", local_cap=local_cap)
    if entry is None:
        r.missing.append(capability)
        r.reason = reason
        return r
    # 显式拒 OCR onnx 模型:scanner 同样标 image-to-text(PP-OCRv3/PP-OCRv4),
    # 但 OCR 走 rapidocr 进程内不走 infinity sidecar,被 prefer_format fallback
    # 抓到会让 infinity 加载失败。
    if entry.format == "onnx":
        r.missing.append(capability)
        r.reason = (
            f"image-embedding 候选 {entry.model_id!r} format={entry.format!r} 是 OCR ONNX,"
            f"不是 CLIP-like 模型;安装 transformers/safetensors 版 CLIP 后再启动"
        )
        return r

    frozen = getattr(sys, "frozen", False)
    if frozen:
        # PyInstaller: chayuan-server.exe 自我转化 sidecar(主入口需实现 --sidecar-mode)
        r.args.extend([
            "--sidecar-mode", capability,
            "--model", entry.model_id,
        ])
    else:
        # 开发模式: sys.executable 是 python(.exe),直接 -m
        r.args.extend([
            "-m", "chayuan.server.image_source.infinity_server",
            "--model", entry.model_id,
        ])

    if n_threads is not None:
        r.args.extend(["--threads", str(int(n_threads))])
    r.resolved_models[capability] = entry.model_id
    r.reason = reason
    return r


def resolve_infinity_args() -> Resolution:
    """``infinity_emb``(michaelf34/infinity binary)启动时需要的额外 args。

    Plan 1 老接口,处理 michaelf34/infinity binary 自身的多模型挂载:
    * ``embedding`` ← capability default(text-embedding;偏好 hf_transformers 格式)
    * ``rerank``    ← capability default(rerank;同上)

    两类同时可选;只要解出一个就发 args(``--model-id <ID> --model-id <ID>``
    重复列出 = infinity 同时挂载多模型)。

    Plan 3D 起新加 :func:`resolve_image_embedding_args`(签名不同,处理
    image-embedding capability)。两个函数共存,本函数不替换。
    """
    r = Resolution(process="infinity")
    for panel_cap, local_cap in (("embedding", "text-embedding"), ("rerank", "rerank")):
        entry, reason = _resolve(panel_cap, prefer_format="hf_transformers",
                                 local_cap=local_cap)
        if entry is None:
            r.missing.append(panel_cap)
            continue
        # infinity 用 model_id(repo 风格)或本地路径都可
        r.args.extend(["--model-id", entry.path or entry.model_id])
        r.resolved_models[panel_cap] = entry.model_id
        if not r.reason:
            r.reason = reason
    return r


# ───────────────────────── ollama ─────────────────────────


def resolve_ollama_env(*, models_dir: Optional[str] = None) -> Resolution:
    """``ollama`` 通过 ``OLLAMA_MODELS`` 环境变量指定模型仓库根目录。

    Args:
        models_dir: 显式传 dir 覆盖;不传则从 :func:`local_index.models_dir`
            的 chat 子目录推导(layout.yaml 约定 chat 模型释放到
            ``<CHAYUAN_ROOT>/models/chat/``)。
    """
    r = Resolution(process="ollama")
    if models_dir:
        r.env["OLLAMA_MODELS"] = str(models_dir)
        r.reason = "explicit models_dir"
        return r

    try:
        from chayuan.server.model_registry.local_index import models_dir as md
        chat_dir = md() / "chat" / "_ollama"
        r.env["OLLAMA_MODELS"] = str(chat_dir)
        r.reason = f"derived from models_dir() = {chat_dir!s}"
    except Exception as e:  # noqa: BLE001
        r.missing.append("ollama_models_dir")
        r.reason = f"failed to derive: {e!r}"
    return r


# ───────────────────────── whispercpp ─────────────────────────

_WHISPER_CAPABILITIES = ("asr",)
# scanner identifier 把 whisper / wav2vec2 等都标 'speech-to-text'(见
# identifier.py:_PATH_HINTS + _CAPABILITY_BY_MODELTYPE),用这个去 local_index 查
# 才能命中。原来写 'asr' 永远找不到本地 ggml-tiny.bin。
_WHISPER_LOCAL_CAP_MAP = {
    "asr": "speech-to-text",
}


def resolve_whisper_args(
    *,
    capability: str = "asr",
    n_threads: Optional[int] = None,
) -> Resolution:
    """``whisper-server`` 启动时的 args。

    capability:
      * ``asr`` → asr default + ggml 模型 + --model <path>
    """
    if capability not in _WHISPER_CAPABILITIES:
        raise ValueError(f"Unknown capability for whisper: {capability!r}")

    local_cap = _WHISPER_LOCAL_CAP_MAP[capability]
    r = Resolution(process="whispercpp")
    entry, reason = _resolve(capability, prefer_format="ggml", local_cap=local_cap)
    if entry is None or entry.format != "ggml":
        r.missing.append(capability)
        r.reason = reason if entry is None else (
            f"{capability} model {entry.model_id!r} format={entry.format!r} 不是 ggml"
        )
        return r

    # 同 llama-server:entry.path 可能是目录,whisper-server --model 也要文件路径
    model_path = _expand_to_ggml_bin(entry, r, capability)
    if model_path is None:
        return r
    r.args.extend(["--model", str(model_path)])
    if n_threads is not None:
        r.args.extend(["--threads", str(int(n_threads))])
    r.resolved_models[capability] = entry.model_id
    r.reason = reason
    return r


# ───────────────────────── 集成入口 ─────────────────────────


def resolve_all() -> Dict[str, Resolution]:
    """一次跑完三个推理引擎的解析。

    给 supervisor 启动钩子 / admin 调试接口用。结果是只读快照,**不会**写盘
    或重启进程。
    """
    return {
        "llamacpp": resolve_llamacpp_args(),
        "infinity": resolve_infinity_args(),
        "ollama":   resolve_ollama_env(),
    }


__all__ = [
    "Resolution",
    "resolve_llamacpp_args",
    "resolve_image_embedding_args",
    "resolve_infinity_args",
    "resolve_ollama_env",
    "resolve_whisper_args",
    "resolve_all",
]
