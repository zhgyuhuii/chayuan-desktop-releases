#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键下载 / 替换 vendor/bundled_models/ 里的"瘦身默认集"。

特性
====

1. **多 source + 候选列表自动 fallback**:每个 cap 配置 HF 候选清单 + MS 候选
   清单。按 ``--source`` 决定顺序(auto = HF→MS,modelscope = MS→HF),逐个
   尝试,第一个能下载成功的胜出;一路 404 / 超时就报错并打印所有尝试明细。
2. **绕开 hf CLI 各种坑**:直接用 ``huggingface_hub`` Python API,镜像在脚本
   里写死,HF_ENDPOINT 不持久化也不影响本脚本。
3. **HF Xet 兜底**:HF 的 Xet LFS 后端(``cas-bridge.xethub.hf.co``)
   不被 hf-mirror.com 代理,大文件经常卡 0%。``--source modelscope`` 直接跳过
   HF 走 ModelScope(国内 Alibaba 镜像,稳)。
4. **失败不阻断后续**:某个 cap 全 source 都挂了不影响其它 cap;结尾汇总
   失败明细 + 排查建议。

默认目标(瘦身集,全部 < 2 GB 单文件,Windows installer 友好)
==============================================================

| cap        | 推荐                            | 备选                  | 大小    |
|------------|---------------------------------|-----------------------|---------|
| chat       | Qwen3-4B-Instruct-2507 Q3_K_S   | Q3_K_M / IQ3          | ~1.8 GB |
| embedding  | bge-m3 GGUF Q5_K_M              | -                     | ~468 MB |
| rerank     | bge-reranker-v2-m3 GGUF Q4_K_M  | -                     | ~438 MB |
| asr        | whisper.cpp ggml-tiny           | -                     | ~74 MB  |

用法
====

# 全量下,默认 HF→MS auto fallback
python scripts/install-bundled-models.py

# 已知 HF Xet 拉不动,直接走 MS(国内最稳)
python scripts/install-bundled-models.py --source modelscope

# 干净换装(清掉旧的再下)
python scripts/install-bundled-models.py --clean

# 只下指定 cap
python scripts/install-bundled-models.py --only chat
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

# ===== 镜像优先级:CLI 显式 > 环境变量 > 默认 hf-mirror.com =====
DEFAULT_ENDPOINTS = [
    "https://hf-mirror.com",   # 国内首选(免登录、社区维护)
    "https://huggingface.co",  # 兜底
]

# 让 hf 在 xethub 卡 0% 时早点放弃,触发 fallback。30s × 5 重试 ≈ 2-3 分钟。
_HF_DOWNLOAD_TIMEOUT_DEFAULT = "30"

