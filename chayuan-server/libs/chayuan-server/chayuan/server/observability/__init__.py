"""可观测性：日志 / 指标 / 链路追踪。

按"能启用就启用、缺包就降级"的原则组织：
- ``logging.py``：request_id contextvar + 可选 JSON formatter；
- ``metrics.py``：Prometheus 埋点；缺 prometheus_client 时全部变为 no-op；
- ``tracing.py``：OpenTelemetry 可选初始化；缺 opentelemetry-* 时跳过。

对外统一入口：``setup_observability()``，在 API 进程启动时调用一次即可。
"""
from __future__ import annotations

from chayuan.server.observability.logging import (  # noqa: F401
    current_request_id,
    init_logging,
    reapply_noise_filter,
    set_request_id,
)
from chayuan.server.observability.metrics import (  # noqa: F401
    PrometheusMetricsMiddleware,
    is_metrics_enabled,
    record_llm_call,
    render_metrics,
)
from chayuan.server.observability.tracing import init_tracing  # noqa: F401


def setup_observability(app=None) -> None:
    """API 进程启动后调用；安全幂等。"""
    init_logging()
    # app 可选：仅用于可能的 OTel FastAPI instrumentation
    init_tracing(app)
