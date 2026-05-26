"""服务配置页的「服务探针」抽象。

每个外部服务实现一个 ``ServiceCheck``：
- ``probe()``：返回 ``ServiceStatus``，供拓扑图 / 卡片展示
- ``describe_config()``：告诉 UI 该服务要问用户哪些字段、当前 yaml 里填了什么

与 ``server/config_panel/health.py``（深度健康体检，进 doctor 报告）有区别：
- health.py 是**运维时诊断**，侧重可观测 / 修复建议
- service_checks 是**首次配置向导**，侧重「是否就绪、去哪里填、能不能现在就测一下」

设计约定：
- 只读，不改任何文件；编辑 / 保存由 UI 另走 yaml_store
- 所有探测都带短超时（1-3s），永不因外部服务超慢拖累面板
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple


logger = logging.getLogger("chayuan.service_checks")


ServiceLevel = Literal["critical", "recommended", "optional"]


@dataclass
class ServiceStatus:
    service: str                   # 稳定 id，如 "db" / "redis" / "vs" / "llm" / "embed"
    label: str                     # 中文展示名
    level: ServiceLevel
    ok: bool                       # 是否就绪（critical 不 OK → 整个系统不能用）
    configured: bool               # 用户是否填过（区分「没填」vs「填了但连不上」）
    endpoint: str = ""             # 当前连接串脱敏后
    detail: str = ""               # 一句话人类可读状态 / 错误摘要
    latency_ms: Optional[int] = None
    fields: List[Dict[str, Any]] = field(default_factory=list)
    # fields 是 UI 渲染弹窗用的元数据，每项: {name, label, widget, value_masked, help}

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ServiceCheck:
    """子类实现 ``probe()``；构造时接收 ``basic_settings / model_settings`` 等。"""
    service: str = ""
    label: str = ""
    level: ServiceLevel = "optional"

    def probe(self) -> ServiceStatus:  # pragma: no cover - 子类实现
        raise NotImplementedError


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

class DBServiceCheck(ServiceCheck):
    service = "db"
    label = "主业务数据库"
    level = "critical"

    def probe(self) -> ServiceStatus:
        from chayuan.server.config_panel.db_config import ensure_database_from_uri
        from chayuan.settings import Settings

        bs = Settings.basic_settings
        uri = getattr(bs, "SQLALCHEMY_DATABASE_URI", "") or ""
        configured = bool(uri and uri.strip() and not uri.startswith("sqlite:///:memory"))

        masked = _mask_uri(uri)
        endpoint = masked

        if not configured:
            return ServiceStatus(
                service=self.service, label=self.label, level=self.level,
                ok=False, configured=False, endpoint=endpoint,
                detail="尚未填写 SQLALCHEMY_DATABASE_URI",
                fields=_fields_uri_like(uri),
            )

        try:
            res = ensure_database_from_uri(uri, timeout=3)
            ok = bool(res.get("ok"))
            detail = res.get("message") or ("连接成功" if ok else "连接失败")
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {e}"

        return ServiceStatus(
            service=self.service, label=self.label, level=self.level,
            ok=ok, configured=True, endpoint=endpoint, detail=detail,
            fields=_fields_uri_like(uri),
        )


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------

class RedisServiceCheck(ServiceCheck):
    service = "redis"
    label = "Redis（可选）"
    level = "recommended"

    def probe(self) -> ServiceStatus:
        from chayuan.settings import Settings

        url = (getattr(Settings.basic_settings, "REDIS_URL", "") or "").strip()
        configured = bool(url)
        if not configured:
            return ServiceStatus(
                service=self.service, label=self.label, level=self.level,
                ok=False, configured=False, endpoint="",
                detail="未配置 REDIS_URL（可选；开启后支持配置中心跨副本、限流、WS Hub fan-out）",
                fields=[{"name": "REDIS_URL", "label": "Redis URL", "widget": "text",
                         "value_masked": "", "help": "redis://host:6379/0"}],
            )

        ok, detail, latency = False, "", None
        # 探针走「同步 redis 客户端 + 短超时」，避开 asyncio.run() 在运行中 event loop 里会
        # 抛 RuntimeError、进而导致协程 `Redis.execute_command` 泄漏 (RuntimeWarning) 的坑。
        client = None
        try:
            import time
            try:
                import redis as _redis_sync  # redis-py 同步 API
            except Exception as e:  # noqa: BLE001
                return ServiceStatus(
                    service=self.service, label=self.label, level=self.level,
                    ok=False, configured=True, endpoint=_mask_uri(url),
                    detail=f"redis 包未安装：{type(e).__name__}: {e}",
                    fields=[{"name": "REDIS_URL", "label": "Redis URL", "widget": "text",
                             "value_masked": _mask_uri(url), "help": "redis://host:6379/0"}],
                )

            try:
                client = _redis_sync.Redis.from_url(
                    url,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                    retry_on_timeout=False,
                )
                t0 = time.monotonic()
                pong = client.ping()
                ok = bool(pong)
                latency = int((time.monotonic() - t0) * 1000)
                detail = "PING OK" if ok else "PING 返回假值"
            except Exception as e:  # noqa: BLE001
                detail = f"{type(e).__name__}: {e}"
        except BaseException as e:  # noqa: BLE001
            # 绝不让探针把面板/服务带崩
            detail = f"{type(e).__name__}: {e}"
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass

        return ServiceStatus(
            service=self.service, label=self.label, level=self.level,
            ok=ok, configured=True, endpoint=_mask_uri(url),
            detail=detail, latency_ms=latency,
            fields=[{"name": "REDIS_URL", "label": "Redis URL", "widget": "text",
                     "value_masked": _mask_uri(url), "help": "redis://host:6379/0"}],
        )


# ---------------------------------------------------------------------------
# 向量库
# ---------------------------------------------------------------------------

class VectorStoreServiceCheck(ServiceCheck):
    service = "vs"
    label = "向量库"
    level = "critical"

    def probe(self) -> ServiceStatus:
        from chayuan.settings import Settings

        kb = getattr(Settings, "kb_settings", None)
        default_vs = str(getattr(kb, "DEFAULT_VS_TYPE", "") or "")
        configured = bool(default_vs)

        if not configured:
            return ServiceStatus(
                service=self.service, label=self.label, level=self.level,
                ok=False, configured=False, endpoint="",
                detail="未选择向量库类型（DEFAULT_VS_TYPE）",
                fields=[{"name": "DEFAULT_VS_TYPE", "label": "向量库类型",
                         "widget": "select",
                         "choices": ["faiss", "milvus", "pg", "zilliz"],
                         "value_masked": "", "help": "入门推荐 faiss（本地文件，零依赖）"}],
            )

        detail = f"已选择 {default_vs}"
        endpoint = default_vs
        # faiss 是本地文件，视为 configured+ok；其它需要实际连接串在 kb_settings.kbs_config
        ok = True
        if default_vs in ("milvus", "zilliz", "pg"):
            cfg = getattr(kb, "kbs_config", {}) or {}
            if not cfg.get(default_vs):
                ok = False
                detail = f"{default_vs} 已选中，但 kbs_config.{default_vs} 未配置连接串"

        return ServiceStatus(
            service=self.service, label=self.label, level=self.level,
            ok=ok, configured=True, endpoint=endpoint, detail=detail,
            fields=[],
        )


# ---------------------------------------------------------------------------
# LLM 平台 / 默认模型
# ---------------------------------------------------------------------------

class LLMServiceCheck(ServiceCheck):
    service = "llm"
    label = "默认 LLM"
    level = "critical"

    def probe(self) -> ServiceStatus:
        from chayuan.settings import Settings
        from chayuan.server.utils import get_config_models

        default_model = str(getattr(Settings.model_settings, "DEFAULT_LLM_MODEL", "") or "")
        if not default_model:
            return ServiceStatus(
                service=self.service, label=self.label, level=self.level,
                ok=False, configured=False, endpoint="",
                detail="未配置 DEFAULT_LLM_MODEL",
                fields=[{"name": "DEFAULT_LLM_MODEL", "label": "默认 LLM 模型",
                         "widget": "text", "value_masked": "",
                         "help": "在模型配置里选一个 llm_models 里已注册的模型名"}],
            )

        try:
            models = get_config_models(model_type="llm")
            available = list(models.keys())
            ok = default_model in available
            platform = ""
            if ok:
                info = models[default_model]
                platform = info.get("platform_name") or info.get("platform_type") or ""
            detail = (f"OK（platform={platform}）" if ok else
                      f"DEFAULT_LLM_MODEL={default_model} 不在可用模型里，"
                      f"当前可用：{', '.join(available[:5])}"
                      + ("..." if len(available) > 5 else ""))
            return ServiceStatus(
                service=self.service, label=self.label, level=self.level,
                ok=ok, configured=True, endpoint=default_model, detail=detail,
                fields=[],
            )
        except Exception as e:  # noqa: BLE001
            return ServiceStatus(
                service=self.service, label=self.label, level=self.level,
                ok=False, configured=True, endpoint=default_model,
                detail=f"{type(e).__name__}: {e}", fields=[],
            )


# ---------------------------------------------------------------------------
# 嵌入模型
# ---------------------------------------------------------------------------

class EmbedServiceCheck(ServiceCheck):
    service = "embed"
    label = "默认 Embedding"
    level = "critical"

    def probe(self) -> ServiceStatus:
        from chayuan.settings import Settings
        from chayuan.server.utils import get_config_models

        default_model = str(getattr(Settings.model_settings, "DEFAULT_EMBEDDING_MODEL", "") or "")
        if not default_model:
            return ServiceStatus(
                service=self.service, label=self.label, level=self.level,
                ok=False, configured=False, endpoint="",
                detail="未配置 DEFAULT_EMBEDDING_MODEL", fields=[],
            )
        try:
            models = get_config_models(model_type="embed")
            ok = default_model in models
            detail = "OK" if ok else (
                f"DEFAULT_EMBEDDING_MODEL={default_model} 不在可用 embed 模型里："
                f"{', '.join(list(models.keys())[:5])}"
            )
            return ServiceStatus(
                service=self.service, label=self.label, level=self.level,
                ok=ok, configured=True, endpoint=default_model,
                detail=detail, fields=[],
            )
        except Exception as e:  # noqa: BLE001
            return ServiceStatus(
                service=self.service, label=self.label, level=self.level,
                ok=False, configured=True, endpoint=default_model,
                detail=f"{type(e).__name__}: {e}", fields=[],
            )


# ---------------------------------------------------------------------------
# 重排模型(reranker)
# ---------------------------------------------------------------------------

class RerankServiceCheck(ServiceCheck):
    """默认重排模型(91 题新增,与 LLM / Embedding 同档展示)。

    定级 ``recommended``:USE_RERANKER=False 时 RAG 也能跑(降级到向量分数排序),
    所以不强制必填;但 USE_RERANKER=True 又拿不到模型 → 标黄提醒。
    """
    service = "rerank"
    label = "默认 Rerank"
    level: ServiceLevel = "recommended"

    def probe(self) -> ServiceStatus:
        from chayuan.settings import Settings
        from chayuan.server.utils import get_config_models

        # 来源:kb_settings.RERANKER_MODEL(现有字段,不新增 schema)。
        # 89 题落地后会迁到 model_settings.capability_defaults.rerank,本期沿用旧字段。
        kb = Settings.kb_settings
        use_rerank = bool(getattr(kb, "USE_RERANKER", False))
        default_model = str(getattr(kb, "RERANKER_MODEL", "") or "").strip()

        if not default_model:
            return ServiceStatus(
                service=self.service, label=self.label, level=self.level,
                ok=not use_rerank,  # 不用 rerank → 没配也算 OK(降级 BM25)
                configured=False, endpoint="",
                detail=("USE_RERANKER=True 但未配置 RERANKER_MODEL"
                        if use_rerank else
                        "未启用重排(USE_RERANKER=False),向量分数直排"),
                fields=[{"name": "RERANKER_MODEL", "label": "默认 Rerank 模型",
                         "widget": "text", "value_masked": "",
                         "help": "在模型配置里选一个 rerank_models 里已注册的模型名"}],
            )

        try:
            models = get_config_models(model_type="rerank")
            available = list(models.keys())
            ok = default_model in available
            platform = ""
            if ok:
                info = models[default_model]
                platform = info.get("platform_name") or info.get("platform_type") or ""
            detail = (f"OK(platform={platform})" if ok else
                      f"RERANKER_MODEL={default_model} 不在可用模型里,"
                      f"当前可用:{', '.join(available[:5])}"
                      + ("..." if len(available) > 5 else ""))
            return ServiceStatus(
                service=self.service, label=self.label, level=self.level,
                ok=ok, configured=True, endpoint=default_model, detail=detail,
                fields=[],
            )
        except Exception as e:  # noqa: BLE001
            return ServiceStatus(
                service=self.service, label=self.label, level=self.level,
                ok=False, configured=True, endpoint=default_model,
                detail=f"{type(e).__name__}: {e}", fields=[],
            )


# ---------------------------------------------------------------------------
# 文件存储 / MinIO（按 FILE_STORAGE_BACKEND 动态定级）
# ---------------------------------------------------------------------------

class FileStorageServiceCheck(ServiceCheck):
    """文件存储后端状态。**定级随 ``FILE_STORAGE_BACKEND`` 动态变化**：

    - ``local``（默认）→ ``optional``：不需要配置，ok=True（本地磁盘），
      只做一行状态提示，不把它染红去逼着用户配。
    - ``minio``/``s3`` → ``recommended``：此时要求把 endpoint / 凭据填齐，
      任何一项缺失 / 连不通都会标红，引导用户回到服务管理页修复。
    """
    service = "minio"
    label = "文件存储（MinIO / 本地磁盘）"
    level: ServiceLevel = "optional"

    def probe(self) -> ServiceStatus:
        from chayuan.settings import Settings

        bs = Settings.basic_settings
        backend = str(getattr(bs, "FILE_STORAGE_BACKEND", "local") or "local").strip().lower()
        endpoint = str(getattr(bs, "MINIO_ENDPOINT", "") or "").strip()
        access_key = str(getattr(bs, "MINIO_ACCESS_KEY", "") or "").strip()
        secret_key = str(getattr(bs, "MINIO_SECRET_KEY", "") or "").strip()
        secure = bool(getattr(bs, "MINIO_SECURE", False))

        fields = [
            {"name": "FILE_STORAGE_BACKEND", "label": "后端",
             "widget": "select", "choices": ["local", "minio"],
             "value_masked": backend,
             "help": "local = 本机磁盘；minio = S3 兼容对象存储"},
            {"name": "MINIO_ENDPOINT", "label": "MinIO endpoint",
             "widget": "text", "value_masked": endpoint,
             "help": "形如 127.0.0.1:9000 或 https://s3.amazonaws.com"},
            {"name": "MINIO_ACCESS_KEY", "label": "Access Key",
             "widget": "text",
             "value_masked": _mask_key(access_key),
             "help": "MinIO console 生成；docker/dev-stack 默认 minioadmin"},
            {"name": "MINIO_SECRET_KEY", "label": "Secret Key",
             "widget": "password",
             "value_masked": ("*" * 6) if secret_key else "",
             "help": "对应 Secret；docker/dev-stack 默认 minioadmin"},
        ]

        # --- 分支 1：本地磁盘模式 ---
        if backend not in ("minio", "s3"):
            return ServiceStatus(
                service=self.service, label=self.label, level="optional",
                ok=True, configured=True,
                endpoint=f"local · {getattr(bs, 'FILE_STORAGE_LOCAL_ROOT', '') or '(默认路径)'}",
                detail=(
                    "当前使用本地磁盘，无需配置 MinIO。"
                    "如需分布式 / S3 预签名 URL / 跨节点共享，改为 minio 后填写下方字段。"
                ),
                fields=fields,
            )

        # --- 分支 2：minio 模式，按字段完整度 + 可达性判定 ---
        # 任何必填项缺失：configured=False（红色，critical 级）
        if not endpoint:
            return ServiceStatus(
                service=self.service, label=self.label, level="recommended",
                ok=False, configured=False, endpoint="",
                detail="已选 MinIO 后端，但 MINIO_ENDPOINT 未配置",
                fields=fields,
            )
        if not access_key or not secret_key:
            return ServiceStatus(
                service=self.service, label=self.label, level="recommended",
                ok=False, configured=False, endpoint=endpoint,
                detail="MinIO 凭据（ACCESS_KEY / SECRET_KEY）未填完整",
                fields=fields,
            )

        # --- 实际探活（minio-py 优先 → list_buckets；退回 TCP）---
        host, port, _secure = _parse_minio_endpoint_simple(endpoint, secure)
        ok, detail, latency = False, "", None
        try:
            import time
            try:
                from minio import Minio  # type: ignore
            except Exception:
                import socket as _socket
                t0 = time.monotonic()
                try:
                    with _socket.create_connection((host, port), timeout=2):
                        ok = True
                        detail = f"TCP 可达（{host}:{port}）；未安装 minio-py，凭据未验证"
                except OSError as e:
                    detail = f"{host}:{port} 不可达：{e}"
                latency = int((time.monotonic() - t0) * 1000)
            else:
                try:
                    raw_ep = endpoint
                    _sec = secure
                    if raw_ep.startswith("https://"):
                        raw_ep = raw_ep[len("https://"):]
                        _sec = True
                    elif raw_ep.startswith("http://"):
                        raw_ep = raw_ep[len("http://"):]
                        _sec = False
                    raw_ep = raw_ep.strip("/")
                    client = Minio(
                        raw_ep, access_key=access_key, secret_key=secret_key,
                        secure=_sec,
                        region=str(getattr(bs, "MINIO_REGION", "us-east-1") or "us-east-1"),
                    )
                    t0 = time.monotonic()
                    buckets = list(client.list_buckets())
                    latency = int((time.monotonic() - t0) * 1000)
                    ok, detail = True, f"连接 OK，可见 {len(buckets)} 个 bucket"
                except Exception as e:  # noqa: BLE001
                    detail = f"{type(e).__name__}: {e}"
        except BaseException as e:  # noqa: BLE001
            detail = f"{type(e).__name__}: {e}"

        return ServiceStatus(
            service=self.service, label=self.label, level="recommended",
            ok=ok, configured=True, endpoint=endpoint,
            detail=detail, latency_ms=latency,
            fields=fields,
        )


def _mask_key(key: str) -> str:
    """访问密钥脱敏：前 3 后 2 保留，中间打码。空串返回空。"""
    if not key:
        return ""
    if len(key) <= 5:
        return "*" * len(key)
    return f"{key[:3]}{'*' * (len(key) - 5)}{key[-2:]}"


def _parse_minio_endpoint_simple(
    endpoint: str, secure_hint: bool,
) -> Tuple[str, int, bool]:
    """和 ``service_page._parse_minio_endpoint`` 行为一致；独立拷贝避免跨模块循环导入。"""
    ep = (endpoint or "").strip()
    secure = bool(secure_hint)
    if ep.startswith("https://"):
        ep = ep[len("https://"):]
        secure = True
    elif ep.startswith("http://"):
        ep = ep[len("http://"):]
        secure = False
    ep = ep.strip("/")
    host, _, port_s = ep.partition(":")
    try:
        port = int(port_s) if port_s else (443 if secure else 9000)
    except ValueError:
        port = 443 if secure else 9000
    return host, port, secure


# ---------------------------------------------------------------------------
# kkFileView 旁车(文件预览服务)
# ---------------------------------------------------------------------------

class KkFileViewServiceCheck(ServiceCheck):
    """文件预览服务 kkFileView 旁车。可选服务:

    - 未配置 ``KKFILEVIEW_URL`` → optional + ok=True(客户端会回退内置 renderer,不挡路)
    - 已配置 → 探活 GET /,2xx/3xx 视为在线;失败标红
    """
    service = "kkfileview"
    label = "文件预览(kkFileView 旁车)"
    level: ServiceLevel = "optional"

    def probe(self) -> ServiceStatus:
        from chayuan.settings import Settings

        kb = getattr(Settings, "kb_settings", None)
        url = str(getattr(kb, "KKFILEVIEW_URL", "") or "").strip() if kb else ""
        fields = [{
            "name": "KKFILEVIEW_URL",
            "label": "kkFileView 地址",
            "widget": "text",
            "value_masked": url,
            "help": "形如 http://127.0.0.1:8012;留空则客户端走内置 renderer(office/pdf 预览体验弱)",
        }]

        if not url:
            return ServiceStatus(
                service=self.service, label=self.label, level="optional",
                ok=True, configured=False, endpoint="",
                detail="未配置 KKFILEVIEW_URL,客户端走内置 renderer(可选服务,不影响主业务)",
                fields=fields,
            )

        # 已配置 → HEAD/GET 探活;短超时避免拖慢
        import time
        import urllib.error
        import urllib.request

        base = url.rstrip("/")
        ok, detail, latency = False, "", None
        try:
            req = urllib.request.Request(
                f"{base}/", headers={"User-Agent": "chayuan-probe/1.0"},
            )
            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                code = int(getattr(resp, "status", 200))
                latency = int((time.monotonic() - t0) * 1000)
                if 200 <= code < 400:
                    ok, detail = True, f"HTTP {code} · 在线"
                else:
                    detail = f"HTTP {code}"
        except urllib.error.HTTPError as e:
            detail = f"HTTP {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            detail = f"网络错误: {e.reason}"
        except Exception as e:  # noqa: BLE001
            detail = f"{type(e).__name__}: {e}"

        # 已配置但连不通 → recommended 级标红,引导用户去 KB 设置改 URL
        return ServiceStatus(
            service=self.service, label=self.label,
            level=("optional" if ok else "recommended"),
            ok=ok, configured=True, endpoint=base,
            detail=detail, latency_ms=latency,
            fields=fields,
        )


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 95-4:已配置的 runtime 框架(ollama / vllm / infinity 等)动态生成 ServiceCheck
# ---------------------------------------------------------------------------

class RuntimeFrameworkServiceCheck(ServiceCheck):
    """95-4:把 ``probe_all_frameworks`` 的一项包装成 ServiceCheck,
    让"服务配置(系统先决条件)"页也能显示 ollama / vllm / infinity 状态。

    与基础设施的 critical/recommended 不同,runtime 是 ``optional`` —
    用户没用对应能力时不会挡路,但配好了卡片就显绿。
    """

    def __init__(self, name: str, label: str):
        # ServiceCheck 类属性 service/label 是定义时静态;实例化需要绑定到 self
        self.service = name
        self.label = label
        self.level: ServiceLevel = "optional"

    def probe(self) -> ServiceStatus:
        try:
            from chayuan.server.config_panel.runtime_framework_panel import (
                get_framework_spec_by_name, probe_framework,
            )
            spec = get_framework_spec_by_name(self.service)
            if spec is None:
                return ServiceStatus(
                    service=self.service, label=self.label, level=self.level,
                    ok=False, configured=False, endpoint="",
                    detail="未在 catalog 中找到该 framework",
                    fields=[],
                )
            h = probe_framework(spec)
        except Exception as e:  # noqa: BLE001
            return ServiceStatus(
                service=self.service, label=self.label, level=self.level,
                ok=False, configured=False, endpoint="",
                detail=f"探活异常: {type(e).__name__}: {e}",
                fields=[],
            )
        ok = h.state == "running"
        configured = h.state in ("running", "installed")
        detail_map = {
            "running":   "运行中",
            "installed": "已安装,未启动",
            "missing":   "未安装",
        }
        return ServiceStatus(
            service=self.service, label=self.label, level=self.level,
            ok=ok, configured=configured, endpoint=h.url,
            detail=detail_map.get(h.state, h.state), fields=[],
        )


def _runtime_framework_checks() -> List["ServiceCheck"]:
    """95-4:返已注册 runtime 的 RuntimeFrameworkServiceCheck 实例集合。

    **重要(96 题修复)**:这里**只读 catalog** 拿名字 + label,**不 probe**!
    探活留给 ``probe_all()`` 的并发阶段(每个 ServiceCheck 自己 probe 时跑)。

    之前在 ``all_checks()`` 内调用 ``probe_all_frameworks()`` 会做几十次
    HTTP / docker IO 阻塞主线程(数秒~十几秒)→ "服务配置"页打不开。
    """
    out: List["ServiceCheck"] = []
    try:
        # 用 catalog 拿名字 — 这个是纯文件读取(扫 compose/*.yaml),很快
        from chayuan.server.config_panel.runtime_framework_panel import (
            _get_effective_catalog,
        )
        for spec in _get_effective_catalog():
            label = spec.label or spec.name
            out.append(RuntimeFrameworkServiceCheck(spec.name, label))
    except Exception as e:  # noqa: BLE001
        logger.debug("[runtime_framework_checks] enum failed: %r", e)
    return out


def all_checks() -> List[ServiceCheck]:
    # 顺序与 service_config_page._DESIRED_ORDER 一致(基础设施 → 默认模型 →
    # 协同 → runtime),UI 渲染会再按 _DESIRED_ORDER 重排。
    #
    # 106 题:**①运行时与服务 tab 已删除**。framework cards
    # (comfyui/infinity/llamacpp/ollama/vllm/...)迁回服务配置页,在
    # service_config_page 给它们加 jump_target 跳到 ② 模型厂商对应 dialog。
    base: List[ServiceCheck] = [
        DBServiceCheck(),
        RedisServiceCheck(),
        FileStorageServiceCheck(),
        KkFileViewServiceCheck(),
        VectorStoreServiceCheck(),
        LLMServiceCheck(),
        # 108 题:默认 Embedding / Rerank 已迁移到 ② 默认模型 tab,
        # 服务配置页不再重复显示
    ]
    base.extend(_runtime_framework_checks())  # 106 题:framework 卡片
    return base


_PROBE_PER_CHECK_TIMEOUT_S = 3.0  # 单 check 硬超时 — 不能让某个慢 check 拖垮整页
_PROBE_MAX_WORKERS = 12           # 并发上限(11 个静态 check + N runtime,够用)


def _probe_one(c: "ServiceCheck") -> ServiceStatus:
    """跑单个 check.probe(),catch 一切异常 → 返"红色但可读"的 ServiceStatus。"""
    import warnings
    svc = getattr(c, "service", "?") or "?"
    label = getattr(c, "label", svc) or svc
    level = getattr(c, "level", "optional") or "optional"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            status = c.probe()
        if not isinstance(status, ServiceStatus):
            status = ServiceStatus(
                service=svc, label=label, level=level,  # type: ignore[arg-type]
                ok=False, configured=False, endpoint="",
                detail=f"probe returned {type(status).__name__}, expected ServiceStatus",
            )
        return status
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        logger.exception("probe %s failed", svc)
        return ServiceStatus(
            service=svc, label=label, level=level,  # type: ignore[arg-type]
            ok=False, configured=False, endpoint="",
            detail=f"probe crashed: {type(e).__name__}: {e}",
        )


def probe_all() -> List[ServiceStatus]:
    """永不抛错;所有子探针**并发**跑 + 单 check 硬超时。

    96 hotfix:之前是串行循环,每个 RuntimeFrameworkServiceCheck.probe() 会触发
    docker subprocess(Windows 4-10s)+ HTTP ping + inventory 多次 endpoint。
    N 个 runtime × 数秒 → 整页卡几十秒,NiceGUI 客户端超时,服务配置页打不开。

    现在:
      * ThreadPoolExecutor 并发跑
      * 每个 check 硬超时 3 秒;超时的标"探活超时"
      * KeyboardInterrupt / SystemExit 仍放行
    """
    import concurrent.futures as _cf

    try:
        checks = all_checks()
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        logger.exception("all_checks() failed, returning empty probe list")
        return []

    if not checks:
        return []

    out: List[ServiceStatus] = []
    workers = min(_PROBE_MAX_WORKERS, max(2, len(checks)))
    # 关键(96 hotfix):不用 with — ThreadPoolExecutor.__exit__ 默认 wait=True,
    # 会等所有 future 完成,导致 timeout 失效(用户报"页面打不开")。
    # 手动 shutdown(wait=False) 让慢的 future 后台跑,主线程不阻塞返回。
    ex = _cf.ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="probe-",
    )
    try:
        future_map = [(ex.submit(_probe_one, c), c) for c in checks]
        # 用 _cf.wait + 总超时保护;每个 future 也不会等过 _PROBE_PER_CHECK_TIMEOUT_S
        deadline = time.time() + _PROBE_PER_CHECK_TIMEOUT_S
        for fut, c in future_map:
            svc = getattr(c, "service", "?") or "?"
            label = getattr(c, "label", svc) or svc
            level = getattr(c, "level", "optional") or "optional"
            remaining = max(0.05, deadline - time.time())
            try:
                status = fut.result(timeout=remaining)
                out.append(status)
            except _cf.TimeoutError:
                logger.warning(
                    "probe %s 超时(>%.1fs),跳过",
                    svc, _PROBE_PER_CHECK_TIMEOUT_S,
                )
                out.append(ServiceStatus(
                    service=svc, label=label, level=level,  # type: ignore[arg-type]
                    ok=False, configured=False, endpoint="",
                    detail=f"探活超时(>{_PROBE_PER_CHECK_TIMEOUT_S:.0f}s),"
                           "可能 docker / HTTP 不通;请到运行时与服务页查看",
                ))
            except BaseException as e:  # noqa: BLE001
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                logger.exception("probe %s wrapper crashed", svc)
                out.append(ServiceStatus(
                    service=svc, label=label, level=level,  # type: ignore[arg-type]
                    ok=False, configured=False, endpoint="",
                    detail=f"探活崩溃: {type(e).__name__}: {e}",
                ))
    finally:
        # 不等慢任务,让后台线程自生自灭;Python 3.9+ 支持 cancel_futures
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            ex.shutdown(wait=False)
    return out


def system_ready(statuses: Optional[List[ServiceStatus]] = None) -> bool:
    """全部 critical 服务都 ok → 系统就绪。"""
    st = statuses if statuses is not None else probe_all()
    return all(s.ok for s in st if s.level == "critical")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _mask_uri(uri: str) -> str:
    if not uri:
        return ""
    try:
        # user:pass@host:port → user:***@host:port
        if "@" in uri and "://" in uri:
            scheme, rest = uri.split("://", 1)
            creds, loc = rest.split("@", 1)
            if ":" in creds:
                user, _pw = creds.split(":", 1)
                return f"{scheme}://{user}:***@{loc}"
        return uri
    except Exception:  # noqa: BLE001
        return uri


def _fields_uri_like(uri: str) -> List[Dict[str, Any]]:
    return [{
        "name": "SQLALCHEMY_DATABASE_URI",
        "label": "数据库 URI",
        "widget": "text",
        "value_masked": _mask_uri(uri),
        "help": ("sqlite:///<abs_path>/info.db  (开发默认) "
                 "或  postgresql+psycopg2://user:pass@host:5432/db"),
    }]
