"""Prometheus 埋点。

设计：
- 缺 ``prometheus_client`` 时所有 API 退化为 no-op，不影响业务；
- 指标分三大类：
    chayuan_http_requests_total{method, path, status}
    chayuan_http_request_duration_seconds{method, path}  histogram
    chayuan_http_in_flight_requests                      gauge
    chayuan_llm_calls_total{platform, model, status}
    chayuan_llm_call_duration_seconds{platform, model}   histogram
- ``path`` 用注册的 FastAPI 路由模板，避免高基数（否则每个 uuid URI 都是一个 label）；
  对未匹配的 URL 统一归入 ``__unknown__``。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

logger = logging.getLogger("chayuan.observability.metrics")

# ---- 条件导入 ------------------------------------------------------------

_PROM_OK = False
try:
    from prometheus_client import (  # type: ignore
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        REGISTRY,
        generate_latest,
    )

    _PROM_OK = True
except ImportError:
    CONTENT_TYPE_LATEST = "text/plain; charset=utf-8"  # type: ignore


# ---- 指标对象（延迟构建，避免多次 import 重复注册） --------------------------

_METRICS: dict = {}


def _ensure_metrics() -> dict:
    if not _PROM_OK:
        return {}
    if _METRICS:
        return _METRICS

    _METRICS["http_requests_total"] = Counter(
        "chayuan_http_requests_total",
        "API 请求总数",
        labelnames=("method", "path", "status"),
    )
    _METRICS["http_request_duration"] = Histogram(
        "chayuan_http_request_duration_seconds",
        "API 请求耗时分布（秒）",
        labelnames=("method", "path"),
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
    )
    _METRICS["http_in_flight"] = Gauge(
        "chayuan_http_in_flight_requests",
        "当前正在处理的 HTTP 请求数",
    )
    _METRICS["llm_calls_total"] = Counter(
        "chayuan_llm_calls_total",
        "LLM 调用总数",
        labelnames=("platform", "model", "status"),
    )
    _METRICS["llm_call_duration"] = Histogram(
        "chayuan_llm_call_duration_seconds",
        "LLM 调用耗时分布（秒）",
        labelnames=("platform", "model"),
        buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
    )

    # ----- 业务级指标 -----
    # 每个 App 维度的请求量（仅在签名过的 /openapi/v1/* 路径上采集，避免把公共端点污染进来）
    _METRICS["app_requests_total"] = Counter(
        "chayuan_app_requests_total",
        "开放平台 App 请求总数（按 App / 路由模板 / 方法 / 状态码）",
        labelnames=("app_id", "path", "method", "status"),
    )
    _METRICS["app_request_duration"] = Histogram(
        "chayuan_app_request_duration_seconds",
        "开放平台 App 请求耗时分布（秒）",
        labelnames=("app_id", "path"),
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
    )
    # 工具调用：看清谁调谁、成功率、延迟
    _METRICS["tool_calls_total"] = Counter(
        "chayuan_tool_calls_total",
        "Agent 工具调用总数（按工具名 / 状态）",
        labelnames=("tool", "status"),
    )
    _METRICS["tool_call_duration"] = Histogram(
        "chayuan_tool_call_duration_seconds",
        "Agent 工具调用耗时（秒）",
        labelnames=("tool",),
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
    )
    # LLM token 消耗（区分方向：prompt vs completion）
    _METRICS["llm_tokens_total"] = Counter(
        "chayuan_llm_tokens_total",
        "LLM token 累计消耗（按 model / 方向 prompt|completion）",
        labelnames=("model", "direction"),
    )

    # ----- P2-10 观测闭环 -----
    # RAGAS 分数 Gauge：每次 run_eval_against_golden 跑完 set 一次最新分数
    _METRICS["rag_faithfulness_score"] = Gauge(
        "chayuan_rag_faithfulness_score",
        "RAGAS faithfulness 评分（0..1，越高越忠于原文）",
        labelnames=("kb",),
    )
    _METRICS["rag_context_precision_score"] = Gauge(
        "chayuan_rag_context_precision_score",
        "RAGAS context_precision 评分（0..1，越高召回上下文越相关）",
        labelnames=("kb",),
    )
    _METRICS["rag_answer_correctness_score"] = Gauge(
        "chayuan_rag_answer_correctness_score",
        "RAGAS answer_correctness 评分（0..1）",
        labelnames=("kb",),
    )
    _METRICS["rag_hit_rate"] = Gauge(
        "chayuan_rag_hit_rate",
        "golden 集命中率（0..1）",
        labelnames=("kb",),
    )

    # Supervisor（T11）多 Agent 执行
    _METRICS["supervisor_runs_total"] = Counter(
        "chayuan_supervisor_runs_total",
        "Supervisor 多 Agent 执行次数（按 role / status）",
        labelnames=("role", "status"),
    )
    _METRICS["supervisor_run_duration"] = Histogram(
        "chayuan_supervisor_run_duration_seconds",
        "Supervisor 单角色执行耗时（秒）",
        labelnames=("role",),
        buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600),
    )

    # Modality（T10）：音频 / 视频处理
    _METRICS["modality_ops_total"] = Counter(
        "chayuan_modality_ops_total",
        "多模态能力调用次数（按 op asr|tts|video / status）",
        labelnames=("op", "status"),
    )
    _METRICS["modality_op_duration"] = Histogram(
        "chayuan_modality_op_duration_seconds",
        "多模态能力调用耗时（秒）",
        labelnames=("op",),
        buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
    )

    # P2-7 circuit breaker 状态：closed=0 / open=1 / half_open=2
    _METRICS["llm_breaker_state"] = Gauge(
        "chayuan_llm_breaker_state",
        "LLM provider 熔断器状态（0=closed, 1=open, 2=half_open）",
        labelnames=("provider",),
    )

    # 89-12 图像嵌入客户端 — 用于追踪降级矩阵 / 性能 / 容量
    _METRICS["image_embedder_calls_total"] = Counter(
        "chayuan_image_embedder_calls_total",
        "图像嵌入客户端调用次数(按 client_kind=infinity|inproc / status=ok|error|fallback)",
        labelnames=("client_kind", "status"),
    )
    _METRICS["image_embedder_fallback_total"] = Counter(
        "chayuan_image_embedder_fallback_total",
        "图像嵌入主路径失败 → fallback 次数(from_kind / to_kind)",
        labelnames=("from_kind", "to_kind"),
    )
    _METRICS["image_embedder_call_duration"] = Histogram(
        "chayuan_image_embedder_call_duration_seconds",
        "图像嵌入 encode 耗时分布(client_kind)",
        labelnames=("client_kind",),
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
    )
    _METRICS["image_embedder_batch_size"] = Histogram(
        "chayuan_image_embedder_batch_size",
        "图像嵌入单次调用 batch 大小分布",
        labelnames=("client_kind",),
        buckets=(1, 2, 4, 8, 16, 32, 64, 128),
    )

    # ----- plan v1.3 §5.5 KB 集成专属指标 -----
    # subject_kind ∈ {user, app, dl_token}; status ∈ {ok, denied, error}
    _METRICS["kb_search_total"] = Counter(
        "chayuan_kb_search_total",
        "知识库批量搜索请求总数（按 subject 类型 / 融合方式 / 状态）",
        labelnames=("subject_kind", "fusion", "status"),
    )
    _METRICS["kb_search_duration"] = Histogram(
        "chayuan_kb_search_duration_seconds",
        "知识库批量搜索耗时分布（秒）",
        labelnames=("subject_kind", "fusion"),
        buckets=(0.05, 0.1, 0.25, 0.5, 0.8, 1.5, 3, 5, 10, 30),
    )
    # 单次搜索内子查询数 / 命中 chunk 数
    _METRICS["kb_search_subqueries"] = Histogram(
        "chayuan_kb_search_subqueries",
        "单次 search_batch 子查询数",
        buckets=(1, 2, 3, 5, 8, 12, 16, 24, 32, 64),
    )
    _METRICS["kb_search_chunks_returned"] = Histogram(
        "chayuan_kb_search_chunks_returned",
        "单次 search_batch 最终融合返回 chunk 数",
        buckets=(0, 1, 3, 5, 10, 20, 50, 100, 200),
    )
    # ACL 拒绝码细化（与 contract §4 对齐）
    _METRICS["kb_denied_total"] = Counter(
        "chayuan_kb_denied_total",
        "知识库 ACL 拒绝总数（按 subject 类型 / 错误码 / 操作）",
        labelnames=("subject_kind", "code", "action"),
    )
    # 下载（拆 token / acl 两条路径）
    _METRICS["kb_download_total"] = Counter(
        "chayuan_kb_download_total",
        "知识库附件下载总数（按 subject 类型 / 是否走短期 token / 状态）",
        labelnames=("subject_kind", "via_dl_token", "status"),
    )
    return _METRICS


# ---- 公共 API ------------------------------------------------------------

def is_metrics_enabled() -> bool:
    """是否真正在埋点（依赖装好 & 配置允许）。"""
    if not _PROM_OK:
        return False
    try:
        from chayuan.settings import Settings
        return bool(getattr(Settings.basic_settings, "METRICS_ENABLED", True))
    except Exception:
        return True


def record_llm_call(platform: str, model: str, status: str, duration_s: float) -> None:
    """LLM 调用方在 finally 里调用，status 建议 `success` / `error` / `timeout`。"""
    if not is_metrics_enabled():
        return
    m = _ensure_metrics()
    try:
        m["llm_calls_total"].labels(platform or "-", model or "-", status).inc()
        m["llm_call_duration"].labels(platform or "-", model or "-").observe(
            max(0.0, duration_s)
        )
    except Exception:  # noqa: BLE001
        logger.debug("record_llm_call failed", exc_info=True)


def record_app_request(
    app_id: str, path: str, method: str, status: str, duration_s: float,
) -> None:
    """签名 OpenAPI 请求的 per-App 指标采集。``path`` 传路由模板避免高基数。"""
    if not is_metrics_enabled():
        return
    m = _ensure_metrics()
    try:
        m["app_requests_total"].labels(
            app_id or "-", path or "-", method or "-", str(status),
        ).inc()
        m["app_request_duration"].labels(app_id or "-", path or "-").observe(
            max(0.0, duration_s)
        )
    except Exception:  # noqa: BLE001
        logger.debug("record_app_request failed", exc_info=True)


def record_tool_call(tool: str, status: str, duration_s: float) -> None:
    """工具调用结束时调用；``status`` 建议 ``success`` / ``error`` / ``not_found`` / ``timeout``。"""
    if not is_metrics_enabled():
        return
    m = _ensure_metrics()
    try:
        m["tool_calls_total"].labels(tool or "-", status).inc()
        m["tool_call_duration"].labels(tool or "-").observe(max(0.0, duration_s))
    except Exception:  # noqa: BLE001
        logger.debug("record_tool_call failed", exc_info=True)


def record_rag_scores(kb: str, scores: dict, hit_rate: Optional[float] = None) -> None:
    """P2-10：把 RAGAS summary 分数写到 Prometheus Gauge。

    ``scores`` 结构来自 ``eval/runner._ragas_scores``：
    ``{"faithfulness": 0.9, "context_precision": 0.8, "answer_correctness": 0.7}``

    任意 key 缺失或非数值的都会被忽略；不装 prometheus 时整体 no-op。
    """
    if not is_metrics_enabled():
        return
    m = _ensure_metrics()
    label = kb or "-"
    mapping = {
        "faithfulness": "rag_faithfulness_score",
        "context_precision": "rag_context_precision_score",
        "answer_correctness": "rag_answer_correctness_score",
    }
    for k, metric_key in mapping.items():
        v = scores.get(k) if isinstance(scores, dict) else None
        if v is None:
            continue
        try:
            m[metric_key].labels(label).set(float(v))
        except Exception:  # noqa: BLE001
            logger.debug("record_rag_scores %s failed", k, exc_info=True)
    if hit_rate is not None:
        try:
            m["rag_hit_rate"].labels(label).set(float(hit_rate))
        except Exception:  # noqa: BLE001
            pass


def record_supervisor_run(role: str, status: str, duration_s: float) -> None:
    """P2-10 Supervisor 埋点。role 形如 researcher/writer/reviewer/team。"""
    if not is_metrics_enabled():
        return
    m = _ensure_metrics()
    try:
        m["supervisor_runs_total"].labels(role or "-", status or "-").inc()
        m["supervisor_run_duration"].labels(role or "-").observe(max(0.0, duration_s))
    except Exception:  # noqa: BLE001
        logger.debug("record_supervisor_run failed", exc_info=True)


def record_modality_op(op: str, status: str, duration_s: float) -> None:
    """P2-10 多模态埋点。op ∈ {asr, tts, video_understand}。"""
    if not is_metrics_enabled():
        return
    m = _ensure_metrics()
    try:
        m["modality_ops_total"].labels(op or "-", status or "-").inc()
        m["modality_op_duration"].labels(op or "-").observe(max(0.0, duration_s))
    except Exception:  # noqa: BLE001
        logger.debug("record_modality_op failed", exc_info=True)


def set_breaker_state(provider: str, state: str) -> None:
    """P2-7 circuit breaker 状态。state ∈ {closed, open, half_open}。"""
    if not is_metrics_enabled():
        return
    m = _ensure_metrics()
    try:
        code = {"closed": 0, "open": 1, "half_open": 2}.get(state.lower(), 0)
        m["llm_breaker_state"].labels(provider or "-").set(code)
    except Exception:  # noqa: BLE001
        logger.debug("set_breaker_state failed", exc_info=True)


def record_llm_tokens(model: str, prompt_tokens: int, completion_tokens: int) -> None:
    """LLM 调用结束时调用，计 prompt/completion 两个方向的 token 消耗。"""
    if not is_metrics_enabled():
        return
    if prompt_tokens <= 0 and completion_tokens <= 0:
        return
    m = _ensure_metrics()
    try:
        if prompt_tokens > 0:
            m["llm_tokens_total"].labels(model or "-", "prompt").inc(prompt_tokens)
        if completion_tokens > 0:
            m["llm_tokens_total"].labels(model or "-", "completion").inc(completion_tokens)
    except Exception:  # noqa: BLE001
        logger.debug("record_llm_tokens failed", exc_info=True)


def record_kb_search(
    subject_kind: str, fusion: str, status: str,
    duration_s: float, sub_queries: int = 0, chunks_returned: int = 0,
) -> None:
    """plan v1.3 §5.5：批量搜索埋点。

    ``subject_kind`` ∈ {user, app, dl_token}; ``fusion`` ∈ {rrf, weighted};
    ``status`` ∈ {ok, denied, error}.
    """
    if not is_metrics_enabled():
        return
    m = _ensure_metrics()
    try:
        m["kb_search_total"].labels(subject_kind or "-", fusion or "-", status or "-").inc()
        m["kb_search_duration"].labels(subject_kind or "-", fusion or "-").observe(
            max(0.0, duration_s)
        )
        if sub_queries > 0:
            m["kb_search_subqueries"].observe(int(sub_queries))
        if chunks_returned >= 0:
            m["kb_search_chunks_returned"].observe(int(chunks_returned))
    except Exception:  # noqa: BLE001
        logger.debug("record_kb_search failed", exc_info=True)


def record_kb_denied(subject_kind: str, code: int, action: str = "read") -> None:
    """ACL 拒绝埋点（4011/4014/4031/4032/4033 等）。"""
    if not is_metrics_enabled():
        return
    m = _ensure_metrics()
    try:
        m["kb_denied_total"].labels(subject_kind or "-", str(int(code)), action or "-").inc()
    except Exception:  # noqa: BLE001
        logger.debug("record_kb_denied failed", exc_info=True)


def record_kb_download(subject_kind: str, via_dl_token: bool, status: str) -> None:
    """下载埋点。``via_dl_token`` 区分短期 token 与常规 ACL 路径。"""
    if not is_metrics_enabled():
        return
    m = _ensure_metrics()
    try:
        m["kb_download_total"].labels(
            subject_kind or "-", "1" if via_dl_token else "0", status or "-",
        ).inc()
    except Exception:  # noqa: BLE001
        logger.debug("record_kb_download failed", exc_info=True)


def render_metrics() -> Optional[bytes]:
    """返回 Prometheus 文本，缺依赖时返回 None。"""
    if not _PROM_OK:
        return None
    if not is_metrics_enabled():
        return None
    return generate_latest(REGISTRY)


def metrics_content_type() -> str:
    return CONTENT_TYPE_LATEST


# ---- 中间件 --------------------------------------------------------------

_SKIP_PREFIXES = ("/healthz", "/readyz", "/metrics", "/docs", "/redoc", "/openapi.json")


def _route_template(request: Request) -> str:
    """返回已注册路由的模板（如 `/chat/{conv_id}`）；未匹配时返回 `__unknown__`。"""
    try:
        route = request.scope.get("route")
        if route is not None and getattr(route, "path", None):
            return route.path  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    return "__unknown__"


class PrometheusMetricsMiddleware(BaseHTTPMiddleware):
    """HTTP 指标中间件。不埋业务语义，只埋技术指标。"""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not is_metrics_enabled():
            return await call_next(request)

        path = request.url.path
        if path.startswith(_SKIP_PREFIXES):
            return await call_next(request)

        m = _ensure_metrics()
        in_flight = m.get("http_in_flight")
        if in_flight is not None:
            in_flight.inc()

        start = time.perf_counter()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        except Exception:
            raise
        finally:
            elapsed = time.perf_counter() - start
            template = _route_template(request)
            try:
                m["http_request_duration"].labels(request.method, template).observe(elapsed)
                m["http_requests_total"].labels(request.method, template, status).inc()
            except Exception:  # noqa: BLE001
                logger.debug("metrics update failed", exc_info=True)

            # 业务级：/openapi/v1/* 的签名请求另算一份 per-App 指标
            if path.startswith("/openapi/v1/"):
                app = getattr(request.state, "app", None)
                app_id = getattr(app, "app_id", "") or "-"
                try:
                    record_app_request(
                        app_id=app_id, path=template, method=request.method,
                        status=status, duration_s=elapsed,
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("record_app_request failed", exc_info=True)

            if in_flight is not None:
                in_flight.dec()
