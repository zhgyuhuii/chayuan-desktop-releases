"""Windows / Linux 托盘（基于 ``pystray``）。

MVP 阶段仅提供骨架：在 macOS 之外的平台打包脚本暂未实现，这里保持可 import、
可运行，便于后续 M5 里直接复用菜单定义。实际功能与 ``app_mac`` 等价。
"""

from __future__ import annotations

import atexit
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Optional

from chayuan.tray import common
from chayuan.tray.supervisor import Backend


def _install_cleanup_handlers(backend: Backend) -> None:
    """外部信号 / 正常退出时都级联关闭后端。与 ``app_mac`` 保持同构。"""
    def _cleanup() -> None:
        try:
            backend.stop()
        except Exception:
            pass

    atexit.register(_cleanup)

    def _sig_handler(signum, frame):
        _cleanup()
        os._exit(0)

    for sig_name in ("SIGTERM", "SIGHUP", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _sig_handler)
        except (OSError, ValueError):
            pass


def _icon_image():
    """读 PNG 成 PIL.Image 返给 pystray。图标缺失时返回 None 由 pystray 兜底。"""
    try:
        import chayuan
        from PIL import Image

        pkg_dir = Path(chayuan.__file__).resolve().parent
        # pystray 也偏好小图：Win 系统托盘 16×16/32×32，Linux AppIndicator 22×22
        for name in ("tray_icon.png", "logo.png", "chatchat_icon_blue_square_v2.png"):
            icon = pkg_dir / "img" / name
            if icon.is_file():
                return Image.open(icon)
    except Exception:
        pass
    return None


def run() -> None:
    try:
        import pystray
    except ImportError:
        sys.stderr.write(
            "[tray] 未安装 pystray；请 `pip install 'pystray>=0.19'` + 对应平台依赖。\n"
        )
        raise

    common.ensure_bootstrapped()

    backend = Backend(log_path=common.tray_log_path())
    _install_cleanup_handlers(backend)

    icon: Optional[pystray.Icon] = None

    def _open(url_fn, name: str):
        def _(_icon=None, _item=None):
            url = url_fn()
            if not common.ready_for_open(url):
                _notify(f"{name} 尚未就绪，稍后再试。")
                return
            common.open_url(url)

        return _

    def _notify(msg: str) -> None:
        try:
            if icon is not None:
                icon.notify(msg, "察元")
        except Exception:
            pass

    def _restart(_icon=None, _item=None):
        _notify("正在重启后台服务...")
        threading.Thread(target=backend.restart, daemon=True).start()

    def _quit(_icon=None, _item=None):
        try:
            backend.stop()
        finally:
            if icon is not None:
                icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("打开配置面板", _open(common.config_panel_url, "配置面板")),
        pystray.MenuItem("打开 API 文档", _open(common.api_docs_url, "API 文档")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "打开数据目录",
            lambda _i, _m: common.open_path_in_file_manager(common.chayuan_root()),
        ),
        pystray.MenuItem(
            "查看日志",
            lambda _i, _m: common.open_path_in_file_manager(common.logs_dir()),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("重启服务", _restart),
        pystray.MenuItem("退出", _quit),
    )

    # 第一个参数是 pystray 内部 ID(必须 ASCII 安全 — Linux 在 D-Bus 注册要)
    # 第二个 hover tooltip 是用户能看到的;改成中文。
    icon = pystray.Icon("chayuan", _icon_image(), "察元", menu)

    threading.Thread(target=backend.start, daemon=True).start()

    try:
        icon.run()
    finally:
        backend.stop()
