"""知识源 / Text2SQL / RAG / 缓存 的业务维度 Prometheus 指标（P0-3）。

原则：
- 缺 ``prometheus_client`` 时全部 no-op，不影响业务
- label 基数严格控制：高基数字段（user_id / query）不做 label，只做 bucket 或 hash
- 延迟用 Histogram；次数用 Counter；当前状态用 Gauge

集成点：
- Connector.test_connection / search / introspect
- Orchestrator.multi_search_stream 每源
- graph_text2sql 每节点
- 三层缓存 get/set
- RAG hybrid / rerank
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger("chayuan.observability.ks_metrics")

_PROM_OK = False
try:
    from prometheus_client import Counter, Histogram, Gauge  # type: ignore
    _PROM_OK = True
except Exception:  # noqa: BLE001
    Counter = Histogram = Gauge = None  # type: ignore


class _NoopMetric:
    def labels(self, **_kw):
        return self

    def inc(self, *_a, **_kw):
        return None

    def dec(self, *_a, **_kw):
        return None

    def observe(self, *_a, **_kw):
        return None

    def set(self, *_a, **_kw):
        return None


def _counter(name: str, doc: str, labels=()):
    if not _PROM_OK:
        return _NoopMetric()
    try:
        return Counter(name, doc, labelnames=labels)
    except ValueError:
        # 已存在（热重载场景），从 REGISTRY 找回
        from prometheus_client import REGISTRY
        for metric in REGISTRY.collect():
            if metric.name == name:
                return _NoopMetric()  # 简化：不重复注册，返回 noop
        return _NoopMetric()


def _hist(name: str, doc: str, labels=(), buckets=None):
    if not _PROM_OK:
        return _NoopMetric()
    try:
        if buckets is not None:
            return Histogram(name, doc, labelnames=labels, buckets=buckets)
        return Histogram(name, doc, labelnames=labels)
    except ValueError:
        return _NoopMetric()


def _gauge(name: str, doc: str, labels=()):
    if not _PROM_OK:
        return _NoopMetric()
    try:
        return Gauge(name, doc, labelnames=labels)
    except ValueError:
        return _NoopMetric()


# ---------------------------------------------------------------------------
# Connector / 多源 / Text2SQL / Cache / RAG 指标
# ---------------------------------------------------------------------------

DEFAULT_DUR_BUCKETS = (
    0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 60.0,
)

# Connector 层
KS_CONNECTOR_SEARCH = _hist(
    "chayuan_ks_connector_search_seconds",
    "Connector.search() 耗时（秒）",
    labels=("kind", "dialect", "status"),
    buckets=DEFAULT_DUR_BUCKETS,
)
KS_CONNECTOR_TEST = _counter(
    "chayuan_ks_connector_test_total",
    "Connector.test_connection 次数",
    labels=("dialect", "status"),
)
KS_CONNECTOR_INTROSPECT = _hist(
    "chayuan_ks_connector_introspect_seconds",
    "Connector.introspect 耗时（秒）",
    labels=("kind", "dialect"),
    buckets=DEFAULT_DUR_BUCKETS,
)

# 多源并行
KS_MULTI_SOURCE_FANOUT = _hist(
    "chayuan_ks_multi_source_fanout",
    "每次多源检索的扇出度（源数量）",
    labels=(),
    buckets=(1, 2, 3, 5, 8, 12, 20, 50),
)
KS_MULTI_SOURCE_TOTAL_SECONDS = _hist(
    "chayuan_ks_multi_source_total_seconds",
    "多源检索总耗时（秒）",
    labels=("status",),
    buckets=DEFAULT_DUR_BUCKETS,
)
KS_MULTI_SOURCE_TIMEOUT_TOTAL = _counter(
    "chayuan_ks_multi_source_timeout_total",
    "每源超时次数",
    labels=("kind", "dialect"),
)

# VS 单 collection 检索（知识库"范围"功能：多集合并发扇出）
# status ∈ {ok, error, timeout}
VS_COLLECTION_SEARCH = _hist(
    "chayuan_vs_collection_search_seconds",
    "ExternalVsConnector 单 collection 检索耗时（秒）",
    labels=("dialect", "collection", "status"),
    buckets=DEFAULT_DUR_BUCKETS,
)

# Text2SQL
T2SQL_NODE_DURATION = _hist(
    "chayuan_text2sql_graph_node_seconds",
    "LangGraph Text2SQL 各节点耗时",
    labels=("dialect", "node", "status"),
    buckets=DEFAULT_DUR_BUCKETS,
)
T2SQL_RETRY_TOTAL = _counter(
    "chayuan_text2sql_retry_total",
    "Text2SQL 自纠错重试次数",
    labels=("dialect", "reason"),
)
T2SQL_SQL_LENGTH = _hist(
    "chayuan_text2sql_sql_length_chars",
    "生成 SQL 字符数",
    labels=("dialect",),
    buckets=(50, 100, 200, 400, 800, 1500, 3000),
)
T2SQL_RAG_HITS = _counter(
    "chayuan_text2sql_rag_hits_total",
    "RAG 训练样本命中次数（pair/ddl/doc）",
    labels=("dialect", "kind"),
)

# 白名单违规：LLM 生成的 SQL 引用了 allowed_tables 之外的表
T2SQL_TABLE_VIOLATION = _counter(
    "chayuan_text2sql_table_violation_total",
    "生成 SQL 引用了白名单之外的表的次数",
    labels=("dialect", "reason"),
)

# 三层缓存
CACHE_HIT = _counter(
    "chayuan_ks_cache_hits_total",
    "三层缓存命中次数",
    labels=("layer",),
)
CACHE_MISS = _counter(
    "chayuan_ks_cache_miss_total",
    "三层缓存未命中次数",
    labels=("layer",),
)
CACHE_BYPASS = _counter(
    "chayuan_ks_cache_bypass_total",
    "因时间敏感等原因绕过缓存",
    labels=("layer", "reason"),
)

# RAG 层
RAG_HYBRID_LATENCY = _hist(
    "chayuan_rag_hybrid_seconds",
    "Hybrid 检索耗时",
    labels=("status",),
    buckets=DEFAULT_DUR_BUCKETS,
)
RAG_RERANK_LATENCY = _hist(
    "chayuan_rag_rerank_seconds",
    "CrossEncoder rerank 耗时",
    labels=("status",),
    buckets=DEFAULT_DUR_BUCKETS,
)
RAG_CANDIDATE_POOL = _hist(
    "chayuan_rag_candidate_pool",
    "rerank 前的候选池大小",
    labels=(),
    buckets=(3, 5, 10, 20, 50, 100),
)

# LLM 层（token / 成本归因）
LLM_TOKEN_TOTAL = _counter(
    "chayuan_llm_tokens_total",
    "LLM token 消耗累计",
    labels=("model", "role", "user_bucket"),
)
LLM_COST_USD_TOTAL = _counter(
    "chayuan_llm_cost_usd_total",
    "LLM 累计成本（美元，近似）",
    labels=("model",),
)


# ---------------------------------------------------------------------------
# 通用计时器上下文
# ---------------------------------------------------------------------------

@contextmanager
def timeit(histogram, **labels):
    """with timeit(KS_CONNECTOR_SEARCH, kind='sql', dialect='mysql', status='ok'): ..."""
    t0 = time.time()
    try:
        yield
        _observe(histogram, time.time() - t0, **labels)
    except Exception:
        elapsed = time.time() - t0
        # 把 status label 自动翻成 error；若原本已经给了非 ok 状态则不覆盖
        if "status" in labels and labels["status"] == "ok":
            labels["status"] = "error"
        _observe(histogram, elapsed, **labels)
        raise


def _observe(metric, value: float, **labels):
    try:
        if labels:
            metric.labels(**labels).observe(float(value))
        else:
            metric.observe(float(value))
    except Exception:  # noqa: BLE001
        pass


def user_bucket(user_id: Optional[int]) -> str:
    """把 user_id 做粗粒度分桶，避免 Prometheus label 基数爆炸。"""
    if user_id is None:
        return "anon"
    try:
        uid = int(user_id)
        if uid == 1:
            return "admin"
        return f"u{uid % 10}"  # 10 个桶；统计热用户行为足矣
    except Exception:  # noqa: BLE001
        return "other"
