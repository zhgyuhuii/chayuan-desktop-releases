"""性能与可扩展性健康体检页。

面板左栏"⑨ 性能与可扩展性"入口调用本模块。核心能力：
- 识别当前配置中会在 5000 并发下翻车的项（SQLite / FAISS / 单 worker / 无 Redis / Ollama 等）；
- 按问题严重度分级：critical（红）/ warning（橙）/ info（蓝）/ ok（绿）；
- 每条检查附"为什么"、"怎么改"以及可一键复制的配置片段；
- 底部根据通过项给出预计可承载并发数量级。

本页面**只读**：只做检测、不写任何 yaml；修改请回到对应的配置页或 CLI。

参考文档：docs/scalability.md
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("chayuan.config_panel.scalability")


# ---------------------------------------------------------------------------
# 诊断数据结构
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}

SEVERITY_META = {
    "critical": {"icon": "error",        "color": "negative", "label": "严重"},
    "warning":  {"icon": "warning",      "color": "warning",  "label": "警告"},
    "info":     {"icon": "info",         "color": "info",     "label": "建议"},
    "ok":       {"icon": "check_circle", "color": "positive", "label": "达标"},
}


@dataclass
class Check:
    """一条体检结论。"""

    key: str
    """唯一标识，用于稳定排序。"""

    title: str
    """显示标题。"""

    severity: str
    """critical / warning / info / ok"""

    summary: str
    """一句话结论（"当前 … 在 5000 并发下会 …"）。"""

    impact: str = ""
    """为什么是问题，说人话。"""

    fix_hint: str = ""
    """怎么改，点到具体的 yaml 字段。"""

    snippet: str = ""
    """可一键复制的 yaml/命令片段。"""

    snippet_lang: str = "yaml"


@dataclass
class Report:
    checks: List[Check] = field(default_factory=list)
    mode: str = "dev"

    @property
    def score(self) -> int:
        """按 `critical=-3, warning=-1, info=0, ok=+1` 折算的简单分数。"""
        table = {"critical": -3, "warning": -1, "info": 0, "ok": 1}
        return sum(table.get(c.severity, 0) for c in self.checks)

    @property
    def est_concurrent_users(self) -> str:
        """极粗略的并发承载估计，按 critical / warning 个数给出档位。"""
        nc = sum(1 for c in self.checks if c.severity == "critical")
        nw = sum(1 for c in self.checks if c.severity == "warning")
        if nc >= 1:
            return "≈ 几十 (有严重瓶颈，不适合上生产)"
        if nw >= 3:
            return "≈ 几百"
        if nw >= 1:
            return "≈ 1000–2000"
        return "≈ 5000+ (已满足扩容目标)"


# ---------------------------------------------------------------------------
# 各项检查
# ---------------------------------------------------------------------------

def _cpu_count() -> int:
    try:
        return max(1, mp.cpu_count())
    except Exception:
        return max(1, os.cpu_count() or 1)


def _check_deployment_mode(bs) -> Check:
    mode = (getattr(bs, "DEPLOYMENT_MODE", "dev") or "dev").lower()
    if mode == "prod":
        return Check(
            key="deployment_mode",
            title="部署模式 DEPLOYMENT_MODE",
            severity="ok",
            summary="当前 DEPLOYMENT_MODE=prod，会对不达标项亮红色告警。",
        )
    return Check(
        key="deployment_mode",
        title="部署模式 DEPLOYMENT_MODE",
        severity="info",
        summary="当前 DEPLOYMENT_MODE=dev（默认）。上生产前请改为 prod 让所有瓶颈变红。",
        fix_hint="在「③ 基础配置」或 basic_settings.yaml 中把 DEPLOYMENT_MODE 改为 prod。",
        snippet="DEPLOYMENT_MODE: prod\n",
    )


def _check_database(bs) -> Check:
    uri = (getattr(bs, "SQLALCHEMY_DATABASE_URI", "") or "").strip()
    mode = (getattr(bs, "DEPLOYMENT_MODE", "dev") or "dev").lower()

    if uri.startswith("sqlite:"):
        sev = "critical" if mode == "prod" else "warning"
        return Check(
            key="db",
            title="元数据数据库 SQLALCHEMY_DATABASE_URI",
            severity=sev,
            summary="当前使用 SQLite，单写者锁在 50+ 并发写就会开始 'database is locked'。",
            impact=(
                "SQLite 的写操作会全表加锁；5000 并发场景下会话/反馈/知识库索引"
                "写入都走这个库，几乎必然 500。"
            ),
            fix_hint=(
                "切换到 PostgreSQL 或 MySQL。先起库，再把 SQLALCHEMY_DATABASE_URI "
                "指向新库并重启服务。迁移数据可参考 `chayuan init-db` 或 pgloader。"
            ),
            snippet=(
                "SQLALCHEMY_DATABASE_URI: postgresql+psycopg2://chayuan:chayuan@127.0.0.1:5432/chayuan\n"
                "DB_POOL_SIZE: 20\n"
                "DB_MAX_OVERFLOW: 20\n"
                "DB_POOL_RECYCLE: 3600\n"
                "DB_POOL_PRE_PING: true\n"
            ),
        )

    if uri.startswith(("postgresql", "postgres", "mysql", "mariadb")):
        return Check(
            key="db",
            title="元数据数据库 SQLALCHEMY_DATABASE_URI",
            severity="ok",
            summary=f"使用 {uri.split('://', 1)[0]}，可支撑高并发。",
        )

    if not uri:
        return Check(
            key="db",
            title="元数据数据库 SQLALCHEMY_DATABASE_URI",
            severity="critical",
            summary="SQLALCHEMY_DATABASE_URI 为空。",
            fix_hint="请在 basic_settings.yaml 中配置一个有效的数据库连接串。",
        )

    return Check(
        key="db",
        title="元数据数据库 SQLALCHEMY_DATABASE_URI",
        severity="info",
        summary=f"使用自定义驱动：{uri.split('://', 1)[0]}。请自行确认支持高并发。",
    )


def _check_db_pool(bs) -> Check:
    uri = (getattr(bs, "SQLALCHEMY_DATABASE_URI", "") or "").strip()
    if uri.startswith("sqlite:"):
        return Check(
            key="db_pool",
            title="数据库连接池",
            severity="info",
            summary="SQLite 已禁用连接池（StaticPool），改用 Postgres/MySQL 后再关注本项。",
        )
    pool = int(getattr(bs, "DB_POOL_SIZE", 10) or 10)
    overflow = int(getattr(bs, "DB_MAX_OVERFLOW", 20) or 20)
    workers = int(getattr(bs, "UVICORN_WORKERS", 1) or 1)
    total = (pool + overflow) * max(1, workers)
    if pool < 10:
        return Check(
            key="db_pool",
            title="数据库连接池 DB_POOL_SIZE",
            severity="warning",
            summary=f"DB_POOL_SIZE={pool} 偏低；生产建议 ≥ 20。",
            fix_hint=(
                f"当前每 worker 峰值连接 = {pool + overflow}，"
                f"全集群峰值 ≈ {total}（workers={workers}）；数据库侧需能扛住这么多连接。"
            ),
            snippet="DB_POOL_SIZE: 20\nDB_MAX_OVERFLOW: 20\n",
        )
    return Check(
        key="db_pool",
        title="数据库连接池 DB_POOL_SIZE",
        severity="ok",
        summary=(
            f"pool_size={pool}, max_overflow={overflow}, workers={workers}，"
            f"全集群峰值 ≈ {total} 连接。"
        ),
    )


def _check_uvicorn_workers(bs) -> Check:
    workers = int(getattr(bs, "UVICORN_WORKERS", 1) or 1)
    cpu = _cpu_count()
    recommend = 2 * cpu + 1
    mode = (getattr(bs, "DEPLOYMENT_MODE", "dev") or "dev").lower()
    if workers <= 1:
        sev = "critical" if mode == "prod" else "warning"
        return Check(
            key="workers",
            title="API 进程 UVICORN_WORKERS",
            severity=sev,
            summary=f"当前 UVICORN_WORKERS={workers}，其它 {cpu - 1} 个 CPU 核心闲置。",
            impact=(
                "单 worker 的 FastAPI 在流式 LLM 场景下每个请求占 1 个 await 槽，"
                "并发很快会撞上 GIL / 事件循环瓶颈。"
            ),
            fix_hint=f"基础配置把 UVICORN_WORKERS 调到 {recommend}（2 × CPU + 1）并重启。",
            snippet=f"UVICORN_WORKERS: {recommend}\n",
        )
    if workers < recommend // 2:
        return Check(
            key="workers",
            title="API 进程 UVICORN_WORKERS",
            severity="warning",
            summary=f"UVICORN_WORKERS={workers} 低于推荐值 {recommend}（2 × CPU + 1）。",
            snippet=f"UVICORN_WORKERS: {recommend}\n",
        )
    return Check(
        key="workers",
        title="API 进程 UVICORN_WORKERS",
        severity="ok",
        summary=f"UVICORN_WORKERS={workers}，已用满 {cpu} 核心级并发。",
    )


def _check_redis(bs) -> Check:
    url = (getattr(bs, "REDIS_URL", "") or "").strip()
    mode = (getattr(bs, "DEPLOYMENT_MODE", "dev") or "dev").lower()
    if url:
        return Check(
            key="redis",
            title="共享状态 REDIS_URL",
            severity="ok",
            summary=f"已配置 Redis：{url}，可用于限流 / 会话 / 流 buffer / 队列。",
        )
    sev = "warning" if mode == "prod" else "info"
    return Check(
        key="redis",
        title="共享状态 REDIS_URL",
        severity=sev,
        summary="未配置 Redis。多副本横向扩展时会因状态不共享导致限流/会话不一致。",
        fix_hint="起一个 Redis 实例，然后在 basic_settings.yaml 里设置 REDIS_URL。",
        snippet="REDIS_URL: redis://127.0.0.1:6379/0\n",
    )


def _check_rate_limit(bs) -> Check:
    enabled = bool(getattr(bs, "RATE_LIMIT_ENABLED", False))
    redis_url = (getattr(bs, "REDIS_URL", "") or "").strip()
    if enabled and redis_url:
        return Check(
            key="rate_limit",
            title="API 限流",
            severity="ok",
            summary=(
                f"已开启限流，每分钟 "
                f"{int(getattr(bs, 'RATE_LIMIT_PER_MINUTE', 120))} 次。"
            ),
        )
    if enabled and not redis_url:
        return Check(
            key="rate_limit",
            title="API 限流",
            severity="warning",
            summary="RATE_LIMIT_ENABLED=true 但未配置 REDIS_URL，多副本间限流计数不共享。",
            snippet="REDIS_URL: redis://127.0.0.1:6379/0\n",
        )
    mode = (getattr(bs, "DEPLOYMENT_MODE", "dev") or "dev").lower()
    sev = "warning" if mode == "prod" else "info"
    return Check(
        key="rate_limit",
        title="API 限流",
        severity=sev,
        summary="未开启限流，5000 用户中任何一人都能打爆 LLM / 检索。",
        fix_hint="配置 Redis 后开启 RATE_LIMIT_ENABLED，并根据业务调大/调小 RATE_LIMIT_PER_MINUTE。",
        snippet="RATE_LIMIT_ENABLED: true\nRATE_LIMIT_PER_MINUTE: 120\n",
    )


def _check_vector_store(kb) -> Check:
    vs_type = (getattr(kb, "DEFAULT_VS_TYPE", "faiss") or "faiss").lower()
    if vs_type == "faiss":
        return Check(
            key="vector_store",
            title="向量库 DEFAULT_VS_TYPE",
            severity="critical",
            summary="默认向量库是 FAISS（进程内），无法跨 worker / 副本共享；切 KB 会整库 reload。",
            impact=(
                "FAISS 索引在每个 worker 进程各加载一份，内存放大 N 倍；"
                "写入必须停读；索引更新也难以做热加载。"
            ),
            fix_hint="推荐 Milvus（独立集群）或 pgvector（共用已有 Postgres）。",
            snippet=(
                "DEFAULT_VS_TYPE: milvus\n"
                "kbs_config:\n"
                "  milvus:\n"
                "    host: 127.0.0.1\n"
                "    port: '19530'\n"
            ),
        )
    if vs_type in ("milvus", "zilliz", "pg", "relyt", "es"):
        return Check(
            key="vector_store",
            title="向量库 DEFAULT_VS_TYPE",
            severity="ok",
            summary=f"使用 {vs_type}，可横向扩展。",
        )
    return Check(
        key="vector_store",
        title="向量库 DEFAULT_VS_TYPE",
        severity="info",
        summary=f"使用 {vs_type}，请自行确认该后端在 100+ QPS 下的表现。",
    )


def _check_llm_backend(model) -> Check:
    platforms = list(getattr(model, "MODEL_PLATFORMS", []) or [])
    if not platforms:
        return Check(
            key="llm_backend",
            title="LLM 后端 MODEL_PLATFORMS",
            severity="critical",
            summary="未配置任何模型平台。",
            fix_hint="去「⑤ 模型配置」添加至少一个 MODEL_PLATFORMS 条目。",
        )

    def _field(p: Any, key: str, default: Any = None) -> Any:
        # MODEL_PLATFORMS 在运行时可能是 dict（yaml 原样）或 PlatformConfig pydantic 模型；
        # 统一用 getattr / mapping 兼容两种形态，避免 AttributeError: 'PlatformConfig' has no attribute 'get'
        if isinstance(p, dict):
            return p.get(key, default)
        return getattr(p, key, default)

    has_vllm_like = any(
        (_field(p, "platform_type") or "").lower()
        in ("openai", "xinference", "custom openai", "custom_openai")
        for p in platforms
    )
    ollama_only = all(
        (_field(p, "platform_type") or "").lower() == "ollama"
        for p in platforms
    )
    low_concurrency = any(
        int(_field(p, "api_concurrencies", 5) or 5) < 16
        for p in platforms
    )

    if ollama_only:
        return Check(
            key="llm_backend",
            title="LLM 后端 MODEL_PLATFORMS",
            severity="warning",
            summary="全部模型平台都是 Ollama；Ollama 单模型串行调度，5000 人会严重排队。",
            impact=(
                "Ollama 没有 continuous batching / PagedAttention，"
                "多用户同时请求同一模型会按顺序串行执行。"
            ),
            fix_hint="生产推荐 vLLM / TGI（OpenAI 兼容接口），并在后端做多副本负载均衡。",
            snippet=(
                "MODEL_PLATFORMS:\n"
                "  - platform_name: vllm\n"
                "    platform_type: openai\n"
                "    api_base_url: http://127.0.0.1:8000/v1\n"
                "    api_key: EMPTY\n"
                "    api_concurrencies: 128\n"
                "    llm_models: [qwen2.5-14b]\n"
            ),
        )
    if low_concurrency:
        return Check(
            key="llm_backend",
            title="LLM 后端 MODEL_PLATFORMS",
            severity="warning",
            summary="至少一个平台的 api_concurrencies < 16，会限制整体并发。",
            fix_hint=(
                "vLLM/TGI 真实可并发 > 100，这里的 api_concurrencies 决定客户端侧限制，"
                "可以直接调到 64 或 128。"
            ),
        )
    if has_vllm_like:
        return Check(
            key="llm_backend",
            title="LLM 后端 MODEL_PLATFORMS",
            severity="ok",
            summary="已配置 OpenAI 兼容后端（vLLM/TGI/xinference 等），适合高并发。",
        )
    return Check(
        key="llm_backend",
        title="LLM 后端 MODEL_PLATFORMS",
        severity="info",
        summary="模型平台配置已存在，请根据业务并发挑选 api_concurrencies。",
    )


def _check_health_endpoints(bs) -> Check:
    """探活 API 自身 /healthz /readyz 是否已经在线。"""
    import socket
    import urllib.request

    api = dict(getattr(bs, "API_SERVER", {}) or {})
    host = (api.get("host") or "127.0.0.1").strip()
    port = api.get("port") or 62581
    if host == "0.0.0.0":
        host = "127.0.0.1"

    # 先简单 socket 探活
    try:
        with socket.create_connection((host, int(port)), timeout=0.6):
            pass
    except OSError:
        return Check(
            key="health",
            title="健康检查端点 /healthz /readyz",
            severity="info",
            summary=f"当前 API {host}:{port} 未在运行，体检无法确认 /readyz 是否通。",
        )

    try:
        req = urllib.request.Request(f"http://{host}:{port}/healthz", method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status == 200:
                # 再尝试 /readyz
                try:
                    req2 = urllib.request.Request(
                        f"http://{host}:{port}/readyz", method="GET"
                    )
                    with urllib.request.urlopen(req2, timeout=2.0) as resp2:
                        ok = resp2.status == 200
                        return Check(
                            key="health",
                            title="健康检查端点 /healthz /readyz",
                            severity="ok" if ok else "warning",
                            summary=(
                                "/healthz 200，/readyz 200。Nginx/k8s 可直接挂 active probe。"
                                if ok
                                else "/healthz 200 但 /readyz 未就绪，请检查 DB / Redis。"
                            ),
                        )
                except Exception as e:  # noqa: BLE001
                    return Check(
                        key="health",
                        title="健康检查端点 /healthz /readyz",
                        severity="warning",
                        summary=f"/healthz 通但 /readyz 失败：{e}",
                    )
    except Exception as e:  # noqa: BLE001
        return Check(
            key="health",
            title="健康检查端点 /healthz /readyz",
            severity="warning",
            summary=(
                f"API 运行中但 /healthz 未命中（{e}）。可能当前进程是旧版本，"
                "执行「立即重启」加载最新代码。"
            ),
        )

    return Check(
        key="health",
        title="健康检查端点 /healthz /readyz",
        severity="info",
        summary="未能完成探测。",
    )


def _check_metrics(bs) -> Check:
    enabled = bool(getattr(bs, "METRICS_ENABLED", True))
    try:
        import prometheus_client  # type: ignore  # noqa: F401
        has_pkg = True
    except ImportError:
        has_pkg = False

    if not has_pkg:
        return Check(
            key="metrics",
            title="Prometheus 指标 /metrics",
            severity="warning",
            summary="未安装 prometheus-client，/metrics 暂无数据。",
            impact=(
                "没有指标就没法做流量预警 / 延迟回归 / 错误率告警；"
                "5000 并发下出问题只能看日志回溯，排障效率非常差。"
            ),
            fix_hint="安装后重启即可自动开始埋点：`pip install prometheus-client`。",
            snippet="pip install prometheus-client\n",
            snippet_lang="bash",
        )
    if not enabled:
        return Check(
            key="metrics",
            title="Prometheus 指标 /metrics",
            severity="info",
            summary="METRICS_ENABLED=false，已主动关闭指标埋点。",
            fix_hint="如要开启，「⑨ 可观测性 / 弹性」里把 METRICS_ENABLED 打开。",
            snippet="METRICS_ENABLED: true\n",
        )
    return Check(
        key="metrics",
        title="Prometheus 指标 /metrics",
        severity="ok",
        summary=(
            "已开启：QPS / latency / in-flight / LLM 错误率 都会自动上报；"
            "Prometheus 侧直接抓 /metrics 即可。"
        ),
    )


def _check_json_logs(bs) -> Check:
    json_logs = bool(getattr(bs, "JSON_LOGS", False))
    mode = (getattr(bs, "DEPLOYMENT_MODE", "dev") or "dev").lower()
    if json_logs:
        return Check(
            key="json_logs",
            title="结构化日志 JSON_LOGS",
            severity="ok",
            summary="已开启 JSON 日志，每条日志自带 request_id，Loki / ELK 友好。",
        )
    sev = "warning" if mode == "prod" else "info"
    return Check(
        key="json_logs",
        title="结构化日志 JSON_LOGS",
        severity=sev,
        summary="当前为纯文本日志。生产建议切 JSON，方便集中检索与告警。",
        fix_hint="「⑨ 可观测性 / 弹性」把 JSON_LOGS 打开即可，request_id 自动注入每条日志。",
        snippet="JSON_LOGS: true\n",
    )


def _check_otel(bs) -> Check:
    enabled = bool(getattr(bs, "OTEL_ENABLED", False))
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    try:
        import opentelemetry.sdk  # type: ignore  # noqa: F401
        has_sdk = True
    except ImportError:
        has_sdk = False

    if not enabled:
        return Check(
            key="otel",
            title="OpenTelemetry 链路追踪",
            severity="info",
            summary="OTEL_ENABLED=false，未启用链路追踪。",
            fix_hint=(
                "如需端到端看 API → LLM → 向量库链路，先 `pip install opentelemetry-sdk "
                "opentelemetry-exporter-otlp-proto-http opentelemetry-instrumentation-fastapi"
                " opentelemetry-instrumentation-sqlalchemy opentelemetry-instrumentation-requests`，"
                "再把 OTEL_ENABLED 打开，通过环境变量 OTEL_EXPORTER_OTLP_ENDPOINT 指向 collector。"
            ),
            snippet=(
                "OTEL_ENABLED: true\nOTEL_SERVICE_NAME: chayuan-api\n"
                "# 通过环境变量配置：\n"
                "# export OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318\n"
            ),
        )
    if not has_sdk:
        return Check(
            key="otel",
            title="OpenTelemetry 链路追踪",
            severity="warning",
            summary="OTEL_ENABLED=true 但 opentelemetry-sdk 未安装，实际上没在上报。",
            fix_hint="`pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http`",
            snippet=(
                "pip install opentelemetry-sdk \\\n"
                "  opentelemetry-exporter-otlp-proto-http \\\n"
                "  opentelemetry-instrumentation-fastapi \\\n"
                "  opentelemetry-instrumentation-sqlalchemy \\\n"
                "  opentelemetry-instrumentation-requests\n"
            ),
            snippet_lang="bash",
        )
    if not endpoint:
        return Check(
            key="otel",
            title="OpenTelemetry 链路追踪",
            severity="warning",
            summary=(
                "已启用但未配置 OTEL_EXPORTER_OTLP_ENDPOINT；spans 会被打到 localhost:4318/4317，"
                "大概率丢弃。"
            ),
            fix_hint="在启动 env 里设置 OTEL_EXPORTER_OTLP_ENDPOINT 指向 collector/agent。",
            snippet="export OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318\n",
            snippet_lang="bash",
        )
    return Check(
        key="otel",
        title="OpenTelemetry 链路追踪",
        severity="ok",
        summary=f"已启用，endpoint={endpoint}",
    )


def _check_llm_retry(bs) -> Check:
    attempts = int(getattr(bs, "LLM_RETRY_ATTEMPTS", 3) or 0)
    wait_max = float(getattr(bs, "LLM_RETRY_WAIT_MAX", 8.0) or 0.0)
    timeout = float(getattr(bs, "LLM_TIMEOUT_SECONDS", 120.0) or 0.0)
    if attempts <= 0:
        return Check(
            key="llm_retry",
            title="LLM 调用重试 / 超时",
            severity="warning",
            summary="LLM_RETRY_ATTEMPTS=0：LLM 抖动 / 偶发 5xx 会直接失败返给用户。",
            fix_hint=(
                "建议 ≥ 2 次重试（指数退避 + 抖动）。"
                "做为最后一道防线，会显著降低用户侧感知错误率。"
            ),
            snippet="LLM_RETRY_ATTEMPTS: 3\nLLM_RETRY_WAIT_MAX: 8\nLLM_TIMEOUT_SECONDS: 120\n",
        )
    if timeout <= 0:
        return Check(
            key="llm_retry",
            title="LLM 调用重试 / 超时",
            severity="warning",
            summary="LLM_TIMEOUT_SECONDS ≤ 0：没有超时意味着慢响应会一直占 worker 槽。",
            fix_hint="建议与流式体验匹配，例如 120 秒。",
            snippet="LLM_TIMEOUT_SECONDS: 120\n",
        )
    return Check(
        key="llm_retry",
        title="LLM 调用重试 / 超时",
        severity="ok",
        summary=(
            f"已开启重试：attempts={attempts}, wait_max={wait_max}s, timeout={timeout}s。"
        ),
    )


def _check_auth(bs) -> Check:
    """多用户鉴权配置体检。"""
    auth_required = bool(getattr(bs, "AUTH_REQUIRED", False))
    jwt_secret = (getattr(bs, "JWT_SECRET", "") or "").strip()
    mode = (getattr(bs, "DEPLOYMENT_MODE", "dev") or "dev").lower()
    workers = int(getattr(bs, "UVICORN_WORKERS", 1) or 1)

    if not auth_required and mode == "prod":
        return Check(
            key="auth",
            title="多用户鉴权",
            severity="critical",
            summary="生产模式但 AUTH_REQUIRED=false：任何人都可以访问 chat / kb 端点并看别人的数据。",
            fix_hint=(
                "开启 AUTH_REQUIRED，并通过「用户管理」或 /auth/register 创建账号；"
                "外部调用方需要携带 Bearer token。"
            ),
            snippet="AUTH_REQUIRED: true\nJWT_SECRET: \"use-a-long-random-string\"\n",
        )

    if not auth_required:
        return Check(
            key="auth",
            title="多用户鉴权",
            severity="info",
            summary="AUTH_REQUIRED=false：匿名可访问（单机 / 体验场景合理）。",
            fix_hint="多租户或对外暴露前记得打开。",
        )

    if not jwt_secret and (workers > 1 or mode == "prod"):
        return Check(
            key="auth",
            title="多用户鉴权",
            severity="critical",
            summary="JWT_SECRET 未配置：每次启动都会重新生成临时密钥，多 worker / 多副本互相不认同一 token。",
            fix_hint=(
                "在 basic_settings.yaml 或环境变量里配置一个长随机串，所有副本共用。"
            ),
            snippet="JWT_SECRET: \"<不可预测的 64 位字符串>\"\n",
        )

    return Check(
        key="auth",
        title="多用户鉴权",
        severity="ok",
        summary=(
            f"AUTH_REQUIRED=true, JWT_SECRET={'已配置' if jwt_secret else '进程内自动生成'}；"
            f"access ttl={int(getattr(bs, 'JWT_ACCESS_TTL_SECONDS', 0) or 0)}s。"
        ),
    )


def _check_ingest_queue(bs) -> Check:
    enabled = bool(getattr(bs, "INGEST_ASYNC_ENABLED", False))
    redis_url = (getattr(bs, "REDIS_URL", "") or "").strip()
    if not enabled:
        return Check(
            key="ingest_queue",
            title="异步入库队列",
            severity="info",
            summary="INGEST_ASYNC_ENABLED=false：上传文件会占用 API 进程做向量化，易被大文件卡住。",
            fix_hint=(
                "生产建议开启：pip install arq、REDIS_URL 就位，然后 `chayuan worker` 起消费者。"
            ),
            snippet="INGEST_ASYNC_ENABLED: true\n",
        )

    missing = []
    try:
        import arq  # noqa: F401
    except ImportError:
        missing.append("arq")
    if not redis_url:
        missing.append("REDIS_URL")

    if missing:
        return Check(
            key="ingest_queue",
            title="异步入库队列",
            severity="critical",
            summary=f"已启用但缺少依赖：{', '.join(missing)}。此时 upload_docs 会降级同步运行。",
            fix_hint="补齐依赖与 REDIS_URL，或暂时关掉 INGEST_ASYNC_ENABLED 避免误导。",
        )

    return Check(
        key="ingest_queue",
        title="异步入库队列",
        severity="ok",
        summary=(
            f"队列就绪：arq 已安装，REDIS_URL 已配置，"
            f"max_jobs={int(getattr(bs, 'ARQ_MAX_JOBS', 10) or 10)}。"
            "记得单独跑 `chayuan worker` 作为消费者。"
        ),
    )


def _check_semantic_cache(bs) -> Check:
    enabled = bool(getattr(bs, "SEMANTIC_CACHE_ENABLED", False))
    redis_url = (getattr(bs, "REDIS_URL", "") or "").strip()
    if not enabled:
        return Check(
            key="semcache",
            title="语义缓存",
            severity="info",
            summary="SEMANTIC_CACHE_ENABLED=false：FAQ 类重复问题每次都打 LLM，浪费 token + 延迟。",
            fix_hint=(
                "客服 / 知识库场景强烈建议开启；命中时直接返回历史答案。"
                "key 里带 user_id/kb/model，不会串权限。"
            ),
            snippet="SEMANTIC_CACHE_ENABLED: true\nSEMANTIC_CACHE_TTL_SECONDS: 900\n",
        )

    if not redis_url:
        return Check(
            key="semcache",
            title="语义缓存",
            severity="warning",
            summary="已启用但 REDIS_URL 空，缓存每次都会静默 miss。",
            fix_hint="配置 REDIS_URL 或关闭该开关。",
        )

    ttl = int(getattr(bs, "SEMANTIC_CACHE_TTL_SECONDS", 900) or 0)
    return Check(
        key="semcache",
        title="语义缓存",
        severity="ok",
        summary=f"缓存已启用；TTL={ttl}s，命名空间={getattr(bs, 'SEMANTIC_CACHE_NAMESPACE', '') or 'chayuan:semcache'}。",
    )


def _check_reverse_proxy(bs) -> Check:
    host = (getattr(bs, "API_SERVER", {}) or {}).get("host", "127.0.0.1")
    if host in ("127.0.0.1", "localhost"):
        return Check(
            key="proxy",
            title="反向代理 / TLS",
            severity="info",
            summary="API 当前绑定 127.0.0.1，预期通过反向代理（nginx/traefik）对外暴露。",
            fix_hint="生产请挂 nginx/traefik + TLS + HTTP/2 + gzip + 粘性会话。",
            snippet=(
                "# nginx 关键片段\n"
                "upstream chayuan_api { least_conn;\n"
                "  server 127.0.0.1:62581; }\n"
                "server { listen 443 ssl http2;\n"
                "  location / { proxy_pass http://chayuan_api; proxy_http_version 1.1;\n"
                "    proxy_set_header Connection ''; proxy_buffering off;\n"
                "    proxy_read_timeout 600s; } }\n"
            ),
            snippet_lang="nginx",
        )
    return Check(
        key="proxy",
        title="反向代理 / TLS",
        severity="warning",
        summary=f"API 直接绑定 {host}，直接暴露到公网没有 TLS / 限流 / keepalive 优化。",
        fix_hint="把 API 改回 127.0.0.1，前面加 nginx/traefik 处理 TLS 与限流。",
    )


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------

def build_report() -> Report:
    from chayuan.settings import Settings

    bs = Settings.basic_settings
    kb = Settings.kb_settings
    model = Settings.model_settings

    checks = [
        _check_deployment_mode(bs),
        _check_database(bs),
        _check_db_pool(bs),
        _check_uvicorn_workers(bs),
        _check_redis(bs),
        _check_rate_limit(bs),
        _check_vector_store(kb),
        _check_llm_backend(model),
        _check_reverse_proxy(bs),
        _check_health_endpoints(bs),
        _check_metrics(bs),
        _check_json_logs(bs),
        _check_otel(bs),
        _check_llm_retry(bs),
        _check_auth(bs),
        _check_ingest_queue(bs),
        _check_semantic_cache(bs),
    ]
    checks.sort(key=lambda c: (SEVERITY_ORDER.get(c.severity, 99), c.key))

    mode = (getattr(bs, "DEPLOYMENT_MODE", "dev") or "dev").lower()
    return Report(checks=checks, mode=mode)


# ---------------------------------------------------------------------------
# UI 渲染
# ---------------------------------------------------------------------------

def render_scalability(ui) -> None:
    """在已建立 NiceGUI 页面上下文中调用。

    自 v1.0 起本页不再只渲染 ``scalability`` 静态体检，而是复用
    :mod:`chayuan.server.config_panel.health` 的统一多维健康报告：

    - 分类 chips（配置 / 资源 / 运行时 / 连通性 / 服务 / 性能扩展）；
    - 每个分类一个可折叠面板，默认展开出问题的类别，达标类别收起；
    - 顶部 action bar 支持「重新体检 / 一键修复」；
    - Redis 卡片、生产部署资源、运行时特性说明保持原位。

    与 CLI 侧 ``chayuan doctor`` 完全同源。
    """
    from chayuan.server.config_panel import health as _health

    ui.label("性能与可扩展性 · 多维健康体检").classes(
        "text-2xl font-semibold q-mb-sm"
    )
    ui.label(
        "统一走 health 引擎跑 config / resource / runtime / connectivity / api / scale"
        "六大维度；有 critical / warning 时会在上方汇总。可点「一键修复」让系统"
        "自动执行可修复条目（幂等），也可以在 CLI 里 `chayuan doctor --fix`。"
    ).classes("text-sm text-grey-8 q-mb-md")

    # Redis 编辑入口不放在性能页，避免 REDIS_URL 多处编辑。
    with ui.card().classes("w-full q-pa-md q-mt-sm").style(
        "background:#eef6ff;border-left:4px solid #2185d0"
    ):
        with ui.row().classes("items-center w-full no-wrap").style("gap:10px"):
            ui.icon("info").classes("text-info").style("font-size:24px;flex:none")
            with ui.column().classes("flex-1 q-gutter-none"):
                ui.label("Redis 连接配置").classes(
                    "text-base font-semibold"
                )
                ui.label(
                    "本页只保留性能/可扩展性"
                    "相关参数（连接池、worker 数、并发上限等），不再重复 Redis 编辑面板。"
                ).classes("text-xs text-grey-8")

    # —— 健康报告容器：重新体检 / 一键修复后会清空重建 ——
    report_container = ui.column().classes("w-full")

    def _render_report_into(container) -> None:
        container.clear()
        try:
            report = _health.build_report()
        except Exception as e:  # noqa: BLE001
            logger.exception("health.build_report failed")
            with container:
                ui.label(f"体检失败：{type(e).__name__}: {e}").classes(
                    "text-negative"
                )
            return
        with container:
            _render_health_report(ui, report, on_refresh=lambda: _render_report_into(container))

    _render_report_into(report_container)

    # 底部资源 / 模板
    with ui.card().classes("w-full q-mt-md"):
        ui.label("生产部署资源").classes("text-base font-semibold")
        ui.label(
            "项目已内置一份 5000 并发生产模板（Postgres / Redis / Milvus / vLLM / "
            "Nginx + TLS），含 docker-compose + 配置示例："
        ).classes("text-sm text-grey-8")
        with ui.column().classes("q-mt-sm"):
            for p, desc in [
                ("docs/scalability.md", "深度对标分析、容量模型、验收标准"),
                ("docker/prod/README.md", "生产目录总览"),
                ("docker/prod/docker-compose.prod.yaml", "完整 compose：pg/redis/milvus/vllm/tei/nginx"),
                ("docker/prod/nginx.conf", "反代：TLS / HTTP2 / 限流 / 流式优化"),
                ("docker/prod/chayuan/basic_settings.yaml", "生产模式基础配置示例"),
                ("docker/prod/chayuan/kb_settings.yaml", "Milvus 知识库配置示例"),
                ("docker/prod/chayuan/model_settings.yaml", "vLLM + TEI 模型平台示例"),
            ]:
                with ui.row().classes("items-center no-wrap q-gutter-xs"):
                    ui.icon("description").classes("text-grey-7")
                    ui.label(p).classes("text-xs font-mono")
                    ui.label(f"— {desc}").classes("text-xs text-grey-7")

    # 可观测性 / 中间件说明
    with ui.card().classes("w-full q-mt-md"):
        ui.label("已启用的运行时特性").classes("text-base font-semibold")
        ui.label(
            "除了静态配置检查，本次升级还向 API 进程内置了下列通用能力（无需额外配置）："
        ).classes("text-sm text-grey-8 q-mb-sm")
        for bullet in [
            "X-Request-ID 中间件：每个请求自动贴 UUID；通过 contextvar 注入每条 logging 输出。",
            "JSON_LOGS=true：logging 输出切为单行 JSON，自带 request_id / level / logger / msg。",
            "令牌桶限流：RATE_LIMIT_ENABLED=true 时生效，Redis 优先、内存降级。",
            "/healthz：进程存活探针（nginx / k8s liveness 直接用）。",
            "/readyz：探活 DB + Redis，不就绪返回 503 让 LB 摘流。",
            "Prometheus 指标：PrometheusMetricsMiddleware 自动埋 QPS / latency / in-flight；"
            "LangChain MetricsCallbackHandler 埋 LLM 调用数 / 耗时 / 成功失败。",
            "OpenTelemetry（OTEL_ENABLED=true）：按 OTLP 上报 traces，自动 instrument "
            "FastAPI / SQLAlchemy / requests。",
            "LLM 重试：LLM_RETRY_ATTEMPTS / LLM_RETRY_WAIT_MAX / LLM_TIMEOUT_SECONDS 控制"
            "指数退避 + 抖动；通过 resilience.llm_retry 装饰器显式启用。",
            "多用户鉴权（AUTH_REQUIRED=true）：JWT access+refresh，chat / kb_chat 按 user_id 隔离；"
            "KB 支持 owner + explicit grant + public 三种可见性。",
            "异步入库队列（INGEST_ASYNC_ENABLED=true）：upload_docs 只落盘后入 Arq 队列，"
            "`chayuan worker` 作消费者做解析 + 向量化；Redis / arq 缺失时自动同步降级。",
            "语义缓存（SEMANTIC_CACHE_ENABLED=true）：非流式 chat / kb_chat 短 query 命中直接返回，"
            "key 带 user_id/kb/model，避免串权限。",
        ]:
            ui.label("• " + bullet).classes("text-xs")


def _render_health_report(ui, report, *, on_refresh) -> None:
    """把一份 :class:`HealthReport` 渲染成「汇总 + action bar + 按类折叠 + 详情」。

    ``on_refresh`` 由外层提供：重新调用 ``build_report`` 并替换 DOM。
    """
    from chayuan.server.config_panel import health as _health

    counts = report.counts
    n_crit = counts.get("critical", 0)
    n_warn = counts.get("warning", 0)
    n_info = counts.get("info", 0)
    n_ok = counts.get("ok", 0)
    fixables = report.fixable_checks()

    # ---- 顶部汇总 ----
    with ui.card().classes("w-full q-mt-md"):
        with ui.row().classes("w-full items-center"):
            ui.label("健康报告").classes("text-base font-semibold")
            ui.space()
            ui.badge(f"部署模式：{report.mode}").props(
                "color=" + ("green" if report.mode == "prod" else "grey")
            )
            ui.badge(f"耗时 {report.elapsed_ms} ms").props("color=grey-5").tooltip(
                f"@ {report.generated_at}"
            )
        with ui.row().classes("w-full q-mt-sm q-gutter-md"):
            ui.chip(f"严重 {n_crit}", icon="error").props(
                "color=negative text-color=white" if n_crit else "color=grey-4"
            )
            ui.chip(f"警告 {n_warn}", icon="warning").props(
                "color=warning text-color=white" if n_warn else "color=grey-4"
            )
            ui.chip(f"建议 {n_info}", icon="info").props(
                "color=info text-color=white" if n_info else "color=grey-4"
            )
            ui.chip(f"达标 {n_ok}", icon="check_circle").props(
                "color=positive text-color=white" if n_ok else "color=grey-4"
            )
        with ui.row().classes("w-full q-mt-sm items-center"):
            ui.label("预计可承载同时在线：").classes("text-sm text-grey-8")
            ui.label(report.est_concurrent_users).classes(
                "text-sm font-semibold "
                + ("text-negative" if n_crit else "text-primary")
            )

        # Action bar
        with ui.row().classes("w-full q-mt-sm items-center no-wrap").style("gap:8px"):
            ui.space()
            ui.button(
                "重新体检", icon="refresh", on_click=on_refresh,
            ).props("flat dense color=primary").tooltip("重新抓取所有检查结果")
            fix_btn = ui.button(
                f"一键修复（{len(fixables)} 项可修复）",
                icon="auto_fix_high",
                on_click=lambda: _on_fix_all(ui, report, on_refresh),
            ).props(
                f"color={'negative' if n_crit else 'primary'} dense"
            ).tooltip(
                "对所有可修复条目依次执行 fixer（生成面板凭据 / 创建目录 / pip install 依赖等），"
                "修完自动重新体检。"
            )
            if not fixables:
                fix_btn.disable()

    # ---- 按分类折叠展示 ----
    by_cat = report.by_category()
    for cat_key in sorted(by_cat, key=lambda c: _health.CATEGORIES.get(c, {}).get("order", 99)):
        entries = by_cat.get(cat_key) or []
        if not entries:
            continue
        meta = _health.CATEGORIES.get(cat_key, {})
        cat_label = meta.get("label", cat_key)
        cat_icon = meta.get("icon", "check_circle")
        cat_desc = meta.get("desc", "")
        cat_counts = {"critical": 0, "warning": 0, "info": 0, "ok": 0}
        for c in entries:
            cat_counts[c.severity] = cat_counts.get(c.severity, 0) + 1

        # 默认：只要该分类里存在 critical/warning/info 就展开；全 ok 时默认折叠。
        has_issue = cat_counts["critical"] or cat_counts["warning"] or cat_counts["info"]
        # 组合分类标题：图标 + 名称 + 严重/警告徽章 + 描述
        header_text = (
            f"{cat_label}   "
            + (f"严重 {cat_counts['critical']}  " if cat_counts['critical'] else "")
            + (f"警告 {cat_counts['warning']}  " if cat_counts['warning'] else "")
            + (f"建议 {cat_counts['info']}  " if cat_counts['info'] else "")
            + (f"达标 {cat_counts['ok']}" if cat_counts['ok'] else "")
        )
        with ui.expansion(header_text, icon=cat_icon, value=bool(has_issue)).classes(
            "w-full q-mt-sm"
        ):
            if cat_desc:
                ui.label(cat_desc).classes("text-xs text-grey-7 q-mb-sm")
            for c in entries:
                _render_single_check(ui, c, on_refresh=on_refresh)

    # ---- CLI 提示 ----
    # 只保留一个「在命令行里同样能跑」的速查卡；外层 render_scalability 里保留的
    # 「生产部署资源 / 已启用的运行时特性」不动，避免重复展示。
    with ui.card().classes("w-full q-mt-md"):
        ui.label("在命令行里同样能跑").classes("text-base font-semibold")
        ui.label(
            "本体检引擎与 `chayuan doctor` 同源。CI / k8s liveness 建议直接用 CLI "
            "版本，`--json` 输出可被监控平台直接解析。"
        ).classes("text-sm text-grey-8 q-mb-sm")
        _render_snippet(
            ui,
            "chayuan doctor                           # 彩色文本\n"
            "chayuan doctor --verbose                 # 展开影响 / 建议 / snippet\n"
            "chayuan doctor --category config,runtime # 按分类过滤\n"
            "chayuan doctor --fix                     # 跑完自动修复可修复项\n"
            "chayuan doctor --json | jq '.counts'     # 机器可读\n"
            "chayuan doctor --fail-on critical        # CI / probe：critical 时 exit 2\n",
            "bash",
        )


def _render_single_check(ui, c, *, on_refresh) -> None:
    """渲染一条检查：icon + 标题 + 严重度 badge + 可修复按钮 + 说明 / snippet。"""
    meta = SEVERITY_META.get(c.severity, SEVERITY_META["info"])
    with ui.card().classes("w-full q-mb-sm"):
        with ui.row().classes("w-full items-center no-wrap"):
            ui.icon(meta["icon"]).classes(f"text-{meta['color']} text-lg")
            ui.label(c.title).classes("text-base font-semibold")
            ui.badge(c.id).props("color=grey-4 outline").classes("q-ml-sm").tooltip(
                "体检项稳定标识（可在 CLI `--fix-id` 里引用）"
            )
            ui.space()
            ui.badge(meta["label"]).props(f"color={meta['color']}")
            if c.fixable:
                ui.button(
                    "修复", icon="auto_fix_high",
                    on_click=lambda _=None, check=c: _on_fix_one(ui, check, on_refresh),
                ).props("color=primary dense flat").tooltip(
                    f"执行 fixer：{c.fixer_id}"
                )
        ui.label(c.summary).classes("text-sm")
        if c.impact:
            ui.label("影响：" + c.impact).classes("text-xs text-grey-8")
        if c.fix_hint:
            ui.label("建议：" + c.fix_hint).classes("text-xs text-grey-8")
        if c.snippet:
            _render_snippet(ui, c.snippet, c.snippet_lang)
        if c.id == "scale.redis" or c.id == "connectivity.redis":
            ui.label("👉 可在本页顶部「Redis 连接配置」直接修改并验证。").classes(
                "text-xs text-primary"
            )


def _on_fix_one(ui, check, on_refresh) -> None:
    """执行单条 fixer 并 notify + 刷新。"""
    from chayuan.server.config_panel.health import run_fixer
    result = run_fixer(check.fixer_id)
    msg = str(result.get("message") or "")
    color = "positive" if result.get("ok") else "negative"
    ui.notify(f"[{check.id}] {msg}", color=color, timeout=8000)
    try:
        on_refresh()
    except Exception:  # noqa: BLE001
        pass


def _on_fix_all(ui, report, on_refresh) -> None:
    """对所有可修复项（scale 分类除外，避免自动改性能配置）跑 fixer。"""
    from chayuan.server.config_panel.health import run_fixers
    fids = [c.fixer_id for c in report.fixable_checks() if c.category != "scale"]
    if not fids:
        ui.notify("没有可自动修复的项。", color="info")
        return
    results = run_fixers(fids)
    oks = sum(1 for r in results if r.get("ok"))
    failed = [r for r in results if not r.get("ok")]
    if failed:
        ui.notify(
            f"{oks}/{len(results)} 项已修复；失败 {len(failed)} 项，请查看下方日志。",
            color="warning", timeout=8000,
        )
        for r in failed:
            ui.notify(f"[{r.get('fixer_id', '?')}] {r.get('message','')}",
                      color="negative", timeout=10000)
    else:
        ui.notify(f"已修复全部 {oks} 项。", color="positive", timeout=6000)
    try:
        on_refresh()
    except Exception:  # noqa: BLE001
        pass


def _render_snippet(ui, text: str, lang: str) -> None:
    with ui.row().classes("w-full items-start q-mt-sm no-wrap"):
        ui.code(text, language=lang).classes("flex-1").style(
            "white-space:pre;overflow-x:auto;font-size:12px"
        )
        ui.button(
            icon="content_copy",
            on_click=lambda t=text: _copy(ui, t),
        ).props("flat round dense").tooltip("复制到剪贴板")


def _copy(ui, text: str) -> None:
    try:
        # NiceGUI 1.4 的 run_javascript 仍可用
        ui.run_javascript(f"navigator.clipboard.writeText({text!r})")
        ui.notify("已复制到剪贴板", color="info")
    except Exception as e:  # noqa: BLE001
        ui.notify(f"复制失败：{e}", color="negative")
