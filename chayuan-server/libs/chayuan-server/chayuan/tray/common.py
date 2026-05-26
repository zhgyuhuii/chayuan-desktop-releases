"""跨平台托盘实现共用的辅助逻辑（菜单点击动作、URL、路径、首次初始化）。

菜单项的实际渲染在 ``app_mac.py`` / ``app_generic.py`` 里，本模块只提供
「点击后应该发生什么」的纯函数，便于单测。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Tuple


# ---------------------------------------------------------------------------
# URL / 路径
# ---------------------------------------------------------------------------


def _safe_load_settings() -> Tuple[str, int, str, int]:
    """读取 API / 配置面板的 host+port。失败则回退默认值。

    托盘进程独立于业务进程，Settings 的导入开销可接受（一次性）。
    """
    try:
        from chayuan.settings import Settings

        bs = Settings.basic_settings
        api_host = bs.API_SERVER.get("host", "127.0.0.1")
        api_port = int(bs.API_SERVER.get("port", 62581))
        cfg_host = bs.CONFIG_SERVER.get("host", "127.0.0.1")
        cfg_port = int(bs.CONFIG_SERVER.get("port", 8502))
        # 127.0.0.1 优于 0.0.0.0 / 空串用于浏览器访问
        api_host = _browser_host(api_host)
        cfg_host = _browser_host(cfg_host)
        return api_host, api_port, cfg_host, cfg_port
    except Exception:
        return "127.0.0.1", 62581, "127.0.0.1", 8502


def _browser_host(host: str) -> str:
    """0.0.0.0 / 空串 / :: 在浏览器里都打不开，统一改成 127.0.0.1。"""
    h = (host or "").strip()
    if not h or h in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return h


def api_docs_url() -> str:
    h, p, _, _ = _safe_load_settings()
    return f"http://{h}:{p}/docs"


def config_panel_url() -> str:
    """返回配置面板登录 URL。``PANEL_LOGIN_PATH`` 由 ``basic_settings.yaml``
    随机生成，这里拼出完整地址。"""
    _, _, h, p = _safe_load_settings()
    try:
        from chayuan.settings import Settings

        login_path = (Settings.basic_settings.PANEL_LOGIN_PATH or "").strip().strip("/")
    except Exception:
        login_path = ""
    if login_path:
        return f"http://{h}:{p}/{login_path}"
    return f"http://{h}:{p}"


def chayuan_root() -> Path:
    """返回当前 CHAYUAN_ROOT 路径。"""
    try:
        from chayuan.settings import CHAYUAN_ROOT

        return Path(CHAYUAN_ROOT)
    except Exception:
        from chayuan.paths import resolve_chayuan_root

        return resolve_chayuan_root().path


def logs_dir() -> Path:
    return chayuan_root() / "data" / "logs"


def tray_log_path() -> Path:
    return logs_dir() / "tray.log"


# ---------------------------------------------------------------------------
# 首次运行初始化：确保 basic_settings.yaml 存在；否则跑一遍 `chayuan init -q`。
# ---------------------------------------------------------------------------


def _needs_bootstrap(root: Path) -> bool:
    return not (root / "basic_settings.yaml").is_file()


def ensure_bootstrapped() -> None:
    """托盘第一次启动时调用。若数据目录未初始化就静默跑 `init -q --profile local`。

    用子进程而不是直接 call 函数——``chayuan.init`` 命令里很多地方会 import
    Settings，而我们可能已经在主进程里 import 过（CHAYUAN_ROOT 缓存）。分子进程
    最干净，避免状态交叉。
    """
    root = chayuan_root()
    if not _needs_bootstrap(root):
        return

    root.mkdir(parents=True, exist_ok=True)
    log_path = tray_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    env = os.environ.copy()
    env["CHAYUAN_ROOT"] = str(root)

    # Edition 决定 init profile：
    #   - personal （默认）     → local   （sqlite + faiss，无鉴权）
    #   - enterprise / prod    → prod    （postgres + milvus + redis + 鉴权）
    # launcher.sh 已经把 CHAYUAN_EDITION 和/或 CHAYUAN_INIT_PROFILE 导出
    # 到了 env；这里只做最后一次校验 + 默认值兜底。
    edition = (os.environ.get("CHAYUAN_EDITION") or "").lower()
    explicit_profile = (os.environ.get("CHAYUAN_INIT_PROFILE") or "").lower()

    if explicit_profile in ("local", "prod", "enterprise"):
        profile = explicit_profile
    elif edition == "enterprise":
        profile = "enterprise"
    else:
        profile = "local"

    env["CHAYUAN_INIT_PROFILE"] = profile

    cmd = [
        sys.executable,
        "-m",
        "chayuan.cli",
        "init",
        "-q",
        "--profile",
        profile,
    ]

    try:
        with open(log_path, "ab", buffering=0) as fh:
            subprocess.run(
                cmd, env=env, stdout=fh, stderr=subprocess.STDOUT, check=False,
                timeout=180,
            )
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[tray] 首次初始化失败（稍后可手动执行 chayuan init）：{e!r}\n")


# ---------------------------------------------------------------------------
# 动作：菜单点击后要做的事
# ---------------------------------------------------------------------------


def open_url(url: str) -> None:
    webbrowser.open(url)


def open_path_in_file_manager(p: Path) -> None:
    """macOS: open, Windows: explorer, Linux: xdg-open。失败仅写 stderr。"""
    p = Path(p)
    if not p.exists():
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(p)], check=False)
        elif sys.platform.startswith("win"):
            os.startfile(str(p))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(p)], check=False)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[tray] 打开路径失败 {p}：{e!r}\n")


def wait_port_ready(host: str, port: int, timeout: float = 0.8) -> bool:
    """TCP 探活。失败不阻塞（超时 < 1s）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ready_for_open(url: str) -> bool:
    """解析 URL 的 host:port 快速探活，告诉托盘该不该提示用户再等等。"""
    try:
        from urllib.parse import urlparse

        u = urlparse(url)
        host = u.hostname or "127.0.0.1"
        port = u.port or (443 if u.scheme == "https" else 80)
        return wait_port_ready(host, port)
    except Exception:
        return True
