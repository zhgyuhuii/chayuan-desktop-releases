"""data_mount —— 训练数据挂载的源适配 + 物化 + 注册中心。

本包是历史 ``annotation/datasets/mount`` 的"通用化升级":
* 之前 mount 的"源"硬编码成 annotation samples;
* 现在以 :class:`DataSourceAdapter` 协议为底,12 种源(KB / 文件 / Web / SQL /
  Mongo / S3 / Notion / Confluence / GitHub / 知识源 / 标注 / 历史对话)
  按统一接口接入。

核心抽象
========

* :class:`DataSourceAdapter` —— 所有源都实现这个协议,4 个方法:
  ``probe`` / ``sample`` / ``load`` / ``spec_form``
* :func:`get_registry` —— 全局注册中心;前端 ``GET /data-mounts/sources``
  通过它列出可用源
* :class:`MountMaterializer` —— 把 adapter 输出的 Documents 按 ``mount_mode``
  转成 5 类 artifact:corpus_pending / runtime_context / fewshot / safety /
  preference
* :func:`analyze_schema` —— 抽样统计字段分布,给前端"自动分析"按钮用

公共 API
========

::

    from chayuan.server.data_mount import (
        get_registry,                  # 注册中心
        DataSourceAdapter,             # 协议基类
        ProbeResult, SampleResult,     # 数据类
        analyze_schema,                # 字段分析
        materialize_mount,             # 物化入口 (publish 时调)
    )
"""
from chayuan.server.data_mount.base import (
    DataSourceAdapter,
    DocumentRecord,
    FieldSchema,
    ProbeResult,
    SampleResult,
    SourceSpec,
)
from chayuan.server.data_mount.registry import (
    SourceRegistry,
    get_registry,
    register_default_sources,
)
from chayuan.server.data_mount.schema_analyzer import analyze_schema
from chayuan.server.data_mount.materializer import (
    MountMaterializer,
    materialize_mount,
)

# 模块加载即注册默认源
register_default_sources()

__all__ = [
    "DataSourceAdapter",
    "DocumentRecord",
    "FieldSchema",
    "MountMaterializer",
    "ProbeResult",
    "SampleResult",
    "SourceRegistry",
    "SourceSpec",
    "analyze_schema",
    "get_registry",
    "materialize_mount",
    "register_default_sources",
]
