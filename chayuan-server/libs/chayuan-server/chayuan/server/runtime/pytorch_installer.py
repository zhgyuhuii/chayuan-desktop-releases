"""PyTorch + torchvision 检测与安装器。

为什么需要这个模块
==================
``chayuan-server`` 的 image 能力(jina-clip / siglip2 / RapidOCR 之类)走
``transformers`` → ``transformers.image_utils`` import ``torchvision.transforms.
InterpolationMode`` → 触发 torchvision **加载注册算子**(``torchvision::nms``)。
torch 跟 torchvision 是 C++ ABI 严格配对的:torch 2.11 ↔ torchvision 0.26、
torch 2.6 ↔ torchvision 0.21……版本错配就出 ``operator torchvision::nms does
not exist`` 链路崩。

PyTorch wheel 本身**不打包进 chayuan-server installer**:
  - 体积:CPU ~250 MB,CUDA 2-3 GB;
  - 不能二选一发版(CPU/CUDA 同包名不同 wheel);
  - GPU 用户必须从 ``https://download.pytorch.org/whl/cuXXX`` 装,不能用 PyPI 默认源。

本模块负责:
  - **detect**:跑 ``nvidia-smi`` 看有没有 GPU;读当前 env 装的 torch/torchvision
    版本,验证它们能否 import(不报 nms 错)。
  - **install_pytorch_wheels**:纯 Python(**无 pip**)安装器 —— 自己下 wheel +
    用标准库 ``zipfile`` 解压到 ``torch_install_target_dir()``。pip 不支持被
    PyInstaller freeze(``-m`` 不支持、pip 没打进包、distlib finder 失效),所以
    彻底放弃 pip-in-frozen,改走 wheel 解压(wheel 本质就是 zip)。
  - **build_install_recipe**:生成一条调 ``chayuan install-pytorch`` Click 子命令
    的 InstallRecipe,交给 ``install_task_manager.start()`` 异步跑,前端流式取日志。
"""
from __future__ import annotations

import json
import logging
import os
import platform as _platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

logger = logging.getLogger("chayuan.runtime.pytorch_installer")

# CUDA 版本 → PyTorch wheel index;按"新优先 + LTS 优先"顺序
# (用户 NVIDIA 驱动版本反推可装 CUDA 上限,这里给推荐 wheel)
_PYTORCH_INDEXES: Dict[str, str] = {
    "cpu":   "https://download.pytorch.org/whl/cpu",
    "cu118": "https://download.pytorch.org/whl/cu118",
    "cu121": "https://download.pytorch.org/whl/cu121",
    "cu124": "https://download.pytorch.org/whl/cu124",
    "cu126": "https://download.pytorch.org/whl/cu126",
}

# PyTorch 官方下载页 —— 给「不想在线装,自己下 wheel」的用户。前端 UI 直接展示。
PYTORCH_DOWNLOAD_PAGE = "https://pytorch.org/get-started/locally/"

# NVIDIA 驱动版本 → 最高可装 CUDA(参考 PyTorch 官方兼容表;保守取整)
# 用户驱动 >= 这些版本就能装对应 CUDA;否则降级
_DRIVER_TO_CUDA: List[tuple[int, str]] = [
    (560, "cu126"),
    (525, "cu124"),
    (515, "cu121"),
    (450, "cu118"),
]

Variant = Literal["cpu", "cu118", "cu121", "cu124", "cu126", "auto"]


@dataclass
class TorchStatus:
    """detect_status() 返回结构。"""
    torch_version: Optional[str] = None
    torch_cuda_build: Optional[str] = None       # "cu124" / "cpu" / None=未装
    torchvision_version: Optional[str] = None
    import_ok: bool = False                       # 整个 import 链能否走通
    import_error: Optional[str] = None            # 不通时的诊断字符串
    has_nvidia_gpu: bool = False
    nvidia_driver: Optional[str] = None           # "560.94" 之类
    suggested_variant: Variant = "cpu"            # 建议的安装 variant(给"一键自动")
    can_install: bool = True                      # pip 可用、磁盘有空间
    # 内置(离线)wheel 是否可用 — 用户无网情况下也能装上
    offline_wheels_available: bool = False
    offline_wheels_dir: Optional[str] = None      # 调试用,告诉用户去哪看
    # torch 的运行时安装目标目录(<CHAYUAN_ROOT>/py_packages)。前端 UI 直接展示,
    # 告诉用户「手动装好的 torch / 解压好的 wheel 放这里」。
    install_target_dir: Optional[str] = None
    notes: List[str] = field(default_factory=list)  # 给前端的额外提示

    def to_dict(self) -> Dict[str, Any]:
        # 给「不想在线装、想自己下 wheel」的用户的官方下载入口。
        # download_index 按 suggested_variant 给出对应 wheel index 直链
        # (CPU → /whl/cpu,GPU → /whl/cuXXX)。
        sv = self.suggested_variant if self.suggested_variant in _PYTORCH_INDEXES else "cpu"
        return {
            "torch_version": self.torch_version,
            "torch_cuda_build": self.torch_cuda_build,
            "torchvision_version": self.torchvision_version,
            "import_ok": self.import_ok,
            "import_error": self.import_error,
            "has_nvidia_gpu": self.has_nvidia_gpu,
            "nvidia_driver": self.nvidia_driver,
            "suggested_variant": self.suggested_variant,
            "can_install": self.can_install,
            "offline_wheels_available": self.offline_wheels_available,
            "offline_wheels_dir": self.offline_wheels_dir,
            "install_target_dir": self.install_target_dir,
            "notes": list(self.notes),
            # 下载说明:官方页 + 按建议 variant 的 wheel index 直链 + pinned 版本
            "download_page": PYTORCH_DOWNLOAD_PAGE,
            "download_index": _PYTORCH_INDEXES[sv],
            "download_index_cpu": _PYTORCH_INDEXES["cpu"],
            "download_index_cuda": (
                _PYTORCH_INDEXES[sv] if sv != "cpu" else None
            ),
            "pinned_torch_version": _TORCH_VERSION,
            "pinned_torchvision_version": _TORCHVISION_VERSION,
            "pinned_transformers_version": _TRANSFORMERS_VERSION,
        }


def _detect_nvidia() -> tuple[bool, Optional[str]]:
    """跑 ``nvidia-smi`` 探有没有 NVIDIA GPU + 驱动版本。

    返 (has_gpu, driver_version)。失败一律返 (False, None)。
    """
    if not shutil.which("nvidia-smi"):
        return False, None
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
            # 显式 UTF-8 解码,不依赖平台默认编码(中文 Windows 默认 GBK 会崩)
            encoding="utf-8", errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError):
        return False, None
    if r.returncode != 0:
        return False, None
    line = (r.stdout or "").strip().splitlines()
    if not line or not line[0].strip():
        # --query-gpu=driver_version 一行都没有 = nvidia-smi 跑通(exit 0)但枚举到
        # 0 个 GPU。常见于装过 NVIDIA 驱动 / GeForce 套件、但机器其实没有 GPU
        # (或虚拟机)—— nvidia-smi.exe 还在 PATH 里、能跑、却查不到设备。
        # 真有 GPU 时这个查询必然返回驱动版本号,不会为空 → 空即判无 GPU。
        return False, None
    return True, line[0].strip()


def _suggest_cuda_variant(driver: Optional[str]) -> str:
    """驱动 → 推荐 CUDA wheel。驱动太老或拿不到 → cpu。"""
    if not driver:
        return "cpu"
    try:
        major = int(re.split(r"[.\s]+", driver, maxsplit=1)[0])
    except (ValueError, IndexError):
        return "cpu"
    for min_major, variant in _DRIVER_TO_CUDA:
        if major >= min_major:
            return variant
    return "cpu"


def _offline_wheels_dir() -> Optional[Path]:
    """找内置 PyTorch wheel 的目录(无网安装兜底)。

    探测顺序(跟 model_registry.local_index.bundled_models_dir 风格一致):
      1. ``$CHAYUAN_TORCH_WHEELS_DIR`` 环境变量(运维 / CI 自定义)
      2. ``<CHAYUAN_ROOT>/torch_wheels/`` — 首启 :func:`seed_torch_wheels` 拷过来的
         运行时目录,跨进程重启稳定可用
      3. PyInstaller frozen:``sys._MEIPASS / vendor / torch_wheels``(老路径,
         2026-05-18 起新 build 不再嵌进 sidecar exe,但旧版本兼容保留)
      4. Tauri 安装目录:``<install>/torch_wheels/``(新路径,Tauri resources
         打包到这里,装机后 first_launch 从这里拷到 CHAYUAN_ROOT)
      5. dev 仓库:``<repo>/vendor/torch_wheels``
      6. 可执行文件同级:``<argv0_dir>/torch_wheels``(扁平 fallback)

    目录约定:子目录布局如 ``win_amd64_py312_cpu/torch-2.6.0+cpu-...whl``;
    早期版本曾扁平放,_find_offline_wheels 用 rglob 同时兼容两种。
    """
    env = os.environ.get("CHAYUAN_TORCH_WHEELS_DIR")
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p

    # CHAYUAN_ROOT/torch_wheels:首启 seed_torch_wheels 拷过来的运行时目录。
    # 优先级最高(用户已经把对应 variant 装到这里 / 手动补了 CUDA wheel)。
    try:
        from chayuan.settings import CHAYUAN_ROOT
        p = Path(CHAYUAN_ROOT) / "torch_wheels"
        if p.is_dir() and any(p.rglob("*.whl")):
            return p
    except Exception:  # noqa: BLE001
        pass

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(meipass) / "vendor" / "torch_wheels"
        if p.is_dir():
            return p

    # Tauri 安装目录 — chayuan-server.exe 落在 <install>/ 下,
    # Tauri resources 落到 <install>/torch_wheels/(对齐 bundled_models 风格)。
    try:
        argv0 = Path(sys.argv[0]).resolve()
        if argv0.is_file():
            p = argv0.parent / "torch_wheels"
            if p.is_dir() and any(p.rglob("*.whl")):
                return p
            # 老布局兼容:argv0_dir/vendor/torch_wheels
            p = argv0.parent / "vendor" / "torch_wheels"
            if p.is_dir():
                return p
    except (IndexError, OSError):
        pass

    # dev: <repo>/libs/chayuan-server/chayuan/server/runtime/pytorch_installer.py
    #       parents[5] = <repo>(chayuan-server 仓库根)
    try:
        repo_root = Path(__file__).resolve().parents[5]
        p = repo_root / "vendor" / "torch_wheels"
        if p.is_dir():
            return p
    except (IndexError, OSError):
        pass

    return None


def _find_install_torch_wheels_dir() -> Optional[Path]:
    """专找 Tauri 安装目录 ``<install>/torch_wheels/`` — 给 seed_torch_wheels 用。

    跟 :func:`_offline_wheels_dir` 区别:这个**只**返回安装目录的源头,
    不返回 CHAYUAN_ROOT(因为 CHAYUAN_ROOT 是种植目标,不能自己拷自己)。
    """
    # PyInstaller frozen:argv0 是 chayuan-server.exe,parent 是 <install>/
    try:
        argv0 = Path(sys.argv[0]).resolve()
        if argv0.is_file():
            p = argv0.parent / "torch_wheels"
            if p.is_dir() and any(p.rglob("*.whl")):
                return p
    except (IndexError, OSError):
        pass
    # dev / 测试:dev 仓库的 vendor 兜底,让首启在 dev 环境也能 seed
    try:
        repo_root = Path(__file__).resolve().parents[5]
        p = repo_root / "vendor" / "torch_wheels"
        if p.is_dir() and any(p.rglob("*.whl")):
            return p
    except (IndexError, OSError):
        pass
    return None


