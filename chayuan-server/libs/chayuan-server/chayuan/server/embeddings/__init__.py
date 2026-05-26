"""单机版本地 embedding 包。

模块
----
- ``onnx_local`` : 用 onnxruntime + HuggingFace tokenizer 加载本地 ONNX 模型,
  CPU 推理,无外部服务依赖。CLAUDE.md §2.4 规划的"开箱即用"方案。
"""