# ===== 下载清单 =====
# 每个 cap 的 ``hf`` / ``ms`` 字段都是 **候选清单**,按顺序逐个尝试,第一个
# 能下载就停。snapshot_download 的 ``allow_patterns`` 用来限定整仓内只拉
# 需要的文件(尤其是 GGUF 仓库通常含十几个量化版本,只要其中一个 < 2 GB 的)。
#
# 添加新候选:复制一条 {repo, allow_patterns, ...},仿照下面格式追加即可。
#
# dest_subdir:本地落盘到 ``<cap>/<dest_subdir>/`` 下;chayuan 扫描器只看
# 后缀和标志文件,不在乎子目录名。
MANIFEST: dict[str, dict[str, Any]] = {
    "chat": {
        "approx_mb": 1850,
        "note": "Qwen3-4B chat,Q3 系列量化(~1.7-1.95 GB,Windows installer 友好)",
        "dest_subdir": "Qwen3-4B-Instruct-2507-GGUF",
        # 2026-05-16 全部 HF 验证过:HEAD api/models/<repo> 返 200。
        # 老 bartowski/Qwen3-4B-Instruct-2507-GGUF 已被作者改名(返 401),
        # 用 bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF 新路径。
        "hf": [
            # 首选:unsloth 全量化矩阵(Q3_K_S/M/L、IQ3 系列),77k downloads 最稳
            {"repo": "unsloth/Qwen3-4B-Instruct-2507-GGUF",
             "allow_patterns": ["*Q3_K_S*"]},
            # 备选:lmstudio-community,LM Studio 团队整理,9.8k downloads
            {"repo": "lmstudio-community/Qwen3-4B-Instruct-2507-GGUF",
             "allow_patterns": ["*Q3_K_S*"]},
            # 备选:bartowski 新路径(老路径 bartowski/Qwen3-4B-Instruct-2507-GGUF 401)
            {"repo": "bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF",
             "allow_patterns": ["*Q3_K_S*"]},
            # 兜底:gabriellarson 也做全量化(73 downloads,新)
            {"repo": "gabriellarson/Qwen3-4B-Instruct-2507-GGUF",
             "allow_patterns": ["*Q3_K_S*"]},
        ],
        "ms": [
            # 2026-05-22 实测:unsloth/Qwen3-4B-Instruct-2507-GGUF 在 ModelScope
            # 上存在(与 HF 首选同名同档,仓内有 Qwen3-4B-Instruct-2507-Q3_K_S.gguf)
            # → MS fallback 直接拿同一个 Qwen3-4B GGUF,不再降级成另一个模型。
            # 注:官方 qwen/Qwen3-4B-Instruct-2507-GGUF 命名空间仍 404,别用那个。
            {"repo": "unsloth/Qwen3-4B-Instruct-2507-GGUF",
             "allow_patterns": ["*Q3_K_S*"]},
            # 末位兜底:Qwen2.5-3B 官方 GGUF(2024 老版,仅 Qwen3 整个拉不动时用)
            {"repo": "qwen/Qwen2.5-3B-Instruct-GGUF",
             "allow_patterns": ["*q4_k_m*"]},
        ],
    },
    "embedding": {
        "approx_mb": 468,
        "note": "BAAI BGE-M3 多语种 embedding GGUF Q5_K_M(~468 MB,8192 ctx,568M 参数);"
                "GGUF 给 llama-server --embedding 单文件加载。"
                "embedding 是 RAG 检索召回的根基,用 Q5_K_M(K-quant 稳妥档)比 Q8 省"
                " ~165 MB、检索质量基本无损;不取 Q4 以更稳地保住召回。"
                "原计划用 gte-multilingual-base GGUF 但社区无人做(2026-05-16 HF 全网无效),"
                "改用 bge-m3 — 同档多语种 embedding,功能等价。",
        "dest_subdir": "bge-m3",
        # 2026-05-16 验证:gpustack/bge-m3-GGUF 17.5k downloads 最稳;
        # vonjack/bge-m3-gguf 备份;原 gte-multilingual GGUF 全 404 已删除。
        "hf": [
            # GGUF 首选 — llama-server --embedding 单文件加载,Q5_K_M ~468 MB
            {"repo": "gpustack/bge-m3-GGUF",
             "allow_patterns": ["*Q5_K_M.gguf", "*q5_k_m.gguf"]},
            # 备选:bbvch-ai 镜像。该 repo 只发了 Q4_K_M 单档(没有 Q5/Q8)→
            # 备选退到 Q4_K_M;回退路径极少命中,可接受。
            # (注:旧 *Q8_0 模式在该 repo 匹配不到任何文件,备选其实早已失效。)
            {"repo": "bbvch-ai/bge-m3-GGUF",
             "allow_patterns": ["*Q4_K_M.gguf", "*q4_k_m.gguf"]},
            # transformers fallback(infinity_emb 路径,~2.4 GB 全权重 — llama-server
            # 不要 transformers 格式,但保留给 infinity sidecar / 通用 fallback)
            {"repo": "BAAI/bge-m3",
             "allow_patterns": [
                 "*.json", "*.txt", "*.safetensors",
                 "sentencepiece*", "tokenizer*", "vocab*",
             ]},
        ],
        "ms": [
            # GGUF 首选 — llama-server --embedding 只吃 GGUF。2026-05-22 实测
            # gpustack/bge-m3-GGUF 在 ModelScope 上存在,仓内有 bge-m3-Q5_K_M.gguf。
            # 之前这里只挂 BAAI/bge-m3(transformers 整仓)→ 国内走 MS 下到的
            # embedding 模型 llama-server 加载不了、embedding 服务起不来。
            {"repo": "gpustack/bge-m3-GGUF",
             "allow_patterns": ["*Q5_K_M.gguf", "*q5_k_m.gguf"]},
            # transformers 兜底(infinity sidecar / 通用 fallback;llama 引擎不用)
            {"repo": "BAAI/bge-m3",
             "allow_patterns": [
                 "*.json", "*.txt", "*.safetensors",
                 "sentencepiece*", "tokenizer*", "vocab*",
             ]},
        ],
    },
    "rerank": {
        "approx_mb": 438,
        "note": "BAAI BGE-Reranker-v2-m3 GGUF Q4_K_M(~438 MB,多语种 cross-encoder reranker);"
                "GGUF 给 llama-server --reranking 单文件加载。"
                "reranker 是 cross-encoder 打分,对量化不敏感,Q4_K_M 比 Q8 省 ~195 MB、"
                "排序质量几乎无损。",
        # dest_subdir 跟实际拉的 repo 对齐(原 gte-multilingual-reranker-base 是历史遗留命名;
        # 现在用 gpustack/bge-reranker-v2-m3-GGUF 作主源)。
        "dest_subdir": "bge-reranker-v2-m3",
        # 2026-05-16 验证:gpustack/bge-reranker-v2-m3-GGUF 11.2k downloads 主源;
        # gpustack/gte-multilingual-reranker-base-GGUF 213 downloads(更轻 ~316 MB Q8)备选。
        "hf": [
            # 首选:bge-reranker-v2-m3 GGUF — Q4_K_M 单文件,~438 MB
            # 注意: gpustack repo 里有 F32/F16/Q8/Q6/Q5/Q4/Q3/Q2 八种量化共 ~4.85 GB,
            #       allow_patterns 必须卡死 *Q4_K_M.gguf,否则 *.gguf 会把全家桶拉下来。
            {"repo": "gpustack/bge-reranker-v2-m3-GGUF",
             "allow_patterns": ["*Q4_K_M.gguf", "*q4_k_m.gguf"]},
            # 备选:更轻的 gte-multilingual-reranker-base GGUF(Q4_K_M ~200 MB)
            {"repo": "gpustack/gte-multilingual-reranker-base-GGUF",
             "allow_patterns": ["*Q4_K_M.gguf", "*q4_k_m.gguf"]},
            # transformers fallback(走 infinity_emb 或自实现 cross-encoder)
            {"repo": "BAAI/bge-reranker-v2-m3",
             "allow_patterns": [
                 "*.json", "*.txt", "*.safetensors",
                 "sentencepiece*", "tokenizer*", "vocab*",
             ]},
        ],
        "ms": [
            # GGUF 首选 — llama-server --reranking 只吃 GGUF。2026-05-22 实测
            # gpustack/bge-reranker-v2-m3-GGUF 在 ModelScope 上存在,仓内有
            # bge-reranker-v2-m3-Q4_K_M.gguf。之前这里只挂 transformers 整仓,
            # 国内走 MS 下到的 rerank 模型 llama-server 加载不了。
            {"repo": "gpustack/bge-reranker-v2-m3-GGUF",
             "allow_patterns": ["*Q4_K_M.gguf", "*q4_k_m.gguf"]},
            # transformers 兜底(infinity sidecar / 通用 fallback)
            {"repo": "BAAI/bge-reranker-v2-m3",
             "allow_patterns": [
                 "*.json", "*.txt", "*.safetensors",
                 "sentencepiece*", "tokenizer*", "vocab*",
             ]},
        ],
    },
    "asr": {
        "approx_mb": 74,
        "note": "whisper.cpp tiny(~74 MB)",
        "dest_subdir": "whisper.cpp",
        "hf": [
            {"repo": "ggerganov/whisper.cpp",
             "allow_patterns": ["ggml-tiny.bin"]},
        ],
        # MS 上没有 whisper.cpp 现成镜像;HF/hf-mirror 失败时落到下面 urls 直链。
        "ms": [],
        # SDK 全炸的最后兜底:urllib 直接 GET。74 MB 小文件不需要 huggingface_hub
        # 那一整坨栈,绕开 SSL 读超时 / LFS endpoint 不稳的各种坑。
        "urls": [
            ("https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin",
             "ggml-tiny.bin"),
            ("https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin",
             "ggml-tiny.bin"),
        ],
    },
    "tts": {
        "approx_mb": 64,
        "note": "piper zh_CN-huayan-medium 中文女声(~60 MB,CPU 实时 离线)",
        "dest_subdir": "piper",
        # rhasspy/piper-voices 整仓 ~500 个 voice 几 GB;allow_patterns 只拉
        # 中文 huayan-medium 两个文件(.onnx + .onnx.json)。snapshot_download
        # 会保留源仓库的 zh/zh_CN/huayan/medium/ 嵌套目录 — chayuan-server
        # audio._find_bundled_piper_voice() 用 rglob 递归扫,不嫌嵌套。
        "hf": [
            {"repo": "rhasspy/piper-voices",
             "allow_patterns": [
                 "zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx",
                 "zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json",
             ]},
        ],
        # ModelScope 上 piper-voices 没现成镜像。
        "ms": [],
        # SDK 都炸时:直链兜底。两个小文件(~60 MB onnx + ~1 KB json)放扁平 dest_subdir/。
        # 注意:_download_one 的 urls 循环遇到第一个成功就 return —— 所以兜底直链路径不能
        # 保证拿到 json 一并下载;真依赖直链兜底时建议 HF 路径修通再说。
        "urls": [
            ("https://hf-mirror.com/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx",
             "zh_CN-huayan-medium.onnx"),
            ("https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx",
             "zh_CN-huayan-medium.onnx"),
        ],
    },
    "ocr": {
        "approx_mb": 16,
        "note": "RapidOCR PP-OCRv4 det + rec + PP-OCRv3 cls(中文文档,~16 MB)",
        "dest_subdir": "RapidOCR",
        "hf": [
            # SWHL/RapidOCR 整仓有十几个 PP-OCR 版本,只拉 v4 主线 + cls(实测 200)
            {"repo": "SWHL/RapidOCR",
             "allow_patterns": [
                 "PP-OCRv4/ch_PP-OCRv4_det_infer.onnx",
                 "PP-OCRv4/ch_PP-OCRv4_rec_infer.onnx",
                 "PP-OCRv3/ch_ppocr_mobile_v2.0_cls_train.onnx",
             ]},
        ],
        # MS 上 RapidAI/RapidOCR 存在但文件布局不同(onnx/ 子目录里命名不一样),
        # chayuan 加载逻辑认 HF 那种 PP-OCRv4 / PP-OCRv3 路径。HF 这三个 ONNX
        # 加起来 16 MB,Xet 风险极低,不需要 MS fallback。
        "ms": [],
    },
    "image": {
        "approx_mb": 605,
        "note": "OpenAI CLIP-B/32 图文 embedding(~605 MB,LZMA 后 ~380 MB)",
        "dest_subdir": "clip-vit-base-patch32",
        "hf": [
            # OpenAI 官方 CLIP,仓库里 pytorch_model.bin + tf_model.h5 +
            # flax_model.msgpack 是三份等价权重(同一模型不同框架格式),
            # 必须 allow_patterns 卡死只拿 pytorch 那份,否则会下 1.8 GB 三套。
            # 实测这十个文件全部 200,且这个仓库**不在 Xet 上**,hf-mirror 走得通。
            {"repo": "openai/clip-vit-base-patch32",
             "allow_patterns": [
                 "pytorch_model.bin",
                 "*.json", "*.txt",   # config / preprocessor / tokenizer / vocab / merges
             ]},
        ],
        # 2026-05-16 验证:AI-ModelScope/clip-vit-base-patch32 404;openai-mirror
        # 命名空间下存在(200),用它作 MS fallback。如果 openai-mirror 下文件布局
        # 与 HF 官方一致(pytorch_model.bin + config/preprocessor/tokenizer.json),
        # chayuan-server 加载逻辑能复用。
        "ms": [
            {"repo": "openai-mirror/clip-vit-base-patch32",
             "allow_patterns": [
                 "pytorch_model.bin",
                 "*.json", "*.txt",
             ]},
        ],
    },
}