def seed_torch_wheels(*, target_dir: Optional[Path] = None) -> dict:
    """首启把 ``<install>/torch_wheels/<sub>/`` 拷到 ``<CHAYUAN_ROOT>/torch_wheels/<sub>/``。

    跟 :func:`chayuan.server.model_registry.bundled_seed.seed_bundled_models` 同样
    的思路 — 装机时 Tauri 把 wheel 放在 install 目录,这里把它"种"到用户数据目录,
    让 ``_offline_wheels_dir`` 优先命中(免每次启动都看 install 那份)。

    幂等:目标目录已存在同名 + 同大小的 wheel 时跳过,只补缺。

    Args:
        target_dir: 显式指定目标根(测试用)。默认 ``<CHAYUAN_ROOT>/torch_wheels/``。

    Returns:
        简单 dict:``{"source": ..., "target": ..., "copied": [...], "skipped": [...],
        "errors": [...]}``。跟 bundled_seed 风格保持一致,但不弄成 dataclass(本模块
        只这一处用 report,过度抽象不值)。
    """
    report = {"source": None, "target": None, "copied": [], "skipped": [], "errors": []}
    src = _find_install_torch_wheels_dir()
    if src is None:
        logger.debug("[torch-seed] install 目录没找到 torch_wheels/,跳过")
        return report
    report["source"] = str(src)

    if target_dir is not None:
        dst_root = Path(target_dir)
    else:
        try:
            from chayuan.settings import CHAYUAN_ROOT
            dst_root = Path(CHAYUAN_ROOT) / "torch_wheels"
        except Exception as e:  # noqa: BLE001
            report["errors"].append(f"resolve CHAYUAN_ROOT failed: {e!r}")
            return report
    report["target"] = str(dst_root)

    try:
        dst_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        report["errors"].append(f"mkdir {dst_root}: {e!r}")
        return report

    for whl in src.rglob("*.whl"):
        try:
            rel = whl.relative_to(src)
        except ValueError:
            continue
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # 幂等:目标存在且大小一致 → 跳过
        try:
            if dst.is_file() and dst.stat().st_size == whl.stat().st_size:
                report["skipped"].append(str(rel))
                continue
        except OSError:
            pass
        try:
            import shutil
            shutil.copy2(whl, dst)
            report["copied"].append(str(rel))
        except OSError as e:
            report["errors"].append(f"copy {rel}: {e!r}")

    if report["copied"] or report["errors"]:
        logger.info(
            "[torch-seed] src=%s -> dst=%s copied=%d skipped=%d errors=%d",
            report["source"], report["target"],
            len(report["copied"]), len(report["skipped"]), len(report["errors"]),
        )
    return report


def torch_install_target_dir() -> Optional[Path]:
    """运行时安装 torch 的可写目标目录 —— frozen 单机版必须显式 ``--target``。

    为什么需要
    ==========
    PyInstaller frozen 的 ``chayuan-server.exe`` **没有可写的常规 site-packages**:
      - ``_internal/`` 是只读 bundle(随安装包发,普通用户对 Program Files 无写权);
      - frozen 解释器的 ``site`` 模块被裁剪,``pip install`` 默认落点(purelib)
        指向构建机路径,装机环境根本不存在。
    所以 ``pip install torch`` 必须带 ``--target <可写目录>``,且该目录运行时要在
    ``sys.path`` 上,装完(重启 server)``import torch`` 才能命中。

    目录选择:``<CHAYUAN_ROOT>/py_packages/`` —— 跟 torch_wheels / models 等
    运行时资产同在用户数据目录,可写、跨重启稳定、卸载 app 不残留到系统目录。
    可用 ``CHAYUAN_PY_PACKAGES_DIR`` 环境变量覆盖(运维 / 测试)。

    返回 None 表示拿不到 CHAYUAN_ROOT(理论上不会发生);caller 应据此放弃
    ``--target`` 退回默认行为。
    """
    env = os.environ.get("CHAYUAN_PY_PACKAGES_DIR")
    if env:
        return Path(env).expanduser()
    try:
        from chayuan.settings import CHAYUAN_ROOT
        return Path(CHAYUAN_ROOT) / "py_packages"
    except Exception:  # noqa: BLE001
        return None


def torch_dir_has_torch(path: Any) -> bool:
    """``path`` 目录里有没有**真正可 import 的** torch 包。

    只看 ``torch/`` 目录在不在不够 —— 半套 / 装坏 / 装到一半被中断的
    ``py_packages`` 里,``torch/`` 可能是个**空壳目录**(没有 ``__init__.py``)。
    必须看到 ``torch/__init__.py`` 才算数。

    只有 ``torchvision/`` 没有真 ``torch/`` 的「半套」目录**绝不能**加进
    ``sys.path`` / ``PYTHONPATH`` —— 那会让 ``import torchvision`` 命中这里、
    ``import torch`` 漏到外部环境(conda / 系统 Python),两者版本错配,
    触发 ``RuntimeError: operator torchvision::nms does not exist``。
    """
    try:
        return (Path(path) / "torch" / "__init__.py").is_file()
    except Exception:  # noqa: BLE001
        return False


def _is_half_torch_dir(path: Any) -> bool:
    """``path`` 是不是「半套」torch 目录:有 ``torchvision/`` 真包、却没有
    真 ``torch/`` —— 留在 sys.path 上必然触发 torchvision::nms 错配。"""
    try:
        p = Path(path)
        return (p / "torchvision" / "__init__.py").is_file() and not torch_dir_has_torch(p)
    except Exception:  # noqa: BLE001
        return False


def ensure_torch_target_on_syspath() -> Optional[str]:
    """把生效的 torch 目录加进 ``sys.path``,让装好的 torch 可 import。

    幂等:重复调用只插一次。返回实际(最高优先级)加入的目录字符串。

    生效目录选择:优先用户在「PyTorch 选用配置」里指定 / 自动判定的目录
    (:func:`resolve_active_torch_dir`)—— 这允许 torch 装在外部目录(系统
    Python site-packages 等)而非本应用的 ``py_packages/``;拿不到时退回
    :func:`torch_install_target_dir`(``py_packages/``,保持老行为)。

    防混用(只加一个 torch 目录)
    ============================
    历史实现把 ``active`` **和** ``py_packages/`` 都插进 sys.path。当 ``active``
    是外部目录(用户系统 Python,可能装着另一个版本的 torch,例如 2.11)而
    ``py_packages/`` 里又有我们自己装的一套 torch/torchvision(2.6 / 0.21)时,
    会出现 ``import torch`` 命中外部 2.11、``import torchvision`` 漏到 py_packages
    的 0.22 —— 版本错配,触发 ``operator torchvision::nms does not exist``。
    所以这里**只加一个 torch 目录**:有 ``active`` 就只加 ``active``(它内部
    torch+torchvision 自洽,由 resolve_active_torch_dir 保证 auto 模式优先
    py_packages);没有 ``active`` 才退回只加 ``py_packages/``。绝不同时加两个,
    杜绝跨目录拼装。

    调用时机:**任何 ``import torch`` 之前**。frozen 单机版由 PyInstaller runtime
    hook(multiprocessing_freeze.py)在每个进程最早期调用,覆盖 API server /
    config panel / worker 等所有 frozen 子进程。非 frozen(dev / pytest)环境
    torch 走常规 site-packages,这里加一个不存在的目录也无害。
    """
    # py_packages/ 默认目标 —— 作为返回值(本应用的安装落点)。
    target = torch_install_target_dir()
    target_s = str(target) if target is not None else None

    # 用户选用 / 自动判定的目录(可能是外部 site-packages)。选用配置探测涉及
    # 读盘 + 扫盘,失败忽略。
    active: Optional[str] = None
    try:
        active = resolve_active_torch_dir()
    except Exception as e:  # noqa: BLE001
        logger.debug("[torch-syspath] resolve_active_torch_dir 失败: %r", e)

    # 先主动清掉 sys.path 上已经存在的「半套」torch 目录(有 torchvision/ 真包、
    # 没真 torch/)—— 不论它是上一轮本函数加的、还是别处 / 环境变量带进来的。
    # 留着它:import torchvision 命中半套、import torch 漏到外部环境 → 版本错配
    # → 'operator torchvision::nms does not exist'。摘掉后 torchvision 会和
    # torch 一起从同一个完整环境(conda 等)解析,自洽。
    for _p in list(sys.path):
        try:
            if _is_half_torch_dir(_p):
                sys.path.remove(_p)
                logger.warning(
                    "[torch-syspath] 从 sys.path 摘掉半套 torch 目录 %s"
                    "(有 torchvision/ 无 torch/),避免 torchvision::nms 错配。", _p,
                )
        except Exception:  # noqa: BLE001
            continue

    # 只挑一个 torch 目录加进 sys.path,避免「外部 torch + py_packages torchvision」
    # 跨目录混装。active 已自洽(auto 模式优先 py_packages);没有 active 才退回
    # py_packages 本身。
    chosen = active or target_s
    # 只把**含 torch/ 子目录的目录**加进 sys.path。半套目录(有 torchvision/
    # 没 torch/ —— 例如 py_packages 被装坏、只剩 torchvision)一旦加进来,
    # import torchvision 命中这里、import torch 漏到外部环境 → 版本错配 →
    # 'operator torchvision::nms does not exist'。宁可不加,让 torch /
    # torchvision 都从同一个外部环境(conda 等)解析,保持自洽。
    if chosen and not torch_dir_has_torch(chosen):
        logger.warning(
            "[torch-syspath] %s 缺 torch/ 子目录(半套安装),不加进 sys.path"
            " —— 避免 torch / torchvision 跨目录版本错配。", chosen,
        )
        chosen = None
    if chosen and chosen not in sys.path:
        sys.path.insert(0, chosen)
    # 返回 py_packages 目标(本应用的安装落点);拿不到时退回实际加入的目录。
    return target_s if target_s is not None else chosen


def _find_offline_wheels(variant: str) -> List[Path]:
    """在 offline_wheels_dir 找匹配本机 + variant 的 torch / torchvision wheel。

    返回 [torch_wheel, torchvision_wheel](顺序,各零一个)。找不全返空 list 让
    caller 走在线 fallback。
    """
    d = _offline_wheels_dir()
    if d is None:
        return []
    # 选 wheel 的最简策略:文件名包含 "torch-" / "torchvision-",且
    # variant tag 出现在 +build 段或 abi tag 段。
    # 例如 cpu:torch-2.6.0+cpu-cp312-cp312-win_amd64.whl
    # 例如 cu124:torch-2.6.0+cu124-cp312-cp312-win_amd64.whl
    # 但 PyPI 官方 cpu wheel 文件名是 torch-2.6.0-cp312-...(没 +cpu),
    # 所以 cpu 走 "没 +cuXXX" 兜底。
    #
    # ⚠ 必须用 rglob — chayuan-server.spec 把 wheel 打到子目录
    # ``vendor/torch_wheels/<platform>_py<ver>_<variant>/*.whl``(见 spec:200-206),
    # 顶层是空的。早期用 d.glob 在装机后永远扫不到 wheel,默默走在线下载。
    # 多 variant 共存(cpu + cu124 都打)时,下面 variant 过滤会按 +cuXXX 标识
    # 把跨 variant 的混入剔掉,所以 rglob 不会乱串。
    found_torch: Optional[Path] = None
    found_tv: Optional[Path] = None
    for whl in d.rglob("*.whl"):
        name = whl.name.lower()
        if variant == "cpu":
            # cpu wheel 通常没 +cuXXX,文件名也不含 cu1xx
            if "+cu" in name:
                continue
        else:
            # CUDA wheel 必须含 +cuXXX 标识
            if f"+{variant}-" not in name and f"+{variant}.whl" not in name:
                continue
        if name.startswith("torch-") and found_torch is None:
            found_torch = whl
        elif name.startswith("torchvision-") and found_tv is None:
            found_tv = whl
    if found_torch and found_tv:
        return [found_torch, found_tv]
    return []


def _probe_torch_install() -> tuple[Optional[str], Optional[str]]:
    """返 (torch_version, torch_cuda_build);未装返 (None, None)。"""
    try:
        import torch  # type: ignore
    except ImportError:
        return None, None
    ver = getattr(torch, "__version__", "") or ""
    # 形如 "2.11.0+cu124" / "2.11.0+cpu" / "2.11.0"(PyPI 默认就是 cpu)
    if "+" in ver:
        base, build = ver.split("+", 1)
        return base, build
    return ver or None, "cpu"


def _probe_torchvision_install() -> Optional[str]:
    try:
        import torchvision  # type: ignore
    except ImportError:
        return None
    except Exception as e:  # noqa: BLE001 — torchvision import 时若 ABI 不配也会抛 RuntimeError
        logger.info("torchvision import 抛异常但版本可拿: %r", e)
        try:
            from importlib.metadata import version
            return version("torchvision")
        except Exception:  # noqa: BLE001
            return None
    return getattr(torchvision, "__version__", None)


