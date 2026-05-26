"""图像知识源子系统（image source）。

在 KnowledgeSource 家族里新增 ``kind=image``；图像向量化由 ImageEmbedder 统一抽象，
支持多种 world-best 模型（默认 SigLIP 2，fallback CLIP / Chinese-CLIP / JinaCLIP v2）。

对外暴露：
- ``get_embedder(model_name)``：拿到 embedder 单例
- ``list_supported_models()``：模型目录（用于 WebUI 下拉）
- ``ImageConnector``：检索 Connector
- ``build_image_index_for_source(source_id, paths)``：离线索引入口（可走 Arq）
"""

# 图像向量化已下线 —— embedder / connector 硬依赖 torch + transformers,
# 而 PyInstaller bundle 现在不再带 torch 全家桶,这里若执行会 ImportError
# 顶崩 ocr_client / pipeline 等同包子模块的 import(Python 加载子模块时会
# 先跑父包 __init__.py)。OCR(modality_routes 用到的 ocr_client / pipeline)
# 跟 torch 无关,所以这里把 embedder / connector 的导出注销,只保留 docstring
# 即可,子模块仍可被 import 到。
# 要恢复:取消下面两段注释,并把 pyproject 的 torch / transformers / image
# extras 与 spec 的 LITE_BUILD 一并恢复。
# from chayuan.server.image_source.embedder import (  # noqa: F401
#     BaseImageEmbedder,
#     ImageEmbeddingResult,
#     get_embedder,
#     list_supported_models,
# )
# from chayuan.server.image_source.connector import ImageConnector  # noqa: F401
