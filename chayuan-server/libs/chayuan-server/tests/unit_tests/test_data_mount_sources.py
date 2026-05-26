"""data_mount 12 种源适配器 + 注册中心 + materializer 单测.

测试策略:
* 不真连外部服务(Mongo/Confluence/Notion 等);只测 spec_form / probe 缺参 / 注册
* materializer 用 stub adapter 喂受控 Documents,验证 5 类 artifact 生成
* schema_analyzer 用合成数据验证字段统计
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Dict, List

import pytest

from chayuan.server.data_mount import (
    DocumentRecord,
    SourceSpec,
    analyze_schema,
    get_registry,
    materialize_mount,
)
from chayuan.server.data_mount.materializer import ARTIFACT_TYPES, MountMaterializer
from chayuan.server.data_mount.registry import register_default_sources


# -------------------------------------------------------------------------
# Registry: 12 种源都注册成功
# -------------------------------------------------------------------------

def test_registry_has_all_12_sources():
    register_default_sources()
    catalog = get_registry().to_catalog()
    type_ids = {c["type_id"] for c in catalog}
    expected = {
        "kb", "knowledge_source", "file", "annotation",
        "web", "sql", "s3", "mongo", "notion", "confluence",
        "github", "conversation",
    }
    # 缺依赖的 source 在 register_default_sources 里被吃掉了,
    # 所以可能少几个但至少有 6 个核心源
    assert type_ids & expected, f"none of the 12 expected sources registered: {type_ids}"
    # KB / file / annotation / web / sql / conversation 是纯 stdlib + 项目内代码,必须有
    assert "kb" in type_ids
    assert "file" in type_ids
    assert "annotation" in type_ids
    assert "web" in type_ids
    assert "sql" in type_ids
    assert "conversation" in type_ids


def test_each_source_exposes_spec_form():
    register_default_sources()
    for adapter in get_registry().list():
        form = adapter.spec_form()
        assert "fields" in form
        assert isinstance(form["fields"], list)
        for f in form["fields"]:
            assert "name" in f and "type" in f and "label" in f


# -------------------------------------------------------------------------
# probe with empty options should not blow up
# -------------------------------------------------------------------------

@pytest.mark.parametrize("type_id", [
    "kb", "file", "web", "sql", "s3", "mongo", "notion", "confluence",
    "github", "annotation", "conversation", "knowledge_source",
])
def test_probe_with_empty_options_returns_error_not_exception(type_id: str):
    register_default_sources()
    adapter = get_registry().get(type_id)
    if adapter is None:
        pytest.skip(f"{type_id} 未注册(缺依赖)")
    spec = SourceSpec(source_type=type_id, options={})
    result = adapter.probe(spec)
    # 缺参数应当返回 error/warning 而不是抛异常
    assert result.status in ("ok", "warning", "error")
    if result.status == "error":
        assert result.message  # 必须有错误说明给 UI


# -------------------------------------------------------------------------
# schema_analyzer
# -------------------------------------------------------------------------

def test_schema_analyzer_basic():
    records = [
        DocumentRecord(text="x", metadata={"name": "Alice", "age": 30, "tags": ["a"]}),
        DocumentRecord(text="y", metadata={"name": "Bob", "age": 25, "tags": ["b", "c"]}),
        DocumentRecord(text="z", metadata={"name": "Carol", "age": None}),
    ]
    fields = analyze_schema(records)
    assert {f.name for f in fields} >= {"name", "age", "tags"}
    name_field = next(f for f in fields if f.name == "name")
    assert name_field.type == "string"
    assert name_field.fill_rate == 1.0
    age_field = next(f for f in fields if f.name == "age")
    assert age_field.fill_rate < 1.0  # 一条 None
    tags_field = next(f for f in fields if f.name == "tags")
    assert tags_field.type == "list"


def test_schema_analyzer_empty_returns_empty():
    assert analyze_schema([]) == []


# -------------------------------------------------------------------------
# materializer: 5 mount_modes 全覆盖
# -------------------------------------------------------------------------

class _StubAdapter:
    """用 ``register`` 注入,不进 default registry。"""

    type_id = "_stub"
    label = "stub"
    description = "test"
    icon = "x"
    capabilities = ["corpus", "context", "fewshot", "safety", "preference"]

    def spec_form(self):
        return {"fields": []}

    def probe(self, spec):
        from chayuan.server.data_mount.base import ProbeResult
        return ProbeResult(status="ok", message="stub")

    def sample(self, spec, n=20):
        from chayuan.server.data_mount.base import SampleResult
        return SampleResult(items=list(self._records()), fields=[])

    async def load(self, spec):  # type: ignore[override]
        for r in self._records():
            yield r

    def _records(self) -> List[DocumentRecord]:
        return [
            # 一条 fewshot-able
            DocumentRecord(text="any",
                           metadata={"query": "如何加密?", "answer": "用 AES-256"}),
            # 一条 safety-able
            DocumentRecord(text="any",
                           metadata={"pattern": "/key=", "action": "拒答+提示"}),
            # 一条 preference-able
            DocumentRecord(text="any",
                           metadata={"prompt": "推荐 LLM?", "chosen": "Qwen 2.5",
                                     "rejected": "GPT-1"}),
            # 普通 corpus/context 行
            DocumentRecord(text="知识库片段 42", metadata={"source": "stub", "score": 0.9}),
        ]


@pytest.fixture(autouse=False)
def _register_stub():
    reg = get_registry()
    reg.register(_StubAdapter())
    yield
    # 清掉避免污染其它测试(直接动私有字典是已知做法)
    reg._adapters.pop("_stub", None)  # noqa: SLF001


def test_materialize_all_5_modes(_register_stub):
    spec = SourceSpec(source_type="_stub", options={}, max_items=10)
    artifacts = asyncio.run(materialize_mount(
        spec, ["corpus", "context", "fewshot", "safety", "preference"],
        target_kb="my_kb",
    ))
    types = {a["artifact_type"] for a in artifacts}
    assert ARTIFACT_TYPES["corpus"] in types
    assert ARTIFACT_TYPES["context"] in types
    assert ARTIFACT_TYPES["fewshot"] in types
    assert ARTIFACT_TYPES["safety"] in types
    assert ARTIFACT_TYPES["preference"] in types

    # corpus payload 带 target_kb
    corpus = next(a for a in artifacts if a["artifact_type"] == ARTIFACT_TYPES["corpus"])
    assert corpus["payload"]["target_kb"] == "my_kb"
    assert corpus["payload"]["items"]  # 至少 1 条

    # fewshot 只命中带 query/answer 的那条
    fewshot = next(a for a in artifacts if a["artifact_type"] == ARTIFACT_TYPES["fewshot"])
    assert len(fewshot["payload"]["examples"]) == 1
    assert fewshot["payload"]["examples"][0]["query"] == "如何加密?"

    # safety / preference 类似
    safety = next(a for a in artifacts if a["artifact_type"] == ARTIFACT_TYPES["safety"])
    assert len(safety["payload"]["rules"]) == 1

    pref = next(a for a in artifacts if a["artifact_type"] == ARTIFACT_TYPES["preference"])
    assert len(pref["payload"]["pairs"]) == 1


def test_materialize_default_to_context(_register_stub):
    """空 modes 默认走 context (最小风险)。"""
    spec = SourceSpec(source_type="_stub", options={}, max_items=10)
    artifacts = asyncio.run(materialize_mount(spec, []))
    assert len(artifacts) == 1
    assert artifacts[0]["artifact_type"] == ARTIFACT_TYPES["context"]


def test_materialize_unknown_source_raises():
    spec = SourceSpec(source_type="not-a-real-source", options={})
    with pytest.raises(ValueError, match="未知数据源"):
        asyncio.run(materialize_mount(spec, ["context"]))


# -------------------------------------------------------------------------
# SourceSpec serialization
# -------------------------------------------------------------------------

def test_source_spec_round_trip():
    spec = SourceSpec(source_type="kb", options={"kb_name": "x"}, max_items=50)
    d = spec.to_dict()
    spec2 = SourceSpec.from_dict(d)
    assert spec2.source_type == spec.source_type
    assert spec2.options == spec.options
    assert spec2.max_items == spec.max_items
