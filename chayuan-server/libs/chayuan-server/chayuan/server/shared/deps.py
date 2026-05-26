"""按需自动安装运行时可选依赖（redis / arq 等）。

背景
----
项目中一部分依赖（如 ``redis``、``arq``）在 P0 路径上用不到，但是一旦用户打开
「Redis 连接配置」卡片、开启限流 / 语义缓存 / 异步入库队列等功能就会立刻需要。
之前我们的处理只是抛 "No module named 'redis'"，用户需要自己去 pip install，
体验非常糟糕——尤其在内网 + Nexus 环境里，用户不熟悉镜像源地址时更容易卡住。

本模块提供两套对外 API：

1. :func:`ensure_pkg` ——按 import name 检查并按需 ``pip install``。幂等、轻量，
   适合在需要该包的入口（如 ``redis_config.render_redis_card``）调用。
2. :func:`ensure_runtime_deps` ——一次性把一组"几乎所有部署都会用到"的可选依赖
   装齐，建议在 ``chayuan init`` / 首次 ``chayuan start`` 时调用，让用户第一次
   打开面板就是"齐备"状态。

设计要点
--------
- 只做安装，不做卸载 / 升级；已装过的包完全不动。
- 默认使用 pyproject 里配的 ``https://pypi.tuna.tsinghua.edu.cn/simple`` 镜像，
  内网 Nexus 可通过 ``CHAYUAN_PIP_INDEX_URL`` 环境变量覆盖。
- 安装失败不抛异常，只返回 False + 写一行 warning —— 调用方继续走降级路径。
- 进程内缓存结果，避免每次 render 都去 fork pip。

这是参考 ``nltk.download`` / ``streamlit`` 的 runtime dependency 处理方式，用
以补齐"一键起服务"体验。
"""
from __future__ import annotations

import importlib
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("chayuan.shared.deps")

# 镜像源：优先环境变量 -> pyproject 默认 -> pip 自身默认
_DEFAULT_INDEX_URL = (
    os.environ.get("CHAYUAN_PIP_INDEX_URL")
    or os.environ.get("PIP_INDEX_URL")
    or "https://pypi.tuna.tsinghua.edu.cn/simple"
)

# 网络探测 / pip 超时：默认足够短，**断网时 ensure_pkg 应在秒级内返回**，
# 绝不阻塞服务启动。可用环境变量覆盖。
_NET_PROBE_TIMEOUT = float(os.environ.get("CHAYUAN_NET_PROBE_TIMEOUT") or 2.0)
_PIP_NETWORK_TIMEOUT = int(float(os.environ.get("CHAYUAN_PIP_TIMEOUT") or 10))
_PIP_TOTAL_TIMEOUT = int(float(os.environ.get("CHAYUAN_PIP_TOTAL_TIMEOUT") or 90))

# 网络探测结果的进程内缓存：同一镜像在短时间内反复探，会把启动拖慢。
# 断网时多次调用就只打一次 warning。
_NET_CACHE_TTL = 30.0  # seconds
_NET_CACHE: Dict[str, Tuple[float, bool]] = {}

# (import_name, pip_requirement) —— 服务端常见的可选运行时依赖。
# 这些包要么体积小（<5MB）、要么用户一旦启用对应功能就一定需要，
# 不做按需 lazy import 会让用户在"验证连接"/"保存"时才踩坑。
DEFAULT_OPTIONAL_DEPS: List[Tuple[str, str]] = [
    ("redis", "redis>=5.0,<6.0"),
    ("arq", "arq>=0.25,<0.27"),
]

_ENSURE_LOCK = threading.Lock()
_ENSURED: Dict[str, bool] = {}


def _module_available(import_name: str) -> bool:
    try:
        importlib.import_module(import_name)
        return True
    except Exception:  # noqa: BLE001
        return False


def _index_reachable(index_url: str, *, timeout: float = _NET_PROBE_TIMEOUT) -> bool:
    """用 TCP connect 探测镜像源是否可达；带 30s 结果缓存。

    断网 / DNS 不可达 / 镜像宕机时，返回 False，不会让 pip 真正去跑——
    防止 pip 自己的 retry/backoff 把服务启动卡几十秒。
    """
    if not index_url:
        return True  # 让 pip 自己判定

    now = time.time()
    cached = _NET_CACHE.get(index_url)
    if cached and now - cached[0] < _NET_CACHE_TTL:
        return cached[1]

    try:
        parsed = urlparse(index_url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except Exception:  # noqa: BLE001
        _NET_CACHE[index_url] = (now, False)
        return False

    if not host:
        _NET_CACHE[index_url] = (now, False)
        return False

    ok = False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ok = True
    except (OSError, socket.timeout):
        ok = False

    _NET_CACHE[index_url] = (now, ok)
    return ok


def _run_pip_install(requirement: str, *, index_url: Optional[str], quiet: bool) -> bool:
    """阻塞式调用 ``python -m pip install <requirement>``。

    - 先做一次秒级 TCP 探活：镜像不可达直接返回 False，**永不卡住**。
    - pip 本身带 ``--timeout``（单请求）和 subprocess 总超时兜底。
    """
    target_index = index_url or _DEFAULT_INDEX_URL
    if target_index and not _index_reachable(target_index):
        logger.warning(
            "chayuan.deps: 无法连到镜像 %s（断网或 DNS 不可达），跳过安装 %s",
            target_index,
            requirement,
        )
        return False

    cmd: List[str] = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--timeout",
        str(_PIP_NETWORK_TIMEOUT),
        "--retries",
        "1",
    ]
    if index_url:
        cmd += ["--index-url", index_url]
    if quiet:
        cmd.append("-q")
    cmd.append(requirement)

    logger.info("chayuan.deps: pip install %s (index=%s)", requirement, index_url or "<default>")
    try:
        # Windows 上 ``text=True`` 默认走 ``locale.getpreferredencoding()``,中文版
        # 系统是 GBK;pip 输出 UTF-8 字节会让 subprocess._readerthread 在 .decode()
        # 时抛 UnicodeDecodeError 把整条 reader 线程崩掉,日志只剩乱码尾巴。
        # 强制 utf-8 + errors=replace 兜底,日志最差也是问号占位,不丢线程。
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PIP_TOTAL_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning(
            "chayuan.deps: pip install %s 失败（超时 / 进程错误）：%s",
            requirement,
            e,
        )
        return False

    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-10:]
        logger.warning(
            "chayuan.deps: pip install %s 失败 rc=%d，末尾输出：\n%s",
            requirement,
            result.returncode,
            "\n".join(tail),
        )
        return False

    logger.info("chayuan.deps: %s 安装成功", requirement)
    return True