# ──────────────────────────── 工具:打印 / 环境 ────────────────────────────

def _print(msg: str) -> None:
    print(msg, flush=True)


def _ensure_hf_lib() -> None:
    try:
        import huggingface_hub  # noqa: F401
    except ImportError:
        _print("[install] 检测到未装 huggingface_hub,自动 pip 安装...")
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "--upgrade", "huggingface_hub>=0.24,<1.0",
        ])


def _ensure_ms_lib() -> None:
    try:
        import modelscope  # noqa: F401
    except ImportError:
        _print("[install] 检测到未装 modelscope(MS fallback 需要),自动 pip 安装...")
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--upgrade", "modelscope",
        ])


def _pick_endpoint(user_pref: str | None) -> str:
    if user_pref:
        return user_pref.rstrip("/")
    env = os.environ.get("HF_ENDPOINT", "").strip().rstrip("/")
    if env:
        return env
    return DEFAULT_ENDPOINTS[0]


def _set_endpoint(endpoint: str) -> None:
    """huggingface_hub 在 import 时读 HF_ENDPOINT,必须 import 前设。"""
    os.environ["HF_ENDPOINT"] = endpoint
    os.environ["HUGGINGFACE_HUB_ENDPOINT"] = endpoint
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", _HF_DOWNLOAD_TIMEOUT_DEFAULT)


