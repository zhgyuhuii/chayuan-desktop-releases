"""用户手册资源 + 部署 + 渲染。

资源
----

Markdown 源文件位于 :mod:`chayuan.server.manuals.resources`(包内静态文件):

* ``user_manual.md`` — 察元 AI 助手 用户使用手册(全功能覆盖)

首次启动
--------

:func:`deploy_user_manuals` 会幂等地把包内 .md 复制到
``<CHAYUAN_ROOT>/manuals/`` 并尝试生成 ``.docx``(``python-docx`` 可用时,
该依赖已在 pyproject 锁定)。

下载入口
--------

admin 路由 ``GET /admin/manuals/list`` + ``GET /admin/manuals/{name}``
给前端"打开用户手册"按钮用,具体见 :mod:`chayuan.server.api_server.admin_routes`。
"""
from chayuan.server.manuals.deploy import (
    MANUAL_FILES,
    MANUALS_DIRNAME,
    deploy_user_manuals,
    get_manual_path,
    list_deployed_manuals,
)

__all__ = [
    "MANUALS_DIRNAME",
    "MANUAL_FILES",
    "deploy_user_manuals",
    "get_manual_path",
    "list_deployed_manuals",
]