def _probe_import_chain() -> tuple[bool, Optional[str]]:
    """模拟 transformers.image_utils 的关键 import,看链路是否健康。"""
    try:
        # 这一句正是 transformers 4.5x image_utils.py 第 55 行的 import
        from torchvision.transforms import InterpolationMode  # noqa: F401
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def detect_status() -> TorchStatus:
    """组装当前 PyTorch 安装状态。"""
    # 关键:探测前先把生效的 torch 目录加进 sys.path。否则用户刚装好 / 选好
    # torch,而本后端进程启动时 sys.path 还没有它 —— 下面 _probe_torch_install
    # 的 import torch 必然失败,状态卡在「未就绪」,但磁盘扫描(scan_installed_
    # torch)却明明认出了 torch,造成「扫到了 ✓ 却未就绪」的矛盾。
    # ensure_torch_target_on_syspath 幂等,可安全重复调用。
    try:
        ensure_torch_target_on_syspath()
    except Exception as e:  # noqa: BLE001
        logger.debug("[detect_status] ensure_torch_target_on_syspath 失败: %r", e)

    has_gpu, drv = _detect_nvidia()
    torch_ver, torch_build = _probe_torch_install()
    tv_ver = _probe_torchvision_install()
    import_ok, import_err = _probe_import_chain()
    suggested = _suggest_cuda_variant(drv) if has_gpu else "cpu"

    notes: List[str] = []
    if torch_ver is None:
        # 加进 sys.path 后仍 import 不到 torch —— 但磁盘上可能其实装好了。
        # 给一句可操作提示,别让用户对着「扫到了却未就绪」干瞪眼。
        try:
            disk = scan_installed_torch()
            if disk.get("torch_installed"):
                notes.append(
                    f"磁盘上已检测到 torch {disk.get('torch_version') or ''},"
                    "但当前后端进程未能加载它 —— 多半是 torch 在后端启动之后才"
                    "装好 / 选好。请重启后端服务(或重启整个应用)后再查看状态。"
                )
        except Exception:  # noqa: BLE001
            pass
    if has_gpu and suggested == "cpu":
        notes.append(
            f"检测到 NVIDIA GPU 但驱动版本 {drv!r} 太老,装 CUDA 版可能不工作;"
            "建议先升级 NVIDIA 驱动到 525+ 再装 CUDA 12.4 版"
        )
    if torch_ver and not import_ok:
        if tv_ver is None:
            # torchvision 压根没 import 到(No module named 'torchvision')——
            # 不是版本不配对,是 torchvision 没装 / 没和 torch 放在同一个目录。
            notes.append(
                f"torch {torch_ver} 已装,但找不到 torchvision(No module named "
                "'torchvision')。torch 与 torchvision 必须解压在【同一个目录】里"
                "——该目录要同时直接含 torch/ 和 torchvision/ 两个子目录。请把配对的"
                " torchvision wheel(torch 2.6.0 ↔ torchvision 0.21.0)解压到 torch "
                "所在的同一目录后重启后端。"
            )
        else:
            notes.append(
                f"torch {torch_ver} + torchvision {tv_ver} 已装但 import 链断裂"
                "——多半是两者版本不配对。请装配对版本(torch 2.6.0 ↔ "
                "torchvision 0.21.0),且放在同一个目录。"
            )

    offline_dir = _offline_wheels_dir()
    offline_ok = bool(offline_dir and _find_offline_wheels(suggested))
    if offline_dir and not offline_ok:
        notes.append(
            f"内置 wheel 目录存在({offline_dir})但找不到匹配 {suggested} variant 的 "
            "torch+torchvision 配对;离线安装不可用,将走在线下载"
        )

    target_dir = torch_install_target_dir()

    return TorchStatus(
        torch_version=torch_ver,
        torch_cuda_build=torch_build,
        torchvision_version=tv_ver,
        import_ok=import_ok,
        import_error=import_err,
        has_nvidia_gpu=has_gpu,
        nvidia_driver=drv,
        suggested_variant=suggested,  # type: ignore[arg-type]
        can_install=shutil.which("pip") is not None or bool(sys.executable),
        offline_wheels_available=offline_ok,
        offline_wheels_dir=str(offline_dir) if offline_dir else None,
        install_target_dir=str(target_dir) if target_dir else None,
        notes=notes,
    )


# ════════════════════════════════════════════════════════════════════════
# 无 pip 的 wheel 安装器 —— 自己下 wheel + 用 zipfile 解压
# ════════════════════════════════════════════════════════════════════════
#
# 为什么不用 pip
# ==============
# 「在线安装 PyTorch」原本让 frozen ``chayuan-server.exe -m pip install`` 跑 pip,
# 但 pip 官方**不支持被 PyInstaller freeze**,连撞三个坑:
#   ① frozen 解释器不支持 ``-m pip``(pip 没在 sys.modules,argv 处理也不对)
#   ② pip 包没打进 frozen bundle
#   ③ pip 自带的 distlib 在 frozen 下 ``Unable to locate finder``
# 后面还有 ScriptMaker launcher / certifi / build-isolation 子进程一串坑。
# pip-in-frozen 是死路。
#
# 替代方案:wheel 本质就是 zip。下载 .whl + 用标准库 ``zipfile`` 解压到
# ``torch_install_target_dir()``,该目录已被 ``ensure_torch_target_on_syspath()``
# 注入 sys.path → 解压完即「安装完成」。完全绕开 pip / distlib / setuptools。
#
# 依赖清单:固化 pinned 版本
# ==========================
# torch 的运行期依赖树很浅、很封闭(见下),lite 包场景版本也可控,所以这里
# **固化一份 pinned 清单**而不是运行时解析 METADATA —— 最稳、最可复现、不受
# PyPI 上游新版本 breaking change 影响。版本来源记录在 _TORCH_PINS 注释里。
# 若要升 torch,改 _TORCH_PINS 后重跑 packaging/preflight_torch.py 即可。

# 固化的 torch 版本 + 完整运行期依赖树。
# ── 版本来源(2026-05 核对)──────────────────────────────────────────
# torch / torchvision:取 download.pytorch.org/whl/cpu 上 cp312 有 manylinux_2_28
#   wheel 的稳定配对。torch 2.6.0 ↔ torchvision 0.21.0(torchvision wheel 的
#   METADATA 里 ``Requires-Dist: torch (==2.6.0)`` 强约束配对;官方配对表见
#   github.com/pytorch/vision —— 2.6↔0.21 / 2.7↔0.22 / 2.8↔0.23)。
#   ⚠ 不要升到 2.7.0:download.pytorch.org/whl/cpu 上没有
#   ``torch-2.7.0+cpu-cp312-cp312-win_amd64.whl``(在线装报 HTTP 403);2.6.0 的
#   +cpu Windows wheel 存在、实测可下。要升必须先确认目标版本的 +cpu win wheel 存在。
#   ⚠ torch 与 torchvision 必须**同时升级**且保持官方配对,否则 torchvision 的
#   C++ 算子(torchvision::nms)注册不上,image 链直接崩。
# torch METADATA(Requires-Dist,已剔除 ``extra ==`` 可选项 optree/opt-einsum):
#   filelock / typing-extensions>=4.10.0 / setuptools(py>=3.12) / sympy>=1.13.3
#   / networkx / jinja2 / fsspec
# torchvision METADATA 额外:numpy / pillow(!=8.3.*,>=5.3.0)
# 传递依赖:sympy→mpmath(<1.4,>=1.1.0);jinja2→markupsafe(>=2.0)。
# 其余包(filelock/networkx/fsspec/typing-extensions/setuptools)无运行期依赖。
# ── transformers 全家桶(CLIP / SigLIP2 图像向量化所需)─────────────────
# image-embedding(hf_clip.py)用 ``transformers.AutoProcessor`` + ``AutoModel``
# 加载 CLIP / SigLIP2 / Chinese-CLIP / Jina-CLIP。默认推荐模型是 SigLIP 2 ——
# 需 transformers>=4.49(SigLIP2 在 4.49.0 引入)。环境里若没装 / 装了太旧的
# transformers,就报 "当前 transformers 包没有 AutoProcessor"。所以 transformers
# 必须跟 torch 一起装进同一个 py_packages/ 目录,版本自洽。
# transformers 4.57.1 METADATA(Requires-Dist 核心,已剔除 framework extra):
#   tokenizers(>=0.22,<=0.23)/ safetensors>=0.4.3 / huggingface-hub(>=0.34,<1.0)
#   / regex / pyyaml>=5.1 / requests / numpy>=1.17 / packaging>=20.0 / filelock
#   / tqdm>=4.27
# 传递依赖:
#   tokenizers / huggingface-hub → huggingface-hub(已收)
#   huggingface-hub → fsspec(已收)/ typing-extensions(已收)/ packaging /
#                     pyyaml / requests / tqdm / filelock(已收)
#   requests → certifi / charset-normalizer / idna / urllib3
# numpy / filelock / fsspec / typing-extensions 与 torch 依赖树共用,不重复列。
# ── 纯 python vs 平台相关 ────────────────────────────────────────────
# pure(py3-none-any,任何平台同一个 wheel):filelock / typing-extensions /
#   setuptools / sympy / mpmath / networkx / jinja2 / fsspec / huggingface-hub /
#   packaging / pyyaml(有纯 py3-none-any wheel)/ tqdm / requests / certifi /
#   idna / charset-normalizer(取其 py3-none-any 纯 wheel)/ transformers
# 平台相关(有 C / Rust 扩展,需按 platform+abi 选 wheel):markupsafe / numpy /
#   pillow / tokenizers(cp39-abi3)/ safetensors(cp38-abi3)/ regex
# torch / torchvision 本身当然也是平台相关(C++ 扩展)。
# abi3 wheel 由 _resolve_pypi_wheel_url 的 ``-abi3-`` 分支命中,无需特殊处理。
_TORCH_VERSION = "2.6.0"
_TORCHVISION_VERSION = "0.21.0"

# transformers 主版本 —— 跟随 _TORCH_DEP_PINS 里的 transformers 条目,单列出来
# 便于诊断 / 日志展示。
_TRANSFORMERS_VERSION = "4.57.1"

# (pkg_name, version, is_pure) —— is_pure=True 走 py3-none-any 通配 wheel,
# is_pure=False 需按本机 platform+abi 选具体 wheel。
_TORCH_DEP_PINS: List[tuple[str, str, bool]] = [
    ("filelock", "3.18.0", True),
    ("typing_extensions", "4.13.2", True),
    ("setuptools", "80.9.0", True),
    ("sympy", "1.14.0", True),
    ("mpmath", "1.3.0", True),
    ("networkx", "3.4.2", True),
    ("jinja2", "3.1.6", True),
    ("markupsafe", "3.0.2", False),
    ("fsspec", "2025.3.2", True),
    # torchvision 额外:
    ("numpy", "2.2.6", False),
    ("pillow", "11.2.1", False),
    # ── transformers 全家桶(image-embedding / CLIP 所需,含 AutoProcessor)──
    ("transformers", _TRANSFORMERS_VERSION, True),
    ("tokenizers", "0.22.1", False),       # Rust 扩展,cp39-abi3
    ("safetensors", "0.6.2", False),       # Rust 扩展,cp38-abi3
    ("huggingface_hub", "0.35.3", True),
    ("regex", "2024.11.6", False),         # C 扩展;此版本支持 py3.8-3.13
    # ⚠ 不要把 pyyaml 加回来。chayuan-server 自身(frozen bundle / dev venv)
    #   已经带 yaml,而 py_packages 在 sys.path 靠前;一旦这里也装一份,运行中的
    #   进程会从 py_packages import yaml、把 yaml/_yaml.*.pyd 这个 C 扩展锁住,
    #   后续 force 重装解压到它就 PermissionError、整个 py_packages 被弄成半套
    #   (image-embedding 报 ModuleNotFoundError: yaml.error)。transformers 用
    #   yaml 跟版本无关,交给外层那份即可 —— 这里不装,import yaml 自然落到外层。
    ("packaging", "25.0", True),
    ("tqdm", "4.67.1", True),
    # requests + 其传递依赖:
    ("requests", "2.32.5", True),
    ("certifi", "2025.4.26", True),
    # charset-normalizer 近版本只发 mypyc 编译 wheel(无 py3-none-any 纯版),
    # 按 platform+abi 选 cp3XX wheel。
    ("charset_normalizer", "3.4.2", False),
    ("idna", "3.10", True),
    ("urllib3", "2.4.0", True),
]

