"""第三方库兼容性补丁包。

被 ``chayuan.server.utils`` 在 import 阶段触发,所有补丁在 sidecar 启动一次性应用。
"""
from . import langchain_openai_patches as _langchain_openai_patches  # noqa: F401
