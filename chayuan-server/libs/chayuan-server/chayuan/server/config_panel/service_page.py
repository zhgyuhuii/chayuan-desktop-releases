"""服务管理页面：可视化拓扑 + 连线动画 + 单服务配置/探活/重启。

设计要点
--------
- 顶部一张"服务拓扑"卡片：用 SVG 在固定画布上画出
    Client → Nginx → (API / WebUI / ConfigPanel) → (Redis / DB / Milvus)
  的连线。SVG 路径走 ``stroke-dasharray`` + CSS animation 实现流光效果；
  连线颜色随下游节点状态在绿/红之间切换。
- 每个节点是一张绝对定位的 NiceGUI 卡片，状态由后台探活决定：
    🟢 ok   —— 端口可达 / 业务探针通过；
    🟡 down —— 配置存在但探活失败（未就绪；提示而非报警）；
    ⚪ n/a  —— 未配置（如 REDIS_URL 空、Milvus 未启用等）。
  点击节点会打开对应的服务详情 dialog，里面复用现有的配置卡组件。
- 下方一张"服务列表"卡，列出每个服务的 host:port / 状态 / 操作按钮：
  「配置」「探活」「重启」——重启仅在支持的节点上可点。
- 页面每 ~5 秒自动刷新一次状态（轻量探活，默认 600ms timeout），
  不会阻塞 UI；也可以点顶部"立即刷新"手动触发。

本页面**只读 yaml**；真正的配置写入发生在各自的 dialog 里
（Redis / DB / Milvus 三张卡直接复用 redis_config / db_config / vs_config）。
"""
from __future__ import annotations

import logging
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from chayuan.server.config_panel import yaml_store

logger = logging.getLogger("chayuan.config_panel.service_page")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

# 状态枚举：
#   ok       —— 探活成功
#   down     —— 探活失败（配置存在但连不通，红色 + 脉动）
#   disabled —— 未配置（灰色，线断开）
#   unknown  —— 初始化 / 无法判定
STATUS_OK = "ok"
STATUS_DOWN = "down"
STATUS_DISABLED = "disabled"
STATUS_UNKNOWN = "unknown"


@dataclass
class ServiceSnapshot:
    """一个服务的实时快照，用于渲染节点/列表。"""

    sid: str
    label: str
    group: str                       # "edge" / "proxy" / "app" / "infra"
    icon: str                        # Quasar material icon
    status: str = STATUS_UNKNOWN
    summary: str = ""                # host:port / URL 等一行描述
    detail: str = ""                 # 错误或成功的额外信息
    restartable: bool = False        # 是否支持通过 restart.trigger_restart 重启
    config_kind: str = ""            # "api" / "panel" / "nginx" / "redis" / "db" / "milvus" / ""
    x: int = 0
    y: int = 0


@dataclass
class Topology:
    services: Dict[str, ServiceSnapshot] = field(default_factory=dict)
    edges: List[Tuple[str, str]] = field(default_factory=list)
    generated_at: float = field(default_factory=time.time)

    def ordered(self) -> List[ServiceSnapshot]:
        # 保留插入顺序（Python 3.7+ dict 有序），调用侧用于列表渲染。
        return list(self.services.values())


# ---------------------------------------------------------------------------
# 探活工具
# ---------------------------------------------------------------------------