# PyPI JSON API —— 给纯依赖解析 wheel 下载 URL 用(torch / torchvision 走
# download.pytorch.org,不经过这里)。
_PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"

# 一次 HTTP 请求超时(秒);torch wheel ~250MB,connect/read 给足。
_HTTP_TIMEOUT = 60
_HTTP_HEADERS = {"User-Agent": "chayuan-pytorch-installer/1.0"}


def _host_platform_tags() -> List[str]:
    """返回本机可接受的 wheel platform tag 列表(优先级从精确到宽松)。

    用于从 PyPI 的多 wheel 里挑 markupsafe / numpy / pillow 这类含 C 扩展的包。
    torch / torchvision 走 download.pytorch.org 的固定文件名,不经过这里。
    """
    sys_name = sys.platform
    machine = _platform.machine().lower()
    is_x64 = machine in ("x86_64", "amd64")
    is_arm64 = machine in ("arm64", "aarch64")
    if sys_name.startswith("win"):
        return ["win_amd64"] if is_x64 else (["win_arm64"] if is_arm64 else ["win_amd64"])
    if sys_name.startswith("linux"):
        if is_x64:
            return ["manylinux_2_28_x86_64", "manylinux2014_x86_64",
                    "manylinux_2_17_x86_64", "linux_x86_64"]
        if is_arm64:
            return ["manylinux_2_28_aarch64", "manylinux2014_aarch64",
                    "manylinux_2_17_aarch64", "linux_aarch64"]
        return ["manylinux2014_" + machine, "linux_" + machine]
    if sys_name == "darwin":
        # macOS 上不少含 C/Rust 扩展的包(charset-normalizer 等)只发
        # ``universal2`` wheel(Intel + Apple Silicon 合一),不单独发 arm64 /
        # x86_64 —— 所以 universal2 tag 必须一并列入候选,否则 arm64 / Intel
        # 机器都会漏选这类 wheel。
        if is_arm64:
            return ["macosx_11_0_arm64", "macosx_12_0_arm64", "macosx_13_0_arm64",
                    "macosx_14_0_arm64",
                    "macosx_10_13_universal2", "macosx_11_0_universal2",
                    "macosx_10_9_universal2"]
        return ["macosx_10_9_x86_64", "macosx_10_13_x86_64",
                "macosx_11_0_x86_64", "macosx_12_0_x86_64",
                "macosx_10_13_universal2", "macosx_11_0_universal2",
                "macosx_10_9_universal2"]
    return []


def _py_tag() -> str:
    """当前解释器的 CPython abi tag,如 ``cp312``。"""
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def _torch_wheel_platform_tag() -> Optional[str]:
    """download.pytorch.org 上 torch/torchvision wheel 文件名用的 platform tag。

    与 :func:`_host_platform_tags` 不同:这里只返回 PyTorch 官方 wheel **实际
    使用**的那一个规范 tag(torch >=2.6 的 Linux wheel 是 manylinux_2_28_*)。
    """
    sys_name = sys.platform
    machine = _platform.machine().lower()
    is_x64 = machine in ("x86_64", "amd64")
    is_arm64 = machine in ("arm64", "aarch64")
    if sys_name.startswith("win") and is_x64:
        return "win_amd64"
    if sys_name.startswith("linux") and is_x64:
        return "manylinux_2_28_x86_64"
    if sys_name.startswith("linux") and is_arm64:
        return "manylinux_2_28_aarch64"
    if sys_name == "darwin" and is_arm64:
        return "macosx_11_0_arm64"
    if sys_name == "darwin" and is_x64:
        return "macosx_10_9_x86_64"
    return None


def _torch_wheel_platform_tags() -> List[str]:
    """download.pytorch.org 上 torch/torchvision wheel 的**候选** platform tag。

    与 :func:`_torch_wheel_platform_tag`(单一 tag)不同:download.pytorch.org
    对不同 torch 版本、不同平台,Linux wheel 用的 platform tag 并不固定 ——
    ``linux_x86_64`` / ``manylinux_2_28_*`` / ``manylinux2014_*`` 都出现过。
    硬拼单一 tag 经常 404,所以下载前要逐个候选探测(见
    :func:`_resolve_torch_cdn_wheel_url`)。顺序:最可能命中的在前。
    """
    sys_name = sys.platform
    machine = _platform.machine().lower()
    is_x64 = machine in ("x86_64", "amd64")
    is_arm64 = machine in ("arm64", "aarch64")
    if sys_name.startswith("win") and is_x64:
        return ["win_amd64"]
    if sys_name.startswith("linux") and is_x64:
        return ["linux_x86_64", "manylinux_2_28_x86_64",
                "manylinux2014_x86_64", "manylinux_2_17_x86_64"]
    if sys_name.startswith("linux") and is_arm64:
        return ["linux_aarch64", "manylinux_2_28_aarch64",
                "manylinux2014_aarch64", "manylinux_2_17_aarch64"]
    if sys_name == "darwin" and is_arm64:
        return ["macosx_11_0_arm64"]
    if sys_name == "darwin" and is_x64:
        return ["macosx_10_9_x86_64"]
    return []


class WheelInstallError(RuntimeError):
    """wheel 下载 / 解压 / 校验失败。"""


def _http_get(url: str, *, range_header: Optional[str] = None) -> "urllib.request.addinfourl":
    headers = dict(_HTTP_HEADERS)
    if range_header:
        headers["Range"] = range_header
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT)  # noqa: S310


def _url_exists(url: str) -> bool:
    """探测 URL 能不能下:发一个 1 字节 ranged GET,2xx/3xx → True,404 等 → False。

    用 ranged GET 而非 HEAD —— download.pytorch.org 走 CDN,Range 一定支持,
    HEAD 个别边缘节点会 405。只取 1 字节,探测开销可忽略。
    """
    try:
        with _http_get(url, range_header="bytes=0-0") as r:
            return int(getattr(r, "status", 200)) < 400
    except Exception:  # noqa: BLE001 — 404 / 连接失败都算"不可下载"
        return False


