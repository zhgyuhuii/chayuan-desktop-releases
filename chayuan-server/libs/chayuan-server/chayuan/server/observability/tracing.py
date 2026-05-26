"""OpenTelemetry 可选初始化。

- 只在 ``basic_settings.OTEL_ENABLED=true`` 且相关包已安装时启用；
- 遵循标准 OTLP 环境变量（``OTEL_EXPORTER_OTLP_ENDPOINT`` / ``OTEL_EXPORTER_OTLP_HEADERS``）；
- 自动 instrument FastAPI / SQLAlchemy / requests（若对应 instrument 包已装）；
- 任何环节异常都 swallow，不影响 API 进程启动。

推荐安装：

    pip install \
        opentelemetry-sdk \
        opentelemetry-exporter-otlp-proto-http \
        opentelemetry-instrumentation-fastapi \
        opentelemetry-instrumentation-sqlalchemy \
        opentelemetry-instrumentation-requests
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("chayuan.observability.tracing")

_INITIALIZED: bool = False


def init_tracing(app=None) -> bool:
    """幂等初始化。返回是否真的启用成功。"""
    global _INITIALIZED
    if _INITIALIZED:
        return True

    try:
        from chayuan.settings import Settings
        enabled = bool(getattr(Settings.basic_settings, "OTEL_ENABLED", False))
        service_name = (
            getattr(Settings.basic_settings, "OTEL_SERVICE_NAME", "chayuan-api") or "chayuan-api"
        )
    except Exception:
        return False

    if not enabled:
        return False

    try:
        from opentelemetry import trace  # type: ignore
        from opentelemetry.sdk.resources import Resource  # type: ignore
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore
    except ImportError as e:
        logger.warning(
            "OTEL_ENABLED=true 但 opentelemetry 相关包缺失：%s；"
            "请 `pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http`",
            e,
        )
        return False

    # 默认 OTLP HTTP exporter；若装了 grpc 版本，用户用 OTEL_EXPORTER_OTLP_PROTOCOL=grpc 覆盖
    exporter = _try_otlp_exporter()
    if exporter is None:
        return False

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.instance.id": os.environ.get("HOSTNAME", ""),
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    _instrument_fastapi(app)
    _instrument_sqlalchemy()
    _instrument_requests()

    logger.info("OpenTelemetry tracing enabled (service=%s)", service_name)
    _INITIALIZED = True
    return True


def _try_otlp_exporter():
    # 优先 HTTP；HTTP 依赖少且更稳定
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore
            OTLPSpanExporter as _HTTP,
        )
        return _HTTP()
    except ImportError:
        pass
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore
            OTLPSpanExporter as _GRPC,
        )
        return _GRPC()
    except ImportError:
        logger.warning(
            "未安装 otlp exporter；请 `pip install opentelemetry-exporter-otlp-proto-http` "
            "或 `opentelemetry-exporter-otlp-proto-grpc`。"
        )
        return None


def _instrument_fastapi(app) -> None:
    if app is None:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore
        FastAPIInstrumentor.instrument_app(app)
    except Exception as e:  # noqa: BLE001
        logger.debug("fastapi instrument skipped: %r", e)


def _instrument_sqlalchemy() -> None:
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor  # type: ignore
        from chayuan.server.db.base import engine

        SQLAlchemyInstrumentor().instrument(engine=engine)
    except Exception as e:  # noqa: BLE001
        logger.debug("sqlalchemy instrument skipped: %r", e)


def _instrument_requests() -> None:
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor  # type: ignore
        RequestsInstrumentor().instrument()
    except Exception as e:  # noqa: BLE001
        logger.debug("requests instrument skipped: %r", e)


# ---------------------------------------------------------------------------
# 业务层 helper：安全的 span 上下文管理器（缺 OTel 时退化为 nullcontext）
# ---------------------------------------------------------------------------

def start_as_current_span(name: str, attributes: Optional[dict] = None):
    """返回一个上下文管理器，能安全地 start_as_current_span。

    - OTel 未初始化 / 未装包 → 返回 nullcontext（什么都不做，不报错）
    - 初始化成功 → 返回真的 span ctx，上下文里 span.set_attribute 可用
    """
    class _Null:
        def __enter__(self_inner):
            return self_inner
        def __exit__(self_inner, *a):
            return False
        def set_attribute(self_inner, *a, **kw):
            return None

    try:
        from opentelemetry import trace  # type: ignore
    except Exception:  # noqa: BLE001
        return _Null()
    try:
        tracer = trace.get_tracer("chayuan")
        ctx = tracer.start_as_current_span(name)
        # 延迟 attributes set——封装一层，以便缺 span.set_attribute 时不炸
        class _Wrap:
            def __enter__(self_inner):
                span = ctx.__enter__()
                if attributes:
                    for k, v in attributes.items():
                        try:
                            span.set_attribute(k, v)
                        except Exception:  # noqa: BLE001
                            pass
                return span
            def __exit__(self_inner, *a):
                try:
                    return ctx.__exit__(*a)
                except Exception:  # noqa: BLE001
                    return False
        return _Wrap()
    except Exception:  # noqa: BLE001
        return _Null()
