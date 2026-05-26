"""DataSourceAdapter 协议 + 数据类型。

为什么定 4 个方法?
==================

* ``probe(spec)``     —— 给定连接配置,做一次"能不能连上 + 大约多少条"的探活。
                          UI 的「探活」按钮调它,拿绿/黄/红状态。
* ``sample(spec, n)`` —— 拉前 N 条作为 UI 预览;**不入库**;同时给 schema
                          分析器吃。
* ``load(spec)``      —— 物化时调,流式返回所有(或上限内)记录。
                          异步生成器,适合大数据集 chunked。
* ``spec_form()``     —— 返回 UI 的字段表单 schema(JSON Schema 子集);
                          前端 ``MountWizard`` 第 2 步用它动态渲染表单。

为什么不直接把 langchain ``BaseLoader`` 抽出来?
================================================

* 我们要的不只是 "load Documents":需要 probe / sample / spec form,这是
  langchain BaseLoader 没有的。
* 不同源 langchain loader 的 ``__init__`` 签名差异巨大;让它在我们的注册
  系统里同框,需要包一层。

所有适配器**都允许**在内部使用 langchain loader,只要把 Documents 转成
:class:`DocumentRecord` 就行。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, Protocol


# ---------------------------------------------------------------------------
# 数据类型
# ---------------------------------------------------------------------------

ProbeStatus = Literal["ok", "warning", "error"]


@dataclass
class FieldSchema:
    """一个字段的统计画像。"""

    name: str
    type: str  # "string" / "int" / "float" / "bool" / "list" / "dict" / "null"
    sample_values: List[Any] = field(default_factory=list)
    fill_rate: float = 1.0  # 抽样里非空比例
    unique_count: int = 0
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "sample_values": list(self.sample_values),
            "fill_rate": round(float(self.fill_rate), 3),
            "unique_count": int(self.unique_count),
            "notes": self.notes,
        }


@dataclass
class DocumentRecord:
    """统一的"一条记录"语义 —— 供 materializer 消费。

    * ``text``      用于 corpus / context / fewshot 的"内容"主体
    * ``metadata``  citations / tags / 任意结构化字段
    * ``id``        若源能给稳定 ID 就给(用于去重 + corpus_pending 的 doc id)
    """

    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "text": self.text, "metadata": self.metadata}


@dataclass
class ProbeResult:
    status: ProbeStatus
    message: str = ""
    counted: Optional[int] = None  # 估算总条数,None 表示未知
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "counted": self.counted,
            "extra": dict(self.extra),
        }


@dataclass
class SampleResult:
    items: List[DocumentRecord] = field(default_factory=list)
    total_estimate: Optional[int] = None
    fields: List[FieldSchema] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "items": [it.to_dict() for it in self.items],
            "total_estimate": self.total_estimate,
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclass
class SourceSpec:
    """一个挂载源的"完整配置"——存进 ``data_mount.source_filter.spec``。

    ``source_type`` 决定走哪个 adapter;``options`` 是 adapter 特定的字段。
    """

    source_type: str
    options: Dict[str, Any] = field(default_factory=dict)
    # 共享字段(所有 adapter 都识别),便于 UI 在外层渲染
    max_items: int = 1000

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_type": self.source_type,
            "options": dict(self.options),
            "max_items": int(self.max_items),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SourceSpec":
        return cls(
            source_type=str(d.get("source_type") or ""),
            options=dict(d.get("options") or {}),
            max_items=int(d.get("max_items") or 1000),
        )


# ---------------------------------------------------------------------------
# Adapter Protocol
# ---------------------------------------------------------------------------

class DataSourceAdapter(Protocol):
    """所有源适配器都遵守这套协议。

    实现指南:
    * **不要**在 ``__init__`` 接连接;连接信息走 ``spec.options``。
      这让同一个 adapter 实例能服务多个不同 mount。
    * ``probe`` / ``sample`` 必须 *fail-soft*:连接失败返 ProbeResult(status="error")
      而不是抛异常,让 UI 直接渲染错误而不是 500。
    * ``load`` 才是"会抛"的真物化路径:抛了由 materializer 捕获并把 mount
      标 status="error"。
    """

    type_id: str
    """注册到 SourceRegistry 的稳定 ID,如 "kb"、"file"、"web"。"""

    label: str
    """UI 卡片上显示的名称。"""

    description: str
    """一句话能力描述;UI 卡片副标题。"""

    icon: str
    """Lucide 图标名;UI 卡片用。"""

    capabilities: List[str]
    """声明此源支持的 mount_mode 子集。空 list = 全支持。"""

    def spec_form(self) -> Dict[str, Any]:
        """返回 UI 渲染表单用的 JSON Schema 子集。

        约定 keys:
            {
              "fields": [
                {"name": "...", "label": "...", "type": "string|int|select|password|bool",
                 "required": bool, "default": ..., "help": "...",
                 "options": [{"value": "...", "label": "..."}],  # type=select 时
                },
                ...
              ]
            }
        """
        ...

    def probe(self, spec: SourceSpec) -> ProbeResult:
        ...

    def sample(self, spec: SourceSpec, n: int = 20) -> SampleResult:
        ...

    async def load(self, spec: SourceSpec) -> AsyncIterator[DocumentRecord]:
        ...


__all__ = [
    "DataSourceAdapter",
    "DocumentRecord",
    "FieldSchema",
    "ProbeResult",
    "ProbeStatus",
    "SampleResult",
    "SourceSpec",
]
