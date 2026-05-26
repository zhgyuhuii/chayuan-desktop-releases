"""图像 / 向量模型的**离线管理**。

核心问题：图像编码器动辄几百 MB 到几 GB；用户可能：
1. 有网 → 让后端直接 huggingface_hub 拉（带进度流）
2. 无网 → 让用户手动下载后上传 zip / tar.gz，后端解压到 cache 目录
3. 镜像内网 → 默认走 HF_ENDPOINT=https://hf-mirror.com，也可用环境变量覆盖

对外 API：
- ``list_models(filter='image')`` — 列出当前支持的模型 + 磁盘缓存状态
- ``download_model(name, progress_cb)`` — 同步下载（建议放 Arq worker）
- ``upload_model_bundle(model_name, zip_path)`` — 把用户上传的 zip 解到 cache
- ``delete_model(name)`` — 清磁盘缓存
- ``disk_usage_summary()`` — 磁盘占用总览
"""
from __future__ import annotations

import logging
import os
import shutil
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("chayuan.image_source.model_manager")

DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"


def _cache_root() -> Path:
    from chayuan.settings import CHAYUAN_ROOT

    # 图像向量化模型属于项目可迁移数据，默认跟随 CHAYUAN_ROOT，而不是落到用户级 HF_HOME。
    p = Path(CHAYUAN_ROOT) / "models" / "huggingface"
    p.mkdir(parents=True, exist_ok=True)
    return p


def model_cache_root() -> Path:
    """返回图像向量化模型缓存根目录，供 UI 展示实际上传 / 下载位置。"""
    return _cache_root()


def _model_local_dir(model_name: str) -> Path:
    """模型在 chayuan 自家 cache 下的预期路径（HF 规范)。

    ⚠ 这是"理想路径"。实际查模型在不在,用 ``find_cached_model_dir(name)`` —
    它会同时查 HF 默认 cache / HF_HOME / 用户扁平目录,覆盖所有合法落点。
    """
    safe = model_name.replace("/", "--")
    return _cache_root() / "hub" / f"models--{safe}"


def _candidate_cache_roots() -> List[Path]:
    """枚举所有可能放 HF 模型的 cache 根目录。

    HF Transformers / huggingface_hub 默认查找顺序:
      1. ``cache_dir=`` 显式参数(我们传 chayuan 自家)
      2. ``TRANSFORMERS_CACHE`` 环境变量
      3. ``HF_HOME / hub``
      4. ``~/.cache/huggingface/hub``

    用户在装机时常用 ``huggingface-cli download <name>``,会落到 (4);跑 chayuan
    时如果只看 (1),就出现"模型明明在硬盘上,却报没下载"的现象。
    """
    out: List[Path] = []
    seen: set = set()

    def _add(p: Optional[Path]) -> None:
        if p is None:
            return
        try:
            ap = p.expanduser().resolve()
        except Exception:  # noqa: BLE001
            ap = p
        key = str(ap)
        if key in seen:
            return
        seen.add(key)
        out.append(ap)

    # 1. chayuan 自家(下载按钮主路径)
    _add(_cache_root())
    # 2. TRANSFORMERS_CACHE 环境变量
    tc = os.environ.get("TRANSFORMERS_CACHE")
    if tc:
        _add(Path(tc))
    # 3. HF_HOME / hub
    hh = os.environ.get("HF_HOME")
    if hh:
        _add(Path(hh))
    # 4. 默认 ~/.cache/huggingface
    _add(Path.home() / ".cache" / "huggingface")
    return out


def find_cached_model_dir(model_name: str) -> Optional[Path]:
    """在所有候选 cache 根里找名为 ``model_name`` 的模型,返第一个命中的 dir。

    匹配两种布局(都常见):
      - **HF 规范**: ``<root>/hub/models--<safe>/`` 下含 snapshots/
      - **扁平**: 用户 git clone / 离线导入,直接 ``<root>/<org>/<name>/`` 含
        config.json + safetensors 文件

    都没找到返 None,caller 应抛带"已检查的路径列表"的诊断 error。
    """
    safe = model_name.replace("/", "--")
    for root in _candidate_cache_roots():
        # HF 规范布局
        hf_dir = root / "hub" / f"models--{safe}"
        if hf_dir.is_dir():
            # 至少有 snapshots/<hash> 才算真正下载完(空 dir 是 stale download)
            snap = hf_dir / "snapshots"
            if snap.is_dir() and any(snap.iterdir()):
                return hf_dir
        # 扁平布局:<root>/<org>/<name> 或 <root>/<safe>
        if "/" in model_name:
            org, name = model_name.split("/", 1)
            flat1 = root / org / name
            if flat1.is_dir() and (flat1 / "config.json").is_file():
                return flat1
        flat2 = root / safe
        if flat2.is_dir() and (flat2 / "config.json").is_file():
            return flat2
    return None