def _probe_tcp(host: Optional[str], port: Any, timeout: float = 0.6) -> bool:
    """纯 socket 探活。host 为 0.0.0.0 时视作 127.0.0.1（客户端视角）。"""
    try:
        p = int(port)
    except (TypeError, ValueError):
        return False
    h = (host or "127.0.0.1").strip()
    if h == "0.0.0.0":
        h = "127.0.0.1"
    try:
        with socket.create_connection((h, p), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_http(url: str, timeout: float = 1.5) -> Tuple[bool, str]:
    """GET 一次 URL，返回 (ok, detail)。ok 要求 2xx/3xx。"""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = int(getattr(resp, "status", 0) or 0)
            if 200 <= code < 400:
                return True, f"HTTP {code}"
            return False, f"HTTP {code}"
    except urllib.error.HTTPError as e:
        # 4xx/5xx 明确失败
        return False, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _probe_api(host: str, port: Any) -> Tuple[str, str]:
    """先尝试 /healthz HTTP 探针，失败回落到 socket 探活。"""
    try:
        p = int(port)
    except (TypeError, ValueError):
        return STATUS_DISABLED, "未配置端口"
    h = (host or "127.0.0.1").strip()
    if h == "0.0.0.0":
        h = "127.0.0.1"
    ok, detail = _probe_http(f"http://{h}:{p}/healthz", timeout=1.2)
    if ok:
        return STATUS_OK, detail
    # HTTP 失败不代表端口没开，比如旧版 API 没有 /healthz；用 TCP 再试一次
    if _probe_tcp(h, p, timeout=0.6):
        return STATUS_OK, "TCP 可达（/healthz 未命中，可能是旧版本）"
    return STATUS_DOWN, detail


def _probe_redis(url: str, timeout: float = 1.2) -> Tuple[str, str]:
    """先走 ``redis.Redis.from_url().ping()``；未装 redis 包时退化为 TCP 探活。"""
    url = (url or "").strip()
    if not url:
        return STATUS_DISABLED, "REDIS_URL 未配置（限流/缓存/队列会降级为单机内存）"
    try:
        import redis  # type: ignore
    except Exception:
        # 未装 redis 包也不阻塞拓扑渲染，退化成 TCP 探活
        try:
            p = urlparse(url)
            host = p.hostname or "127.0.0.1"
            port = p.port or 6379
            if _probe_tcp(host, port, timeout=timeout):
                return STATUS_OK, "TCP 可达（未安装 redis 包，无法 PING）"
            return STATUS_DOWN, "TCP 不可达"
        except Exception as e:  # noqa: BLE001
            return STATUS_DOWN, f"URL 解析失败：{e}"

    client = None
    try:
        client = redis.Redis.from_url(
            url, socket_connect_timeout=timeout, socket_timeout=timeout,
            decode_responses=True,
        )
        if client.ping():
            try:
                info = client.info("server") or {}
                ver = str(info.get("redis_version") or "")
                return STATUS_OK, f"PING 成功" + (f"（v{ver}）" if ver else "")
            except Exception:
                return STATUS_OK, "PING 成功"
        return STATUS_DOWN, "PING 返回 false"
    except Exception as e:  # noqa: BLE001
        return STATUS_DOWN, f"{type(e).__name__}: {e}"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _probe_db(uri: str, timeout: float = 2.0) -> Tuple[str, str]:
    """用 SQLAlchemy 尝试 ``SELECT 1``。sqlite 只看文件存在即可。"""
    uri = (uri or "").strip()
    if not uri:
        return STATUS_DISABLED, "SQLALCHEMY_DATABASE_URI 未配置"
    if uri.startswith("sqlite"):
        try:
            path = uri.split(":///", 1)[-1] or uri.split("://", 1)[-1]
            from pathlib import Path as _P
            if _P(path).is_file():
                return STATUS_OK, f"SQLite 文件存在（{path}）"
            return STATUS_DOWN, f"SQLite 文件不存在：{path}"
        except Exception as e:  # noqa: BLE001
            return STATUS_DOWN, f"路径解析失败：{e}"

    try:
        from sqlalchemy import create_engine, text  # type: ignore
    except Exception as e:  # noqa: BLE001
        return STATUS_DOWN, f"未安装 sqlalchemy：{e}"

    connect_args: Dict[str, Any] = {}
    if uri.startswith(("postgresql", "postgres", "mysql", "mariadb")):
        connect_args["connect_timeout"] = int(timeout)

    eng = None
    try:
        eng = create_engine(uri, connect_args=connect_args, pool_pre_ping=True)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return STATUS_OK, "SELECT 1 成功"
    except Exception as e:  # noqa: BLE001
        return STATUS_DOWN, f"{type(e).__name__}: {e}"
    finally:
        if eng is not None:
            try:
                eng.dispose()
            except Exception:
                pass


def _probe_milvus(
    host: str, port: Any, milvus_cfg: Optional[Dict[str, Any]] = None,
    timeout: float = 0.8,
) -> Tuple[str, str]:
    """Milvus 的 gRPC/REST 都跑在 19530。

    「就绪」定义：
    - 未填 host → ``disabled``（未启用）
    - auth 字段不一致（``user`` / ``password`` 只填了一个，或 ``secure=True``
      但既没 user+password 也没 token）→ ``down``：这样的配置 ``pymilvus`` 一定
      抛异常，必须提醒用户
    - 填了有效配置 + TCP 可达 → ``ok``
    - 填了 + TCP 不通 → ``down``

    深度探活（真正 ``connect + list_collections``）在单服务 dialog 里单独触发，
    避免首页每 5 秒都去连一次。
    """
    if not host:
        return STATUS_DISABLED, "Milvus 未配置"

    # auth 字段自洽检查 —— 不做真连接，只看"这配置 pymilvus 会不会拒绝"
    cfg = dict(milvus_cfg or {})
    user = str(cfg.get("user") or "").strip()
    password = str(cfg.get("password") or "").strip()
    token = str(cfg.get("token") or "").strip()
    secure = bool(cfg.get("secure") or False)
    if bool(user) != bool(password) and not token:
        return STATUS_DOWN, (
            "Milvus 凭据不完整：user / password 只填了一个，"
            "且未填 token。点卡片进去把凭据补齐。"
        )
    if secure and not (token or (user and password)):
        return STATUS_DOWN, (
            "已开启 secure=True 但未配置任何凭据（user+password 或 token）。"
            "未就绪——Milvus 会拒绝匿名连接。"
        )

    if _probe_tcp(host, port or 19530, timeout=timeout):
        suffix = ""
        if token or (user and password):
            suffix = "；凭据已填（深度验证请用 dialog 测试连接）"
        return STATUS_OK, f"TCP 可达（{host}:{port or 19530}）{suffix}"
    return STATUS_DOWN, f"{host}:{port or 19530} 不可达"


def _parse_minio_endpoint(endpoint: str, secure_hint: bool) -> Tuple[str, int, bool]:
    """MinIO 客户端要求 endpoint 不带 scheme；这里兼容 ``http(s)://host:port`` 写法。

    返回 (host, port, secure)；port 解析失败时回退 9000。
    """
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


def _probe_minio(timeout: float = 0.8) -> Tuple[str, str]:
    """MinIO / 对象存储探活。

    「就绪」定义（与 ``service_checks.FileStorageServiceCheck`` 对齐）：
    - backend=local（默认）→ ``disabled``：本机磁盘模式，MinIO 无需配置，
      节点保留在拓扑里给用户一个"可选后端"的视觉提示。
    - backend=minio + 缺任一必填项（endpoint / access_key / secret_key）
      → ``down``：已切到 MinIO 却未配置完整，对象存储根本无法用；
      绝不能标绿迷惑用户。
    - backend=minio + 三件套齐全 + TCP 可达 → ``ok``。
    - backend=minio + 三件套齐全 + TCP 不可达 → ``down``。

    深度探活（``list_buckets`` 真正验证凭据对不对）仍然留给 dialog 里的
    "测试连接"按钮 / 「服务配置」页做——拓扑 5 秒一轮不该 list_buckets。
    """
    try:
        from chayuan.settings import Settings
        bs = Settings.basic_settings
    except Exception as e:  # noqa: BLE001
        return STATUS_UNKNOWN, f"无法读取 Settings：{type(e).__name__}: {e}"

    backend = str(getattr(bs, "FILE_STORAGE_BACKEND", "local") or "local").strip().lower()
    endpoint = str(getattr(bs, "MINIO_ENDPOINT", "") or "").strip()
    access_key = str(getattr(bs, "MINIO_ACCESS_KEY", "") or "").strip()
    secret_key = str(getattr(bs, "MINIO_SECRET_KEY", "") or "").strip()
    secure = bool(getattr(bs, "MINIO_SECURE", False))

    if backend not in ("minio", "s3"):
        return STATUS_DISABLED, (
            f"当前 FILE_STORAGE_BACKEND={backend}；本地磁盘模式下无需 MinIO。"
            "需要跨节点/S3 兼容的对象存储时再切换。"
        )

    # 任一必填项缺失都算未就绪 —— 有 endpoint 没凭据 / 有凭据没 endpoint 都会让
    # 生产请求直接 403 或 NoCredentialsError。
    missing = []
    if not endpoint:
        missing.append("MINIO_ENDPOINT")
    if not access_key:
        missing.append("MINIO_ACCESS_KEY")
    if not secret_key:
        missing.append("MINIO_SECRET_KEY")
    if missing:
        return STATUS_DOWN, (
            "已选 MinIO 后端，但未就绪：缺少 " + " / ".join(missing)
            + "。点卡片进去把凭据补齐后再试。"
        )

    host, port, _secure = _parse_minio_endpoint(endpoint, secure)
    if not host:
        return STATUS_DOWN, f"MINIO_ENDPOINT 格式异常：{endpoint}"
    if _probe_tcp(host, port, timeout=timeout):
        return STATUS_OK, f"TCP 可达（{host}:{port}）；凭据已填（深度验证请用 dialog 测试连接）"
    return STATUS_DOWN, f"{host}:{port} 不可达"


def _probe_nginx(nginx_url: str) -> Tuple[str, str]:
    """Nginx 没有内置配置；用户在配置面板存一个 URL 就可以探活。"""
    url = (nginx_url or "").strip()
    if not url:
        return STATUS_DISABLED, "Nginx URL 未配置（可选）"
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    ok, detail = _probe_http(url, timeout=1.5)
    return (STATUS_OK if ok else STATUS_DOWN), detail


def _probe_langfuse(timeout: float = 1.5) -> Tuple[str, str]:
    """Langfuse 探针（**非必要服务**，缺就缺不标红）。

    「就绪」定义：
    - ``CHAYUAN_LANGFUSE_DISABLE=True`` → ``disabled``（运维一键关停，不是故障）
    - host / public_key / secret_key 任一缺失 → ``disabled``
      （非必要服务缺凭据标灰而不是黄，避免让用户觉得业务有问题）
    - 三件套齐全 + ``GET {host}/api/public/health`` 2xx → ``ok``
    - 三件套齐全 + HTTP 不通 → ``down``：配置了却连不上才算真未就绪
    """
    try:
        from chayuan.server.observability import langfuse_integration as _lf
    except Exception as e:  # noqa: BLE001
        return STATUS_UNKNOWN, f"langfuse_integration 加载失败：{type(e).__name__}: {e}"

    if _lf._explicit_disabled():  # 运维一键关停
        return STATUS_DISABLED, "CHAYUAN_LANGFUSE_DISABLE=1 一键禁用（运维应急开关）"

    cfg = _lf.effective_config()
    host = (cfg.get("LANGFUSE_HOST") or "").strip()
    pk = (cfg.get("LANGFUSE_PUBLIC_KEY") or "").strip()
    sk = (cfg.get("LANGFUSE_SECRET_KEY") or "").strip()

    if not (host and pk and sk):
        missing = [
            name for name, val in [
                ("LANGFUSE_HOST", host),
                ("LANGFUSE_PUBLIC_KEY", pk),
                ("LANGFUSE_SECRET_KEY", sk),
            ] if not val
        ]
        return STATUS_DISABLED, (
            "未启用 Langfuse（非必要服务）：缺 " + " / ".join(missing)
            + "。需要 LLM 链路追踪时在 dialog 里配置凭据即可。"
        )

    # 三件套齐全 → HTTP 探活 /api/public/health（Langfuse 标准公开健康端点，免鉴权）
    url = host.rstrip("/") + "/api/public/health"
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    ok, detail = _probe_http(url, timeout=timeout)
    if ok:
        return STATUS_OK, f"{detail} · {host}"
    return STATUS_DOWN, f"{host} · {detail}"


def _probe_kkfileview_topo(url: str, timeout: float = 1.2) -> Tuple[str, str]:
    """拓扑用 kkFileView 轻量探活:GET /,2xx/3xx 即在线。

    与 kb_manage_page._probe_kkfileview 同语义,但拓扑里要的是更短超时
    (1.2s)避免 5s 轮询时拖慢整页;状态二元化:ok / down / disabled。
    """
    if not (url or "").strip():
        return STATUS_DISABLED, "未配置"
    base = url.rstrip("/")
    try:
        req = urllib.request.Request(f"{base}/", headers={"User-Agent": "chayuan-probe/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 200)
            if 200 <= code < 400:
                return STATUS_OK, f"{base} · HTTP {code}"
            return STATUS_DOWN, f"HTTP {code}"
    except urllib.error.HTTPError as e:
        return STATUS_DOWN, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return STATUS_DOWN, f"网络错误: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return STATUS_DOWN, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# 快照构建：从 Settings / yaml 读配置，逐个探活并组装拓扑
# ---------------------------------------------------------------------------

def _summary(host: Any, port: Any) -> str:
    if host and port:
        return f"{host}:{port}"
    if host:
        return str(host)
    return "—"


def build_snapshot(*, probe: bool = True) -> Topology:
    """抓取一份服务状态快照。任何子探活异常都会被吞掉成 unknown/down。

    参数 probe:
      - True(默认):并行跑外部探针(nginx/api/panel/redis/db/milvus/
        minio/langfuse),把"最坏 10s 串行"压到 max(单探针 timeout)≈3s。
      - False:**完全跳过探针**,所有外部服务节点 status=unknown 立即返回(<10ms);
        给"首次渲染骨架,后台异步真探针"路径用,避免页面冻结 3s。
    """
    from concurrent.futures import ThreadPoolExecutor
    from chayuan.settings import Settings

    bs = Settings.basic_settings
    kb = Settings.kb_settings

    api = dict(getattr(bs, "API_SERVER", {}) or {})
    cfg = dict(getattr(bs, "CONFIG_SERVER", {}) or {})
    redis_url = str(getattr(bs, "REDIS_URL", "") or "")
    db_uri = str(getattr(bs, "SQLALCHEMY_DATABASE_URI", "") or "")
    nginx_url = str(getattr(bs, "NGINX_URL", "") or "")

    milvus_cfg = {}
    try:
        raw = yaml_store.load_yaml("kb_settings.yaml").doc or {}
        milvus_cfg = ((raw.get("kbs_config") or {}).get("milvus") or {})
    except Exception:
        milvus_cfg = {}
    vs_type = str(getattr(kb, "DEFAULT_VS_TYPE", "") or "").lower()

    # 兼容新老两种 milvus 配置形态:
    #   ① 老式 host/port(2026-04 之前的默认)
    #   ② 新式 uri(http://host:port)
    # 新装默认是 uri,老配置升级也兼容。
    if not milvus_cfg.get("host") and milvus_cfg.get("uri"):
        try:
            from urllib.parse import urlparse as _u
            p = _u(str(milvus_cfg["uri"]))
            if p.hostname:
                milvus_cfg = dict(milvus_cfg)
                milvus_cfg["host"] = p.hostname
                milvus_cfg["port"] = p.port or 19530
        except Exception:  # noqa: BLE001
            pass

    # 位置：画布 880 × 500，每个节点卡片约 160 × 70
    # 基础设施列现在有 5 个节点（Redis / DB / Milvus / MinIO / Langfuse），
    # 纵向均匀分布（间距 ~95px）。Langfuse 作为"非必要观测服务"放最底下——
    # 视觉上远离主干，提示它缺失不影响主业务。
    services: Dict[str, ServiceSnapshot] = {}

    # --- Client --- (展示用，不探活)
    services["client"] = ServiceSnapshot(
        sid="client", label="客户端", group="edge",
        icon="devices",  # 多设备：浏览器 / App / SDK
        status=STATUS_OK,
        summary="浏览器 / App / SDK", detail="用户入口，不进行探活",
        x=80, y=250,
    )

    # === 收集探针入参(无论 probe=True/False 都需要,for 后续节点 summary 渲染)===
    api_host = api.get("host") or "127.0.0.1"
    api_port = api.get("port") or 62581
    cfg_host = cfg.get("host") or "127.0.0.1"
    cfg_port = cfg.get("port") or 8502
    mv_host = str(milvus_cfg.get("host") or "")
    mv_port = milvus_cfg.get("port") or "19530"
    backend = str(getattr(bs, "FILE_STORAGE_BACKEND", "local") or "local").strip().lower()
    mio_endpoint = str(getattr(bs, "MINIO_ENDPOINT", "") or "").strip()
    milvus_enabled = vs_type in ("milvus", "zilliz") and bool(mv_host)

    if not probe:
        # 占位模式:全部 status=unknown,detail="探测中…",立即返回
        ng_status, ng_detail = STATUS_UNKNOWN, "探测中…"
        api_status, api_detail = STATUS_UNKNOWN, "探测中…"
        cfg_alive = False
        rd_status, rd_detail = STATUS_UNKNOWN, "探测中…"
        db_status, db_detail = STATUS_UNKNOWN, "探测中…"
        mv_status, mv_detail = STATUS_UNKNOWN, "探测中…"
        mio_status, mio_detail = STATUS_UNKNOWN, "探测中…"
        lf_status, lf_detail = STATUS_UNKNOWN, "探测中…"
    else:
        # === 并行跑 8 个探针 ===
        # 把每个探针包装成 0 参 Callable,用 ThreadPoolExecutor 一齐 submit;
        # 主线程等所有 future 完成再读结果。每个探针自己已带短超时,worker 异常也
        # 不会蔓延 — 保持原"任何子探活异常吞掉成 unknown/down"语义。
        def _safe(fn, *args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except BaseException as e:  # noqa: BLE001
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                return (STATUS_DOWN, f"probe crashed: {type(e).__name__}: {e}")

        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="topo-probe") as ex:
            f_nginx = ex.submit(_safe, _probe_nginx, nginx_url)
            f_api = ex.submit(_safe, _probe_api, api_host, api_port)
            f_panel = ex.submit(_safe, _probe_tcp, cfg_host, cfg_port, 0.6)
            f_redis = ex.submit(_safe, _probe_redis, redis_url)
            f_db = ex.submit(_safe, _probe_db, db_uri)
            if milvus_enabled:
                f_milvus = ex.submit(_safe, _probe_milvus, mv_host, mv_port, milvus_cfg)
            else:
                f_milvus = None
            f_minio = ex.submit(_safe, _probe_minio)
            f_langfuse = ex.submit(_safe, _probe_langfuse)

            ng_status, ng_detail = f_nginx.result()
            api_status, api_detail = f_api.result()
            cfg_alive = bool(f_panel.result())
            rd_status, rd_detail = f_redis.result()
            db_status, db_detail = f_db.result()
            if f_milvus is not None:
                mv_status, mv_detail = f_milvus.result()
            else:
                mv_status, mv_detail = STATUS_DISABLED, (
                    f"当前 DEFAULT_VS_TYPE={vs_type or '未设置'}，未启用 Milvus"
                )
            mio_status, mio_detail = f_minio.result()
            lf_status, lf_detail = f_langfuse.result()

    # --- Nginx ---
    services["nginx"] = ServiceSnapshot(
        sid="nginx", label="Nginx 反代", group="proxy",
        icon="compare_arrows",  # 双向转发箭头：反向代理语义最贴切
        status=ng_status,
        summary=nginx_url or "（可选，未配置）", detail=ng_detail,
        config_kind="nginx",
        x=260, y=250,
    )

    # --- 应用层：API / Panel ---
    services["api"] = ServiceSnapshot(
        sid="api", label="API 服务", group="app",
        icon="api",  # 原生 api 图标
        status=api_status,
        summary=_summary(api_host, api_port), detail=api_detail,
        restartable=True, config_kind="api",
        x=470, y=120,
    )

    if not probe:
        cfg_status, cfg_detail = STATUS_UNKNOWN, "探测中…"
    else:
        cfg_status = STATUS_OK if cfg_alive else STATUS_DOWN
        cfg_detail = "本进程（必定可达）" if cfg_alive else "端口不可达"

    services["panel"] = ServiceSnapshot(
        sid="panel", label="配置面板", group="app",
        icon="dashboard",  # 仪表盘 - 即本页
        status=cfg_status,
        summary=_summary(cfg_host, cfg_port),
        detail=cfg_detail,
        restartable=True, config_kind="panel",
        x=470, y=380,
    )

    # --- 基础设施层：Redis / DB / Milvus / MinIO / Langfuse ---
    services["redis"] = ServiceSnapshot(
        sid="redis", label="Redis", group="infra",
        icon="memory",  # 内存芯片 - in-memory cache 语义最贴切
        status=rd_status,
        summary=_redis_summary(redis_url), detail=rd_detail,
        config_kind="redis",
        x=700, y=60,
    )

    services["db"] = ServiceSnapshot(
        sid="db", label="业务数据库", group="infra",
        icon="dns",  # 圆柱体 - 最接近传统"数据库"视觉
        status=db_status,
        summary=_db_summary(db_uri), detail=db_detail,
        config_kind="db",
        x=700, y=155,
    )

    services["milvus"] = ServiceSnapshot(
        sid="milvus", label="Milvus", group="infra",
        icon="scatter_plot",  # 散点图 - 向量库语义
        status=mv_status,
        summary=_summary(mv_host, mv_port) if mv_host else "—",
        detail=mv_detail,
        config_kind="milvus",
        x=700, y=250,
    )

    services["minio"] = ServiceSnapshot(
        sid="minio", label="MinIO 对象存储", group="infra",
        icon="inventory_2",  # 仓储盒子 - 对象存储语义
        status=mio_status,
        summary=(mio_endpoint or "—") if backend in ("minio", "s3") else "本地磁盘模式",
        detail=mio_detail,
        config_kind="minio",
        x=700, y=345,
    )

    # 注:kkFileView 旁车已迁至「🚀 服务配置」页(2026-04),从拓扑里移除避免双入口。
    # 见 service_checks.KkFileViewServiceCheck。

    # Langfuse 节点：**非必要观测服务**——不配置也不影响主业务。
    # 缺凭据标灰（disabled）而不是黄，避免让用户误以为业务出了问题。
    lf_cfg_summary = ""
    try:
        from chayuan.server.observability.langfuse_integration import (
            effective_config as _lf_effective,
        )
        _lfc = _lf_effective()
        _host = (_lfc.get("LANGFUSE_HOST") or "").strip()
        lf_cfg_summary = _host or "（未启用）"
    except Exception:  # noqa: BLE001
        lf_cfg_summary = "（未启用）"
    services["langfuse"] = ServiceSnapshot(
        sid="langfuse", label="Langfuse 链路追踪", group="infra",
        icon="insights",  # 洞察图标 - LLM 链路观测语义
        status=lf_status,
        summary=lf_cfg_summary,
        detail=lf_detail,
        config_kind="langfuse",
        # y=440:与 Redis/DB/Milvus/MinIO 同列等间距 95px(60/155/250/345/440)。
        # 老值 y=630 是历史 kkFileView 旁车节点占位的残留(2026-04 已迁走),
        # 没跟着收回来导致 Langfuse 卡片掉到画布外面。见 _CANVAS_H 的注释。
        x=700, y=440,
    )

    edges: List[Tuple[str, str]] = [
        ("client", "nginx"),
        ("nginx", "api"),
        ("nginx", "panel"),
        ("api", "redis"),
        ("api", "db"),
        ("api", "milvus"),
        ("api", "minio"),       # 对象存储走 API 落盘（kb / 图片 / 临时文件）
        ("api", "langfuse"),    # API 侧 LangChain callback → Langfuse（非必要观测）
        ("panel", "db"),
    ]

    # 95-3:把已配置(running 或 installed)的 runtime 也加进拓扑图。
    # 这些是右列 infra 之外的"AI 推理层"节点,放在画布右边一列,
    # 与 API 节点连边表示"API → 调推理服务"。
    _add_runtime_nodes(services, edges)

    return Topology(services=services, edges=edges)


# 95-3:runtime 节点视觉配置 — name → (icon, label override 可选)
# 没列在这里的 runtime 用通用 ``smart_toy`` icon + spec.label
_RUNTIME_NODE_VIS = {
    "ollama":    "memory",         # 本机推理
    "vllm":      "rocket_launch",  # 高吞吐
    "infinity":  "auto_awesome",   # 嵌入 / rerank 一体
    "comfyui":   "image",
    "llamacpp":  "memory",
    "funasr":    "mic",
    "cosyvoice": "graphic_eq",
    "rapidocr":  "document_scanner",
    "paddleocr": "document_scanner",
    "whispercpp": "mic",
}


def _add_runtime_nodes(
    services: Dict[str, ServiceSnapshot],
    edges: List[Tuple[str, str]],
) -> None:
    """95-3:扫已配置的 runtime,加节点 + (api → runtime) 边。

    只加 ``state in ("running", "installed")`` 的 — 没装的不显示,避免画布拥挤。
    """
    try:
        from chayuan.server.config_panel.runtime_framework_panel import (
            probe_all_frameworks,
        )
        healths = probe_all_frameworks(force=False)
    except Exception as e:  # noqa: BLE001
        logger.debug("[topology] runtime probe failed: %r", e)
        return

    # 节点放在右列 x=900,与 infra 列(x=700)分开
    # y 轴从 60 起,每 95px 一个节点
    base_x = 900
    base_y = 60
    step_y = 95
    n = 0
    for h in healths:
        if h.state == "missing":
            continue
        sid = h.spec.name
        if sid in services:
            continue  # 不覆盖已存在节点(如未来某 runtime 进了静态 infra 列表)

        if h.state == "running":
            status = STATUS_OK
            detail = f"运行中 · {h.url}" if h.url else "运行中"
        else:  # installed
            status = STATUS_DOWN
            detail = "已安装,未启动 — 进 ② 模型配置 → 运行时启动"

        services[sid] = ServiceSnapshot(
            sid=sid,
            label=h.spec.label or sid,
            group="runtime",
            icon=_RUNTIME_NODE_VIS.get(sid, "smart_toy"),
            status=status,
            summary=h.url or "—",
            detail=detail,
            config_kind="runtime",
            x=base_x, y=base_y + n * step_y,
        )
        edges.append(("api", sid))
        n += 1
    if n > 0:
        logger.debug("[topology] added %d runtime node(s)", n)


def _redis_summary(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return "—"
    try:
        p = urlparse(url)
        host = p.hostname or "127.0.0.1"
        port = p.port or 6379
        db = (p.path or "").lstrip("/") or "0"
        return f"{host}:{port}/{db}"
    except Exception:
        return url[:40]


def _db_summary(uri: str) -> str:
    uri = (uri or "").strip()
    if not uri:
        return "—"
    if uri.startswith("sqlite"):
        tail = uri.split("://", 1)[-1].lstrip("/")
        return f"SQLite · .../{tail.rsplit('/', 1)[-1]}"
    try:
        p = urlparse(uri)
        backend = (p.scheme or "").split("+", 1)[0]
        return f"{backend} · {p.hostname}:{p.port or '?'}/{(p.path or '').lstrip('/')}"
    except Exception:
        return uri[:40]


# ---------------------------------------------------------------------------
# 渲染：主入口
# ---------------------------------------------------------------------------

# 拓扑画布常量
# 画布高度 500:基础设施列容纳 5 个节点(Redis / DB / Milvus / MinIO / Langfuse),
# 节点 y 坐标 60/155/250/345/440,间距 95px;上下各留 ~30px 避免节点卡片
# (translate(-50%, -50%))超出边界。
# 95-3:画布扩到 1080,容纳右列 runtime 节点(x=900)+ 节点宽度 160 + 边距
_CANVAS_W = 1080
_CANVAS_H = 500
_NODE_W = 160
_NODE_H = 70

# 每种状态对应的 CSS 变量：边框色 / 背景 / 文字
#
# 色板语义：
# - ok       → 绿色（#22c55e / emerald-500）：一切正常
# - down     → 琥珀黄（#f59e0b / amber-500）：服务已配置但未就绪（端口不通 /
#              深度探活失败）；相较红色更"提醒而非报警"，更贴合"可恢复的未就绪"语义
# - disabled → 灰（#d1d5db）：未配置 / 未启用（这条路根本不走）
# - unknown  → 暖灰（#9ca3af）：初始化中或无法判定
_STATUS_STYLE = {
    STATUS_OK:       {"border": "#22c55e", "bg": "rgba(34,197,94,0.10)",   "text": "#15803d", "dot": "#22c55e"},
    STATUS_DOWN:     {"border": "#f59e0b", "bg": "rgba(245,158,11,0.12)",  "text": "#b45309", "dot": "#f59e0b"},
    STATUS_DISABLED: {"border": "#d1d5db", "bg": "rgba(229,231,235,0.35)", "text": "#6b7280", "dot": "#9ca3af"},
    STATUS_UNKNOWN:  {"border": "#9ca3af", "bg": "rgba(229,231,235,0.35)", "text": "#4b5563", "dot": "#9ca3af"},
}

_STATUS_LABEL = {
    STATUS_OK: "运行中",
    STATUS_DOWN: "未就绪",
    STATUS_DISABLED: "未配置",
    STATUS_UNKNOWN: "未知",
}


def render_service_page(
    ui,
    pending_restart: Optional[Dict[str, set]] = None,
    refresh_banner: Optional[Callable[[], None]] = None,
) -> None:
    """服务管理页主入口。``pending_restart`` / ``refresh_banner`` 由 dashboard 传入。

    在 NiceGUI 页面上下文内调用。会注入一段 ``<style>``（只注入一次），
    并放一个 ``ui.timer`` 轮询刷新状态。
    """
    _inject_styles(ui)

    with ui.row().classes("items-center w-full no-wrap q-mb-xs"):
        ui.label("服务管理").classes("text-2xl font-semibold")
        ui.space()
        auto_switch = ui.switch("自动刷新", value=True).props("dense")
        auto_switch.tooltip("开启后每 5 秒重新抓取一次服务状态；关闭可省流量")
        refresh_btn = ui.button(icon="refresh").props("flat round dense")
        refresh_btn.tooltip("立即重新探活所有服务")

    ui.label(
        "下方拓扑图按「客户端 → 反向代理 → 应用层 → 存储 / 中间件」的调用关系铺开；"
        "节点颜色表示实时状态（🟢 运行中 / 🟡 未就绪 / ⚪ 未配置）；"
        "点击节点可查看和修改该服务的连接参数、验证连通性，或在支持时触发重启。"
    ).classes("text-sm text-grey-8 q-mb-md")

    # 加载进度条 — 首次探针运行期间常驻,完成后 .set_visibility(False)
    with ui.row().classes("items-center w-full no-wrap q-mb-sm").style("gap:8px") as progress_row:
        progress_bar = ui.linear_progress(value=None).props("instant-feedback rounded color=primary").classes("flex-1")
        progress_label = ui.label("正在探测各服务可达性…").classes(
            "text-xs text-grey-7 font-mono",
        ).style("white-space:nowrap")

    # 状态：共享给后续的刷新 / dialog / 按钮
    # 首次渲染走 probe=False 占位模式 — 拓扑结构完整,但所有节点 status=unknown,<10ms 返回
    # 真探针由 ui.timer(0.05, _refresh_async, once=True) 推到后台线程跑,结果到了再 _apply_topology
    state: Dict[str, Any] = {
        "topology": build_snapshot(probe=False),
        "nodes": {},       # sid -> ui.element (节点卡)
        "edges": {},       # (a, b) -> svg <path> element id
        "list_rows": {},   # sid -> dict(row refs)
        "last_update_label": None,
        "topology_svg": None,
        "progress_row": progress_row,
        "progress_bar": progress_bar,
        "progress_label": progress_label,
    }

    # ---- 拓扑卡 -------------------------------------------------------------
    with ui.card().classes("w-full q-mb-md q-pa-md topology-card"):
        with ui.row().classes("items-center w-full"):
            ui.label("服务拓扑").classes("text-base font-semibold")
            ui.space()
            state["last_update_label"] = ui.label("").classes(
                "text-xs text-grey-7 font-mono"
            )
            _update_last_updated(state["last_update_label"], state["topology"])

        _render_topology(ui, state)
        _render_legend(ui)

    # ---- 服务列表卡（卡片网格，300 × 200）--------------------------------
    with ui.card().classes("w-full q-mb-md q-pa-md"):
        with ui.row().classes("items-center w-full q-mb-sm"):
            ui.label("服务列表").classes("text-base font-semibold")
            ui.space()
            ui.label(
                "每张卡对应一个服务：左上角彩色图标 = 分组（蓝=应用 / 紫=基础设施 / 琥珀=反代 / 青=客户端），"
                "外框颜色 = 实时状态（绿=运行 / 红=故障 / 灰=未配置）。"
            ).classes("text-xs text-grey-7")
        _render_service_cards(ui, state, pending_restart, refresh_banner)

    # ---- 运行时端口 / 凭据（来自 <CHAYUAN_ROOT>/runtime.json） ----------
    # 把"我现在到底监听哪个端口、自动生成的密码是多少"集中展示，给运维一个
    # 一眼就能查到的入口，避免靠 grep yaml + ps 拼凑。
    _render_runtime_endpoints_section(ui)

    # ---- 本机可用服务（端口扫描 + 协议握手探测的"发现式配置"入口）----------
    _render_local_services_section(ui, state)

    # ---- 全局操作：重启本进程 -----------------------------------------------
    _render_global_restart_card(ui, pending_restart)

    # ---- 异步刷新(探针走后台线程,避免冻结 UI 主循环)--------------------------
    import asyncio

    async def _refresh_async(*, show_progress: bool = False) -> None:
        """真探针走 to_thread,UI 主循环不阻塞。

        show_progress=True 时(首次 / 手动点刷新)亮进度条;周期 tick 静默刷新。
        """
        if show_progress:
            try:
                state["progress_row"].set_visibility(True)
                state["progress_bar"].props("indeterminate")
            except Exception:  # noqa: BLE001
                pass
        try:
            new_topo = await asyncio.to_thread(build_snapshot, probe=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("build_snapshot failed: %s", e)
            return
        finally:
            if show_progress:
                try:
                    state["progress_row"].set_visibility(False)
                except Exception:  # noqa: BLE001
                    pass
        state["topology"] = new_topo
        try:
            _apply_topology(state, new_topo)
            _update_last_updated(state["last_update_label"], new_topo)
        except Exception as e:  # noqa: BLE001
            logger.warning("apply topology failed: %s", e)

    # 手动点刷新 → 显示进度条
    refresh_btn.on("click", lambda _=None: asyncio.create_task(_refresh_async(show_progress=True)))

    async def _tick() -> None:
        if bool(getattr(auto_switch, "value", True)):
            await _refresh_async(show_progress=False)

    # 首次进页面立即触发一次探针(用 ui.timer once 推到下一帧,先让 placeholder 渲染)
    ui.timer(0.05, lambda: asyncio.create_task(_refresh_async(show_progress=True)), once=True)
    # 周期刷新 — safe_timer_cb 防 client deleted 警告
    from chayuan.server.config_panel._safe_ui import safe_timer_cb
    ui.timer(5.0, safe_timer_cb(_tick))


# ---------------------------------------------------------------------------
# 样式
# ---------------------------------------------------------------------------

_STYLE_FLAG = "_chayuan_service_page_styles_injected"


def _inject_styles(ui) -> None:
    """只注入一次全局样式（动画 + 节点布局 + 图例）。

    NiceGUI 内没有"只注入一次"的原生机制，这里用一个进程内的标记变量即可——
    配置面板是单进程进程内状态共享，足以避免重复注入。
    """
    if getattr(ui, _STYLE_FLAG, False):
        return
    css = r"""
    <style>
      .topology-card .topo-wrap {
        position: relative;
        width: 100%;
        max-width: 880px;
        margin: 0 auto;
      }
      .topology-card .topo-canvas {
        position: relative;
        width: 880px;
        height: 500px;
        max-width: 100%;
        margin: 0 auto;
      }
      .topology-card svg.topo-edges {
        position: absolute;
        inset: 0;
        pointer-events: none;
      }
      .topology-card .topo-node {
        position: absolute;
        width: 160px;
        height: 70px;
        transform: translate(-50%, -50%);
        display: flex;
        flex-direction: column;
        justify-content: center;
        padding: 8px 10px;
        border-radius: 10px;
        border: 2px solid #9ca3af;
        background: rgba(229,231,235,0.35);
        box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        cursor: pointer;
        transition: transform .15s ease, box-shadow .15s ease, background .2s ease, border-color .2s ease;
        z-index: 2;
      }
      .topology-card .topo-node:hover {
        transform: translate(-50%, -50%) scale(1.03);
        box-shadow: 0 4px 16px rgba(59,130,246,0.25);
      }
      .topology-card .topo-node .topo-node-head {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 13px;
        font-weight: 600;
      }
      .topology-card .topo-node .topo-node-sub {
        font-size: 11px;
        color: #4b5563;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        font-family: ui-monospace, Menlo, Consolas, monospace;
      }
      .topology-card .topo-node .topo-dot {
        width: 9px; height: 9px; border-radius: 50%;
        background: #9ca3af;
        box-shadow: 0 0 0 0 rgba(0,0,0,0);
      }
      .topology-card .topo-node.status-ok       { border-color:#22c55e; background:rgba(34,197,94,0.10); }
      .topology-card .topo-node.status-ok .topo-dot       { background:#22c55e; box-shadow:0 0 0 3px rgba(34,197,94,0.25); }
      /* 未就绪走琥珀黄：提醒但不报警，与"可恢复"的语义一致 */
      .topology-card .topo-node.status-down     { border-color:#f59e0b; background:rgba(245,158,11,0.12); }
      .topology-card .topo-node.status-down .topo-dot     { background:#f59e0b; animation: topo-pulse 1.4s infinite; }
      .topology-card .topo-node.status-disabled { border-color:#d1d5db; background:rgba(229,231,235,0.35); opacity:0.75; }
      .topology-card .topo-node.status-unknown  { border-color:#9ca3af; }

      /* 连线流光：按状态切色；dashoffset 动画让虚线"流动"。 */
      .topology-card .topo-edge {
        fill: none;
        stroke-width: 2.5;
        stroke-linecap: round;
        stroke-dasharray: 8 6;
        animation: topo-flow 1.2s linear infinite;
      }
      .topology-card .topo-edge.status-ok       { stroke:#3b82f6; }
      .topology-card .topo-edge.status-down     { stroke:#f59e0b; }
      .topology-card .topo-edge.status-disabled { stroke:#d1d5db; stroke-dasharray:4 8; animation:none; }
      .topology-card .topo-edge.status-unknown  { stroke:#9ca3af; }

      @keyframes topo-flow {
        from { stroke-dashoffset: 0; }
        to   { stroke-dashoffset: -28; }
      }
      @keyframes topo-pulse {
        0%,100% { box-shadow: 0 0 0 0 rgba(245,158,11,0.55); }
        50%     { box-shadow: 0 0 0 6px rgba(245,158,11,0.0); }
      }

      .topology-card .topo-legend {
        display:flex; flex-wrap:wrap; gap:14px;
        justify-content:center;
        margin-top:8px;
        font-size:12px; color:#4b5563;
      }
      .topology-card .topo-legend .topo-legend-item { display:flex; align-items:center; gap:6px; }
      .topology-card .topo-legend .lg-dot { width:10px; height:10px; border-radius:50%; }

      /* 服务列表行里的状态 pill */
      .svc-status-pill {
        display:inline-flex; align-items:center; gap:6px;
        padding: 2px 10px;
        border-radius: 999px;
        font-size: 12px;
        border: 1px solid transparent;
        line-height: 1.4;
        white-space: nowrap;
      }
      .svc-status-pill.status-ok       { color:#15803d; background:rgba(34,197,94,0.12);  border-color:#86efac; }
      .svc-status-pill.status-down     { color:#b45309; background:rgba(245,158,11,0.15); border-color:#fcd34d; }
      .svc-status-pill.status-disabled { color:#6b7280; background:rgba(229,231,235,0.5);  border-color:#e5e7eb; }
      .svc-status-pill.status-unknown  { color:#4b5563; background:rgba(229,231,235,0.5);  border-color:#e5e7eb; }

      /* --------- 服务卡片 (300 × 200) --------- */
      .svc-card-grid {
        display:grid;
        grid-template-columns: repeat(auto-fill, 300px);
        gap: 16px;
        justify-content: center;
        width: 100%;
      }
      .svc-card {
        position: relative;
        width: 300px; height: 200px;
        border-radius: 14px;
        background: #ffffff;
        border: 2px solid #e5e7eb;
        box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        padding: 16px;
        display: flex; flex-direction: column;
        gap: 10px;
        transition: transform .15s ease, box-shadow .2s ease, border-color .2s ease;
        overflow: hidden;
      }
      .svc-card::before {
        content: '';
        position: absolute; top:0; left:0; right:0; height:4px;
        background: linear-gradient(90deg, var(--svc-accent, #3b82f6), transparent 80%);
        opacity: 0.9;
      }
      .svc-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 20px rgba(59,130,246,0.18);
      }
      .svc-card.group-edge   { --svc-accent:#0ea5e9; }  /* 客户端：青 */
      .svc-card.group-proxy  { --svc-accent:#f59e0b; }  /* 反代：琥珀 */
      .svc-card.group-app    { --svc-accent:#3b82f6; }  /* 应用：蓝 */
      .svc-card.group-infra  { --svc-accent:#8b5cf6; }  /* 基础设施：紫 */

      .svc-card.status-ok       { border-color:#22c55e; }
      .svc-card.status-down     { border-color:#f59e0b; animation: svc-card-pulse 1.8s infinite; }
      .svc-card.status-disabled { border-color:#e5e7eb; opacity: 0.85; }
      .svc-card.status-unknown  { border-color:#cbd5e1; }

      @keyframes svc-card-pulse {
        0%,100% { box-shadow: 0 2px 10px rgba(245,158,11,0.22); }
        50%     { box-shadow: 0 0 0 4px rgba(245,158,11,0.20), 0 2px 10px rgba(245,158,11,0.32); }
      }

      .svc-card .svc-head {
        display: flex; align-items: center; gap: 12px;
      }
      .svc-card .svc-icon-wrap {
        width: 48px; height: 48px;
        border-radius: 12px;
        display: flex; align-items: center; justify-content: center;
        background: linear-gradient(135deg, var(--svc-accent) 0%, rgba(255,255,255,0.2) 160%);
        color: #fff;
        flex-shrink: 0;
        box-shadow: 0 3px 8px rgba(0,0,0,0.1);
      }
      .svc-card .svc-icon-wrap .material-icons {
        font-size: 28px;
        color: #ffffff;
      }
      .svc-card .svc-title {
        font-size: 16px; font-weight: 600; color: #111827;
        line-height: 1.2;
      }
      .svc-card .svc-subtitle {
        font-size: 11px; color: #6b7280;
        margin-top: 2px;
      }
      .svc-card .svc-summary {
        font-family: ui-monospace, Menlo, Consolas, monospace;
        font-size: 12px; color: #1f2937;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }
      .svc-card .svc-detail {
        font-size: 11px; color: #6b7280;
        overflow: hidden;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        word-break: break-all;
        flex: 1;
      }
      .svc-card .svc-actions {
        display: flex; gap: 4px; align-items: center; margin-top: auto;
      }
      .svc-card .svc-actions .q-btn { flex: 1; min-width: 0; }

      /* --------- 本机可用服务卡片 (280 × 170) --------- */
      .local-svc-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, 280px);
        gap: 14px;
        justify-content: center;
        width: 100%;
      }
      .local-svc-card {
        position: relative;
        width: 280px; height: 170px;
        border-radius: 12px;
        background: #ffffff;
        border: 2px solid #c7d2fe;
        box-shadow: 0 2px 10px rgba(99,102,241,0.08);
        padding: 14px;
        display: flex; flex-direction: column;
        gap: 8px;
        transition: transform .15s ease, box-shadow .2s ease, border-color .2s ease;
        overflow: hidden;
        cursor: pointer;
      }
      .local-svc-card:hover {
        transform: translateY(-2px);
        border-color: #6366f1;
        box-shadow: 0 8px 20px rgba(99,102,241,0.2);
      }
      .local-svc-card.unverified {
        border-color: #fde68a;
        background: linear-gradient(0deg, rgba(253,230,138,0.1), rgba(253,230,138,0.1)), #ffffff;
      }
      .local-svc-card.unverified:hover { border-color: #f59e0b; }
      .local-svc-card .local-head {
        display: flex; align-items: center; gap: 10px;
      }
      .local-svc-card .local-icon {
        width: 40px; height: 40px; border-radius: 10px;
        display: flex; align-items: center; justify-content: center;
        background: linear-gradient(135deg, #6366f1 0%, #a855f7 120%);
        color: #ffffff; flex-shrink: 0;
        box-shadow: 0 2px 6px rgba(99,102,241,0.3);
      }
      .local-svc-card.unverified .local-icon {
        background: linear-gradient(135deg, #f59e0b 0%, #fbbf24 120%);
        box-shadow: 0 2px 6px rgba(245,158,11,0.3);
      }
      .local-svc-card .local-icon .material-icons { font-size: 22px; color: #ffffff; }
      .local-svc-card .local-title {
        font-size: 15px; font-weight: 600; color: #111827; line-height: 1.2;
      }
      .local-svc-card .local-version {
        font-size: 11px; color: #6b7280; margin-top: 2px;
      }
      .local-svc-card .local-endpoint {
        font-family: ui-monospace, Menlo, Consolas, monospace;
        font-size: 12px; color: #1f2937;
        padding: 3px 8px;
        background: #f3f4f6;
        border-radius: 6px;
        display: inline-block;
        width: fit-content;
      }
      .local-svc-card .local-detail {
        font-size: 11px; color: #6b7280;
        flex: 1;
        overflow: hidden;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
      }
      .local-svc-card .local-footer {
        display: flex; align-items: center; justify-content: space-between;
        margin-top: auto;
      }
      .local-svc-card .local-bindings-pill {
        display: inline-flex; align-items: center; gap: 4px;
        padding: 2px 8px; border-radius: 999px;
        background: #eef2ff; color: #4338ca; font-size: 11px;
        border: 1px solid #c7d2fe;
      }
      .local-svc-card.unverified .local-bindings-pill {
        background: #fef3c7; color: #92400e; border-color: #fde68a;
      }
      .local-empty-hint {
        display: flex; align-items: center; justify-content: center;
        padding: 28px 16px;
        color: #6b7280; font-size: 13px;
        border: 1px dashed #d1d5db;
        border-radius: 10px;
        background: #fafafa;
      }

      /* --------- 绑定选择 dialog --------- */
      .bind-option {
        display: flex; flex-direction: column;
        padding: 14px; gap: 6px;
        border: 1px solid #e5e7eb;
        border-radius: 10px;
        transition: border-color .15s ease, background .15s ease;
      }
      .bind-option:hover { border-color: #6366f1; background: #f5f7ff; }
      .bind-option .bind-title {
        font-size: 14px; font-weight: 600; color: #1f2937;
      }
      .bind-option .bind-blurb {
        font-size: 12px; color: #4b5563; line-height: 1.5;
      }
      .bind-option .bind-current {
        font-size: 11px; color: #6b7280;
        font-family: ui-monospace, Menlo, Consolas, monospace;
        padding: 4px 8px; background: #f3f4f6;
        border-radius: 4px; display: inline-block; width: fit-content;
      }
      .bind-option .bind-warning {
        font-size: 11px; color: #92400e;
        background: #fef3c7; padding: 4px 8px; border-radius: 4px;
        border-left: 3px solid #f59e0b;
      }
      .bind-option.same-target .bind-current {
        background: #d1fae5; color: #065f46;
      }
    </style>
    """
    ui.add_head_html(css)
    setattr(ui, _STYLE_FLAG, True)


# ---------------------------------------------------------------------------
# 拓扑渲染
# ---------------------------------------------------------------------------

def _render_topology(ui, state: Dict[str, Any]) -> None:
    """在当前 ui 容器里画出 SVG 边 + 节点卡。"""
    topo: Topology = state["topology"]

    # 外层 wrap（居中） + 画布（绝对定位容器）
    with ui.element("div").classes("topo-wrap"):
        canvas = ui.element("div").classes("topo-canvas")
        with canvas:
            # --- SVG 边 ---
            svg_el = ui.html(_build_edges_svg(topo)).classes("w-full")
            state["topology_svg"] = svg_el

            # --- 节点卡 ---
            for svc in topo.ordered():
                _render_node(ui, state, svc)


def _build_edges_svg(topo: Topology) -> str:
    """生成一整段 SVG，包含所有 edge <path>。节点下游的 status 决定 edge 颜色。"""
    lines: List[str] = [
        f'<svg class="topo-edges" viewBox="0 0 {_CANVAS_W} {_CANVAS_H}" '
        f'preserveAspectRatio="none" aria-hidden="true">'
    ]
    for a, b in topo.edges:
        sa = topo.services.get(a)
        sb = topo.services.get(b)
        if sa is None or sb is None:
            continue
        # edge 状态：下游为 disabled → disabled；任一为 down → down；都 ok → ok
        if sb.status == STATUS_DISABLED or sa.status == STATUS_DISABLED:
            estatus = STATUS_DISABLED
        elif sa.status == STATUS_DOWN or sb.status == STATUS_DOWN:
            estatus = STATUS_DOWN
        elif sa.status == STATUS_OK and sb.status == STATUS_OK:
            estatus = STATUS_OK
        else:
            estatus = STATUS_UNKNOWN

        # 贝塞尔曲线：横向流线 + 中点控制点微调，避免压重叠
        x1, y1 = sa.x, sa.y
        x2, y2 = sb.x, sb.y
        cx = (x1 + x2) / 2
        path = (
            f'M {x1} {y1} C {cx} {y1}, {cx} {y2}, {x2} {y2}'
        )
        lines.append(
            f'<path class="topo-edge status-{estatus}" d="{path}" '
            f'data-edge="{a}->{b}"></path>'
        )
    lines.append("</svg>")
    return "\n".join(lines)


def _render_node(ui, state: Dict[str, Any], svc: ServiceSnapshot) -> None:
    node_div = ui.element("div").classes(f"topo-node status-{svc.status}")
    node_div.style(f"left:{svc.x}px;top:{svc.y}px")
    node_div.on(
        "click",
        lambda _=None, sid=svc.sid: _open_service_dialog(ui, state, sid),
    )
    with node_div:
        with ui.element("div").classes("topo-node-head"):
            ui.element("span").classes("topo-dot")
            ui.html(f'<i class="material-icons" style="font-size:16px">{svc.icon}</i>')
            ui.label(svc.label)
        ui.label(svc.summary or "—").classes("topo-node-sub")
    node_div.tooltip(f"{svc.label} · {_STATUS_LABEL.get(svc.status, '')}\n{svc.detail}")
    state["nodes"][svc.sid] = node_div


def _apply_topology(state: Dict[str, Any], topo: Topology) -> None:
    """把新的快照"局部"应用到已经渲染好的 DOM：更新节点 class + 边 SVG。"""
    state["topology"] = topo

    for sid, svc in topo.services.items():
        node = state["nodes"].get(sid)
        if node is None:
            continue
        node.classes(
            remove="status-ok status-down status-disabled status-unknown"
        )
        node.classes(add=f"status-{svc.status}")
        node.tooltip(
            f"{svc.label} · {_STATUS_LABEL.get(svc.status, '')}\n{svc.detail}"
        )

    # 重新生成 SVG 内容（edge 数量固定，直接整体替换即可）
    svg_el = state.get("topology_svg")
    if svg_el is not None:
        try:
            svg_el.content = _build_edges_svg(topo)
        except Exception:
            try:
                svg_el.set_content(_build_edges_svg(topo))
            except Exception:
                pass

    # 同步服务卡片的状态
    for sid, refs in state.get("list_rows", {}).items():
        svc = topo.services.get(sid)
        if not svc or not refs:
            continue
        try:
            # 卡片自身的状态 class（边框色 / 红色脉动）
            card_el = refs.get("card")
            if card_el is not None:
                card_el.classes(
                    remove="status-ok status-down status-disabled status-unknown",
                )
                card_el.classes(add=f"status-{svc.status}")
                card_el.tooltip(
                    f"{svc.label} · {_STATUS_LABEL.get(svc.status, '')}\n{svc.detail}"
                )
            # 状态 pill / summary / detail —— 都是 ui.html，用 set_content 更新
            _set_html(refs.get("status_label"), _status_pill_html(svc.status))
            _set_html(
                refs.get("summary_label"),
                f'<div class="svc-summary">{_escape(svc.summary or "—")}</div>',
            )
            _set_html(
                refs.get("detail_label"),
                f'<div class="svc-detail">{_escape(svc.detail or "")}</div>',
            )
        except Exception:
            pass


def _set_html(el, html: str) -> None:
    """兼容 NiceGUI 不同版本 ui.html 的内容更新方法。"""
    if el is None:
        return
    try:
        el.content = html
        return
    except Exception:
        pass
    try:
        el.set_content(html)
    except Exception:
        pass


def _status_icon(status: str) -> str:
    # down 用 🟡 而不是 🔴：配置到了但暂时不通是"警告"而非"致命错误"，
    # 与黄色主题统一更符合用户的心智预期。
    return {
        STATUS_OK: "🟢",
        STATUS_DOWN: "🟡",
        STATUS_DISABLED: "⚪",
        STATUS_UNKNOWN: "⚪",
    }.get(status, "⚪")


def _render_legend(ui) -> None:
    """画布下方的状态图例。"""
    legend_html = (
        '<div class="topo-legend">'
        '<span class="topo-legend-item"><span class="lg-dot" style="background:#22c55e"></span>运行中</span>'
        '<span class="topo-legend-item"><span class="lg-dot" style="background:#f59e0b"></span>未就绪 / 探活失败</span>'
        '<span class="topo-legend-item"><span class="lg-dot" style="background:#9ca3af"></span>未配置 / 未启用</span>'
        '<span class="topo-legend-item"><span class="lg-dot" style="background:#3b82f6"></span>流量走向（蓝色=正常，琥珀色=中断）</span>'
        "</div>"
    )
    ui.html(legend_html)


def _update_last_updated(label, topo: Topology) -> None:
    if label is None:
        return
    try:
        ts = time.strftime("%H:%M:%S", time.localtime(topo.generated_at))
        label.set_text(f"最近刷新：{ts}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 服务列表
# ---------------------------------------------------------------------------

def _render_service_cards(
    ui,
    state: Dict[str, Any],
    pending_restart: Optional[Dict[str, set]],
    refresh_banner: Optional[Callable[[], None]],
) -> None:
    """把服务渲染成 300×200 的响应式卡片网格。"""
    topo: Topology = state["topology"]
    # 卡片容器是原生 CSS grid（见 svc-card-grid），容纳所有 300×200 的卡
    with ui.element("div").classes("svc-card-grid"):
        for svc in topo.ordered():
            _render_service_card(ui, state, svc, pending_restart, refresh_banner)


def _render_service_card(
    ui,
    state: Dict[str, Any],
    svc: ServiceSnapshot,
    pending_restart: Optional[Dict[str, set]],
    refresh_banner: Optional[Callable[[], None]],
) -> None:
    """单张服务卡（300 × 200）。

    布局：
    - 顶部 accent 色条（按分组）
    - 左大图标（48×48 圆角渐变底）+ 标题 + 状态 pill
    - summary（host:port / URL，等宽字体一行）
    - detail（两行截断）
    - 底部「配置 / 探活 / 重启」横向 3 按钮等宽
    """
    refs: Dict[str, Any] = {}
    card = ui.element("div").classes(
        f"svc-card group-{svc.group} status-{svc.status}"
    )
    refs["card"] = card
    # 整卡可点：首选打开配置 dialog；对于没有 config_kind 的（如 client），提供 tooltip 说明
    if svc.config_kind:
        card.on(
            "click",
            lambda _=None, sid=svc.sid: _open_service_dialog(ui, state, sid),
        )
        card.style("cursor: pointer")
    card.tooltip(
        f"{svc.label} · {_STATUS_LABEL.get(svc.status, '')}\n{svc.detail}"
    )

    with card:
        # -- 头部：图标 + 标题 + 状态 pill --
        with ui.element("div").classes("svc-head"):
            ui.html(
                f'<div class="svc-icon-wrap">'
                f'<i class="material-icons">{svc.icon}</i>'
                f'</div>'
            )
            with ui.column().classes("q-gutter-none").style("min-width:0;flex:1"):
                ui.html(
                    f'<div class="svc-title">{svc.label}</div>'
                    f'<div class="svc-subtitle">{_group_label(svc.group)}</div>'
                )
            refs["status_label"] = ui.html(
                _status_pill_html(svc.status)
            )

        # -- 连接摘要（host:port / URL）--
        refs["summary_label"] = ui.html(
            f'<div class="svc-summary">{_escape(svc.summary or "—")}</div>'
        )

        # -- 详细信息（两行截断）--
        refs["detail_label"] = ui.html(
            f'<div class="svc-detail">{_escape(svc.detail or "")}</div>'
        )

        # -- 操作按钮（底部）--
        with ui.element("div").classes("svc-actions"):
            cfg_btn = ui.button(
                "配置", icon="settings",
                on_click=lambda _=None, sid=svc.sid: (
                    _open_service_dialog(ui, state, sid) if svc.config_kind else None
                ),
            ).props("flat dense size=sm color=primary no-caps")
            if not svc.config_kind:
                cfg_btn.props("disable")
                cfg_btn.tooltip("该节点仅用于拓扑展示，无可配置项")
            else:
                cfg_btn.tooltip("查看 / 修改该服务的连接参数")

            probe_btn = ui.button(
                "探活", icon="network_check",
                on_click=lambda _=None, sid=svc.sid: _probe_one(ui, state, sid),
            ).props("flat dense size=sm color=secondary no-caps")
            probe_btn.tooltip("对该服务单独进行一次真实探活")

            restart_btn = ui.button(
                "重启", icon="restart_alt",
                on_click=lambda _=None: _confirm_restart(ui, pending_restart),
            ).props("flat dense size=sm color=negative no-caps")
            if not svc.restartable:
                restart_btn.props("disable")
                restart_btn.tooltip("该节点不由本进程托管，无法在此重启")
            else:
                restart_btn.tooltip(
                    "触发一次整进程重启（所有 chayuan start 拉起的服务一起重启）"
                )

    state.setdefault("list_rows", {})[svc.sid] = refs


def _group_label(group: str) -> str:
    return {
        "edge":  "接入层 · 客户端",
        "proxy": "接入层 · 反向代理",
        "app":   "应用层 · 业务进程",
        "infra": "基础设施 · 中间件 / 存储",
    }.get(group, group)


def _status_pill_html(status: str) -> str:
    """用纯 HTML 渲染一个状态 pill，便于通过 ``set_content`` 原地更新。"""
    icon = _status_icon(status)
    text = _STATUS_LABEL.get(status, "")
    return (
        f'<span class="svc-status-pill status-{status}">'
        f'<span>{icon}</span><span>{text}</span>'
        f'</span>'
    )


def _escape(text: str) -> str:
    """最小 HTML 转义——服务名 / 详情来源于配置，可能含 <> &。"""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _probe_one(ui, state: Dict[str, Any], sid: str) -> None:
    """对单个服务做一次独立探活，用 ui.notify 给即时反馈。"""
    try:
        topo = build_snapshot()
    except Exception as e:  # noqa: BLE001
        ui.notify(f"探活失败：{e}", color="negative")
        return
    svc = topo.services.get(sid)
    if svc is None:
        ui.notify(f"未找到服务 {sid}", color="negative")
        return
    color = {
        STATUS_OK: "positive",
        STATUS_DOWN: "negative",
        STATUS_DISABLED: "info",
    }.get(svc.status, "info")
    ui.notify(
        f"{svc.label}：{_STATUS_LABEL.get(svc.status, '')} — {svc.detail}",
        color=color, timeout=5000,
    )
    _apply_topology(state, topo)
    _update_last_updated(state.get("last_update_label"), topo)


# ---------------------------------------------------------------------------
# 全局重启
# ---------------------------------------------------------------------------

def _render_global_restart_card(
    ui, pending_restart: Optional[Dict[str, set]],
) -> None:
    from chayuan.server.config_panel.restart import load_runtime, runtime_meta_path

    meta = load_runtime()
    with ui.card().classes("w-full q-mb-md q-pa-md"):
        with ui.row().classes("items-center w-full no-wrap"):
            ui.icon("restart_alt").classes("text-lg text-primary")
            ui.label("进程级重启").classes("text-base font-semibold")
            ui.space()
            if meta is None:
                ui.badge("未记录运行时元数据", color="grey")
            else:
                ui.badge(f"父进程 pid = {meta.get('pid')}", color="primary")

        if meta is None:
            ui.label(
                "没找到 .chayuan_runtime.json：很可能是用非 `chayuan start` 的方式起的面板，"
                "重启按钮已禁用。请在命令行里手动 Ctrl-C 后重新 `chayuan start`。"
            ).classes("text-sm text-negative q-mt-sm")
        else:
            ui.label(
                "触发后会 fork 出守护脚本，向父 chayuan 进程发送 SIGTERM，等退出后以相同 argv / cwd "
                "重新拉起整套服务（API / WebUI / 配置面板一起来）。本浏览器页面会断开约 10 秒。"
            ).classes("text-sm text-grey-8 q-mt-sm")
            ui.label(f"配置文件路径：{runtime_meta_path()}").classes(
                "text-xs font-mono text-grey-7 q-mt-xs"
            )

        with ui.row().classes("w-full q-mt-sm").style("justify-content:flex-end"):
            restart_btn = ui.button(
                "立即重启", icon="restart_alt",
                on_click=lambda: _confirm_restart(ui, pending_restart),
            )
            if meta is None:
                restart_btn.props("color=grey-5 disabled")
            else:
                restart_btn.props("color=negative")


def _confirm_restart(ui, pending_restart: Optional[Dict[str, set]]) -> None:
    with ui.dialog() as dialog, ui.card():
        ui.label("确定立即重启服务吗？").classes("text-base font-semibold")
        ui.label(
            "这会中断现有 API / 对话 / 面板连接，等待约 10 秒后刷新页面即可重新进入。"
        ).classes("text-sm text-grey-8")
        with ui.row().classes("justify-end w-full"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button(
                "确认重启",
                color="negative",
                on_click=lambda: (_do_restart(ui, pending_restart), dialog.close()),
            )
    dialog.open()


def _do_restart(ui, pending_restart: Optional[Dict[str, set]]) -> None:
    from chayuan.server.config_panel import restart as _restart

    try:
        info = _restart.trigger_restart(delay=1.0)
    except Exception as e:  # noqa: BLE001
        logger.exception("trigger_restart failed")
        ui.notify(f"触发重启失败：{type(e).__name__}: {e}", color="negative")
        return
    if pending_restart is not None:
        for s in pending_restart.values():
            s.clear()
    ui.notify(
        f"已触发重启（目标 pid={info['target_pid']}，守护脚本 pid={info['helper_pid']}）。"
        "请等待约 10 秒后刷新页面。",
        color="positive", timeout=8000,
    )


# ---------------------------------------------------------------------------
# 单服务 dialog
# ---------------------------------------------------------------------------

def _open_service_dialog(ui, state: Dict[str, Any], sid: str) -> None:
    """点击节点 / "配置"按钮时弹出的可滚动 dialog。

    根据 config_kind 选择渲染器：api/panel 走通用的"进程型"视图，
    redis/db/milvus 直接复用对应的配置卡（点"保存"会落盘到对应 yaml）。
    nginx 走一个轻量的 URL 表单。
    """
    topo: Topology = state["topology"]
    svc = topo.services.get(sid)
    if svc is None:
        ui.notify(f"未找到服务 {sid}", color="negative")
        return

    with ui.dialog() as dlg:
        with ui.card().classes("q-pa-md").style(
            "min-width: min(820px, 92vw); max-width: 92vw; "
            "max-height: 92vh; overflow: auto;"
        ):
            _render_dialog_header(ui, svc, dlg)
            ui.separator().classes("q-my-sm")
            try:
                _render_dialog_body(ui, svc)
            except Exception as e:  # noqa: BLE001
                logger.exception("service dialog body failed: %s", svc.config_kind)
                ui.label(
                    f"该服务的详情卡加载失败：{type(e).__name__}: {e}"
                ).classes("text-negative text-sm")
            with ui.row().classes("w-full justify-end q-mt-sm"):
                ui.button("关闭", on_click=dlg.close).props("flat")
    dlg.open()


def _render_dialog_header(ui, svc: ServiceSnapshot, dlg) -> None:
    with ui.row().classes("items-center w-full no-wrap").style("gap:10px"):
        ui.html(f'<i class="material-icons" style="font-size:28px;color:#334155">{svc.icon}</i>')
        with ui.column().classes("q-gutter-none").style("min-width:0;flex:1"):
            with ui.row().classes("items-center").style("gap:8px"):
                ui.label(svc.label).classes("text-xl font-semibold")
                ui.label(
                    f"{_status_icon(svc.status)} {_STATUS_LABEL.get(svc.status, '')}"
                ).classes(f"svc-status-pill status-{svc.status}")
            ui.label(svc.summary or "—").classes("text-xs text-grey-7 font-mono")
            if svc.detail:
                ui.label(svc.detail).classes("text-xs text-grey-6").style(
                    "word-break: break-all"
                )
        ui.button(icon="close", on_click=dlg.close).props("flat round dense").tooltip(
            "关闭"
        )


def _render_relocated_notice(
    ui, svc: "ServiceSnapshot", *, target_label: str, field_hint: str,
) -> None:
    """统一的"已迁至 服务配置"提示卡。

    本页（服务管理）保留只读摘要供运维诊断；编辑入口收敛在 service_config_page。
    """
    with ui.card().classes("w-full q-pa-md").style(
        "background:#eef6ff;border-left:4px solid #2185d0"
    ):
        with ui.row().classes("items-center w-full no-wrap").style("gap:10px"):
            ui.icon("info").classes("text-info").style("font-size:24px;flex:none")
            with ui.column().classes("flex-1 q-gutter-none"):
                ui.label(f"编辑入口已迁至「{target_label}」").classes(
                    "text-base font-semibold"
                )
                ui.label(
                    f"{svc.label} 的 {field_hint} 现在统一在拓扑卡片上点「编辑」。"
                    "这样保证一份 yaml 字段只有一处编辑入口，不会再出现两处写值"
                    "互相覆盖。"
                ).classes("text-xs text-grey-8")

    # 只读摘要：让运维定位时不用再切页
    if svc.summary or svc.detail:
        with ui.card().classes("w-full q-pa-md q-mt-sm"):
            ui.label("当前生效值（只读）").classes("text-xs text-grey-7 q-mb-xs")
            for label, value in (
                ("status", svc.status),
                ("summary", svc.summary),
                ("detail", svc.detail),
            ):
                if not value:
                    continue
                with ui.row().classes("w-full items-baseline").style("gap:10px"):
                    ui.label(label).classes("text-xs text-grey-7").style(
                        "min-width:70px;flex:none"
                    )
                    ui.label(str(value)).classes("text-xs font-mono").style(
                        "word-break:break-all;flex:1"
                    )


def _render_dialog_body(ui, svc: ServiceSnapshot) -> None:
    kind = svc.config_kind
    if kind in ("redis", "db"):
        # Redis / 主业务库的编辑入口已统一收敛到「🚀 服务配置」页拓扑卡片。
        # 这里只显示当前值（只读），引导用户去新位置改。
        _render_relocated_notice(
            ui, svc,
            target_label="🚀 服务配置",
            field_hint="REDIS_URL" if kind == "redis" else "SQLALCHEMY_DATABASE_URI",
        )
        return

    if kind == "milvus":
        from chayuan.server.config_panel.vs_config import render_vs_card
        ui.label(
            "Milvus / 其它向量库都在 kb_settings.yaml 里；可在这里切换默认类型并填写连接参数。"
            "注意 ⚠️：保存 DEFAULT_VS_TYPE 变化后需重启服务才会生效。"
        ).classes("text-xs text-grey-7 q-mb-xs")
        render_vs_card(ui)
        return

    if kind == "minio":
        _render_minio_dialog(ui, svc)
        return

    if kind == "nginx":
        _render_nginx_dialog(ui, svc)
        return

    if kind == "langfuse":
        _render_langfuse_dialog(ui, svc)
        return

    if kind == "kkfileview":
        _render_kkfileview_dialog(ui, svc)
        return

    if kind in ("api", "panel"):
        _render_process_dialog(ui, svc)
        return

    ui.label(f"{svc.label} 暂无可配置项。").classes("text-sm text-grey-7")


def _render_kkfileview_dialog(ui, svc: ServiceSnapshot) -> None:
    """kkFileView 旁车配置 — 与 KB 管理页的同名块共用同一份 yaml 字段
    (kb_settings.KKFILEVIEW_URL),保证两边视图一致。

    UI 上故意保持轻量:URL + 验证 + 保存 三件套即可,不重复造轮子。
    """
    cur = (svc.summary or "").strip()
    if cur in ("—", "未配置"):
        cur = ""

    with ui.card().classes("w-full q-pa-md"):
        ui.label("kkFileView 文件预览旁车").classes("text-base font-semibold q-mb-xs")
        ui.label(
            "配置后客户端预览会全格式接管(Office / WPS / 3D / CAD / 视频转码 等 100+ 格式);"
            "留空走前端内置 renderer。\n"
            "部署:`docker run -d -p 8012:8012 keking/kkfileview` —— 容器自带 LibreOffice + ffmpeg。\n"
            "浏览器要能直连本地址;同机部署时通常用 http://127.0.0.1:8012。",
        ).classes("text-xs text-grey-7 q-mb-sm").style("white-space: pre-line")

        url_el = ui.input(
            label="KKFILEVIEW_URL",
            value=cur,
            placeholder="http://127.0.0.1:8012",
        ).props("outlined dense clearable").classes("w-full")

        result_row = ui.row().classes("items-center q-mt-xs").style(
            "gap:6px; min-width:0"
        )
        with result_row:
            result_icon = ui.icon("info").style("font-size: 20px; color:#94a3b8")
            result_text = ui.label("").classes("text-sm text-grey-7")

        def _on_verify() -> None:
            val = (url_el.value or "").strip()
            if not val:
                result_icon.props("name=info color=grey")
                result_text.set_text("空地址 — 客户端会走内置 renderer")
                return
            result_icon.props("name=hourglass_top color=warning")
            result_text.set_text("验证中…")
            try:
                # 同步探活 1.5s 超时;dialog 是单次操作,不用线程池
                status, detail = _probe_kkfileview_topo(val, timeout=1.5)
            except Exception as e:  # noqa: BLE001
                result_icon.props("name=error color=negative")
                result_text.set_text(f"探活异常:{type(e).__name__}: {e}")
                return
            if status == STATUS_OK:
                result_icon.props("name=check_circle color=positive")
                result_text.set_text(f"在线 · {detail}")
            else:
                result_icon.props("name=error color=negative")
                result_text.set_text(f"不可达 · {detail}")

        def _on_save() -> None:
            val = (url_el.value or "").strip()
            try:
                yaml_store.save_updates("kb_settings.yaml", {"KKFILEVIEW_URL": val})
            except Exception as e:  # noqa: BLE001
                ui.notify(f"保存失败:{type(e).__name__}: {e}", type="negative")
                return
            try:
                from chayuan.settings import Settings as _S
                if hasattr(_S.kb_settings, "KKFILEVIEW_URL"):
                    _S.kb_settings.KKFILEVIEW_URL = val
            except Exception:  # noqa: BLE001
                pass
            ui.notify(
                "已保存到 kb_settings.yaml,前端下次刷新预览即生效"
                if val else "已清空 kkFileView 地址,客户端走内置 renderer",
                type="positive",
            )

        with ui.row().classes("q-mt-sm").style("gap:8px"):
            ui.button(
                "验证连通性", icon="network_check", on_click=_on_verify,
            ).props("flat dense color=primary")
            ui.button(
                "保存", icon="save", on_click=_on_save,
            ).props("unelevated dense color=primary")


def _render_process_dialog(ui, svc: ServiceSnapshot) -> None:
    """API / 配置面板 —— 都是本进程族里的服务；

    host / port 来自 basic_settings.yaml 的 API_SERVER / CONFIG_SERVER。
    这里展示 + 允许编辑；保存落 yaml，改动需要整进程重启才会生效。
    """
    kind_key = {
        "api":   ("API_SERVER",    "API 服务"),
        "panel": ("CONFIG_SERVER", "配置面板"),
    }.get(svc.config_kind, (None, svc.label))
    field_name = kind_key[0]
    if not field_name:
        ui.label("未知服务").classes("text-negative")
        return

    doc = yaml_store.load_yaml("basic_settings.yaml").doc or {}
    cfg = dict(doc.get(field_name) or {})
    host = str(cfg.get("host") or "127.0.0.1")
    port = str(cfg.get("port") or "")

    with ui.card().classes("w-full q-pa-md"):
        ui.label(f"{kind_key[1]} 绑定地址").classes("text-base font-semibold q-mb-sm")
        ui.label(
            "这是 chayuan 进程监听的 host:port；修改后需要触发「重启」让守护脚本以新端口"
            "重新起进程（旧连接会断开）。本机部署建议保持 127.0.0.1 + Nginx 反代的形式。"
        ).classes("text-xs text-grey-7 q-mb-sm").style("white-space: pre-line")

        with ui.grid(columns=2).classes("w-full q-gutter-sm"):
            host_el = (
                ui.input(label="绑定 host", value=host)
                .props("outlined dense")
                .classes("w-full")
            )
            host_el.tooltip(
                "0.0.0.0 = 监听所有网卡；127.0.0.1 = 仅本机可达（推荐 + Nginx 反代）"
            )
            port_el = (
                ui.input(label="绑定 port", value=port)
                .props("outlined dense")
                .classes("w-full")
            )
            port_el.tooltip("修改后 chayuan 主进程需要重启才会切新端口")

        result_row = ui.row().classes("items-center q-mt-sm").style(
            "gap:6px; min-width:0"
        )
        result_row.visible = False
        with result_row:
            result_icon = ui.icon("info").style("font-size: 20px")
            result_text = ui.label("").classes("text-sm")

        def _show(ok: bool, msg: str) -> None:
            result_row.visible = True
            result_icon.props(
                "name=check_circle color=positive" if ok
                else "name=error color=negative"
            )
            result_text.set_text(msg)

        def _on_test() -> None:
            try:
                p = int(str(port_el.value or "").strip())
            except ValueError:
                _show(False, "端口必须是整数")
                return
            if svc.config_kind == "api":
                status, detail = _probe_api(str(host_el.value or "127.0.0.1"), p)
                _show(status == STATUS_OK, detail)
            else:
                ok = _probe_tcp(str(host_el.value or "127.0.0.1"), p, timeout=0.8)
                _show(ok, "TCP 可达" if ok else "端口不可达")

        def _on_save() -> None:
            try:
                p = int(str(port_el.value or "").strip())
            except ValueError:
                ui.notify("端口必须是整数", color="negative")
                return
            h = str(host_el.value or "").strip() or "127.0.0.1"
            updates = {
                f"{field_name}.host": h,
                f"{field_name}.port": p,
            }
            try:
                _path, _bak, changes = yaml_store.save_updates(
                    "basic_settings.yaml", updates,
                )
            except Exception as e:  # noqa: BLE001
                ui.notify(f"保存失败：{type(e).__name__}: {e}", color="negative")
                return
            if changes:
                ui.notify(
                    f"已保存到 basic_settings.yaml（{len(changes)} 项）；"
                    "重启后会切到新 host:port。",
                    color="positive", timeout=6000,
                )
            else:
                ui.notify("配置未变化", color="info", timeout=3000)

        with ui.row().classes("w-full justify-end q-mt-sm").style("gap:8px"):
            ui.button("探活", icon="network_check", on_click=_on_test).props(
                "color=secondary dense"
            ).tooltip("对当前表单里的 host:port 真实跑一次探测")
            ui.button("保存", icon="save", on_click=_on_save).props(
                "color=primary dense"
            ).tooltip("写入 basic_settings.yaml（下次重启生效）")


def _render_minio_dialog(ui, svc: ServiceSnapshot) -> None:
    """MinIO / 文件存储编辑入口已迁至「🚀 服务配置」页拓扑卡片。

    本服务管理页只保留只读摘要 + 跳转引导，避免一份 yaml 字段多处编辑导致冲突。
    """
    _render_relocated_notice(
        ui, svc,
        target_label="🚀 服务配置",
        field_hint="FILE_STORAGE_BACKEND / MINIO_*",
    )


def _render_nginx_dialog(ui, svc: ServiceSnapshot) -> None:
    """Nginx 没有内置生命周期；只暴露一个 URL 用来探活，并附参考配置。"""
    doc = yaml_store.load_yaml("basic_settings.yaml").doc or {}
    current_url = str(doc.get("NGINX_URL") or "")

    with ui.card().classes("w-full q-pa-md"):
        ui.label("Nginx 反向代理").classes("text-base font-semibold q-mb-sm")
        ui.label(
            "Nginx 不由 chayuan 管理生命周期；这里只保存一个对外地址用于拓扑图的状态探活。"
            "生产部署强烈建议在 chayuan 前加一层反代：TLS 卸载、限流、keep-alive、"
            "gzip 都走 Nginx 做，让 API 只关心业务。"
        ).classes("text-xs text-grey-7 q-mb-sm").style("white-space: pre-line")

        url_el = (
            ui.input(label="Nginx 对外 URL", value=current_url,
                     placeholder="https://chayuan.example.com/")
            .props("outlined dense stack-label")
            .classes("w-full")
        )
        url_el.tooltip(
            "填写对外可访问的 URL，例如 https://chayuan.example.com 或 http://10.0.0.1。"
            "留空则拓扑图该节点显示为「未配置」。"
        )

        result_row = ui.row().classes("items-center q-mt-sm").style("gap:6px;min-width:0")
        result_row.visible = False
        with result_row:
            result_icon = ui.icon("info").style("font-size: 20px")
            result_text = ui.label("").classes("text-sm")

        def _show(ok: bool, msg: str) -> None:
            result_row.visible = True
            result_icon.props(
                "name=check_circle color=positive" if ok
                else "name=error color=negative"
            )
            result_text.set_text(msg)

        def _on_test() -> None:
            url = str(url_el.value or "").strip()
            if not url:
                _show(False, "URL 不能为空")
                return
            status, detail = _probe_nginx(url)
            _show(status == STATUS_OK, detail)

        def _on_save() -> None:
            url = str(url_el.value or "").strip()
            try:
                _path, _bak, changes = yaml_store.save_updates(
                    "basic_settings.yaml", {"NGINX_URL": url},
                )
            except Exception as e:  # noqa: BLE001
                ui.notify(f"保存失败：{type(e).__name__}: {e}", color="negative")
                return
            if changes:
                ui.notify("已保存 NGINX_URL", color="positive", timeout=4000)
            else:
                ui.notify("URL 未变化", color="info", timeout=3000)

        with ui.row().classes("w-full justify-end q-mt-sm").style("gap:8px"):
            ui.button("探活", icon="network_check", on_click=_on_test).props(
                "color=secondary dense"
            ).tooltip("对填入的 URL 发一个 GET 请求，2xx/3xx 视为通")
            ui.button("保存", icon="save", on_click=_on_save).props(
                "color=primary dense"
            ).tooltip("写入 basic_settings.yaml:NGINX_URL")

    with ui.expansion("示例 nginx.conf（流式 LLM 友好）", icon="code").classes(
        "w-full q-mt-sm"
    ):
        snippet = (
            "upstream chayuan_api { least_conn; server 127.0.0.1:62581; }\n"
            "server {\n"
            "  listen 443 ssl http2;\n"
            "  server_name chayuan.example.com;\n"
            "  # ssl_certificate / ssl_certificate_key ...\n"
            "\n"
            "  location /api/ {\n"
            "    proxy_pass http://chayuan_api/;\n"
            "    proxy_http_version 1.1;\n"
            "    proxy_set_header Connection '';\n"
            "    proxy_set_header Host $host;\n"
            "    proxy_buffering off;          # 保留流式 SSE\n"
            "    proxy_read_timeout 600s;\n"
            "    proxy_send_timeout 600s;\n"
            "  }\n"
            "\n"
            "}\n"
        )
        ui.code(snippet, language="nginx").classes("w-full").style(
            "white-space:pre; overflow-x:auto; font-size:12px"
        )


def _render_langfuse_dialog(ui, svc: ServiceSnapshot) -> None:
    """Langfuse 配置 dialog（LLM 链路追踪，**非必要服务**）。

    三件套（host / public_key / secret_key）写到 basic_settings.yaml；
    运行时环境变量同名项存在时会**优先于** yaml，所以 dialog 会标注每项的
    "生效来源"（env / yaml / unset），避免用户在面板里改了却奇怪"还是用旧的"。

    同时提供一个"一键禁用"开关对应 ``CHAYUAN_LANGFUSE_DISABLE``——当 Langfuse
    侧故障拖慢业务请求时，运维可以从这里一键跳过所有 Langfuse 调用。
    """
    doc = yaml_store.load_yaml("basic_settings.yaml").doc or {}
    cur_host = str(doc.get("LANGFUSE_HOST") or "")
    cur_pk = str(doc.get("LANGFUSE_PUBLIC_KEY") or "")
    cur_sk = str(doc.get("LANGFUSE_SECRET_KEY") or "")
    cur_kill = bool(doc.get("CHAYUAN_LANGFUSE_DISABLE") or False)

    # 读"运行时生效值的来源"供用户对齐心理模型
    src_info = {}
    try:
        from chayuan.server.observability.langfuse_integration import (
            effective_config as _lf_effective,
        )
        src_info = _lf_effective()
    except Exception:  # noqa: BLE001
        src_info = {}

    def _src_badge(field: str) -> str:
        source = src_info.get(field + "_SOURCE", "unset")
        if source == "env":
            return (
                f'<span style="display:inline-block;font-size:11px;'
                f'padding:1px 6px;border-radius:999px;'
                f'background:#fef3c7;color:#92400e;'
                f'border:1px solid #fde68a;margin-left:6px">'
                f'env 覆盖中</span>'
            )
        if source == "yaml":
            return (
                f'<span style="display:inline-block;font-size:11px;'
                f'padding:1px 6px;border-radius:999px;'
                f'background:#d1fae5;color:#065f46;'
                f'border:1px solid #86efac;margin-left:6px">'
                f'来自 yaml</span>'
            )
        return (
            f'<span style="display:inline-block;font-size:11px;'
            f'padding:1px 6px;border-radius:999px;'
            f'background:#f3f4f6;color:#6b7280;'
            f'border:1px solid #e5e7eb;margin-left:6px">'
            f'未设置</span>'
        )

    with ui.card().classes("w-full q-pa-md"):
        with ui.row().classes("items-center w-full no-wrap").style("gap:8px"):
            ui.label("Langfuse 链路追踪").classes("text-base font-semibold")
            ui.badge("非必要服务").props("color=grey")
        ui.label(
            "LLM 可观测性平台，记录每次对话 / Agent / RAG 的 prompt-response / "
            "token 消耗 / 延迟。缺失时主业务完全不受影响，仅失去链路追踪视图。"
            "\n\n"
            "运行时优先读环境变量 $LANGFUSE_HOST / $LANGFUSE_PUBLIC_KEY / "
            "$LANGFUSE_SECRET_KEY（K8s Secret 友好），env 未设时回退到下方 yaml。"
            "每个字段右侧徽章提示当前生效来源。"
        ).classes("text-xs text-grey-7 q-mb-sm").style("white-space: pre-line")

        with ui.grid(columns=1).classes("w-full q-gutter-sm"):
            # host
            with ui.row().classes("items-center no-wrap"):
                ui.html(
                    '<span style="font-size:12px;color:#374151">LANGFUSE_HOST</span>'
                    + _src_badge("LANGFUSE_HOST")
                )
            host_el = (
                ui.input(
                    label="服务端地址",
                    value=cur_host,
                    placeholder="http://127.0.0.1:3000 或 https://cloud.langfuse.com",
                )
                .props("outlined dense stack-label")
                .classes("w-full")
            )
            host_el.tooltip(
                "自托管：Docker Compose 默认 3000 端口；SaaS：cloud.langfuse.com 或 "
                "us.cloud.langfuse.com。留空即禁用 Langfuse。"
            )

            # public key
            with ui.row().classes("items-center no-wrap"):
                ui.html(
                    '<span style="font-size:12px;color:#374151">LANGFUSE_PUBLIC_KEY</span>'
                    + _src_badge("LANGFUSE_PUBLIC_KEY")
                )
            pk_el = (
                ui.input(
                    label="Public Key",
                    value=cur_pk,
                    placeholder="pk-lf-xxxxxxxx",
                )
                .props("outlined dense stack-label")
                .classes("w-full")
            )
            pk_el.tooltip("Langfuse 控制台项目页 → Settings → API Keys 里生成")

            # secret key（password widget）
            with ui.row().classes("items-center no-wrap"):
                ui.html(
                    '<span style="font-size:12px;color:#374151">LANGFUSE_SECRET_KEY</span>'
                    + _src_badge("LANGFUSE_SECRET_KEY")
                )
            sk_el = (
                ui.input(
                    label="Secret Key",
                    value=cur_sk,
                    placeholder="sk-lf-xxxxxxxx",
                    password=True,
                    password_toggle_button=True,
                )
                .props("outlined dense stack-label")
                .classes("w-full")
            )
            sk_el.tooltip(
                "对应 Secret Key。生产强烈建议通过环境变量 / Secret Manager 注入，"
                "避免写入 yaml。展示时已脱敏。"
            )

            # kill switch
            kill_el = ui.switch(
                "一键禁用 Langfuse（应急开关）",
                value=cur_kill,
            ).props("dense")
            kill_el.tooltip(
                "打开后，即使上面三项都填了也会跳过所有 Langfuse 调用。"
                "用于 Langfuse 侧故障拖慢业务时的应急兜底。"
                "等价环境变量 CHAYUAN_LANGFUSE_DISABLE=1（任一生效即禁用）。"
            )

        # 结果行
        result_row = ui.row().classes("items-center q-mt-sm").style(
            "gap:6px;min-width:0"
        )
        result_row.visible = False
        with result_row:
            result_icon = ui.icon("info").style("font-size: 20px")
            result_text = ui.label("").classes("text-sm")

        def _show(ok: bool, msg: str) -> None:
            result_row.visible = True
            result_icon.props(
                "name=check_circle color=positive" if ok
                else "name=error color=negative"
            )
            result_text.set_text(msg)

        def _on_test() -> None:
            """对当前表单里的 host 调 /api/public/health（不需凭据）。"""
            host = str(host_el.value or "").strip()
            if not host:
                _show(False, "LANGFUSE_HOST 不能为空")
                return
            url = host.rstrip("/") + "/api/public/health"
            if not url.startswith(("http://", "https://")):
                url = "http://" + url
            ok, detail = _probe_http(url, timeout=2.0)
            _show(ok, detail + (f" · {url}" if ok else ""))

        def _on_save() -> None:
            updates = {
                "LANGFUSE_HOST": str(host_el.value or "").strip(),
                "LANGFUSE_PUBLIC_KEY": str(pk_el.value or "").strip(),
                "LANGFUSE_SECRET_KEY": str(sk_el.value or ""),
                "CHAYUAN_LANGFUSE_DISABLE": bool(kill_el.value),
            }
            try:
                _path, _bak, changes = yaml_store.save_updates(
                    "basic_settings.yaml", updates,
                )
            except Exception as e:  # noqa: BLE001
                ui.notify(f"保存失败：{type(e).__name__}: {e}", color="negative")
                return
            if not changes:
                ui.notify("Langfuse 配置未变化", color="info", timeout=3000)
                return
            # 让 is_enabled 的缓存失效，下次请求会重新判定
            try:
                from chayuan.server.observability.langfuse_integration import (
                    reset_for_tests,
                )
                reset_for_tests()
            except Exception:  # noqa: BLE001
                pass
            ui.notify(
                f"已保存 {len(changes)} 项到 basic_settings.yaml；"
                "Langfuse 将在下一次 LLM 调用时按新配置启用。",
                color="positive", timeout=6000,
            )

        with ui.row().classes("w-full justify-end q-mt-sm").style("gap:8px"):
            ui.button("探活", icon="network_check", on_click=_on_test).props(
                "color=secondary dense"
            ).tooltip("对 /api/public/health 发 GET；2xx 视为通")
            ui.button("保存", icon="save", on_click=_on_save).props(
                "color=primary dense"
            ).tooltip("写入 basic_settings.yaml 对应字段")


# ---------------------------------------------------------------------------
# 本机可用服务发现区（Local Services Discovery）
#
# 原则：
# - 扫描 / 探针的全部实现放在 ``local_service_detector``；
# - 业务上"本机服务 → 项目配置点"的映射放在 ``local_service_catalog``；
# - 本模块只负责渲染成卡片 + 绑定选择 dialog，不做业务判断。
# ---------------------------------------------------------------------------

def _render_runtime_endpoints_section(ui) -> None:
    """渲染 ``<CHAYUAN_ROOT>/runtime.json`` 中的服务端点 + 凭据快照。

    与 ``_render_local_services_section`` 不同：
      - local_services 关心"本机系统里跑着哪些 Postgres/Redis"（发现 / 接入）；
      - runtime_endpoints 关心"本进程已经分配 / 启动了哪些服务"（事后审计）。

    所有密码默认 ``****``；点"显示明文"才能看到真实值，避免被同事路过看到。
    单击行右侧的复制按钮可一键拷贝 host:port / 用户 / URL，方便配 yaml。
    """
    try:
        from chayuan.server.runtime import (
            allocate_core_ports,
            get_runtime_info,
        )
    except Exception:  # noqa: BLE001
        # runtime 子包尚未安装 / 加载失败：直接跳过本卡片，不影响主页面
        return

    state_local = {"reveal": False}

    def _refresh_table() -> None:
        ri = get_runtime_info()
        eps = ri.list_endpoints()
        rows.clear()
        for ep in eps:
            m = ep if state_local["reveal"] else ep.masked()
            rows.append({
                "name": str(m.get("name") or ""),
                "kind": str(m.get("kind") or ""),
                "host": str(m.get("host") or ""),
                "port": int(m.get("port") or 0),
                "user": str(m.get("user") or "-"),
                "password": str(m.get("password") or "-"),
                "url": str(m.get("url") or ""),
                "started": (
                    int(m.get("started_at"))
                    if m.get("started_at") else 0
                ),
            })
        table.update()
        empty_label.set_visibility(len(rows) == 0)

    def _toggle_reveal(e):  # noqa: ARG001
        state_local["reveal"] = bool(reveal_switch.value)
        _refresh_table()

    def _do_recheck() -> None:
        try:
            result = allocate_core_ports()
        except Exception as e:  # noqa: BLE001
            ui.notify(f"重新分配端口失败：{e}", type="negative")
            return
        if result.warnings:
            ui.notify(
                "端口已重新分配；有 {} 项需要注意（详见日志）".format(len(result.warnings)),
                type="warning",
            )
        else:
            ui.notify("端口与凭据已刷新", type="positive")
        _refresh_table()

    with ui.card().classes("w-full q-mb-md q-pa-md"):
        with ui.row().classes("items-center w-full q-mb-sm no-wrap"):
            ui.label("运行时端点 / 凭据").classes("text-base font-semibold")
            ui.space()
            reveal_switch = ui.switch("显示明文密码", value=False).props("dense color=warning")
            reveal_switch.on("update:model-value", _toggle_reveal)
            ui.button("重新分配端口", icon="autorenew").props("flat dense size=sm color=primary").on("click", lambda _: _do_recheck())

        ui.label(
            "下表来自 <CHAYUAN_ROOT>/runtime.json：API / 配置面板 / vendor 服务最终绑定的"
            "端口与自动生成的凭据；密码默认 **** 掩码，确认四下无人后再点'显示明文'。"
        ).classes("text-xs text-grey-7 q-mb-sm")

        rows: List[Dict[str, Any]] = []
        cols = [
            {"name": "name", "label": "服务", "field": "name", "align": "left"},
            {"name": "kind", "label": "类型", "field": "kind", "align": "left"},
            {"name": "host", "label": "Host", "field": "host", "align": "left"},
            {"name": "port", "label": "Port", "field": "port", "align": "right"},
            {"name": "user", "label": "用户", "field": "user", "align": "left"},
            {"name": "password", "label": "密码", "field": "password", "align": "left"},
            {"name": "url", "label": "URL", "field": "url", "align": "left"},
        ]
        table = ui.table(columns=cols, rows=rows, row_key="name").props("dense flat bordered").classes("w-full")
        empty_label = ui.label(
            "（runtime.json 还没有任何服务记录；点'重新分配端口'让 chayuan 立即写入一份）"
        ).classes("text-xs text-grey-7")

        _refresh_table()


def _render_local_services_section(ui, state: Dict[str, Any]) -> None:
    """渲染"本机可用服务"卡片区。

    用一张大卡装一个可刷新的网格；初始化时异步跑一次扫描（探针都是短超时，
    总时间 <1s，直接同步跑也不会卡 UI，这里就同步调用保持简单）。
    """
    from chayuan.server.config_panel.local_service_detector import (
        detect_local_services, guess_extra_ports_from_settings,
    )

    with ui.card().classes("w-full q-mb-md q-pa-md"):
        with ui.row().classes("items-center w-full q-mb-sm no-wrap"):
            ui.label("本机可用服务").classes("text-base font-semibold")
            ui.space()
            scan_result_label = ui.label("").classes("text-xs text-grey-7 font-mono")
            rescan_btn = ui.button(
                "重新扫描", icon="radar",
            ).props("flat dense size=sm color=primary").tooltip(
                "重新扫描本机正在监听的服务端口并做协议握手"
            )

        ui.label(
            "扫描本机 LISTEN 端口并做协议握手，列出所有**可以接入本项目**的后端"
            "（Redis / Postgres / MySQL / Milvus / MinIO / Elasticsearch 等）。"
            "点击任一服务，系统会识别它在项目里可能的用途（业务库 / 知识库 / 缓存 / 对象存储），"
            "引导你一步到位把它接进来；多用途服务（如 Postgres）会让你先选用途再配置。"
        ).classes("text-xs text-grey-7 q-mb-sm").style("white-space: pre-line")

        # 网格容器 —— 里面由 _render_local_services_grid 动态刷新
        grid_container = ui.element("div").classes("local-svc-grid")

        def _rescan() -> None:
            grid_container.clear()
            try:
                services = detect_local_services(
                    extra_ports=guess_extra_ports_from_settings(),
                )
            except Exception as e:  # noqa: BLE001
                logger.exception("detect_local_services failed")
                with grid_container:
                    ui.element("div").classes("local-empty-hint").props(
                        f'data-error="{type(e).__name__}"'
                    )
                    ui.label(f"扫描失败：{type(e).__name__}: {e}").classes(
                        "text-sm text-negative"
                    )
                scan_result_label.set_text("扫描失败")
                return
            _render_local_services_grid(ui, grid_container, services, state)
            ts = time.strftime("%H:%M:%S", time.localtime())
            scan_result_label.set_text(f"{ts} · 发现 {len(services)} 项")

        rescan_btn.on("click", lambda _=None: _rescan())
        state["local_rescan"] = _rescan
        _rescan()


def _render_local_services_grid(
    ui,
    container,
    services: List[Any],   # List[DetectedService]，类型延迟引用避免 import 循环
    state: Dict[str, Any],
) -> None:
    """把扫描结果渲染成卡片网格。空列表时给个友好的占位。"""
    from chayuan.server.config_panel.local_service_catalog import get_service_type

    with container:
        if not services:
            with ui.element("div").classes("local-empty-hint"):
                ui.html(
                    '<div style="text-align:center;line-height:1.6">'
                    '<div style="font-size:22px;margin-bottom:6px">🔍</div>'
                    '没有在本机发现 Redis / Postgres / MySQL / Milvus / MinIO / ES。'
                    '<br>如果你确实跑了某个服务，可能是端口不在默认候选表里；'
                    '先到「基础配置 / 知识库配置」把端口填上，再回来扫描就能识别。'
                    '</div>'
                )
            return
        for svc in services:
            entry = get_service_type(svc.kind)
            _render_local_service_card(ui, svc, entry, state)


def _render_local_service_card(
    ui,
    svc,                   # DetectedService
    entry,                 # Optional[ServiceTypeEntry]
    state: Dict[str, Any],
) -> None:
    """单张本机服务卡。

    - verified=True → 紫色主题 + "可接入"按钮
    - verified=False → 琥珀色 + 提示"端口占用但协议未验证"
    - entry=None（目录未覆盖该 kind）→ 卡片仅展示信息，按钮置灰
    """
    card_cls = "local-svc-card"
    if not svc.verified:
        card_cls += " unverified"
    card = ui.element("div").classes(card_cls)
    card.tooltip(
        f"{svc.label} · {svc.host}:{svc.port}"
        + (f"\n{svc.detail}" if svc.detail else "")
    )

    can_bind = entry is not None and bool(entry.bindings)
    if can_bind:
        card.on(
            "click",
            lambda _=None, s=svc, e=entry: _open_bind_dialog(ui, s, e, state),
        )
    else:
        card.style("cursor: default")

    icon_name = (entry.icon if entry else None) or _default_icon_for_kind(svc.kind)

    with card:
        with ui.element("div").classes("local-head"):
            ui.html(
                f'<div class="local-icon">'
                f'<i class="material-icons">{icon_name}</i>'
                f'</div>'
            )
            with ui.column().classes("q-gutter-none").style("min-width:0;flex:1"):
                ui.html(f'<div class="local-title">{_escape(svc.label)}</div>')
                sub = ""
                if svc.version:
                    sub = f"v{svc.version}"
                elif not svc.verified:
                    sub = "未验证"
                if svc.process_name:
                    sub = (sub + " · " if sub else "") + f"{svc.process_name}"
                if sub:
                    ui.html(f'<div class="local-version">{_escape(sub)}</div>')

        ui.html(
            f'<span class="local-endpoint">{svc.host}:{svc.port}</span>'
        )

        ui.html(
            f'<div class="local-detail">{_escape(svc.detail or "")}</div>'
        )

        with ui.element("div").classes("local-footer"):
            if can_bind:
                binding_count = len(entry.bindings)
                pill_text = (
                    "1 个用途" if binding_count == 1
                    else f"{binding_count} 个用途"
                )
                ui.html(
                    f'<span class="local-bindings-pill">'
                    f'<i class="material-icons" style="font-size:12px">link</i>'
                    f'{pill_text}</span>'
                )
                ui.button(
                    "接入项目" if binding_count == 1 else "选择用途",
                    icon="arrow_forward",
                    on_click=lambda _=None, s=svc, e=entry: _open_bind_dialog(
                        ui, s, e, state,
                    ),
                ).props("flat dense size=sm color=primary no-caps")
            else:
                ui.html(
                    '<span class="local-bindings-pill">'
                    '<i class="material-icons" style="font-size:12px">info</i>'
                    '仅展示</span>'
                )


def _default_icon_for_kind(kind: str) -> str:
    return {
        "redis": "memory",
        "postgresql": "storage",
        "mysql": "storage",
        "milvus": "scatter_plot",
        "minio": "inventory_2",
        "elasticsearch": "travel_explore",
    }.get(kind, "lan")


def _open_bind_dialog(ui, svc, entry, state: Dict[str, Any]) -> None:
    """『绑定用途选择』对话框。

    单绑定 → 直接在 dialog 里展示一个条目 + 「应用并配置」按钮；
    多绑定 → 展示所有候选，用户挑一个。点击条目：
    1. 调用 ``binding.prefill(svc)`` 把 host/port 写进对应 yaml（只写连接端点）；
    2. 关闭本 dialog；
    3. 打开 ``binding.open_dialog(ui)`` 让用户补凭据、测试、保存。
    """
    with ui.dialog() as dlg:
        with ui.card().classes("q-pa-md").style(
            "min-width: min(640px, 92vw); max-width: 92vw; "
            "max-height: 92vh; overflow: auto;"
        ):
            with ui.row().classes("items-center w-full no-wrap").style("gap:10px"):
                ui.html(
                    f'<div class="local-icon" style="width:40px;height:40px">'
                    f'<i class="material-icons">'
                    f'{entry.icon or _default_icon_for_kind(svc.kind)}</i>'
                    f'</div>'
                )
                with ui.column().classes("q-gutter-none").style("min-width:0;flex:1"):
                    ui.label(
                        f"接入本机 {svc.label} → 项目"
                    ).classes("text-lg font-semibold")
                    summary_parts = [f"{svc.host}:{svc.port}"]
                    if svc.version:
                        summary_parts.append(f"v{svc.version}")
                    if svc.process_name:
                        summary_parts.append(svc.process_name)
                    ui.label(" · ".join(summary_parts)).classes(
                        "text-xs text-grey-7 font-mono"
                    )
                ui.button(icon="close", on_click=dlg.close).props(
                    "flat round dense"
                ).tooltip("关闭")

            ui.separator().classes("q-my-sm")

            if len(entry.bindings) > 1:
                ui.label(
                    "该服务在项目里有多个用途，选择一个你想接入的位置："
                ).classes("text-sm text-grey-8 q-mb-sm")
            else:
                ui.label(
                    "本机这个服务可以接入项目的如下位置："
                ).classes("text-sm text-grey-8 q-mb-sm")

            for binding in entry.bindings:
                _render_binding_option(ui, svc, binding, dlg, state)

            ui.separator().classes("q-my-sm")
            ui.label(
                "温馨提示：点击「接入并配置」会把 host:port 先写入对应 yaml（不会"
                "碰凭据 / 数据库名等已有字段），然后弹出该服务的完整配置卡；"
                "你在配置卡里补齐凭据并保存即可真正生效。"
            ).classes("text-xs text-grey-7").style("white-space: pre-line")

            with ui.row().classes("w-full justify-end q-mt-sm"):
                ui.button("关闭", on_click=dlg.close).props("flat")
    dlg.open()


def _render_binding_option(ui, svc, binding, parent_dlg, state: Dict[str, Any]) -> None:
    """单条"绑定用途"的卡片 + 操作按钮。"""
    try:
        preview = binding.preview(svc)
    except Exception as e:  # noqa: BLE001
        logger.exception("binding preview failed: %s", binding.target_id)
        from chayuan.server.config_panel.local_service_catalog import BindingPreview
        preview = BindingPreview(
            configured=False,
            current_summary=f"预览失败：{type(e).__name__}: {e}",
        )

    extra_cls = " same-target" if preview.same_as_detected else ""
    with ui.element("div").classes(f"bind-option{extra_cls}"):
        with ui.row().classes("items-center w-full no-wrap"):
            ui.html(f'<div class="bind-title">{_escape(binding.label)}</div>')
            ui.space()
            ui.html(
                f'<span class="text-xs text-grey-7" '
                f'style="font-family:ui-monospace,Menlo,monospace">'
                f'{_escape(binding.config_file)}</span>'
            )

        ui.html(f'<div class="bind-blurb">{_escape(binding.blurb)}</div>')

        # 当前状态
        state_line = (
            f"当前：{preview.current_summary}" if preview.configured
            else preview.current_summary
        )
        ui.html(f'<div class="bind-current">{_escape(state_line)}</div>')
        if preview.same_as_detected:
            ui.label("✓ 当前配置就是这个本机实例，无需更改").classes(
                "text-xs text-positive"
            )

        if preview.warning:
            ui.html(
                f'<div class="bind-warning">⚠️ {_escape(preview.warning)}</div>'
            )

        with ui.row().classes("w-full justify-end q-mt-xs").style("gap:8px"):
            ui.button(
                "只看不改，打开配置", icon="visibility",
                on_click=lambda _=None, b=binding, d=parent_dlg: _apply_binding(
                    ui, svc, b, d, state, do_prefill=False,
                ),
            ).props("flat dense size=sm color=grey-7 no-caps").tooltip(
                "不写入任何 yaml，直接打开该服务的配置卡"
            )
            btn_label = "已一致，打开配置" if preview.same_as_detected else "接入并配置"
            ui.button(
                btn_label, icon="link",
                on_click=lambda _=None, b=binding, d=parent_dlg: _apply_binding(
                    ui, svc, b, d, state, do_prefill=not preview.same_as_detected,
                ),
            ).props("dense size=sm color=primary no-caps")


def _apply_binding(
    ui, svc, binding, parent_dlg, state: Dict[str, Any], *, do_prefill: bool,
) -> None:
    """执行绑定：prefill 写端点 → 关闭本 dialog → 打开目标服务的配置卡。"""
    if do_prefill:
        try:
            changed = binding.prefill(svc)
        except Exception as e:  # noqa: BLE001
            logger.exception("binding prefill failed: %s", binding.target_id)
            ui.notify(
                f"写入 {binding.config_file} 失败：{type(e).__name__}: {e}",
                color="negative",
            )
            return
        if changed:
            ui.notify(
                f"已把 {svc.host}:{svc.port} 写入 {binding.config_file}（"
                f"{len(changed)} 项：{', '.join(changed[:3])}"
                f"{'…' if len(changed) > 3 else ''}），"
                "请在下方配置卡里补齐凭据并保存。",
                color="positive", timeout=6000,
            )
        else:
            ui.notify(
                f"{binding.config_file} 已是最新，无需修改；打开配置卡。",
                color="info",
            )

    try:
        parent_dlg.close()
    except Exception:
        pass

    # 打开目标服务的配置卡 dialog（复用现有 render_*_card）
    try:
        with ui.dialog() as cfg_dlg:
            with ui.card().classes("q-pa-md").style(
                "min-width: min(820px, 92vw); max-width: 92vw; "
                "max-height: 92vh; overflow: auto;"
            ):
                with ui.row().classes("items-center w-full no-wrap"):
                    ui.label(f"{binding.label} · 配置").classes(
                        "text-xl font-semibold"
                    )
                    ui.space()
                    ui.button(icon="close", on_click=cfg_dlg.close).props(
                        "flat round dense"
                    ).tooltip("关闭")
                ui.separator().classes("q-my-sm")
                try:
                    binding.open_dialog(ui)
                except Exception as e:  # noqa: BLE001
                    logger.exception(
                        "binding open_dialog failed: %s", binding.target_id,
                    )
                    ui.label(
                        f"配置卡渲染失败：{type(e).__name__}: {e}"
                    ).classes("text-negative text-sm")
                with ui.row().classes("w-full justify-end q-mt-sm"):
                    ui.button(
                        "完成", icon="check",
                        on_click=lambda: (
                            cfg_dlg.close(),
                            # 关闭后顺带重刷扫描：用户保存后端点可能变，同一实例下次会显示"已一致"
                            state.get("local_rescan", lambda: None)(),
                        ),
                    ).props("color=primary")
        cfg_dlg.open()
    except Exception as e:  # noqa: BLE001
        logger.exception("open config dialog failed: %s", binding.target_id)
        ui.notify(
            f"打开配置卡失败：{type(e).__name__}: {e}", color="negative",
        )


__all__ = [
    "render_service_page",
    "build_snapshot",
    "Topology",
    "ServiceSnapshot",
]
