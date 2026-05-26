"""图像生成 Connector 集合。

模块自登记:import 子模块即触发 @register 装饰器写入注册表。
新增厂商 → 加文件 → 在本 __init__ import 即可,router 自动看到。
"""

from chayuan.server.modality.router.connectors.image_gen import dashscope  # noqa: F401
from chayuan.server.modality.router.connectors.image_gen import openai_compat  # noqa: F401

__all__: list[str] = []