def find_model_load_path(model_name: str) -> Optional[str]:
    """给 ``from_pretrained(..., pretrained_model_name_or_path=X)`` 用的 X。

    - 找到 HF 规范布局 → 返 cache 根字符串(让 caller 传 ``cache_dir=root``)
    - 找到扁平布局     → 返扁平目录字符串(让 caller 直接 from_pretrained(dir))
    - 都没找到         → None
    """
    safe = model_name.replace("/", "--")
    for root in _candidate_cache_roots():
        hf_dir = root / "hub" / f"models--{safe}"
        if hf_dir.is_dir() and (hf_dir / "snapshots").is_dir() and any(
            (hf_dir / "snapshots").iterdir()
        ):
            return str(root)
        if "/" in model_name:
            org, name = model_name.split("/", 1)
            flat = root / org / name
            if flat.is_dir() and (flat / "config.json").is_file():
                return str(flat)
        flat2 = root / safe
        if flat2.is_dir() and (flat2 / "config.json").is_file():
            return str(flat2)
    return None


def _hf_endpoint() -> str:
    """下载源：默认走国内镜像，允许部署环境显式覆盖。"""
    return os.environ.get("HF_ENDPOINT") or DEFAULT_HF_ENDPOINT


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except Exception:  # noqa: BLE001
        pass
    return total


def list_models(kind: str = "image") -> List[Dict[str, Any]]:
    from chayuan.server.image_source.embedder import SUPPORTED_MODELS, is_model_ready
    out: List[Dict[str, Any]] = []
    for name, spec in SUPPORTED_MODELS.items():
        # 用 find_cached_model_dir 而不是固定路径 — 用户可能把模型放在 HF 默认
        # cache(~/.cache/huggingface)或 HF_HOME / TRANSFORMERS_CACHE 指定的位置,
        # 之前只看 chayuan 自家 cache 会把"已下载"误报成"未下载"。
        cached_dir = find_cached_model_dir(name)
        size_bytes = _dir_size(cached_dir) if cached_dir else 0
        ready = is_model_ready(name)
        out.append({
            "name": name,
            "family": spec.family,
            "dim": spec.dim,
            "approx_size_mb": spec.approx_size_mb,
            "chinese_level": spec.chinese_level,
            "languages": spec.languages,
            "description": spec.description,
            "cached": cached_dir is not None,
            "cached_size_mb": round(size_bytes / (1024 * 1024), 1),
            "deps_available": ready.get("available"),
            "deps_reason": ready.get("reason"),
            # local_dir 既给 UI 显示也方便用户排查 — 没下时给 chayuan 默认路径
            "local_dir": str(cached_dir if cached_dir else _model_local_dir(name)),
        })
    return out