def ensure_pkg(
    import_name: str,
    pip_requirement: Optional[str] = None,
    *,
    index_url: Optional[str] = None,
    quiet: bool = True,
    auto_install: Optional[bool] = None,
) -> bool:
    """确保 ``import_name`` 可 import，可选自动 pip 安装。

    Parameters
    ----------
    import_name:
        Python 导入名，如 ``"redis"``、``"arq"``。
    pip_requirement:
        传给 pip 的完整 requirement 串；默认等于 ``import_name``（无版本约束）。
    index_url:
        pip 镜像源；默认走 :data:`_DEFAULT_INDEX_URL`。
    quiet:
        pip 是否静默（-q）。
    auto_install:
        - ``True``：缺包必装；
        - ``False``：只检查不装；
        - ``None`` (默认)：走 ``CHAYUAN_AUTO_INSTALL_DEPS`` + ``CHAYUAN_OFFLINE``
          开关，默认"联网时装、断网时跳过"。

    Returns
    -------
    bool
        ``True`` = 现在可以 ``import import_name``。

    Notes
    -----
    **断网安全**：这个函数保证不会阻塞调用方——
    - 先 TCP 探活镜像源（秒级超时）；
    - 探不通就直接记 False 返回，不 fork pip；
    - 即便 pip 起来了，也给了 ``--timeout`` 和 subprocess 总超时兜底。
    这样即使完全断网，整条启动链路最多多花 2-3 秒，不会卡服务。
    """
    # 进程内缓存：同一 import_name 只做一次真正探测 / 安装
    cached = _ENSURED.get(import_name)
    if cached is True:
        return True

    with _ENSURE_LOCK:
        cached = _ENSURED.get(import_name)
        if cached is True:
            return True

        if _module_available(import_name):
            _ENSURED[import_name] = True
            return True

        offline_env = (os.environ.get("CHAYUAN_OFFLINE") or "").strip().lower()
        is_offline = offline_env in {"1", "true", "yes", "on"}

        if auto_install is None:
            env_val = (os.environ.get("CHAYUAN_AUTO_INSTALL_DEPS") or "").strip().lower()
            auto_install = env_val not in {"0", "false", "no", "off"}

        if is_offline:
            logger.warning(
                "chayuan.deps: 缺少依赖 %s，但 CHAYUAN_OFFLINE=true，跳过自动安装（走降级路径）",
                import_name,
            )
            _ENSURED[import_name] = False
            return False

        if not auto_install:
            logger.warning(
                "chayuan.deps: 缺少依赖 %s，且 CHAYUAN_AUTO_INSTALL_DEPS=false，跳过自动安装",
                import_name,
            )
            _ENSURED[import_name] = False
            return False

        req = pip_requirement or import_name
        ok = _run_pip_install(req, index_url=index_url or _DEFAULT_INDEX_URL, quiet=quiet)
        if not ok:
            _ENSURED[import_name] = False
            return False

        # 清掉可能的 import 缓存，让下一次 importlib 拿到刚装好的版本
        importlib.invalidate_caches()
        ok_after = _module_available(import_name)
        _ENSURED[import_name] = ok_after
        if not ok_after:
            logger.warning(
                "chayuan.deps: pip install 返回成功但仍无法 import %s", import_name
            )
        return ok_after


def ensure_runtime_deps(
    deps: Optional[Iterable[Tuple[str, str]]] = None,
    *,
    quiet: bool = True,
) -> Dict[str, bool]:
    """批量补齐 :data:`DEFAULT_OPTIONAL_DEPS` 里还没装上的运行时包。

    用于 ``chayuan init`` / 初次启动时的"一键补齐"。所有包失败都不抛异常，
    只返回每个包的安装状态；调用方可按需打印。
    """
    targets = list(deps) if deps is not None else list(DEFAULT_OPTIONAL_DEPS)
    status: Dict[str, bool] = {}
    for import_name, req in targets:
        status[import_name] = ensure_pkg(import_name, req, quiet=quiet)
    return status


__all__ = [
    "DEFAULT_OPTIONAL_DEPS",
    "ensure_pkg",
    "ensure_runtime_deps",
]
