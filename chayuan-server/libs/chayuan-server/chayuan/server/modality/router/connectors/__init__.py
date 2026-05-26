"""Connector 实现集合。

PR-1 只放 base.py(ABC + registry);PR-2 起按 capability 子目录补充:
  ``image_gen/dashscope.py`` / ``image_gen/openai.py`` / ``image_gen/volcengine.py``
  ``tts/dashscope.py`` / ``tts/local.py``
  ``asr/dashscope.py`` / ``asr/local.py``
  ``video_gen/dashscope.py`` / ``video_gen/runway.py``
  ``image_edit/dashscope.py``

Connector 通过 ``@register(cap, platform_type)`` 装饰器自登记,import 时落入
``_CONNECTORS`` 字典;router 在 dispatch 时按 ``(cap, platform_type)`` 查表。

新增厂商只需新建文件 + import 即可,无需改 router。
"""

from chayuan.server.modality.router.connectors.base import (
    Connector,
    register,
    get_connector,
    pick_connector,
    list_registered,
)

# 导入子包触发 @register 落表 — 新增 capability 子目录后这里加一行即可
from chayuan.server.modality.router.connectors import image_gen  # noqa: F401
from chayuan.server.modality.router.connectors import image_edit  # noqa: F401
from chayuan.server.modality.router.connectors import video_gen  # noqa: F401
from chayuan.server.modality.router.connectors import tts  # noqa: F401
from chayuan.server.modality.router.connectors import asr  # noqa: F401

__all__ = [
    "Connector",
    "register",
    "get_connector",
    "pick_connector",
    "list_registered",
]