def download_model(
    name: str, *, progress_cb: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """同步下载；建议调用方放进 Arq worker，避免 HTTP 超时。

    依赖：``huggingface_hub``（``pip install huggingface_hub``）；
    默认走国内镜像（https://hf-mirror.com），可用 ``HF_ENDPOINT`` 环境变量覆盖。
    """
    endpoint = _hf_endpoint()
    os.environ.setdefault("HF_ENDPOINT", endpoint)
    try:
        from huggingface_hub import snapshot_download  # type: ignore
    except ImportError as e:
        return {"ok": False, "error": f"缺少 huggingface_hub：{e}。请 `pip install huggingface_hub`。"}

    cache_root = _cache_root()
    target = _model_local_dir(name)
    # 下载是"新版本"——无论之前测过没测过，旧结果都作废；同时清掉 embedder 单例
    try:
        from chayuan.server.image_source.embedder import _invalidate_embedder_cache
        from chayuan.server.image_source.model_registry import clear_smoke_tested
        clear_smoke_tested(name)
        _invalidate_embedder_cache(name)
    except Exception:  # noqa: BLE001
        pass
    try:
        if progress_cb:
            progress_cb(f"开始下载 {name} ← {endpoint} → {cache_root}")
        path = snapshot_download(
            repo_id=name, cache_dir=str(cache_root),
            endpoint=endpoint,
            # ignore_patterns 去掉无关大文件（训练 safetensors 有时同时带 bin）
            ignore_patterns=["*.msgpack", "*.h5", "*.ot", "*training_args*"],
        )
        if progress_cb:
            progress_cb(f"下载完成：{path}")
        return {
            "ok": True, "local_dir": path,
            "size_mb": round(_dir_size(Path(path)) / (1024 * 1024), 1),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def upload_model_bundle(
    model_name: str, bundle_path: str,
) -> Dict[str, Any]:
    """用户把 HF snapshot 打包成 zip / tar.gz 上传 → 解到 cache_root 对应位置。

    支持两种 bundle 内部结构：
    - 扁平：直接包含 config.json + tokenizer.json + *.safetensors → 放到 snapshots/main
    - HF 标准：已经是 models--{org}--{name}/snapshots/... 层级 → 直接并入 cache
    """
    bp = Path(bundle_path)
    if not bp.exists():
        return {"ok": False, "error": f"文件不存在：{bundle_path}"}

    target = _model_local_dir(model_name)
    snapshots_dir = target / "snapshots" / "manual-upload"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    # 新一份 bundle 到位，旧 smoke 测试作废 + 单例失效
    try:
        from chayuan.server.image_source.embedder import _invalidate_embedder_cache
        from chayuan.server.image_source.model_registry import clear_smoke_tested
        clear_smoke_tested(model_name)
        _invalidate_embedder_cache(model_name)
    except Exception:  # noqa: BLE001
        pass

    try:
        if bp.suffix == ".zip":
            with zipfile.ZipFile(bp, "r") as z:
                z.extractall(snapshots_dir)
        elif bp.suffixes[-2:] == [".tar", ".gz"] or bp.suffix in (".tar", ".tgz"):
            with tarfile.open(bp, "r:*") as t:
                t.extractall(snapshots_dir)
        else:
            return {"ok": False, "error": f"不支持的 bundle 格式：{bp.name}"}

        # 如果解压后是嵌套目录（HF 整个 models-- 结构），把内部正确部分拷过去
        size = _dir_size(snapshots_dir)
        return {
            "ok": True, "local_dir": str(target),
            "size_mb": round(size / (1024 * 1024), 1),
            "note": "请确认 snapshots/manual-upload 下有 config.json 等文件；无则需手动调整。",
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def delete_model(name: str) -> Dict[str, Any]:
    target = _model_local_dir(name)
    # 删模型同时清 smoke sentinel + embedder 单例，避免悬挂的已加载权重
    try:
        from chayuan.server.image_source.embedder import _invalidate_embedder_cache
        from chayuan.server.image_source.model_registry import clear_smoke_tested
        clear_smoke_tested(name)
        _invalidate_embedder_cache(name)
    except Exception:  # noqa: BLE001
        pass
    if not target.exists():
        return {"ok": True, "msg": "未缓存，无需删除"}
    try:
        shutil.rmtree(target)
        return {"ok": True, "msg": f"已删除 {target}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def smoke_test_model(name: str) -> Dict[str, Any]:
    """对模型做一次最小可用性验证：加载 → embed 1x1 PIL 图 → embed 文本。

    通过后写 sentinel（由 model_registry 负责），KB 下拉才会显示它。
    失败则清 sentinel（避免脏标记）；返回 {"ok": bool, "dim": int, "error": str}。
    """
    from chayuan.server.image_source.embedder import SUPPORTED_MODELS, get_embedder
    from chayuan.server.image_source.model_registry import (
        clear_smoke_tested,
        mark_smoke_tested,
    )

    if name not in SUPPORTED_MODELS:
        return {"ok": False, "error": f"unknown model: {name}"}

    # 依赖预检，给出明确提示
    try:
        import PIL.Image  # noqa: F401
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError as e:
        clear_smoke_tested(name)
        return {
            "ok": False,
            "error": f"缺少依赖 {e.name}；请 `pip install torch transformers pillow`。",
        }

    try:
        from PIL import Image
        emb = get_embedder(name)
        # 构造一张 8x8 纯色 RGB；放大一点避免部分 processor 对 1x1 报错
        img = Image.new("RGB", (8, 8), (128, 128, 128))
        vec_img = emb.embed_image(img)
        vec_txt = emb.embed_text("chayuan smoke test")
        dim = int(getattr(emb, "dim", 0) or len(vec_img))
        if not dim or len(vec_img) != dim or len(vec_txt) != dim:
            clear_smoke_tested(name)
            return {
                "ok": False,
                "error": f"向量维度校验失败：img={len(vec_img)}, txt={len(vec_txt)}, dim={dim}",
            }
        mark_smoke_tested(name, dim=dim)
        return {"ok": True, "dim": dim, "msg": f"通过；{dim} 维向量正常产出"}
    except Exception as e:  # noqa: BLE001
        logger.warning("smoke_test_model(%s) 失败：%r", name, e)
        clear_smoke_tested(name)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def disk_usage_summary() -> Dict[str, Any]:
    root = _cache_root()
    if not root.exists():
        return {"root": str(root), "exists": False, "total_mb": 0, "items": []}
    items = []
    total = 0
    for entry in (root / "hub").glob("models--*") if (root / "hub").exists() else []:
        size = _dir_size(entry)
        total += size
        items.append({
            "name": entry.name.replace("models--", "").replace("--", "/"),
            "path": str(entry),
            "size_mb": round(size / (1024 * 1024), 1),
        })
    items.sort(key=lambda x: -x["size_mb"])
    return {
        "root": str(root), "exists": True,
        "total_mb": round(total / (1024 * 1024), 1),
        "items": items,
    }