def _download_file(
    url: str, dest: Path, *,
    log: Callable[[str], None],
    cancel: Optional[threading.Event] = None,
    label: str = "",
) -> Path:
    """流式下载 ``url`` 到 ``dest``,带进度日志 + 可取消。

    复用 install_task_manager 的「日志 callback + cancel_event」基础设施:
    caller 把 task 的 log/cancel 传进来,本函数只管下载并往里写进度。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    name = label or dest.name
    log(f"[download] {name} <- {url}")
    try:
        with _http_get(url) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            last_pct = -1
            last_log = 0.0
            with open(tmp, "wb") as f:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise WheelInstallError("已取消")
                    chunk = resp.read(1 << 18)  # 256 KB
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    now = time.time()
                    if total > 0:
                        pct = int(got * 100 / total)
                        # 每 5% 或每 3 秒打一行,避免日志刷屏
                        if pct != last_pct and (pct % 5 == 0 or now - last_log > 3):
                            log(f"[download] {name} {pct}% "
                                f"({got // 1048576}/{total // 1048576} MB)")
                            last_pct = pct
                            last_log = now
                    elif now - last_log > 3:
                        log(f"[download] {name} {got // 1048576} MB")
                        last_log = now
    except WheelInstallError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise WheelInstallError(f"下载 {name} 失败: {type(e).__name__}: {e}") from e
    tmp.replace(dest)
    log(f"[download] {name} 完成 ({dest.stat().st_size // 1048576} MB)")
    return dest


def _resolve_pypi_wheel_url(name: str, version: str, *, pure: bool,
                            log: Callable[[str], None]) -> str:
    """查 PyPI JSON API,挑出 ``name==version`` 对本机适用的 wheel 下载 URL。

    pure=True → 取 ``py3-none-any`` / ``py2.py3-none-any`` 通配 wheel。
    pure=False → 按 ``cp3XX`` abi + 本机 platform tag 挑(markupsafe/numpy/pillow)。
    """
    url = _PYPI_JSON.format(name=name, version=version)
    try:
        with _http_get(url) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise WheelInstallError(
            f"查询 PyPI 元数据失败 {name}=={version}: {type(e).__name__}: {e}"
        ) from e
    files = data.get("urls") or []
    wheels = [f for f in files if (f.get("filename") or "").endswith(".whl")]
    if not wheels:
        raise WheelInstallError(f"{name}=={version} 在 PyPI 上没有 wheel")

    if pure:
        for f in wheels:
            fn = f["filename"]
            if "-py3-none-any.whl" in fn or "-py2.py3-none-any.whl" in fn:
                return f["url"]
        # 个别 pure 包用 cp3-abi3 之类;退而取第一个
        log(f"[resolve] {name}=={version} 未命中 py3-none-any,取首个 wheel")
        return wheels[0]["url"]

    py = _py_tag()
    plat_tags = _host_platform_tags()
    # 优先精确 abi(cpXY-cpXY),其次 abi3,各自再按 platform tag 优先级匹配。
    for abi_pat in (f"-{py}-{py}-", f"-{py}-abi3-", "-abi3-"):
        for tag in plat_tags:
            for f in wheels:
                fn = f["filename"]
                if abi_pat in fn and tag in fn:
                    return f["url"]
    raise WheelInstallError(
        f"{name}=={version} 找不到匹配本机 ({py} / {plat_tags[:2]}) 的 wheel；"
        f"可选: {[f['filename'] for f in wheels][:8]}"
    )


def _torch_cdn_wheel_url(pkg: str, version: str, variant: str, plat: str) -> str:
    """按给定 platform tag ``plat`` 拼一个 download.pytorch.org wheel URL。

    PyTorch 官方 wheel 文件名规范(``variant`` 决定 index 路径 + ``+local`` 标识):
      CPU,Windows / Linux: ``/whl/cpu/<pkg>-<ver>+cpu-cp312-cp312-<plat>.whl``
      CPU,macOS:           ``/whl/cpu/<pkg>-<ver>-cp312-cp312-<plat>.whl``(macOS 无 +cpu)
      CUDA(cuXXX):         ``/whl/cu124/<pkg>-<ver>+cu124-cp312-cp312-<plat>.whl``

    ``plat`` 由 :func:`_resolve_torch_cdn_wheel_url` 从候选列表里逐个试。
    """
    if variant not in _PYTORCH_INDEXES:
        raise WheelInstallError(
            f"未知 variant {variant!r};支持 {tuple(_PYTORCH_INDEXES)}"
        )
    if variant != "cpu" and sys.platform == "darwin":
        raise WheelInstallError("macOS 不提供 CUDA 版 PyTorch wheel,请改装 CPU 版")
    py = _py_tag()
    index = _PYTORCH_INDEXES[variant]
    # macOS 的 CPU wheel 文件名无 +local 段;其余(Win/Linux 的 cpu / 所有 cuXXX)都带。
    local = "" if (variant == "cpu" and sys.platform == "darwin") else f"+{variant}"
    fname = f"{pkg}-{version}{local}-{py}-{py}-{plat}.whl"
    # ⚠ download.pytorch.org 的 CDN 要求 URL 路径里的 '+' 必须 %2B 编码 ——
    # 字面 '+'(torch-2.6.0+cpu-...whl)会被 CDN 拒成 403,导致所有候选
    # platform tag 探测全部"链接不可用"。这里只对 URL 编码;本地落盘文件名
    # 由调用方把 %2B 还原成字面 '+'。
    return f"{index}/{fname.replace('+', '%2B')}"


def _resolve_torch_cdn_wheel_url(
    pkg: str, version: str, variant: str = "cpu",
    *, log: Optional[Callable[[str], None]] = None,
) -> str:
    """逐个候选 platform tag 探测,返回第一个**真能下载**的 wheel URL。

    「各平台自己先验证再下载」:download.pytorch.org 对不同 torch 版本 / 不同
    平台,Linux wheel 用的 platform tag 并不固定(``linux_x86_64`` /
    ``manylinux_2_28_*`` / ``manylinux2014_*`` 都出现过),硬拼单一 tag 经常
    404(例:``torch-2.6.0+cpu-cp312-cp312-manylinux_2_28_x86_64.whl`` 在
    ``/whl/cpu`` 上并不存在)。这里按平台列候选 URL、挨个探测,验证某个真能
    下载了才用它 —— 用户点「下载」即可成功,不会撞死链。
    """
    _say = log or (lambda m: None)
    tags = _torch_wheel_platform_tags()
    if not tags:
        raise WheelInstallError(
            f"无法识别本机平台 ({sys.platform}/{_platform.machine()}),"
            f"无法拼 {pkg} wheel URL"
        )
    tried: List[str] = []
    for plat in tags:
        url = _torch_cdn_wheel_url(pkg, version, variant, plat)
        tried.append(url)
        if _url_exists(url):
            _say(f"[install] {pkg} wheel 链接已验证可下载:{url}")
            return url
        _say(f"[install] {pkg} wheel 链接不可用,试下一个候选:{url}")
    raise WheelInstallError(
        f"{pkg} {version}({variant})在 download.pytorch.org 上找不到可下载的 "
        f"wheel —— {len(tried)} 个候选链接探测全部失败。多半是该版本此平台无官方 "
        f"wheel,或当前网络不通。已试链接:\n  " + "\n  ".join(tried)
    )


def _write_wheel_member(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    out: Path,
    *,
    log: Callable[[str], None],
) -> None:
    """把 wheel 里的一个文件写到 ``out``;``out`` 被占用时改名挪走再写。

    背景:Windows 上,正在运行的 chayuan-server 进程一旦 import 过 py_packages/
    里的某个扩展模块(如 ``yaml/_yaml.cp312-win_amd64.pyd``),那个 .pyd / .dll
    就被进程锁住 —— **不能覆盖、也不能删除**(force 重装的 ``rmtree`` 也会跳过它),
    直接 ``open(out, "wb")`` 会抛 ``PermissionError``,整个安装崩掉。

    但 Windows 允许**改名**一个加载中的模块文件。所以撞 PermissionError 时:
    把旧文件改名挪到 ``<name>.old-<pid>-<ts>``(它会一直锁到持有进程退出),
    再把新版本写到原路径。下次重启后新文件生效;``.old-*`` 残留由下一次 force
    重装的 ``rmtree`` 清掉(那时已不再被占用)。
    """
    try:
        with zf.open(info) as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return
    except PermissionError:
        if not out.exists():
            raise  # 不是"旧文件被占用",是真的没写权限 → 如实抛出
    # 旧文件被占用:改名挪走,再写新文件
    sidecar = out.with_name(f"{out.name}.old-{os.getpid()}-{int(time.time())}")
    try:
        os.replace(out, sidecar)
    except OSError as e:
        raise WheelInstallError(
            f"{out.name} 被占用且无法改名挪走 —— 多半是 chayuan-server 仍加载着它。"
            f"请彻底退出察元桌面端后再重装。原始错误:{type(e).__name__}: {e}"
        ) from e
    log(f"[extract] {out.name} 被占用 → 旧文件改名 {sidecar.name},写入新版本(重启后生效)")
    with zf.open(info) as src, open(out, "wb") as dst:
        shutil.copyfileobj(src, dst)


def _extract_wheel(whl: Path, target: Path, *, log: Callable[[str], None]) -> None:
    """按 wheel 规范把 ``whl`` 解压进 ``target``(不需要 pip)。

    wheel 内布局:
      ``<pkg>/...``                        → 直接进 target
      ``<pkg>-<ver>.dist-info/...``         → 直接进 target(保留 metadata)
      ``<pkg>-<ver>.data/purelib/...``      → 内容摊进 target(去掉 .data/purelib 前缀)
      ``<pkg>-<ver>.data/platlib/...``      → 内容摊进 target(同上)
      ``<pkg>-<ver>.data/scripts|headers|data/...`` → 忽略(console script 等不需要)

    所有路径做防穿越校验(zip slip)。
    """
    target.mkdir(parents=True, exist_ok=True)
    target_resolved = target.resolve()
    extracted = 0
    with zipfile.ZipFile(whl) as zf:
        for info in zf.infolist():
            name = info.filename
            if name.endswith("/"):
                continue
            rel: Optional[str] = None
            # <pkg>-<ver>.data/<key>/... — 只 purelib / platlib 摊平,其余丢弃
            m = re.match(r"^[^/]+\.data/([^/]+)/(.+)$", name)
            if m:
                key, sub = m.group(1), m.group(2)
                if key in ("purelib", "platlib"):
                    rel = sub
                else:
                    # scripts / headers / data:不需要
                    continue
            else:
                rel = name
            out = (target / rel).resolve()
            # zip slip 防护:解压目标必须在 target 子树内
            if not str(out).startswith(str(target_resolved) + os.sep) and out != target_resolved:
                raise WheelInstallError(f"wheel 含越界路径,拒绝解压: {name}")
            out.parent.mkdir(parents=True, exist_ok=True)
            _write_wheel_member(zf, info, out, log=log)
            extracted += 1
    log(f"[extract] {whl.name}: {extracted} 个文件 -> {target}")


def _torch_already_installed(target: Path) -> bool:
    """幂等检查:target 下 torch 是否已解压完整(__init__.py + version.py)。"""
    return (target / "torch" / "__init__.py").is_file() and \
           (target / "torch" / "version.py").is_file()


def _scan_for_torch(
    picked: Path, *, max_depth: int = 6, max_dirs: int = 4000,
) -> Dict[str, Optional[Path]]:
    """从用户所选目录**逐层向下**找一份解压好的 torch。

    返回 ``{"root": <含 torch/ 的目录>|None, "whl": <扫到的 torch-*.whl>|None}``。

    支持三种情形:
      1. 选到含 ``torch/`` 的目录本身(site-packages / py_packages);
      2. 选到 ``torch/`` 包目录本身 → 返回其父;
      3. torch 埋在子目录里(如 wheel 解压落进 ``torch-2.6.0+cpu.../``)→ 逐层找。

    优先返回 torch 与 torchvision **同在一层**的目录(运行时两者必须同目录,
    否则跨目录版本错配);只有 torch 没有 torchvision 时退而返回 torch 那层。
    没找到解压好的 torch、但扫到未解压的 ``torch-*.whl`` 时一并带回,便于给出
    "请先解压 wheel" 的精确提示。BFS + ``max_depth`` / ``max_dirs`` 双重限深,
    避免在巨大目录树里空转。
    """
    result: Dict[str, Optional[Path]] = {"root": None, "whl": None}
    # 情形 2:选到 torch/ 包目录本身
    if (picked.name == "torch"
            and (picked / "version.py").is_file()
            and (picked / "__init__.py").is_file()):
        result["root"] = picked.parent
        return result

    torch_only: Optional[Path] = None
    queue: List[tuple[Path, int]] = [(picked, 0)]
    visited = 0
    while queue:
        d, depth = queue.pop(0)
        visited += 1
        if visited > max_dirs:
            break
        if _torch_already_installed(d):
            # torch + torchvision 同目录 = 最佳,立即返回
            if (d / "torchvision" / "__init__.py").is_file():
                result["root"] = d
                return result
            # 只有 torch:先记下,继续找有没有更全的(torch+torchvision)
            if torch_only is None:
                torch_only = d
            continue  # 命中 torch 目录,不再下钻它的子目录
        try:
            entries = sorted(d.iterdir())
        except (OSError, PermissionError):
            continue
        for c in entries:
            if c.is_dir():
                if depth < max_depth:
                    queue.append((c, depth + 1))
            elif (result["whl"] is None and c.is_file()
                  and c.name.lower().startswith("torch-")
                  and c.name.lower().endswith(".whl")):
                result["whl"] = c
    result["root"] = torch_only
    return result


def _parse_torch_version_py(version_py: Path) -> tuple[Optional[str], Optional[str]]:
    """读 ``torch/version.py``,解析 ``__version__`` 与 ``cuda``,返 (version, cuda_build)。

    PyTorch wheel 解压后 ``torch/version.py`` 内容形如::

        __version__ = '2.7.0+cpu'
        debug = False
        cuda: Optional[str] = None          # CPU 版
        # 或  cuda: Optional[str] = '12.4'   # CUDA 版
        git_version = '...'

    解析策略:纯文本正则,**不 import torch**(扫描场景 torch 可能 ABI 不配 /
    跟当前进程已 import 的版本冲突,import 会污染 / 崩)。

    返回:
      version    —— ``__version__`` 去掉 ``+local`` 段后的纯版本(如 ``2.7.0``);拿不到 None。
      cuda_build —— ``cu124`` / ``cpu`` / None(版本文件残缺)。优先用 ``__version__``
                    里的 ``+cuXXX`` / ``+cpu`` local 段;没有再退回 ``cuda = '12.4'``
                    字段(转成 ``cu124``);两者都没有 → ``cpu``。
    """
    try:
        text = version_py.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None

    raw_version: Optional[str] = None
    m = re.search(r"""__version__\s*[:=][^'"]*['"]([^'"]+)['"]""", text)
    if m:
        raw_version = m.group(1).strip()

    version: Optional[str] = None
    local: Optional[str] = None
    if raw_version:
        if "+" in raw_version:
            version, local = raw_version.split("+", 1)
        else:
            version = raw_version

    cuda_build: Optional[str] = None
    if local:
        # +cu124 / +cpu / +rocm6.0 …… 直接采纳(cuXXX / cpu 是我们关心的两类)
        cuda_build = local.strip()
    else:
        # 没 local 段(PyPI 默认 cpu wheel 就没有)→ 看 cuda 字段
        cm = re.search(r"""\bcuda\s*[:=][^'"]*['"]?([0-9.]+|None)['"]?""", text)
        if cm:
            val = cm.group(1).strip()
            if val and val != "None":
                # "12.4" → "cu124";"11.8" → "cu118"
                digits = val.replace(".", "")
                cuda_build = f"cu{digits}"
            else:
                cuda_build = "cpu"
        else:
            cuda_build = "cpu"

    return version, cuda_build


def scan_installed_torch(target: Optional[Path] = None) -> Dict[str, Any]:
    """扫描 ``py_packages/`` 看用户手动放好的 torch 在不在、什么版本、CPU 还是 CUDA。

    对应「模型下载对话框的扫描挂载」体验 —— 用户可以把手动下好 / 解压好的 torch
    wheel 放进 :func:`torch_install_target_dir`,点「扫描」让本函数识别。**纯文件
    探测,不 import torch**(避免污染当前进程 / ABI 不配崩溃)。

    Args:
        target: 显式指定扫描目录(测试用)。默认 :func:`torch_install_target_dir`。

    Returns:
        dict::

            {
              "target_dir": "<CHAYUAN_ROOT>/py_packages",   # 用户该把 torch 放这
              "torch_installed": True/False,                 # py_packages/torch/ 完整
              "torch_version": "2.7.0" | None,
              "torch_cuda_build": "cu124" | "cpu" | None,    # CPU 还是 CUDA 版
              "is_cuda": True/False,                         # 便于前端直接判定
              "torchvision_installed": True/False,
              "torchvision_version": "0.22.0" | None,
            }
    """
    tgt = Path(target) if target is not None else torch_install_target_dir()
    out: Dict[str, Any] = {
        "target_dir": str(tgt) if tgt else None,
        "torch_installed": False,
        "torch_version": None,
        "torch_cuda_build": None,
        "is_cuda": False,
        "torchvision_installed": False,
        "torchvision_version": None,
    }
    if tgt is None:
        return out

    if _torch_already_installed(tgt):
        out["torch_installed"] = True
        ver, build = _parse_torch_version_py(tgt / "torch" / "version.py")
        out["torch_version"] = ver
        out["torch_cuda_build"] = build
        out["is_cuda"] = bool(build and build.startswith("cu"))

    # torchvision 没有独立 version.py;装好就有 torchvision/__init__.py,
    # 版本走 dist-info/METADATA(wheel 解压会保留 metadata)。
    tv_init = tgt / "torchvision" / "__init__.py"
    if tv_init.is_file():
        out["torchvision_installed"] = True
        out["torchvision_version"] = _read_dist_info_version(tgt, "torchvision")

    return out


def _read_dist_info_version(target: Path, pkg: str) -> Optional[str]:
    """从 ``target`` 下 ``<pkg>-<ver>.dist-info/METADATA`` 读包版本(不 import)。"""
    try:
        for child in target.iterdir():
            name = child.name
            if not child.is_dir() or not name.endswith(".dist-info"):
                continue
            stem = name[: -len(".dist-info")]
            # <pkg>-<ver> —— 大小写不敏感比对 pkg 段
            if "-" not in stem:
                continue
            pkg_part, ver_part = stem.rsplit("-", 1)
            if pkg_part.lower().replace("_", "-") == pkg.lower().replace("_", "-"):
                return ver_part or None
    except OSError:
        pass
    # 兜底:读 METADATA 里的 Version 行
    try:
        for meta in target.glob(f"{pkg}-*.dist-info/METADATA"):
            for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lower().startswith("version:"):
                    return line.split(":", 1)[1].strip() or None
    except OSError:
        pass
    return None


# ════════════════════════════════════════════════════════════════════════
# 多目录 torch 探测 + 用户自选 torch 目录 + 「选用哪个 / 不用」配置
# ════════════════════════════════════════════════════════════════════════
#
# 背景
# ====
# 用户机器上 torch 可能装在好几个地方:
#   - 本应用装的 ``<CHAYUAN_ROOT>/py_packages/``(install_pytorch_wheels 落点);
#   - 系统 Python 的 site-packages(用户自己 ``pip install torch`` 过);
#   - 之前手动解压 / 别处 venv 的 site-packages。
# 而且可能同时存在 CPU 版和 CUDA 版(在不同目录)。本节负责:
#   1. :func:`validate_torch_dir` — 验证某个目录里有没有合法 torch,返版本/CPU/CUDA。
#   2. :func:`scan_torch_locations` — 扫所有候选目录,汇总 CPU 版 / GPU 版各自存在与否。
#   3. torch 选用配置(``torch_selection.json``):mode=auto / path / disabled,
#      允许用户「指定一个外部已装好的 torch 目录」或「不使用 PyTorch」。
#   4. :func:`resolve_active_torch_dir` — 按配置算出 sidecar 启动时该把哪个目录
#      加进 PYTHONPATH(或返回 disabled 让 caller 不启 image-embedding)。


def validate_torch_dir(path: Any) -> Dict[str, Any]:
    """验证 ``path`` 目录里是否有一份合法的 torch,返回版本 / CPU-CUDA 判定。

    「合法」标准跟 :func:`_torch_already_installed` 一致:目录下有
    ``torch/__init__.py`` + ``torch/version.py``。纯文件探测,**不 import torch**。

    逐层识别:从 ``path`` 向下递归搜索,以下情形都能自动认出 torch ——
      - 直接指向含 ``torch/`` 子目录的目录(site-packages / py_packages 本身);
      - 指向 ``torch/`` 目录本身(用户选错一层)—— 自动回退到其父目录;
      - torch 埋在子目录里(如 wheel 解压落进 ``torch-2.6.0+cpu.../``)—— 逐层找出。

    Returns:
        dict::

            {
              "valid": True/False,
              "dir": "<规范化后的目录>",        # valid 时是真正含 torch/ 的那层
              "torch_version": "2.7.0" | None,
              "torch_cuda_build": "cu124" | "cpu" | None,
              "is_cuda": True/False,
              "torchvision_installed": True/False,
              "torchvision_version": "0.22.0" | None,
              "reason": "...",                  # invalid 时给人类可读原因
            }
    """
    out: Dict[str, Any] = {
        "valid": False,
        "dir": None,
        "torch_version": None,
        "torch_cuda_build": None,
        "is_cuda": False,
        "torchvision_installed": False,
        "torchvision_version": None,
        "reason": "",
    }
    if not path:
        out["reason"] = "未提供目录路径"
        return out
    try:
        p = Path(str(path)).expanduser()
    except (TypeError, ValueError):
        out["reason"] = f"非法路径: {path!r}"
        return out
    if not p.exists():
        out["reason"] = f"目录不存在: {p}"
        return out
    if not p.is_dir():
        out["reason"] = f"不是目录: {p}"
        return out

    # 逐层识别:从所选目录向下递归找真正"装好 torch 的目录"——
    # 支持选到 site-packages、torch/ 包本身、或 torch 埋在子目录里
    # (如 wheel 解压落进 torch-2.6.0+cpu.../)三种情形。
    scan = _scan_for_torch(p)
    cand = scan["root"]
    if cand is None:
        whl = scan["whl"]
        if whl is not None:
            # 扫到未解压的 wheel —— 给"先解压"的精确提示
            out["reason"] = (
                f"在 {p} 下逐层搜索,只找到未解压的 wheel:{whl.name};"
                "torch / torchvision 的 .whl 本质是 zip,请先解压(可改名 .zip 解压,"
                "或用 7-Zip),再选解压出来的目录。"
            )
        else:
            out["reason"] = (
                f"在 {p} 下逐层搜索仍未找到解压好的 torch"
                "(缺 torch/__init__.py 或 torch/version.py)。请选择「装好 torch 的"
                "目录」——系统 Python 的 site-packages,或解压过 torch wheel 的目录。"
            )
        return out

    ver, build = _parse_torch_version_py(cand / "torch" / "version.py")
    out.update(
        valid=True,
        dir=str(cand),
        torch_version=ver,
        torch_cuda_build=build,
        is_cuda=bool(build and build.startswith("cu")),
        reason="ok",
    )
    tv_init = cand / "torchvision" / "__init__.py"
    if tv_init.is_file():
        out["torchvision_installed"] = True
        out["torchvision_version"] = _read_dist_info_version(cand, "torchvision")
    return out


def _system_site_packages_dirs() -> List[Path]:
    """返回系统 / 当前解释器的 site-packages 候选目录(去重,只保留存在的)。

    frozen 单机版的 ``sys.path`` 里不一定有真实 site-packages;但用户机器上
    可能装了系统 Python 并 ``pip install`` 过 torch。这里尽量多探几处:
      - ``site.getsitepackages()`` / ``site.getusersitepackages()``;
      - ``sys.path`` 上所有名为 ``site-packages`` / ``dist-packages`` 的目录。
    """
    found: List[Path] = []
    seen: set = set()

    def _add(raw: Any) -> None:
        if not raw:
            return
        try:
            p = Path(str(raw)).resolve()
        except (TypeError, ValueError, OSError):
            return
        key = str(p)
        if key in seen or not p.is_dir():
            return
        seen.add(key)
        found.append(p)

    try:
        import site
        getsp = getattr(site, "getsitepackages", None)
        if callable(getsp):
            for d in getsp():
                _add(d)
        getusp = getattr(site, "getusersitepackages", None)
        if callable(getusp):
            _add(getusp())
    except Exception:  # noqa: BLE001 — frozen 下 site 被裁剪,getsitepackages 可能不在
        pass

    for entry in list(sys.path):
        name = os.path.basename(str(entry).rstrip("/\\"))
        if name in ("site-packages", "dist-packages"):
            _add(entry)

    return found


def scan_torch_locations() -> Dict[str, Any]:
    """跨多个候选目录扫 torch,汇总「CPU 版 / GPU(CUDA)版」各自是否存在。

    候选目录(按优先级):
      1. 用户在配置里指定的 torch 目录(``torch_selection.json`` 的 ``path``);
      2. 本应用安装目标 :func:`torch_install_target_dir`(``py_packages/``);
      3. 系统 / 当前解释器的 site-packages(:func:`_system_site_packages_dirs`)。

    每个目录走 :func:`validate_torch_dir` 探测,**纯文件探测不 import torch**。

    Returns:
        dict::

            {
              "locations": [ {dir, source, valid, torch_version,
                              torch_cuda_build, is_cuda, ...}, ... ],
              "has_cpu": True/False,        # 任一目录有 CPU 版 torch
              "has_cuda": True/False,       # 任一目录有 CUDA 版 torch
              "cpu_dir": "<含 CPU 版 torch 的目录>" | None,
              "cuda_dir": "<含 CUDA 版 torch 的目录>" | None,
              "selection": { ... },         # 当前 torch 选用配置(见 get_torch_selection)
              "active_dir": "<当前生效的 torch 目录>" | None,
            }
    """
    selection = get_torch_selection()

    # (source 标签, 目录) —— 顺序即优先级
    candidates: List[tuple[str, Path]] = []
    sel_path = selection.get("path")
    if sel_path:
        try:
            candidates.append(("user-selected", Path(str(sel_path)).expanduser()))
        except (TypeError, ValueError):
            pass
    tgt = torch_install_target_dir()
    if tgt is not None:
        candidates.append(("py_packages", Path(tgt)))
    for sp in _system_site_packages_dirs():
        candidates.append(("system-python", sp))

    locations: List[Dict[str, Any]] = []
    seen_dirs: set = set()
    has_cpu = has_cuda = False
    cpu_dir: Optional[str] = None
    cuda_dir: Optional[str] = None

    for source, raw_dir in candidates:
        try:
            key = str(Path(raw_dir).resolve())
        except (OSError, ValueError):
            key = str(raw_dir)
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        info = validate_torch_dir(raw_dir)
        entry = {
            "dir": info.get("dir") or str(raw_dir),
            "source": source,
            "valid": info["valid"],
            "torch_version": info["torch_version"],
            "torch_cuda_build": info["torch_cuda_build"],
            "is_cuda": info["is_cuda"],
            "torchvision_installed": info["torchvision_installed"],
            "torchvision_version": info["torchvision_version"],
            "reason": info["reason"],
        }
        locations.append(entry)
        if info["valid"]:
            if info["is_cuda"]:
                has_cuda = True
                cuda_dir = cuda_dir or info["dir"]
            else:
                has_cpu = True
                cpu_dir = cpu_dir or info["dir"]

    active = resolve_active_torch_dir(selection=selection,
                                     has_cpu=has_cpu, has_cuda=has_cuda,
                                     cpu_dir=cpu_dir, cuda_dir=cuda_dir)
    return {
        "locations": locations,
        "has_cpu": has_cpu,
        "has_cuda": has_cuda,
        "cpu_dir": cpu_dir,
        "cuda_dir": cuda_dir,
        "selection": selection,
        "active_dir": active,
    }


# ---- torch 选用配置:auto / path / disabled --------------------------------
#
# 存盘位置:``<CHAYUAN_ROOT>/runtime/torch_selection.json``(跟 infinity.yaml
# 等运行时配置同目录)。结构:
#   {"mode": "auto"|"path"|"disabled", "path": "<目录>"|null, "prefer": "cpu"|"cuda"|null}
# - mode=auto    : 自动用 py_packages / 系统 Python 里能找到的 torch;
#                  ``prefer`` 在「CPU + CUDA 两个版本都装了」时决定优先用哪个。
# - mode=path    : 强制用 ``path`` 指定的外部目录(用户指定的「已装好的 torch」)。
# - mode=disabled: 不使用 PyTorch —— image-embedding 等依赖 torch 的功能不可用。

_VALID_TORCH_MODES = ("auto", "path", "disabled")
_VALID_TORCH_PREFER = ("cpu", "cuda")


def _torch_selection_path() -> Optional[Path]:
    """``torch_selection.json`` 的存盘路径。拿不到 CHAYUAN_ROOT 返 None。"""
    env = os.environ.get("CHAYUAN_TORCH_SELECTION_FILE")
    if env:
        return Path(env).expanduser()
    try:
        from chayuan.settings import CHAYUAN_ROOT
        return Path(CHAYUAN_ROOT) / "runtime" / "torch_selection.json"
    except Exception:  # noqa: BLE001
        return None


def _default_torch_selection() -> Dict[str, Any]:
    return {"mode": "auto", "path": None, "prefer": None}


def get_torch_selection() -> Dict[str, Any]:
    """读 torch 选用配置;文件不存在 / 损坏 → 返回默认(mode=auto)。"""
    p = _torch_selection_path()
    if p is None or not p.is_file():
        return _default_torch_selection()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("[torch-select] 配置文件损坏,用默认: %r", e)
        return _default_torch_selection()
    if not isinstance(data, dict):
        return _default_torch_selection()
    mode = data.get("mode")
    if mode not in _VALID_TORCH_MODES:
        mode = "auto"
    prefer = data.get("prefer")
    if prefer not in _VALID_TORCH_PREFER:
        prefer = None
    path = data.get("path")
    return {
        "mode": mode,
        "path": str(path) if path else None,
        "prefer": prefer,
    }


def set_torch_selection(
    *,
    mode: str,
    path: Optional[str] = None,
    prefer: Optional[str] = None,
) -> Dict[str, Any]:
    """写 torch 选用配置。

    mode=path 时会先 :func:`validate_torch_dir` 校验 ``path``,目录里没有合法
    torch 直接 ``ValueError`` 拒绝(不写坏配置)。

    Returns: 写盘后的配置 dict(同 :func:`get_torch_selection`)。
    Raises:  ValueError —— mode 非法 / path 模式但目录无效。
    """
    if mode not in _VALID_TORCH_MODES:
        raise ValueError(
            f"未知 mode {mode!r};支持 {_VALID_TORCH_MODES}"
        )
    if prefer is not None and prefer not in _VALID_TORCH_PREFER:
        raise ValueError(f"未知 prefer {prefer!r};支持 {_VALID_TORCH_PREFER}")

    resolved_path: Optional[str] = None
    if mode == "path":
        if not path:
            raise ValueError("mode=path 必须同时提供 path(已装好 torch 的目录)")
        info = validate_torch_dir(path)
        if not info["valid"]:
            raise ValueError(info["reason"] or f"{path} 不是合法的 torch 目录")
        resolved_path = info["dir"]  # 规范化到真正含 torch/ 的那层

    cfg = {"mode": mode, "path": resolved_path, "prefer": prefer}
    p = _torch_selection_path()
    if p is None:
        raise ValueError("拿不到 CHAYUAN_ROOT,无法保存 torch 选用配置")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        raise ValueError(f"写 torch 选用配置失败: {e}") from e
    logger.info("[torch-select] 已保存: %s", cfg)
    return cfg


def resolve_active_torch_dir(
    *,
    selection: Optional[Dict[str, Any]] = None,
    has_cpu: Optional[bool] = None,
    has_cuda: Optional[bool] = None,
    cpu_dir: Optional[str] = None,
    cuda_dir: Optional[str] = None,
) -> Optional[str]:
    """按 torch 选用配置算出当前应生效的 torch 目录。

    sidecar(image-embedding / infinity)启动时调本函数,把返回目录加进
    ``PYTHONPATH``,让子进程 import 到正确的 torch。

    决策:
      - mode=disabled        → 返回 None(caller 据此「不启 image-embedding」)。
      - mode=path            → 返回配置里的 ``path``(已在 set 时校验过)。
      - mode=auto:
          * **本应用 py_packages/ 自带一份合法 torch → 直接用它**(优先级最高,
            见下「防混用」);
          * 否则同时有 CPU + CUDA 版 → 按 ``prefer`` 选(prefer 缺省时优先 CUDA);
          * 只有一个 → 用那个;
          * 都没有 → 返回 None。

    防混用(为什么 py_packages/ 优先)
    ================================
    :func:`install_pytorch_wheels` 把 torch + torchvision + transformers 全家桶
    **作为一套互相配对的版本**一起解压进 ``py_packages/``。而用户系统 Python 的
    site-packages 里可能装着**另一个版本**的 torch(例如 torch 2.11),且不一定
    配套 torchvision。若 auto 选了系统目录当 active,而 ``ensure_torch_target_on_
    syspath`` 又始终把 ``py_packages/`` 也加进 sys.path,就会出现
    ``import torch`` 命中系统 torch 2.11、``import torchvision`` 命中 py_packages
    的 0.22 —— 版本错配,触发 ``operator torchvision::nms does not exist``。
    所以:只要 ``py_packages/`` 里有我们自己装的那套自洽 torch,auto 模式就**必须**
    用它,绝不退回系统 Python 的 torch。用户若确实想用系统 / 外部 torch,应显式走
    mode=path 指定目录(那种情况下用户自负配对责任)。

    传入 ``selection`` / ``has_*`` / ``*_dir`` 可避免重复扫盘(scan_torch_locations
    内部已经扫过就直接传进来);不传则本函数自己扫。
    """
    sel = selection if selection is not None else get_torch_selection()
    mode = sel.get("mode", "auto")

    if mode == "disabled":
        return None
    if mode == "path":
        return sel.get("path") or None

    # mode=auto 防混用:本应用 py_packages/ 自带合法 torch → 直接用它,不再看
    # 系统 Python。py_packages/ 里的 torch + torchvision + transformers 是
    # install_pytorch_wheels 一次性装的自洽配对,系统 torch 版本不可控,混用会
    # 触发 torchvision::nms 注册失败。
    tgt = torch_install_target_dir()
    if tgt is not None:
        own = validate_torch_dir(tgt)
        if own["valid"]:
            return own["dir"]

    # py_packages/ 没有自带 torch —— 才退回扫系统 Python 等候选目录。
    if has_cpu is None or has_cuda is None:
        has_cpu = has_cuda = False
        cpu_dir = cuda_dir = None
        for d in _system_site_packages_dirs():
            info = validate_torch_dir(d)
            if not info["valid"]:
                continue
            if info["is_cuda"]:
                if not has_cuda:
                    has_cuda, cuda_dir = True, info["dir"]
            else:
                if not has_cpu:
                    has_cpu, cpu_dir = True, info["dir"]

    if has_cpu and has_cuda:
        prefer = sel.get("prefer")
        if prefer == "cpu":
            return cpu_dir
        # prefer=cuda 或未指定 → 优先 GPU(用户既然装了 CUDA 版多半想用)
        return cuda_dir
    if has_cuda:
        return cuda_dir
    if has_cpu:
        return cpu_dir
    return None


def torch_runtime_unavailable_reason() -> Optional[str]:
    """若当前 torch 不可用,返回一句人类可读的诊断;可用则返 None。

    供 image-embedding 失败链路复用 —— 把「为什么没有 torch」翻译成可执行指引。
    判定:
      - mode=disabled       → 用户主动关了 PyTorch;
      - 没有任何生效 torch 目录 → torch 没装;
      - 有目录但 import 链断 → 版本不配对。
    """
    sel = get_torch_selection()
    if sel.get("mode") == "disabled":
        return (
            "当前已在「设置 → 本地模型服务 → PyTorch」选择「不使用 PyTorch」,"
            "图像向量化(image-embedding)依赖 PyTorch,已被禁用。"
            "如需使用,请去该页面改回「自动」或指定一个已装好 torch 的目录。"
        )
    active = resolve_active_torch_dir(selection=sel)
    if not active:
        return (
            "未检测到可用的 PyTorch。图像向量化(image-embedding)依赖 PyTorch + "
            "图像向量模型(CLIP / SigLIP 等)。请到「设置 → 本地模型服务 → PyTorch」"
            "安装 PyTorch(CPU 版或 GPU 版),或指定一个已装好 torch 的目录;"
            "再到「图像嵌入」处下载图像向量模型,然后重新上传图片。"
        )
    return None


def install_pytorch_wheels(
    *,
    variant: str = "cpu",
    prefer_offline: bool = True,
    log: Optional[Callable[[str], None]] = None,
    cancel: Optional[threading.Event] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """纯 Python(无 pip)安装 PyTorch + torchvision + 全部运行期依赖。

    流程:
      1. 确定目标目录 ``torch_install_target_dir()`` 与本机平台 / py tag。
      2. 幂等:已装过(target 下有 torch)且非 force → 直接返回 skipped。
      3. 收集 wheel:
         * ``prefer_offline=True`` 且 vendor/torch_wheels 有对应 variant 配对 →
           直接用本地 wheel 解压 torch + torchvision(纯依赖仍需在线补,见下)。
         * 否则从 download.pytorch.org 对应 index 在线下 torch + torchvision wheel。
         * torch 的纯/平台依赖一律从 PyPI 下(_TORCH_DEP_PINS 固化版本)。
      4. **原子安装**:整套 wheel 解压进 ``<target>.installing/`` staging 目录。
      5. 校验 staging 自洽(torch/__init__.py + version.py)后,才把旧 target
         挪走、staging 换入 —— 安装中途失败绝不破坏原本能用的 torch。

    Args:
        variant: ``cpu``(默认)或 ``cu118`` / ``cu121`` / ``cu124`` / ``cu126``。
            CUDA variant 走 ``download.pytorch.org/whl/cuXXX`` index,torch wheel
            带 CUDA runtime,体积 2-3 GB(CPU 版 ~250 MB);纯依赖不变。
            macOS 无 CUDA wheel,传 cuXXX 会失败。
        prefer_offline: 优先用 vendor/torch_wheels 里打包期预下的 torch/tv wheel。
        log: 日志回调(install_task_manager 把 task.log.append 传进来)。
        cancel: 取消信号(install_task_manager 的 task.cancel_event)。
        force: 已装也重装(否则 target 已有 torch 就跳过)。安装一律**原子化**:
            解压进 staging、校验通过才换入 target,不会半途毁掉旧 torch。

    Returns:
        ``{"action": "installed"|"skipped"|"failed", ...}``。
    """
    _log = log or (lambda m: logger.info("%s", m))

    if variant not in _PYTORCH_INDEXES:
        return {"action": "failed",
                "error": f"未知 variant {variant!r};支持 {tuple(_PYTORCH_INDEXES)}"}
    is_cuda = variant != "cpu"

    target = torch_install_target_dir()
    if target is None:
        return {"action": "failed", "error": "拿不到 CHAYUAN_ROOT / py_packages 目标目录"}
    target = Path(target)

    if _torch_already_installed(target) and not force:
        _log(f"[install] torch 已安装于 {target},跳过(force=True 可强制重装)")
        return {"action": "skipped", "target": str(target),
                "reason": "torch already extracted"}

    py = _py_tag()
    plat = _torch_wheel_platform_tag()
    _log(f"[install] 目标目录: {target}")
    _log(f"[install] variant={variant}"
         + ("(CUDA 版,torch wheel 含 CUDA runtime,体积 2-3 GB)"
            if is_cuda else "(CPU 版,~250 MB)"))
    _log(f"[install] 本机: python={py} platform={plat or '未知'} "
         f"({sys.platform}/{_platform.machine()})")
    if plat is None:
        return {"action": "failed",
                "error": f"不支持的平台 {sys.platform}/{_platform.machine()}"}
    if is_cuda and sys.platform == "darwin":
        return {"action": "failed",
                "error": "macOS 不提供 CUDA 版 PyTorch,请改装 CPU 版"}

    work = Path(tempfile.mkdtemp(prefix="chayuan-torch-"))
    downloaded: List[Path] = []
    try:
        # ---- 1) torch + torchvision wheel ----
        offline = _find_offline_wheels(variant) if prefer_offline else []
        if offline:
            _log(f"[install] 使用内置离线 wheel: {offline[0].parent}")
            torch_whls = list(offline)  # [torch, torchvision]
        else:
            if prefer_offline:
                _log(f"[install] 未找到 {variant} 内置 wheel,"
                     f"改为在线下载 torch / torchvision")
            torch_whls = []
            for pkg, ver in (("torch", _TORCH_VERSION),
                             ("torchvision", _TORCHVISION_VERSION)):
                if cancel is not None and cancel.is_set():
                    raise WheelInstallError("已取消")
                url = _resolve_torch_cdn_wheel_url(pkg, ver, variant, log=_log)
                # URL 里的 '+' 已 %2B 编码;本地文件名还原成字面 '+'(.whl
                # 规范文件名),避免落盘出现 torch-2.6.0%2Bcpu-...whl。
                dest = work / url.rsplit("/", 1)[-1].replace("%2B", "+")
                _download_file(url, dest, log=_log, cancel=cancel, label=f"{pkg} {ver}")
                downloaded.append(dest)
                torch_whls.append(dest)

        # ---- 2) 纯依赖 / 平台依赖一律从 PyPI 下(固化版本)----
        dep_whls: List[Path] = []
        for name, ver, pure in _TORCH_DEP_PINS:
            if cancel is not None and cancel.is_set():
                raise WheelInstallError("已取消")
            url = _resolve_pypi_wheel_url(name, ver, pure=pure, log=_log)
            dest = work / url.rsplit("/", 1)[-1].split("#")[0]
            _download_file(url, dest, log=_log, cancel=cancel, label=f"{name} {ver}")
            downloaded.append(dest)
            dep_whls.append(dest)

        # ---- 3) 原子安装:整套 wheel 解压进 staging 兄弟目录 ----
        # 绝不能再像以前那样「先 rmtree(target) 再原地解压」—— 一旦解压中途
        # 失败(Windows 上 torch 的 .pyd/.dll 被占用、网络断、用户取消),
        # target 就被清空 / 只剩半套,**原本能用的 torch 被毁掉、还装不回来**
        # (这正是「重装一次图像向量化就再也用不了」的根因)。
        # 改为:解压进 <target>.installing/,校验自洽后再整体换入 target;
        # 失败则 target 原封不动,旧 torch 始终可用。
        from chayuan.server.knowledge_base.kb_service.base import force_rmtree

        stage = target.parent / f"{target.name}.installing"
        if stage.exists():
            force_rmtree(str(stage))
        stage.mkdir(parents=True, exist_ok=True)

        # ---- 4) 解压全部 wheel 进 staging ----
        if cancel is not None and cancel.is_set():
            force_rmtree(str(stage))
            raise WheelInstallError("已取消")
        _log(f"[install] 解压 {len(torch_whls) + len(dep_whls)} 个 wheel 到 {stage}")
        for whl in [*torch_whls, *dep_whls]:
            _extract_wheel(Path(whl), stage, log=_log)

        # ---- 5) 校验 staging 自洽 ----
        if not _torch_already_installed(stage):
            force_rmtree(str(stage))
            raise WheelInstallError(
                "解压完成但 staging 下缺 torch/__init__.py / version.py — "
                "wheel 可能损坏或布局异常(target 未动,旧 torch 仍可用)"
            )

        # ---- 6) 换入:校验通过,旧 target 挪走、staging 改名成 target ----
        if target.exists() and not force_rmtree(str(target)):
            force_rmtree(str(stage))
            raise WheelInstallError(
                f"旧 torch 目录 {target} 清不掉(文件被占用?)—— 为避免装成"
                "半套,放弃换入;旧 torch 仍可用。关掉占用进程后重试。"
            )
        try:
            os.rename(str(stage), str(target))
        except Exception:  # noqa: BLE001 — 跨盘等 rename 失败 → 退回 move
            shutil.move(str(stage), str(target))
        _log(f"[install] ✓ torch {_TORCH_VERSION} ({variant}) + "
             f"torchvision {_TORCHVISION_VERSION} + "
             f"transformers {_TRANSFORMERS_VERSION} 已安装到 {target}")
        _log("[install] 重启 chayuan-server 后即可 import torch / transformers"
             "(图像向量化 CLIP / SigLIP2 依赖)")
        return {
            "action": "installed",
            "target": str(target),
            "variant": variant,
            "torch_version": _TORCH_VERSION,
            "torchvision_version": _TORCHVISION_VERSION,
            "transformers_version": _TRANSFORMERS_VERSION,
            "wheels": [Path(w).name for w in (*torch_whls, *dep_whls)],
        }
    except WheelInstallError as e:
        _log(f"[install] ✗ 失败: {e}")
        return {"action": "failed", "error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.exception("install_pytorch_wheels 崩溃")
        _log(f"[install] ✗ 崩溃: {type(e).__name__}: {e}")
        return {"action": "failed", "error": f"{type(e).__name__}: {e}"}
    finally:
        # 在线下载的临时 wheel 清掉;离线 vendor wheel 不动(它们是原始资产)
        shutil.rmtree(work, ignore_errors=True)
        # staging 残骸(解压中途失败留下的)清掉,别占盘
        try:
            _stale_stage = target.parent / f"{target.name}.installing"
            if _stale_stage.exists():
                shutil.rmtree(_stale_stage, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass


def build_install_recipe(
    variant: Variant, *,
    torch_spec: str = ">=2.1,<3.0",
    torchvision_spec: str = "",
    prefer_offline: bool = True,
    force: bool = False,
) -> "InstallRecipe":
    """生成「安装 PyTorch」InstallRecipe —— 调用纯 Python wheel 安装器(**不再用 pip**)。

    背景
    ====
    旧实现拼 ``chayuan-server.exe -m pip install torch ...``,但 pip 不支持被
    PyInstaller freeze(``-m`` 不支持、pip 没打进包、distlib finder 失效……)。
    现改为:recipe 调一条 ``chayuan install-pytorch`` Click 子命令,该子命令内部
    跑 :func:`install_pytorch_wheels` —— 自己下 wheel + ``zipfile`` 解压,绕开 pip。
    Click 子命令在 frozen exe 下工作正常(坑的只是 ``-m``,不是 Click)。

    选择逻辑(由子命令 ``--offline`` 标志承载):
      1. ``prefer_offline=True`` → ``install-pytorch --offline``:优先用
         vendor/torch_wheels 里打包期预下的 torch/tv wheel(无网兜底)。
      2. ``prefer_offline=False`` → ``install-pytorch``:在线从
         ``download.pytorch.org/whl/<variant>`` 现下。
    两种情况纯依赖(filelock/sympy/numpy/...)都从 PyPI 下,版本由
    :data:`_TORCH_DEP_PINS` 固化。

    variant 透传:``--variant`` 标志把 cpu / cuXXX 传给子命令。CPU 版 ~250 MB
    走 ``/whl/cpu``;CUDA 版(cuXXX)2-3 GB 走 ``/whl/cuXXX``,torch wheel 自带
    CUDA runtime。``auto`` 按 detect_status().suggested_variant 解析(有 NVIDIA
    GPU + 驱动够新 → cuXXX,否则 cpu)。
    """
    from chayuan.server.config_panel.install_task_manager import InstallRecipe

    if variant == "auto":
        s = detect_status()
        variant = s.suggested_variant  # type: ignore[assignment]
    if variant not in _PYTORCH_INDEXES:
        raise ValueError(f"unknown variant: {variant!r}; expected one of {tuple(_PYTORCH_INDEXES)}")

    is_cuda = variant != "cpu"
    target_dir = torch_install_target_dir()
    _target_note = f" 装到:{target_dir}。" if target_dir else ""
    _size_note = (
        "CUDA 版 torch wheel 自带 CUDA runtime,体积 2-3 GB(CPU 版 ~250 MB),"
        "下载耗时较长。"
        if is_cuda else "包大小:CPU ~250 MB。"
    )

    # recipe = 一条 shell 命令(install_task_manager 用 subprocess.Popen 跑)。
    # 用 ``sys.executable`` 自调:frozen 下 sys.executable 就是 chayuan-server.exe,
    # ``chayuan-server.exe install-pytorch`` 走 Click 子命令(正常工作);
    # dev / 非 frozen 下 sys.executable 是 python,需补 ``-m chayuan`` 进入 CLI。
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        base_cmd = [sys.executable, "install-pytorch"]
    else:
        base_cmd = [sys.executable, "-m", "chayuan", "install-pytorch"]
    # variant 透传给 CLI 子命令;cpu 也显式带上(子命令默认 cpu,显式更清楚)
    base_cmd = [*base_cmd, "--variant", variant]
    # force 重装:CLI 收到 --force 会在解压前清空 py_packages/,避免新旧版本残留。
    if force:
        base_cmd = [*base_cmd, "--force"]
    _reinstall_tag = " · 重装" if force else ""

    if prefer_offline:
        offline = _find_offline_wheels(variant)
        cmd = [*base_cmd, "--offline"]
        if offline:
            note = (
                f"用内置的 {variant} torch + torchvision wheel 解压安装(无 pip)。"
                f" 路径:{offline[0].parent}。{_target_note}"
                f"依赖(numpy / pillow / transformers 全家桶等)仍从 PyPI 下,"
                f"含图像向量化 CLIP / SigLIP2 所需的 transformers。"
                f"装完需重启 chayuan-server 才能 import。"
            )
        else:
            note = (
                f"未找到 {variant} 内置 wheel,将在线从 download.pytorch.org 下载 + "
                f"解压(无 pip)。{_size_note}{_target_note}装完需重启 chayuan-server。"
            )
        return InstallRecipe(
            label=f"PyTorch {variant} (离线 wheel · 无 pip){_reinstall_tag}",
            cmd=cmd,
            note=note,
            requires="",
        )

    # 在线
    return InstallRecipe(
        label=f"PyTorch {variant} (在线 · 官方源 · 无 pip){_reinstall_tag}",
        cmd=list(base_cmd),
        note=(
            f"从 download.pytorch.org/whl/{variant} 下载 torch + torchvision wheel "
            f"并用 zipfile 解压安装(完全不用 pip)。{_size_note}{_target_note}"
            f"装完需重启 chayuan-server 才能 import 新版本。"
        ),
        requires="",
    )


def start_install(variant: Variant = "auto", *,
                    prefer_offline: bool = True,
                    force: bool = False) -> "InstallTask":
    """启动 PyTorch 安装 task。返 InstallTask(含 task_id,前端轮询查进度)。

    ``prefer_offline=True``(默认):有内置 wheel 用内置,没有走在线。
    ``prefer_offline=False``:强制在线(用户可能要更新的 wheel 版本)。
    ``force=True``(重装):已装也重装,且解压前清空 py_packages/ 去残留 ——
    UI「安装 / 重装」按钮都走 force,保证装出来的 torch/torchvision 配套。
    """
    from chayuan.server.config_panel.install_task_manager import get_install_manager
    recipe = build_install_recipe(variant, prefer_offline=prefer_offline, force=force)
    framework = f"pytorch-{variant if variant != 'auto' else detect_status().suggested_variant}"
    return get_install_manager().start(framework=framework, recipe=recipe)


def auto_install_on_startup() -> dict:
    """首启动自动决定要不要装 PyTorch + 装哪个 variant。

    决策(对齐用户在 install 阶段的"自动判断 GPU"诉求):
      * torch 已经 import 成功 → 啥都不做,return ``{"action": "skipped"}``
      * 检测到 NVIDIA GPU + 驱动 >= 525 → 推荐 cu124(但需要对应 wheel 存在,
        否则降级到 cpu)
      * 没 NVIDIA GPU / 驱动太老 → 装 cpu
      * 离线 wheel 不可用(没 seed 完 / 用户机型缺 variant) → 只 log warn,
        不阻塞 startup;UI 上 PytorchRow 会显示供用户手动操作

    本函数**不**阻塞 startup 链路 — 任何异常吞掉返回 ``{"action":"failed","error":...}``。
    实际安装通过 :func:`start_install` 走 InstallTask 后台 task(非阻塞)。
    """
    try:
        torch_ver, _ = _probe_torch_install()
        import_ok, _ = _probe_import_chain()
        if torch_ver and import_ok:
            logger.info("[torch-auto] torch %s 已就绪,跳过自动安装", torch_ver)
            return {"action": "skipped", "reason": f"torch {torch_ver} already importable"}

        status = detect_status()
        chosen: Variant = status.suggested_variant  # type: ignore[assignment]

        # 检查所选 variant 是否有离线 wheel 可用;没有就降级 cpu(用户当前只打 cpu wheel)
        offline = _find_offline_wheels(chosen)
        if not offline and chosen != "cpu":
            logger.info(
                "[torch-auto] suggested=%s 但 vendor 无对应 wheel,降级到 cpu", chosen,
            )
            chosen = "cpu"
            offline = _find_offline_wheels(chosen)

        if not offline:
            logger.warning(
                "[torch-auto] 没找到任何 offline wheel(variant=%s),不自动装 — "
                "用户需在 UI PytorchRow 手动触发(可能走在线下载)",
                chosen,
            )
            return {
                "action": "deferred",
                "reason": "no offline wheels found for any variant",
                "suggested_variant": chosen,
            }

        # 后台 task 跑 pip install,不阻塞 startup
        task = start_install(chosen, prefer_offline=True)
        logger.info(
            "[torch-auto] 启动 PyTorch %s 自动安装(task=%s,prefer_offline=True)",
            chosen, getattr(task, "task_id", "?"),
        )
        return {
            "action": "installing",
            "variant": chosen,
            "task_id": getattr(task, "task_id", None),
            "offline_wheels_dir": status.offline_wheels_dir,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("[torch-auto] 自动安装失败,降级到手动:%r", e)
        return {"action": "failed", "error": f"{type(e).__name__}: {e}"}
