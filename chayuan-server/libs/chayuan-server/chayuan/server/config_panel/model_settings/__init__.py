"""模型设置页 v2(57 题原 4 tabs;单机版重构后保留 2 tabs)。

历史:旧 ``model_config.render_model_settings_page`` 单页 3196 行,一进就同步
渲染 4 行 + 4 项 prefetch,导致 socket.io 心跳跟不上 → connection lost。

v2 拆成 tab,NiceGUI 原生 lazy(只渲染当前 tab):

- ① 模型厂商配置   (providers_subpage,本地+云厂商分类)
- ② 默认模型 + 对话参数 (defaults_subpage)

(单机版重构移除:① 运行时与服务/③ 模型广场,前者并入 ▸ 服务配置;
后者不再存在,模型只在已配置的厂商详情里展示。)

模块化:subpage 完全独立,共享 ``state_cache`` 做 states / healths /
capability defaults 的 memoize;切 tab 不重复 IO。
"""
from chayuan.server.config_panel.model_settings.page import (
    render_model_settings_v2,
)

__all__ = ["render_model_settings_v2"]
