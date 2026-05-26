"""macOS 菜单栏托盘（基于 ``rumps``）。

运行流程
--------
1. ``main`` 线程跑 ``rumps.App().run()``（即 NSApplication mainloop）；
2. 启动时在后台线程调用 ``Backend.start()`` 起 ``chayuan start -a``——
   子进程自带启动速度（30 秒级），我们不阻塞菜单栏渲染；
3. 菜单项点击全部走 ``chayuan.tray.common`` 的纯函数，方便脱离 rumps 单测；
4. 退出：``rumps.quit_application()`` → ``on_quit`` → ``Backend.stop()``。

为什么用 rumps 而不是 pystray
----------------------------
* rumps 原生基于 PyObjC NSStatusItem，能正确处理模板图（自动适配深/浅色菜单栏）；
* pystray 在 macOS 上是模拟实现，图标在 Sonoma 之后经常糊 / 偏位；
* rumps 对菜单项动态文本、tooltip 支持更好。
"""

from __future__ import annotations

import atexit
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from chayuan.tray import common
from chayuan.tray.supervisor import Backend


def _install_cleanup_handlers(backend: Backend) -> None:
    """保证即使托盘被 SIGTERM / 系统注销强制结束，也能级联关闭后端。

    - ``atexit``：覆盖正常退出路径（rumps.quit_application 走这里）；
    - ``SIGTERM`` / ``SIGHUP`` / ``SIGINT``：覆盖用户 kill、系统重启、Ctrl+C；
      handler 里只做 ``backend.stop()`` + ``os._exit``——不走 rumps 的 NSApp
      退出流程（此时事件循环本来就要结束了，越简单越不易死锁）。
    """
    def _cleanup() -> None:
        try:
            backend.stop()
        except Exception:
            pass

    atexit.register(_cleanup)

    def _sig_handler(signum, frame):
        _cleanup()
        # 不抛 KeyboardInterrupt、不走 rumps；直接退出，避免在信号上下文里再
        # 触碰 AppKit 导致未定义行为。
        os._exit(0)

    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        try:
            signal.signal(sig, _sig_handler)
        except (OSError, ValueError):
            # 某些受限环境（macOS 沙箱 / 非主线程）会抛；忽略即可。
            pass


def _icon_path() -> Optional[str]:
    """返回菜单栏图标路径。

    menu bar 图标的规格：~22pt（Retina 下 44×44 像素），带 alpha。大图
    （比如 1024×1024 的 logo.png）直接塞给 NSStatusItem 不会被自动缩放，
    导致显示空白或只露一小块像素——这是 M3 初版图标不出现的根因。

    查找顺序（第一个命中即用）：

    1. ``chayuan/img/tray_icon.png``（提交到仓库的小图，44×44）
    2. ``chayuan/img/logo.png``（回退；仅在未生成小图时才会让菜单栏发空）

    缺失时 rumps 会退回文字标题 "Chayuan"。
    """
    try:
        import chayuan

        pkg_dir = Path(chayuan.__file__).resolve().parent
        # tray_icon.png 优先于 logo.png：前者是专为 NSStatusItem 生成的小图
        for name in ("tray_icon.png", "logo.png", "chatchat_icon_blue_square_v2.png"):
            icon = pkg_dir / "img" / name
            if icon.is_file():
                return str(icon)
    except Exception:
        pass
    return None


def run() -> None:
    try:
        import rumps
    except ImportError:
        sys.stderr.write(
            "[tray] 未安装 rumps；请执行 `pip install 'rumps>=0.4.0,<0.5'` 后重试。\n"
        )
        raise

    common.ensure_bootstrapped()

    backend = Backend(log_path=common.tray_log_path())
    _install_cleanup_handlers(backend)

    class ChayuanApp(rumps.App):
        def __init__(self) -> None:
            super().__init__(
                name="察元",
                title="",
                icon=_icon_path(),
                template=False,
                quit_button=None,
            )
            self.menu = [
                rumps.MenuItem("打开配置面板", callback=self._open_panel),
                rumps.MenuItem("打开 API 文档", callback=self._open_api_docs),
                None,
                rumps.MenuItem("打开数据目录", callback=self._open_data_dir),
                rumps.MenuItem("查看日志", callback=self._open_logs),
                None,
                rumps.MenuItem("重启服务", callback=self._restart),
                rumps.MenuItem("退出", callback=self._quit),
            ]

            threading.Thread(
                target=self._start_backend_async, name="chayuan-tray-start",
                daemon=True,
            ).start()

        # ------------------------------------------------------------ backend

        def _start_backend_async(self) -> None:
            try:
                backend.start()
            except Exception as e:  # noqa: BLE001
                rumps.notification(
                    title="察元 启动失败",
                    subtitle="",
                    message=str(e),
                )

        # ------------------------------------------------------------ menu cb

        def _open_panel(self, _) -> None:
            self._open_and_hint(common.config_panel_url(), "配置面板")

        def _open_api_docs(self, _) -> None:
            self._open_and_hint(common.api_docs_url(), "API 文档")

        def _open_data_dir(self, _) -> None:
            common.open_path_in_file_manager(common.chayuan_root())

        def _open_logs(self, _) -> None:
            common.open_path_in_file_manager(common.logs_dir())

        def _restart(self, _) -> None:
            rumps.notification(
                title="察元",
                subtitle="",
                message="正在重启后台服务...",
            )
            threading.Thread(target=backend.restart, name="chayuan-tray-restart",
                             daemon=True).start()

        def _quit(self, _) -> None:
            try:
                backend.stop()
            finally:
                rumps.quit_application()

        # ---------------------------------------------------------- helpers

        def _open_and_hint(self, url: str, name: str) -> None:
            if not common.ready_for_open(url):
                rumps.notification(
                    title="察元 还在启动",
                    subtitle=name,
                    message=f"服务尚未就绪，稍后再试或查看日志：{common.tray_log_path()}",
                )
                return
            common.open_url(url)

    app = ChayuanApp()
    try:
        app.run()
    finally:
        backend.stop()