def _resolve_vendor_root() -> Path:
    here = Path(__file__).resolve()
    candidate = here.parent.parent / "chayuan-server" / "vendor" / "bundled_models"
    if candidate.is_dir():
        return candidate
    raise SystemExit(
        f"找不到 vendor/bundled_models/,期望在:{candidate}\n"
        f"用 --workspace <repo_root> 显式指定。"
    )


def _resolve_lite_plan() -> list[str]:
    """从 chayuan-server/packaging/bundle_manifest.py 拿 LITE_CAPS 单一真源。

    路径相对脚本固定 (`<repo>/scripts/` → `<repo>/chayuan-server/packaging/`)。
    任何 import 错误立刻 fail-fast 而不是默默用硬编码兜底,避免清单漂移。
    """
    pkg_dir = Path(__file__).resolve().parent.parent / "chayuan-server" / "packaging"
    if not (pkg_dir / "bundle_manifest.py").is_file():
        raise SystemExit(
            f"--lite 模式找不到清单真源:{pkg_dir}/bundle_manifest.py\n"
            "本仓库 packaging/bundle_manifest.py 是 LITE_CAPS / FULL_CAPS 的唯一来源,"
            "缺失说明 checkout 不完整。"
        )
    sys.path.insert(0, str(pkg_dir))
    try:
        from bundle_manifest import LITE_CAPS  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    return list(LITE_CAPS)


# ──────────────────────────── 存在性预检 ────────────────────────────

def _hf_repo_exists(repo: str) -> tuple[bool, str]:
    """HF 仓库存在性预检。返回 (exists, hint)。"""
    try:
        from huggingface_hub import HfApi
        api = HfApi(endpoint=os.environ.get("HF_ENDPOINT") or None)
        api.repo_info(repo_id=repo, repo_type="model")
        return True, ""
    except Exception as e:
        return False, repr(e)


def _ms_repo_exists(repo: str) -> tuple[bool, str]:
    """MS 仓库存在性预检。借 modelscope.hub.api。"""
    try:
        from modelscope.hub.api import HubApi
        api = HubApi()
        api.get_model(model_id=repo)
        return True, ""
    except Exception as e:
        return False, repr(e)


# ──────────────────────────── 实际下载 ────────────────────────────

def _download_via_hf(spec: dict, dest_dir: Path) -> None:
    from huggingface_hub import hf_hub_download, snapshot_download
    repo = spec["repo"]
    if spec.get("allow_patterns") or spec.get("snapshot"):
        snapshot_download(
            repo_id=repo,
            local_dir=str(dest_dir),
            allow_patterns=spec.get("allow_patterns"),
        )
    elif spec.get("filename"):
        hf_hub_download(
            repo_id=repo,
            filename=spec["filename"],
            local_dir=str(dest_dir),
        )
    else:
        raise ValueError(f"HF spec 缺 allow_patterns / filename:{spec}")


def _download_via_ms(spec: dict, dest_dir: Path) -> None:
    from modelscope import snapshot_download as ms_snapshot_download
    ms_snapshot_download(
        model_id=spec["repo"],
        allow_patterns=spec.get("allow_patterns"),
        local_dir=str(dest_dir),
    )


