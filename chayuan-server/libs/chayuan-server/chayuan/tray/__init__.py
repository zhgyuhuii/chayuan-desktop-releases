"""察元 AI 助手托盘（system-tray / menu-bar）壳程序。

本包负责把原本的命令行 `chayuan start -a`（API + WebUI + 配置面板）包进一个
常驻托盘壳里，供桌面安装包双击启动使用：

- macOS：使用 ``rumps``（NSStatusItem）在菜单栏显示图标，支持点击弹出菜单；
- Windows / Linux：使用 ``pystray``（Shell_NotifyIcon / AppIndicator）；
- 菜单项：打开对话 / 打开配置面板 / 打开 API 文档 / 打开数据目录 /
  查看日志 / 重启服务 / 退出。

所有子进程的生命周期由 ``chayuan.tray.supervisor.Backend`` 管理：Backend
起 ``chayuan start -a`` 为独立 session，退出时向进程组发送 SIGTERM→SIGKILL，
与现有 ``startup.py._kill_previous_instance`` 的约定一致。
"""

__all__ = ["main"]


def main() -> None:
    """托盘入口，按 OS 分派到对应实现。

    设计成薄分发器以便：

    * CI 打包时只打一个 OS 对应的实现（例如 macOS 包不需要 pystray）；
    * 单元测试时可直接 monkeypatch ``platform.system`` 走任一路径。
    """
    import platform

    system = platform.system()
    if system == "Darwin":
        from chayuan.tray.app_mac import run as _run
    else:
        from chayuan.tray.app_generic import run as _run
    _run()
