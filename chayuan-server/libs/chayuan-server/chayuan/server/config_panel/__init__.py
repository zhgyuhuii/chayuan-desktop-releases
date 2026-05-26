"""察元AI助手配置面板（NiceGUI）。

入口：`run_config_panel(started_event)`，由 `chayuan.startup` 以子进程启动。
"""

from chayuan.server.config_panel.app import run_config_panel

__all__ = ["run_config_panel"]