def _download_via_url(url: str, dest_dir: Path, filename: str) -> None:
    """urllib 直接 GET。给小文件兜底(HF/MS SDK 都挂了时用)。

    支持断点续传(若本地有部分文件且服务器接受 Range)。带进度打印。
    """
    import urllib.request
    import urllib.error

    dest = dest_dir / filename
    headers: dict[str, str] = {"User-Agent": "chayuan-install-script/1.0"}

    # 断点续传:本地有部分文件就发 Range 头
    start_byte = 0
    if dest.exists():
        start_byte = dest.stat().st_size
        if start_byte > 0:
            headers["Range"] = f"bytes={start_byte}-"

    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as e:
        # 416 = 请求范围已超(说明本地已经下完);其它 4xx/5xx 抛出
        if e.code == 416 and start_byte > 0:
            _print(f"        服务器报 416,认为已下完 ({start_byte // 1024 // 1024} MB)")
            return
        raise

    total = int(resp.headers.get("Content-Length", "0"))
    if resp.status == 206:
        # Partial Content,Content-Length 是剩余字节数
        total += start_byte
    total_mb = max(1, total // 1024 // 1024)

    mode = "ab" if start_byte > 0 and resp.status == 206 else "wb"
    if mode == "wb" and start_byte > 0:
        # 服务器不支持 Range,重新从头下
        start_byte = 0

    downloaded = start_byte
    chunk = 1024 * 1024  # 1 MB
    with open(dest, mode) as f:
        while True:
            buf = resp.read(chunk)
            if not buf:
                break
            f.write(buf)
            downloaded += len(buf)
            pct = (downloaded * 100 // total) if total else 0
            print(
                f"\r        {downloaded // 1024 // 1024} / {total_mb} MB  ({pct}%)",
                end="", flush=True,
            )
    print()  # newline 收尾


# ──────────────────────────── 调度 ────────────────────────────

def _build_try_order(
    source: str,
    hf_list: list[dict],
    ms_list: list[dict],
) -> list[tuple[str, dict]]:
    """生成 (source_name, spec) 的尝试顺序。

    * ``auto``:HF 全部 → MS 全部
    * ``hf``:只 HF,不 fallback 到 MS
    * ``modelscope``:MS 全部 → 若 ms_list 为空再降级到 HF(asr 这种没 MS 的 cap)
    """
    if source == "auto":
        return [("hf", s) for s in hf_list] + [("ms", s) for s in ms_list]
    if source == "hf":
        return [("hf", s) for s in hf_list]
    if source == "modelscope":
        if ms_list:
            return [("ms", s) for s in ms_list] + [("hf", s) for s in hf_list]
        # 当前 cap 没 MS 候选(如 asr),自动降级到 HF
        return [("hf", s) for s in hf_list]
    raise ValueError(f"未知 source: {source}")


_MODEL_WEIGHT_EXTS = (".gguf", ".safetensors", ".bin", ".onnx", ".pt", ".pth")
_PARTIAL_MARKERS = (".incomplete", ".tmp", ".part", ".lock")


def _collect_all_patterns(cap_spec: dict) -> list[str]:
    """从 cap_spec 的所有 hf/ms 候选里收集 allow_patterns 的并集。

    skip 检查会用这些 patterns 验证"本地是不是已经按某个候选下完整"。
    任一候选的 patterns 全部命中即视为完整。
    """
    all_patterns: list[list[str]] = []
    for src in ("hf", "ms"):
        for cand in (cap_spec.get(src) or []):
            pats = cand.get("allow_patterns") or []
            if pats:
                all_patterns.append(pats)
            elif cand.get("filename"):
                all_patterns.append([cand["filename"]])
    return all_patterns  # list of list


def _patterns_satisfied(dest_dir: Path, patterns: list[str]) -> list[str]:
    """返回 patterns 中**没在 dest_dir 下匹配到任何文件**的项。空列表 = 全部满足。"""
    import fnmatch
    all_files = [f for f in dest_dir.rglob("*")
                 if f.is_file() and not f.name.startswith(".")]
    rel_names = [str(f.relative_to(dest_dir)).replace("\\", "/") for f in all_files]
    missing: list[str] = []
    for pat in patterns:
        pat_norm = pat.replace("\\", "/")
        if not any(fnmatch.fnmatch(name, pat_norm) for name in rel_names):
            missing.append(pat)
    return missing


def _is_already_complete(dest_dir: Path, *, approx_mb: int,
                          cap_spec: dict) -> tuple[bool, str]:
    """判断目标目录是否已经下载完整,避免重复下载。

    完整 = 同时满足:
    1) 目录存在,有 ≥ 1 个权重文件 (.gguf / .safetensors / .bin / .onnx / .pt / .pth)
    2) 目录下**没有** *.incomplete / *.tmp / *.part / *.lock(SDK 留的 partial 标记)
    3) 至少有一个候选的 allow_patterns 全部命中(对方某次完整下载过)
    4) 目录总大小 ≥ approx_mb × 0.85
       (85% 阈值容忍量化偏差。注意:HF 网页用十进制 MB,本地 OS / 脚本通常报二进制 MiB;
        580 MiB ≈ 608 MB decimal,所以 605 MB 的标称基本对应本地 ~577 MiB,
        85% 阈值已留够缓冲。)
    """
    if not dest_dir.exists() or not dest_dir.is_dir():
        return False, ""

    files = [f for f in dest_dir.rglob("*")
             if f.is_file() and not f.name.startswith(".")]
    if not files:
        return False, ""

    # 1) partial 标记
    partial = [f for f in files
               if any(f.name.endswith(m) for m in _PARTIAL_MARKERS)]
    if partial:
        names = ", ".join(f.name for f in partial[:3])
        return False, f"发现 {len(partial)} 个 partial 标记 ({names}{'...' if len(partial)>3 else ''}) — 上次下载没完成,重下"

    # 2) 权重文件
    weight_files = [f for f in files if f.suffix.lower() in _MODEL_WEIGHT_EXTS]
    if not weight_files:
        return False, f"目录里没有任何权重文件 ({', '.join(_MODEL_WEIGHT_EXTS)})"

    # 3) pattern 覆盖:任一候选的 allow_patterns 全部命中即可
    pattern_sets = _collect_all_patterns(cap_spec)
    if pattern_sets:
        matched_any = False
        last_missing: list[str] = []
        for patterns in pattern_sets:
            missing = _patterns_satisfied(dest_dir, patterns)
            if not missing:
                matched_any = True
                break
            last_missing = missing
        if not matched_any:
            return False, (
                f"任何候选的 allow_patterns 都没全部命中本地文件;"
                f"最后一个候选缺:{', '.join(last_missing[:5])}"
            )

    # 4) 大小阈值
    total_mb = sum(f.stat().st_size for f in files) / 1024 / 1024
    threshold_mb = approx_mb * 0.85
    if total_mb < threshold_mb:
        return False, (
            f"已有 {total_mb:.1f} MiB,但 < 期望阈值 {threshold_mb:.0f} MiB "
            f"(approx {approx_mb} MB × 0.85)"
        )

    sample = ", ".join(f.name for f in weight_files[:3])
    if len(weight_files) > 3:
        sample += f", ... 共 {len(weight_files)} 个权重文件"
    return True, f"已有 {sample},总 {total_mb:.1f} MiB,跳过下载"


def _load_canonical_subdirs(cap: str) -> tuple[str, ...]:
    """从 chayuan-server/packaging/bundle_manifest.py 读 cap 当前 canonical 子目录白名单。

    返回空 tuple 表示该 cap 没有显式 canonical 表 → 调用方应保留所有子目录(open-by-default)。
    bundle_manifest 不可读时(本地仓库残破)也返回空 tuple 不阻塞下载流程。
    """
    here = Path(__file__).resolve().parent  # scripts/
    pkg_root = here.parent / "chayuan-server" / "packaging"
    if not (pkg_root / "bundle_manifest.py").is_file():
        return ()
    import importlib.util
    spec = importlib.util.spec_from_file_location("bundle_manifest", pkg_root / "bundle_manifest.py")
    if spec is None or spec.loader is None:
        return ()
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return ()
    if not hasattr(mod, "canonical_subdirs_for"):
        return ()
    return tuple(mod.canonical_subdirs_for(cap))


def _prune_stale_subdirs(cap: str, cap_dir: Path, canonical: tuple[str, ...]) -> int:
    """删除 cap_dir 下不在 canonical 列表里的子目录(--clean-stale 用)。

    保留:.开头隐藏目录、canonical 列表里的子目录、顶层文件(扁平 layout 兼容)。
    删除:同一 cap_dir 里其它"看起来像模型仓库"的子目录(有任意权重文件)。

    Returns:
        删除的子目录数量(0 = 啥都没删,纯检查)。
    """
    if not canonical or not cap_dir.is_dir():
        return 0
    n = 0
    for entry in sorted(cap_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith(".") or entry.name in canonical:
            continue
        # 看起来像模型仓库的标志:任意权重文件 / config.json / model_index.json
        looks_like_model = False
        try:
            for ext in (".gguf", ".safetensors", ".bin", ".onnx", ".pt", ".pth"):
                if any(entry.glob(f"*{ext}")):
                    looks_like_model = True
                    break
            if not looks_like_model and (entry / "config.json").is_file():
                looks_like_model = True
        except OSError:
            continue
        if not looks_like_model:
            continue
        size_mb = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file()) / 1024 / 1024
        _print(f"[{cap}] --clean-stale:删除非 canonical 子目录 {entry.name}/ "
               f"(~{size_mb:.1f} MiB;canonical={canonical!r})")
        shutil.rmtree(entry)
        n += 1
    return n


def _download_one(
    cap: str,
    cap_spec: dict,
    *,
    vendor_root: Path,
    clean: bool,
    clean_cap: bool,
    source: str,
) -> None:
    cap_dir = vendor_root / cap
    dest_dir = cap_dir / cap_spec["dest_subdir"]

    # --clean-cap: 抹掉整个 cap 目录后重建,把误下的其它子目录(旧模型/废候选)
    # 一并清理,只留这次要装的 dest_subdir。比 --clean 更彻底。
    if clean_cap and cap_dir.exists():
        _print(f"[{cap}] --clean-cap:删除整个 {cap_dir}")
        shutil.rmtree(cap_dir)
    cap_dir.mkdir(parents=True, exist_ok=True)

    # Skip-if-exists 预检 (--clean / --clean-cap 时绕过,用户明确要重下)
    if not (clean or clean_cap):
        already, reason = _is_already_complete(
            dest_dir, approx_mb=cap_spec["approx_mb"], cap_spec=cap_spec,
        )
        if already:
            _print(f"[{cap}] ✓ {reason}")
            return
        elif reason:
            _print(f"[{cap}] (本地不完整: {reason} — 继续下载)")

    if clean and not clean_cap and dest_dir.exists():
        _print(f"[{cap}] --clean:删除旧目录 {dest_dir}")
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    _print(f"[{cap}] target ~{cap_spec['approx_mb']} MB")
    _print(f"        {cap_spec['note']}")
    _print(f"        local_dir = {dest_dir}")

    hf_list = cap_spec.get("hf", []) or []
    ms_list = cap_spec.get("ms", []) or []
    plan = _build_try_order(source, hf_list, ms_list)

    if not plan:
        raise RuntimeError(f"[{cap}] 没有任何 source 候选(hf={len(hf_list)}, ms={len(ms_list)})")

    tried: list[tuple[str, str, str]] = []  # (src, repo, err)
    for src_name, spec in plan:
        repo = spec["repo"]

        # 存在性预检,跳过明显不存在的 repo,避免下载 SDK 内部慢启动 + 重试
        if src_name == "hf":
            _ensure_hf_lib()
            exists, err = _hf_repo_exists(repo)
        else:
            _ensure_ms_lib()
            exists, err = _ms_repo_exists(repo)

        if not exists:
            _print(f"[{cap}] {src_name}: {repo} 不存在,跳过")
            tried.append((src_name, repo, f"repo_not_found: {err}"))
            continue

        _print(f"[{cap}] ↓ {src_name}: {repo}")
        try:
            if src_name == "hf":
                _download_via_hf(spec, dest_dir)
            else:
                _download_via_ms(spec, dest_dir)
            _print(f"[{cap}] ✓ 完成 (via {src_name}: {repo})")
            return
        except KeyboardInterrupt:
            raise
        except BaseException as e:  # noqa: BLE001
            tried.append((src_name, repo, repr(e)))
            _print(f"[{cap}] {src_name} {repo} 下载失败:{e}")
            continue

    # SDK 候选用尽,最后兜底:直链 URLs(给小文件用,绕开 huggingface_hub 复杂栈)
    urls = cap_spec.get("urls", []) or []
    for url, fname in urls:
        _print(f"[{cap}] ↓ url: {url}")
        try:
            _download_via_url(url, dest_dir, fname)
            _print(f"[{cap}] ✓ 完成 (via url: {url})")
            return
        except KeyboardInterrupt:
            raise
        except BaseException as e:  # noqa: BLE001
            tried.append(("url", url, repr(e)))
            _print(f"[{cap}] url {url} 失败:{e}")
            continue

    # 全候选 + 全 url 都用尽
    summary_lines = [f"[{cap}] 所有候选都失败,尝试明细:"]
    for src, repo, err in tried:
        summary_lines.append(f"  - {src} {repo}: {err}")
    summary_lines.append("")
    summary_lines.append("修法:")
    summary_lines.append(f"  - 浏览 https://modelscope.cn/search?query={cap_spec['dest_subdir']}")
    summary_lines.append(f"           或 https://huggingface.co/search?q={cap_spec['dest_subdir']}")
    summary_lines.append(f"  - 把对的 repo / 直链 URL 加到 scripts/install-bundled-models.py 的")
    summary_lines.append(f"    MANIFEST[{cap!r}]['hf' / 'ms' / 'urls'] 列表里")
    raise RuntimeError("\n".join(summary_lines))


# ──────────────────────────── 入口 ────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # 别名:runtime registry / API / settings 使用 "image-embedding",但本脚本
    # MANIFEST 历史上用短名 "image"。两个都接受,内部归一到 "image"。
    _ALIASES = {"image-embedding": "image"}
    parser.add_argument(
        "--only",
        choices=list(MANIFEST.keys()) + list(_ALIASES.keys()),
        help="只下指定 capability,不传 = 全下;支持 chat/embedding/rerank/asr/ocr/image(=image-embedding)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="下载前清空目标子目录(干净换装,仅清 dest_subdir)",
    )
    parser.add_argument(
        "--clean-cap",
        action="store_true",
        help="下载前清空整个 cap 目录(embedding/、chat/ 等),把误下的旧模型 / 废候选一并清掉;比 --clean 更彻底",
    )
    parser.add_argument(
        "--clean-stale",
        action="store_true",
        help=("下载前清掉 cap 目录里**非 canonical** 的子目录(历史遗留)。"
              "canonical 名单从 chayuan-server/packaging/bundle_manifest.py "
              "CANONICAL_MODEL_SUBDIRS 读。"
              "例:embedding canonical=bge-m3,本 flag 开启时旧的 "
              "embedding/gte-multilingual-base/ 会被删 — 跟 --clean-cap 区别是"
              "保留 canonical 子目录(如 bge-m3 已下完不重下),只清陈年旧账。"
              "install-vendor.ps1 默认传这个,确保打包前 vendor 是干净状态。"),
    )
    parser.add_argument(
        "--lite",
        action="store_true",
        help=("只下载轻量版子集(embedding / rerank / asr / ocr,~990 MB);"
              "省去 chat (~1.85 GB) 和 image-embedding (~605 MB)。"
              "与 --only 互斥(--only 已经显式指定单 cap 时无须再加 --lite)。"
              "清单真源:chayuan-server/packaging/bundle_manifest.py LITE_CAPS"),
    )
    parser.add_argument(
        "--source",
        choices=["auto", "hf", "modelscope"],
        default="auto",
        help=("下载源选择。auto(默认)= 先 HF 后 MS;"
              "hf = 强制 HF;"
              "modelscope = MS 优先,cap 没 MS 时降级到 HF(适合已知 hf-mirror Xet 不通)。"),
    )
    parser.add_argument(
        "--endpoint",
        help=("HF 镜像 URL,优先级最高。默认 hf-mirror.com;"
              "试 --endpoint https://huggingface.co 直连官方(海外服务器才稳)。"),
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="仓库根目录(含 chayuan-server/)。不传 = 由脚本相对路径自动推。"
             "仅在装到 vendor/bundled_models/(打包暂存)时有意义,与 --chayuan-root 互斥。",
    )
    parser.add_argument(
        "--chayuan-root",
        type=Path,
        help="直接装到 <CHAYUAN_ROOT>/models/bundled/<cap>/...,跳过 vendor/bundled_models/。"
             "scanner 直接扫得到,免去 first_launch seed 拷贝 + 双倍磁盘占用。"
             "不传时:若 $CHAYUAN_ROOT 环境变量有值则用它,否则 fallback 到 vendor/bundled_models/(打包模式)。",
    )
    args = parser.parse_args()

    endpoint = _pick_endpoint(args.endpoint)
    _set_endpoint(endpoint)
    _print(f"[install] source = {args.source}")
    if args.source != "modelscope":
        _print(f"[install] HF 镜像 = {endpoint}")
        _print(f"[install] HF_HUB_DOWNLOAD_TIMEOUT = {os.environ['HF_HUB_DOWNLOAD_TIMEOUT']}s")

    # 装到哪 — 优先级:--chayuan-root CLI > $CHAYUAN_ROOT env > --workspace > 自动探测 vendor/
    # vendor/bundled_models/ 路径仅用于打包暂存(build.py 抓这里),运行时 scanner
    # 不扫这个目录,必须靠 first_launch.seed_bundled_models() 拷到 chayuan_root。
    # 直接装 chayuan_root 可以省掉 seed 这一步 + 双倍磁盘占用。
    if args.chayuan_root and args.workspace:
        raise SystemExit("[install] --chayuan-root 和 --workspace 互斥;选一个")

    chayuan_root: Optional[Path] = args.chayuan_root
    if not chayuan_root:
        env_root = os.environ.get("CHAYUAN_ROOT")
        if env_root:
            chayuan_root = Path(env_root).expanduser()

    if chayuan_root:
        vendor_root = chayuan_root / "models" / "bundled"
        vendor_root.mkdir(parents=True, exist_ok=True)
        _print(f"[install] target = {vendor_root}  (直装 chayuan_root,scanner 立即扫得到,免 seed)")
    elif args.workspace:
        vendor_root = args.workspace / "chayuan-server" / "vendor" / "bundled_models"
        if not vendor_root.is_dir():
            raise SystemExit(f"--workspace 下找不到 vendor/bundled_models/: {vendor_root}")
        _print(f"[install] target = {vendor_root}  (vendor 打包暂存,需 server 首启 seed 拷到 chayuan_root)")
    else:
        vendor_root = _resolve_vendor_root()
        _print(f"[install] target = {vendor_root}  (vendor 打包暂存,需 server 首启 seed 拷到 chayuan_root)")
        _print("[install] 提示:可加 --chayuan-root <path> 或 export CHAYUAN_ROOT=<path> 直装运行时目录,省一份磁盘空间。")
    _print("")

    if args.lite and args.only:
        raise SystemExit("[install] --lite 和 --only 互斥;只下单 cap 直接用 --only,不必再加 --lite")

    only = _ALIASES.get(args.only, args.only) if args.only else None
    if only:
        plan = [only]
    elif args.lite:
        # 从单一真源拿轻量版清单(LITE_CAPS = embedding/rerank/asr/ocr)
        plan = _resolve_lite_plan()
        _print(f"[install] --lite 模式:只下 {' / '.join(plan)} 子集 (~990 MB)")
    else:
        plan = list(MANIFEST.keys())
    failures: list[tuple[str, str]] = []
    pruned_total = 0
    for cap in plan:
        spec = MANIFEST[cap]
        # --clean-stale:在尝试下载前,清掉 cap_dir 里非 canonical 的旧子目录
        # (比如 embedding/gte-multilingual-base → 当前 canonical 已换成 bge-m3 GGUF)。
        if args.clean_stale:
            canonical = _load_canonical_subdirs(cap)
            if canonical:
                pruned_total += _prune_stale_subdirs(cap, vendor_root / cap, canonical)
        try:
            _download_one(
                cap, spec,
                vendor_root=vendor_root,
                clean=args.clean,
                clean_cap=args.clean_cap,
                source=args.source,
            )
        except KeyboardInterrupt:
            _print("[install] 用户中断,退出。已下载的部分文件保留(下次跑会续传)。")
            return 130
        except Exception as e:  # noqa: BLE001
            failures.append((cap, str(e)))
        _print("")

    if args.clean_stale and pruned_total > 0:
        _print(f"[install] --clean-stale 共清理 {pruned_total} 个非 canonical 子目录")

    if failures:
        _print("[install] 部分下载失败:")
        for cap, err in failures:
            # err 可能是多行,缩进一下
            for i, line in enumerate(err.splitlines()):
                _print(("  - " if i == 0 else "    ") + line)
        return 1

    _print("[install] 全部完成。下一步:")
    _print("  - cd 到仓库根 → .\\build-desktop.cmd -IntegratedOnly(Windows)")
    _print("  - 或 ./build-desktop.sh --integrated-only(Linux/Mac)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
