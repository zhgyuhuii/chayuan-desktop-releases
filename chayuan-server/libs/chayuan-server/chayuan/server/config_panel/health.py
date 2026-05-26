"""统一健康检查引擎（doctor）。

本模块把原本散落在 ``scalability.py`` / ``/readyz`` / ``cli status`` 的一次性检查，
整合成「多维度可机读、可自动修复」的一套体检系统，参考 OpenClaw `doctor` 命令
的「检查 → 告知 → 修复」三段式：

1. **检查**：按 **分类** 跑一组 :class:`HealthCheck`；每条有稳定 ``id``、
   ``severity``（critical / warning / info / ok）、说明、修复建议、配置片段。
2. **告知**：输出既可以走 UI（配置面板「⑧ 性能与可扩展性」整页复用本报告），
   也可以走 CLI（``chayuan doctor``）。JSON 输出适合在 CI / k8s probe 里做判定。
3. **修复**：每条检查可以声明 ``fixer_id``，注册表 :data:`FIXERS` 里预置了
   「生成面板凭据 / 创建数据目录 / pip install 运行时依赖 / 初始化 KB 元数据表」
   等幂等操作；用户在面板点一键修复或 CLI 里 ``--fix`` 就能批量执行。

分类划分（:data:`CATEGORIES`）：

- ``config``        配置完整性（yaml 语法、面板凭据、默认模型是否注册等）
- ``resource``      本机资源（磁盘 / 内存 / CPU / 日志大小）
- ``runtime``       运行时依赖（Python 版本、按需 pip 包）
- ``connectivity``  外部连通性（DB / Redis / 向量库 / LLM 平台）
- ``api``           服务健康（/healthz /readyz、各端口存活）
- ``scale``         性能 / 可扩展性（复用 :mod:`scalability` 里的静态体检）

使用示例::

    from chayuan.server.config_panel.health import build_report, run_fixers

    report = build_report()
    for c in report.checks:
        print(c.severity, c.category, c.id, c.summary)

    run_fixers(["ensure_panel_credentials", "make_data_dirs"])

UI / CLI 都走同一个 :func:`build_report`；新增检查只需往对应 ``_check_*`` 列表
里加一条即可，CLI 自动同步。
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import os
import socket
import time
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("chayuan.config_panel.health")


# ---------------------------------------------------------------------------
# 枚举 / 常量
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}

SEVERITY_META: Dict[str, Dict[str, str]] = {
    "critical": {"icon": "error",        "color": "negative", "label": "严重", "cli": "CRIT"},
    "warning":  {"icon": "warning",      "color": "warning",  "label": "警告", "cli": "WARN"},
    "info":     {"icon": "info",         "color": "info",     "label": "建议", "cli": "INFO"},
    "ok":       {"icon": "check_circle", "color": "positive", "label": "达标", "cli": "OK  "},
}

CATEGORIES: Dict[str, Dict[str, Any]] = {
    "config":       {"order": 0, "label": "配置",     "icon": "tune",       "desc": "yaml / 数据目录 / 面板凭据 / 默认模型注册"},
    "resource":     {"order": 1, "label": "系统资源", "icon": "memory",     "desc": "磁盘 / 内存 / CPU / 日志大小"},
    "runtime":      {"order": 2, "label": "运行时",   "icon": "extension",  "desc": "Python 版本与按需依赖包"},
    "connectivity": {"order": 3, "label": "连通性",   "icon": "cable",      "desc": "数据库 / Redis / 向量库 / LLM 平台"},
    "api":          {"order": 4, "label": "服务",     "icon": "monitor_heart", "desc": "API / 对话界面 / 配置面板 / /healthz /readyz"},
    "scale":        {"order": 5, "label": "性能扩展", "icon": "speed",      "desc": "部署模式 / 限流 / 多副本 / 中间件（静态体检）"},
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class HealthCheck:
    """一条体检项结论。

    - ``id``：稳定标识，**UI 折叠 / CLI 过滤 / CI 黑名单** 都靠它；命名约定
      ``<category>.<short_key>``（如 ``runtime.pkg.redis``、``api.readyz``）。
    - ``severity``：``critical`` / ``warning`` / ``info`` / ``ok``。
    - ``fixer_id``：非空时代表有「一键修复」能力，指向 :data:`FIXERS` 的 key。
    """

    id: str
    category: str
    title: str
    severity: str
    summary: str
    impact: str = ""
    fix_hint: str = ""
    snippet: str = ""
    snippet_lang: str = "yaml"
    fixer_id: str = ""

    @property
    def fixable(self) -> bool:
        return bool(self.fixer_id) and self.fixer_id in FIXERS and self.severity != "ok"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["fixable"] = self.fixable
        return d


@dataclass
class HealthReport:
    checks: List[HealthCheck] = field(default_factory=list)
    mode: str = "dev"
    generated_at: str = ""
    elapsed_ms: int = 0

    @property
    def counts(self) -> Dict[str, int]:
        out = {k: 0 for k in SEVERITY_META}
        for c in self.checks:
            out[c.severity] = out.get(c.severity, 0) + 1
        return out

    @property
    def worst_severity(self) -> str:
        worst = "ok"
        for c in self.checks:
            if SEVERITY_ORDER.get(c.severity, 99) < SEVERITY_ORDER.get(worst, 99):
                worst = c.severity
        return worst

    @property
    def est_concurrent_users(self) -> str:
        """粗估并发承载；逻辑与原 scalability.Report 一致。"""
        c = self.counts
        if c.get("critical", 0) >= 1:
            return "≈ 几十 (有严重瓶颈，不适合上生产)"
        if c.get("warning", 0) >= 3:
            return "≈ 几百"
        if c.get("warning", 0) >= 1:
            return "≈ 1000–2000"
        return "≈ 5000+ (已满足扩容目标)"

    def by_category(self) -> Dict[str, List[HealthCheck]]:
        out: Dict[str, List[HealthCheck]] = {k: [] for k in CATEGORIES}
        for c in self.checks:
            out.setdefault(c.category, []).append(c)
        return out

    def fixable_checks(self) -> List[HealthCheck]:
        return [c for c in self.checks if c.fixable]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "mode": self.mode,
            "elapsed_ms": self.elapsed_ms,
            "counts": self.counts,
            "worst_severity": self.worst_severity,
            "est_concurrent_users": self.est_concurrent_users,
            "checks": [c.to_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _cpu_count() -> int:
    try:
        return max(1, mp.cpu_count())
    except Exception:
        return max(1, os.cpu_count() or 1)


def _probe_tcp(host: str, port: Any, timeout: float = 0.8) -> bool:
    try:
        p = int(port)
    except (TypeError, ValueError):
        return False
    h = (host or "127.0.0.1").strip() or "127.0.0.1"
    if h == "0.0.0.0":
        h = "127.0.0.1"
    try:
        with socket.create_connection((h, p), timeout=timeout):
            return True
    except OSError:
        return False


def _http_get(url: str, timeout: float = 1.5) -> Tuple[int, str]:
    """返回 ``(status_code, body_prefix)``；异常时 status=0。body 截断到 400 字符。"""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(400).decode("utf-8", errors="replace")
            return int(resp.status), body
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def _platform_field(p: Any, key: str, default: Any = None) -> Any:
    """MODEL_PLATFORMS 条目可能是 dict 也可能是 pydantic PlatformConfig；统一读取。"""
    if isinstance(p, dict):
        return p.get(key, default)
    return getattr(p, key, default)


def _bytes_human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ---------------------------------------------------------------------------
# 配置 / 数据目录
# ---------------------------------------------------------------------------

def _check_chayuan_root(bs) -> HealthCheck:
    from chayuan.settings import CHAYUAN_ROOT
    root = Path(CHAYUAN_ROOT)
    if not root.exists():
        return HealthCheck(
            id="config.chayuan_root",
            category="config",
            title="数据目录 CHAYUAN_ROOT",
            severity="critical",
            summary=f"CHAYUAN_ROOT 不存在：{root}",
            fix_hint="运行 `chayuan init` 或点「一键修复」自动创建。",
            fixer_id="make_data_dirs",
        )
    if not os.access(root, os.W_OK):
        return HealthCheck(
            id="config.chayuan_root",
            category="config",
            title="数据目录 CHAYUAN_ROOT",
            severity="critical",
            summary=f"CHAYUAN_ROOT 存在但不可写：{root}",
            fix_hint=f"`chmod u+w {root}` 或换一个有写权限的目录。",
        )
    return HealthCheck(
        id="config.chayuan_root",
        category="config",
        title="数据目录 CHAYUAN_ROOT",
        severity="ok",
        summary=f"存在且可写：{root}",
    )


def _check_data_dirs(bs) -> HealthCheck:
    """data/{logs,media,temp} 与 KB_ROOT_PATH 是否就位。"""
    missing: List[str] = []
    for attr in ("DATA_PATH", "LOG_PATH", "MEDIA_PATH", "BASE_TEMP_DIR"):
        try:
            p = Path(getattr(bs, attr))
            if not p.exists():
                missing.append(f"{attr}={p}")
        except Exception as e:  # noqa: BLE001
            missing.append(f"{attr}(读取失败：{e})")
    try:
        kb = Path(getattr(bs, "KB_ROOT_PATH", ""))
        if not kb.exists():
            missing.append(f"KB_ROOT_PATH={kb}")
    except Exception:
        pass

    if missing:
        return HealthCheck(
            id="config.data_dirs",
            category="config",
            title="数据目录子目录",
            severity="warning",
            summary=f"下列目录缺失：{', '.join(missing)}",
            fix_hint="点「一键修复」调用 basic_settings.make_dirs() 幂等创建。",
            fixer_id="make_data_dirs",
        )
    return HealthCheck(
        id="config.data_dirs",
        category="config",
        title="数据目录子目录",
        severity="ok",
        summary="logs / media / temp / knowledge_base 都已就位。",
    )


def _check_yaml_syntax() -> HealthCheck:
    """每个配置 yaml 语法能否被 ruamel 解析。"""
    from chayuan.server.config_panel import schema as _schema
    from chayuan.server.config_panel import yaml_store

    broken: List[str] = []
    for fs in _schema.ALL_SCHEMAS:
        try:
            load = yaml_store.load_yaml(fs.filename)
            if load.exists and load.doc is None:
                broken.append(f"{fs.filename}（内容为空/None）")
        except Exception as e:  # noqa: BLE001
            broken.append(f"{fs.filename}（{type(e).__name__}: {e}）")

    if broken:
        return HealthCheck(
            id="config.yaml_syntax",
            category="config",
            title="YAML 语法",
            severity="critical",
            summary="以下配置文件无法解析：" + "；".join(broken),
            fix_hint="回到对应页的「原始 YAML」tab 校验；或从 .bak 备份还原。",
        )
    return HealthCheck(
        id="config.yaml_syntax",
        category="config",
        title="YAML 语法",
        severity="ok",
        summary="全部配置文件均可解析。",
    )


def _check_panel_credentials(bs) -> HealthCheck:
    keys = ("PANEL_USERNAME", "PANEL_PASSWORD_HASH", "PANEL_SESSION_SECRET", "PANEL_LOGIN_PATH")
    missing = [k for k in keys if not str(getattr(bs, k, "") or "").strip()]
    if missing:
        return HealthCheck(
            id="config.panel_credentials",
            category="config",
            title="配置面板凭据",
            severity="critical",
            summary="以下字段未设置：" + ", ".join(missing),
            impact="未设置面板用户名 / 密码散列时，配置面板会拒绝登录；缺 SESSION_SECRET 会每次重启换密钥导致会话失效。",
            fix_hint="「一键修复」调用 ensure_panel_credentials 生成缺失项并写盘；密码会落在 `<root>/initial_credentials.txt`。",
            fixer_id="ensure_panel_credentials",
        )
    return HealthCheck(
        id="config.panel_credentials",
        category="config",
        title="配置面板凭据",
        severity="ok",
        summary="username / password_hash / session_secret / login_path 均已就位。",
    )


def _check_default_llm(model) -> HealthCheck:
    default_llm = (getattr(model, "DEFAULT_LLM_MODEL", "") or "").strip()
    if not default_llm:
        return HealthCheck(
            id="config.default_llm",
            category="config",
            title="默认 LLM DEFAULT_LLM_MODEL",
            severity="warning",
            summary="未设置默认 LLM 模型。",
            fix_hint="在「④ 模型配置」里指定 DEFAULT_LLM_MODEL。",
        )
    platforms = list(getattr(model, "MODEL_PLATFORMS", []) or [])
    registered = False
    for p in platforms:
        models = _platform_field(p, "llm_models", [])
        if models == "auto" or (isinstance(models, (list, tuple)) and default_llm in list(models)):
            registered = True
            break
    if not registered:
        return HealthCheck(
            id="config.default_llm",
            category="config",
            title="默认 LLM DEFAULT_LLM_MODEL",
            severity="warning",
            summary=f"DEFAULT_LLM_MODEL={default_llm}，但未在任何 MODEL_PLATFORMS.llm_models 中注册。",
            fix_hint="把该模型名加进某个平台的 llm_models，或把平台 auto_detect_model 设为 true。",
        )
    return HealthCheck(
        id="config.default_llm",
        category="config",
        title="默认 LLM DEFAULT_LLM_MODEL",
        severity="ok",
        summary=f"{default_llm} 已在 MODEL_PLATFORMS 中注册。",
    )


def _check_default_embedding(model) -> HealthCheck:
    default_em = (getattr(model, "DEFAULT_EMBEDDING_MODEL", "") or "").strip()
    if not default_em:
        return HealthCheck(
            id="config.default_embedding",
            category="config",
            title="默认 Embedding",
            severity="warning",
            summary="未设置 DEFAULT_EMBEDDING_MODEL。",
        )
    platforms = list(getattr(model, "MODEL_PLATFORMS", []) or [])
    for p in platforms:
        models = _platform_field(p, "embed_models", [])
        if models == "auto" or (isinstance(models, (list, tuple)) and default_em in list(models)):
            return HealthCheck(
                id="config.default_embedding",
                category="config",
                title="默认 Embedding",
                severity="ok",
                summary=f"{default_em} 已在 MODEL_PLATFORMS.embed_models 中注册。",
            )
    return HealthCheck(
        id="config.default_embedding",
        category="config",
        title="默认 Embedding",
        severity="warning",
        summary=f"DEFAULT_EMBEDDING_MODEL={default_em} 未在任何平台的 embed_models 中注册。",
    )


# ---------------------------------------------------------------------------
# 系统资源
# ---------------------------------------------------------------------------

def _check_disk(bs) -> HealthCheck:
    from chayuan.settings import CHAYUAN_ROOT
    root = Path(CHAYUAN_ROOT)
    try:
        import shutil
        usage = shutil.disk_usage(root if root.exists() else root.parent)
    except Exception as e:  # noqa: BLE001
        return HealthCheck(
            id="resource.disk",
            category="resource",
            title="磁盘空间",
            severity="warning",
            summary=f"无法获取磁盘使用量：{type(e).__name__}: {e}",
        )
    free = usage.free
    total = usage.total
    pct_free = (free / total * 100) if total else 0
    if free < 1 * 1024**3:
        sev = "critical"
    elif free < 10 * 1024**3:
        sev = "warning"
    else:
        sev = "ok"
    return HealthCheck(
        id="resource.disk",
        category="resource",
        title="磁盘空间",
        severity=sev,
        summary=(
            f"剩余 {_bytes_human(free)} / {_bytes_human(total)}（{pct_free:.1f}% free）"
            f"，挂载点 {root if root.exists() else root.parent}"
        ),
        fix_hint="清理日志 / 旧向量索引 / 备份（`chayuan_data/data/logs`, `*.bak`），或迁移数据目录。",
    )


def _check_memory() -> HealthCheck:
    total = avail = None
    source = ""
    try:
        import psutil  # type: ignore
        vm = psutil.virtual_memory()
        total, avail = vm.total, vm.available
        source = "psutil"
    except Exception:
        try:
            with open("/proc/meminfo", encoding="utf-8") as f:
                info = {}
                for line in f:
                    k, _, rest = line.partition(":")
                    info[k.strip()] = rest.strip().split()[0]
                total = int(info["MemTotal"]) * 1024
                avail = int(info.get("MemAvailable", info.get("MemFree", 0))) * 1024
                source = "/proc/meminfo"
        except Exception as e:  # noqa: BLE001
            return HealthCheck(
                id="resource.memory",
                category="resource",
                title="内存",
                severity="info",
                summary=f"无法获取内存信息（非 Linux 且未装 psutil）：{e}",
                fix_hint="`pip install psutil` 后可展示详细内存使用。",
            )
    pct_free = (avail / total * 100) if total else 0
    if avail is not None and avail < 512 * 1024**2:
        sev = "critical"
    elif avail is not None and avail < 2 * 1024**3:
        sev = "warning"
    else:
        sev = "ok"
    return HealthCheck(
        id="resource.memory",
        category="resource",
        title="内存",
        severity=sev,
        summary=f"可用 {_bytes_human(avail)} / 总 {_bytes_human(total)}（{pct_free:.1f}% free，{source}）",
    )


def _check_cpu() -> HealthCheck:
    n = _cpu_count()
    sev = "ok" if n >= 2 else "warning"
    return HealthCheck(
        id="resource.cpu",
        category="resource",
        title="CPU 核心",
        severity=sev,
        summary=f"可用 CPU 核心数：{n}（推荐 UVICORN_WORKERS ≈ {2 * n + 1}）",
    )


def _check_logs_size(bs) -> HealthCheck:
    try:
        log_dir = Path(bs.LOG_PATH)
    except Exception:
        return HealthCheck(
            id="resource.logs",
            category="resource",
            title="日志体积",
            severity="info",
            summary="LOG_PATH 未就绪。",
        )
    if not log_dir.exists():
        return HealthCheck(
            id="resource.logs",
            category="resource",
            title="日志体积",
            severity="info",
            summary=f"日志目录尚未创建：{log_dir}",
        )
    total = 0
    files = 0
    for p in log_dir.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
                files += 1
            except OSError:
                pass
    if total > 5 * 1024**3:
        sev = "warning"
    else:
        sev = "ok"
    return HealthCheck(
        id="resource.logs",
        category="resource",
        title="日志体积",
        severity=sev,
        summary=f"{log_dir} 共 {files} 个文件，{_bytes_human(total)}",
        fix_hint="若体积过大可在 basic_settings 中调整日志保留策略，或手动清理旧 run_* 目录。" if sev != "ok" else "",
    )


# ---------------------------------------------------------------------------
# 运行时 / 依赖
# ---------------------------------------------------------------------------

def _check_python_version() -> HealthCheck:
    import sys
    v = sys.version_info
    s = f"{v.major}.{v.minor}.{v.micro}"
    if v < (3, 9):
        sev = "critical"
    elif v < (3, 10):
        sev = "warning"
    else:
        sev = "ok"
    return HealthCheck(
        id="runtime.python",
        category="runtime",
        title="Python 版本",
        severity=sev,
        summary=f"当前 Python {s}（推荐 ≥ 3.10）",
    )


_PKG_CHECK_PLAN: List[Tuple[str, str, str, str]] = [
    # (import_name, pip_requirement, reason_attr / marker, human desc)
    # marker 为空时始终检查；形如 ``attr:X`` 表示 bs.X 真值才检查
]


def _need_pkg(bs, kb, model, check: Tuple[str, str, str]) -> bool:
    """根据运行配置判定这个可选包是否「当下一定需要」。"""
    import_name, _req, marker = check
    if not marker:
        return True
    if marker == "redis_enabled":
        return bool((getattr(bs, "REDIS_URL", "") or "").strip())
    if marker == "rate_limit":
        return bool(getattr(bs, "RATE_LIMIT_ENABLED", False))
    if marker == "ingest_async":
        return bool(getattr(bs, "INGEST_ASYNC_ENABLED", False))
    if marker == "metrics":
        return bool(getattr(bs, "METRICS_ENABLED", True))
    if marker == "otel":
        return bool(getattr(bs, "OTEL_ENABLED", False))
    if marker == "uri_postgres":
        return (getattr(bs, "SQLALCHEMY_DATABASE_URI", "") or "").lower().startswith(("postgresql", "postgres"))
    if marker == "uri_mysql":
        return (getattr(bs, "SQLALCHEMY_DATABASE_URI", "") or "").lower().startswith(("mysql", "mariadb"))
    if marker == "vs_milvus":
        return (getattr(kb, "DEFAULT_VS_TYPE", "faiss") or "").lower() == "milvus"
    if marker == "vs_pg":
        return (getattr(kb, "DEFAULT_VS_TYPE", "faiss") or "").lower() in ("pg", "pgvector")
    return True


def _check_packages(bs, kb, model) -> List[HealthCheck]:
    """运行时可选 / 条件依赖。fixer_id 指向 ``install_pkg:<import_name>``，
    装完后下次体检自动变绿。"""
    plan: List[Tuple[str, str, str, str]] = [
        ("redis", "redis>=5.0,<6.0", "redis_enabled", "限流 / 缓存 / 队列"),
        ("redis", "redis>=5.0,<6.0", "rate_limit", "限流"),
        ("arq", "arq>=0.25,<0.27", "ingest_async", "异步入库队列"),
        ("prometheus_client", "prometheus-client>=0.19", "metrics", "/metrics 埋点"),
        ("opentelemetry.sdk", "opentelemetry-sdk>=1.25", "otel", "OTEL SDK"),
        ("opentelemetry.exporter.otlp", "opentelemetry-exporter-otlp-proto-http>=1.25", "otel", "OTLP 导出"),
        ("psycopg2", "psycopg2-binary>=2.9", "uri_postgres", "PostgreSQL 驱动"),
        ("pymysql", "pymysql>=1.1", "uri_mysql", "MySQL 驱动"),
        ("pymilvus", "pymilvus>=2.6,<2.7", "vs_milvus", "Milvus 客户端（与 v2.6.x server 对齐）"),
        ("langchain_milvus", "langchain-milvus>=0.3,<0.4", "vs_milvus", "LangChain 1.x Milvus 适配"),
    ]
    seen = set()
    checks: List[HealthCheck] = []
    for (import_name, req, marker, desc) in plan:
        if not _need_pkg(bs, kb, model, (import_name, req, marker)):
            continue
        if import_name in seen:
            continue
        seen.add(import_name)

        try:
            __import__(import_name)
            checks.append(HealthCheck(
                id=f"runtime.pkg.{import_name}",
                category="runtime",
                title=f"Python 包 {import_name}",
                severity="ok",
                summary=f"已安装（{desc}）",
            ))
        except Exception:
            checks.append(HealthCheck(
                id=f"runtime.pkg.{import_name}",
                category="runtime",
                title=f"Python 包 {import_name}",
                severity="warning",
                summary=f"缺失；{desc} 路径将走降级或直接不可用。",
                fix_hint=f"`pip install '{req}'`；或点「一键修复」自动通过 chayuan 的镜像源补齐。",
                snippet=f"pip install '{req}'\n",
                snippet_lang="bash",
                fixer_id=f"install_pkg:{import_name}|{req}",
            ))
    return checks


# ---------------------------------------------------------------------------
# 连通性
# ---------------------------------------------------------------------------

def _check_database_connect(bs) -> HealthCheck:
    uri = (getattr(bs, "SQLALCHEMY_DATABASE_URI", "") or "").strip()
    if not uri:
        return HealthCheck(
            id="connectivity.database",
            category="connectivity",
            title="元数据数据库连接",
            severity="critical",
            summary="SQLALCHEMY_DATABASE_URI 为空。",
        )
    try:
        from sqlalchemy import create_engine, text
        eng = create_engine(uri, connect_args={}, pool_pre_ping=True)
        t0 = time.time()
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        cost = int((time.time() - t0) * 1000)
        eng.dispose()
        scheme = uri.split("://", 1)[0]
        return HealthCheck(
            id="connectivity.database",
            category="connectivity",
            title="元数据数据库连接",
            severity="ok",
            summary=f"{scheme} SELECT 1 成功，耗时 {cost} ms。",
        )
    except Exception as e:  # noqa: BLE001
        # 隐藏密码
        safe_uri = uri
        try:
            from urllib.parse import urlparse, urlunparse
            p = urlparse(uri)
            if p.password:
                safe_uri = urlunparse(p._replace(netloc=p.netloc.replace(f":{p.password}", ":***")))
        except Exception:
            pass
        return HealthCheck(
            id="connectivity.database",
            category="connectivity",
            title="元数据数据库连接",
            severity="critical",
            summary=f"连接失败：{type(e).__name__}: {e}",
            impact=f"URI={safe_uri}",
            fix_hint="检查库是否启动、用户密码是否正确；PostgreSQL 请先 `pip install psycopg2-binary`。",
        )


def _check_redis_connect(bs) -> HealthCheck:
    url = (getattr(bs, "REDIS_URL", "") or "").strip()
    if not url:
        mode = (getattr(bs, "DEPLOYMENT_MODE", "dev") or "dev").lower()
        return HealthCheck(
            id="connectivity.redis",
            category="connectivity",
            title="Redis 连接",
            severity=("warning" if mode == "prod" else "info"),
            summary="REDIS_URL 未配置；限流 / 缓存 / 队列将退化为单机内存。",
            fix_hint="在「基础配置 → Redis 连接」或「⑧ 性能与可扩展性」里填写 REDIS_URL。",
            snippet="REDIS_URL: redis://127.0.0.1:6379/0\n",
        )
    try:
        from chayuan.server.shared.redis_health import sync_probe
        ok, reason = sync_probe(timeout=2.0, use_cache=False)
    except Exception as e:  # noqa: BLE001
        ok, reason = False, f"{type(e).__name__}: {e}"
    if ok:
        return HealthCheck(
            id="connectivity.redis",
            category="connectivity",
            title="Redis 连接",
            severity="ok",
            summary=reason,
        )
    return HealthCheck(
        id="connectivity.redis",
        category="connectivity",
        title="Redis 连接",
        severity="critical",
        summary=f"Redis 不可用：{reason}",
        fix_hint="确认 Redis 已启动并可从本进程网络可达；或清空 REDIS_URL 回落单机模式。",
    )


def _check_vector_store_connect(kb) -> HealthCheck:
    vs = (getattr(kb, "DEFAULT_VS_TYPE", "faiss") or "").lower()
    if vs == "faiss":
        return HealthCheck(
            id="connectivity.vector_store",
            category="connectivity",
            title="向量库连通性",
            severity="info",
            summary="默认使用 FAISS（进程内），无外部依赖。",
        )
    if vs == "milvus":
        cfg = (getattr(kb, "kbs_config", {}) or {}).get("milvus", {}) or {}
        # 兼容两种配置形态:① 老式 host+port;② 新式 uri = http://host:port。
        # 优先 uri(新默认),fallback host/port 老配置。
        uri = cfg.get("uri")
        if uri:
            try:
                from urllib.parse import urlparse
                p = urlparse(str(uri))
                host = p.hostname or "127.0.0.1"
                port = p.port or (443 if p.scheme == "https" else 19530)
            except Exception:  # noqa: BLE001
                host, port = "127.0.0.1", 19530
        else:
            host = cfg.get("host") or "127.0.0.1"
            port = cfg.get("port") or 19530
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = 19530

        # **真探活**:之前光做 TCP 握手会让"端口开但 gRPC 没就绪"的状态被误判为 OK,
        # 实际上传文件时再失败。现在分两步:
        #   1) TCP 端口可达(必要不充分)
        #   2) pymilvus connect + list_collections(充分:能完整跑业务)
        # 任一步失败都给精确文案,让运维一眼看出是哪个层。
        tcp_ok = _probe_tcp(host, port, timeout=1.5)
        if not tcp_ok:
            return HealthCheck(
                id="connectivity.vector_store",
                category="connectivity",
                title="Milvus 连通性",
                severity="critical",
                summary=f"无法连接 Milvus {host}:{port}(TCP 端口不可达)",
                fix_hint="先 `docker compose up -d milvus` 或确认安全组开放 19530。",
            )

        # 端口通了,跑一次 pymilvus 握手 + list_collections。失败就 critical,因为
        # 业务上传/检索一定也会失败 — 不要让 health 假绿
        probe_alias = "_health_probe"
        grpc_err: Optional[str] = None
        try:
            from pymilvus import connections
            try:
                # 用临时 alias,跑完就 disconnect,不污染业务的 default alias
                connect_kw = {k: v for k, v in cfg.items() if k != "alias"}
                try:
                    connections.disconnect(probe_alias)
                except Exception:  # noqa: BLE001
                    pass
                connections.connect(alias=probe_alias, **connect_kw)
                handler = connections._fetch_handler(probe_alias)  # type: ignore[attr-defined]
                if handler is None:
                    grpc_err = "pymilvus 握手成功但拿不到 handler"
                else:
                    handler.list_collections(timeout=3.0)
            finally:
                try:
                    connections.disconnect(probe_alias)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            grpc_err = f"{type(e).__name__}: {e}"

        if grpc_err:
            return HealthCheck(
                id="connectivity.vector_store",
                category="connectivity",
                title="Milvus 连通性",
                severity="critical",
                summary=(
                    f"TCP {host}:{port} 可达,但 pymilvus gRPC 握手失败:{grpc_err}"
                ),
                fix_hint=(
                    "Milvus standalone 端口开了不代表已就绪 — 它通常依赖 etcd / minio 先启动。"
                    "确认 docker logs 里看到 'Connected to etcd' 后再试;"
                    "客户端 pymilvus>=2.3 需对应版本的 Milvus 服务端。"
                ),
            )
        return HealthCheck(
            id="connectivity.vector_store",
            category="connectivity",
            title="Milvus 连通性",
            severity="ok",
            summary=f"成功连接 Milvus {host}:{port}(gRPC list_collections 通过)",
        )
    if vs in ("pg", "pgvector", "relyt"):
        return HealthCheck(
            id="connectivity.vector_store",
            category="connectivity",
            title="pgvector 连通性",
            severity="info",
            summary="pgvector 共用 PostgreSQL，参考上方「元数据数据库连接」结果。",
        )
    return HealthCheck(
        id="connectivity.vector_store",
        category="connectivity",
        title=f"向量库连通性（{vs}）",
        severity="info",
        summary="未实现该后端的自动探活；请在「③ 知识库配置」中验证连接。",
    )


def _check_llm_platforms_connect(model) -> List[HealthCheck]:
    """对每个 MODEL_PLATFORMS 条目做 `/v1/models` HTTP 探活；不通时 TCP 兜底。"""
    platforms = list(getattr(model, "MODEL_PLATFORMS", []) or [])
    if not platforms:
        return [HealthCheck(
            id="connectivity.llm_platform",
            category="connectivity",
            title="LLM 平台连通性",
            severity="critical",
            summary="未配置任何 MODEL_PLATFORMS。",
        )]
    checks: List[HealthCheck] = []
    for p in platforms:
        name = _platform_field(p, "platform_name", "?")
        url = (_platform_field(p, "api_base_url", "") or "").strip()
        ptype = (_platform_field(p, "platform_type", "") or "").lower()
        if not url:
            checks.append(HealthCheck(
                id=f"connectivity.llm_platform.{name}",
                category="connectivity",
                title=f"LLM 平台 {name}",
                severity="warning",
                summary="api_base_url 未配置。",
            ))
            continue
        base = url.rstrip("/")
        # 优先试 /models；失败再 TCP 探活。
        probe_url = base if base.endswith("/models") else base + "/models"
        code, body = _http_get(probe_url, timeout=2.0)
        if 200 <= code < 500:
            sev = "ok" if 200 <= code < 300 else "warning"
            summary = f"{probe_url} → HTTP {code}"
        else:
            try:
                from urllib.parse import urlparse
                pp = urlparse(base)
                host = pp.hostname or "127.0.0.1"
                port = pp.port or (443 if pp.scheme == "https" else 80)
            except Exception:
                host, port = "127.0.0.1", 80
            ok = _probe_tcp(host, port, timeout=1.5)
            sev = "warning" if ok else "critical"
            summary = (
                f"HTTP 无响应（{body[:120] if body else '–'}）；"
                f"TCP {'可达' if ok else '不可达'} {host}:{port}"
            )
        checks.append(HealthCheck(
            id=f"connectivity.llm_platform.{name}",
            category="connectivity",
            title=f"LLM 平台 {name}（{ptype or 'unknown'}）",
            severity=sev,
            summary=summary,
            fix_hint="确认模型服务已起、api_base_url 正确；部分厂商 `/v1/models` 需要 api_key。",
        ))
    return checks


# ---------------------------------------------------------------------------
# 服务 / API 健康
# ---------------------------------------------------------------------------

def _check_service_port(bs, attr: str, default_port: int, label: str, check_id: str) -> HealthCheck:
    cfg = dict(getattr(bs, attr, {}) or {})
    host = str(cfg.get("host") or "127.0.0.1")
    port = cfg.get("port") or default_port
    ok = _probe_tcp(host, port, timeout=0.8)
    return HealthCheck(
        id=check_id,
        category="api",
        title=f"{label}（{attr}）",
        severity=("ok" if ok else "info"),
        summary=f"{host}:{port} {'运行中' if ok else '未运行'}",
    )


def _check_healthz(bs) -> HealthCheck:
    cfg = dict(getattr(bs, "API_SERVER", {}) or {})
    host = str(cfg.get("host") or "127.0.0.1")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = cfg.get("port") or 62581
    if not _probe_tcp(host, port, timeout=0.6):
        return HealthCheck(
            id="api.healthz",
            category="api",
            title="/healthz",
            severity="info",
            summary=f"API {host}:{port} 未运行，跳过 /healthz 探测。",
        )
    code, body = _http_get(f"http://{host}:{port}/healthz", timeout=1.5)
    if code == 200:
        return HealthCheck(
            id="api.healthz",
            category="api",
            title="/healthz",
            severity="ok",
            summary="200 OK",
        )
    return HealthCheck(
        id="api.healthz",
        category="api",
        title="/healthz",
        severity="warning",
        summary=f"未命中（HTTP {code}）；{body[:120]}",
        fix_hint="可能当前进程是旧版本，执行「立即重启」加载最新代码。",
    )


def _check_readyz(bs) -> HealthCheck:
    cfg = dict(getattr(bs, "API_SERVER", {}) or {})
    host = str(cfg.get("host") or "127.0.0.1")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = cfg.get("port") or 62581
    if not _probe_tcp(host, port, timeout=0.6):
        return HealthCheck(
            id="api.readyz",
            category="api",
            title="/readyz",
            severity="info",
            summary=f"API {host}:{port} 未运行，跳过 /readyz 探测。",
        )
    code, body = _http_get(f"http://{host}:{port}/readyz", timeout=2.5)
    if code == 200:
        return HealthCheck(
            id="api.readyz",
            category="api",
            title="/readyz",
            severity="ok",
            summary="200 OK（DB + Redis 就绪）",
        )
    if code == 503:
        return HealthCheck(
            id="api.readyz",
            category="api",
            title="/readyz",
            severity="critical",
            summary=f"503 Service Unavailable：{body[:200]}",
            fix_hint="查看上方 connectivity.database / connectivity.redis 条目定位失败源。",
        )
    return HealthCheck(
        id="api.readyz",
        category="api",
        title="/readyz",
        severity="warning",
        summary=f"HTTP {code}：{body[:200]}",
    )


# ---------------------------------------------------------------------------
# 性能 / 可扩展性（scale）：复用 scalability.py 里的 Check 函数
# ---------------------------------------------------------------------------

def _collect_scalability_checks() -> List[HealthCheck]:
    from chayuan.server.config_panel import scalability as sc
    from chayuan.settings import Settings

    bs = Settings.basic_settings
    kb = Settings.kb_settings
    model = Settings.model_settings

    raw_checks = [
        sc._check_deployment_mode(bs),
        sc._check_database(bs),
        sc._check_db_pool(bs),
        sc._check_uvicorn_workers(bs),
        sc._check_redis(bs),
        sc._check_rate_limit(bs),
        sc._check_vector_store(kb),
        sc._check_llm_backend(model),
        sc._check_reverse_proxy(bs),
        sc._check_health_endpoints(bs),
        sc._check_metrics(bs),
        sc._check_json_logs(bs),
        sc._check_otel(bs),
        sc._check_llm_retry(bs),
        sc._check_auth(bs),
        sc._check_ingest_queue(bs),
        sc._check_semantic_cache(bs),
    ]

    # scale 分类下的 fixer 映射：只对「pip 可解决 / make_dirs 可解决」这类给 fixer_id。
    fixer_by_key: Dict[str, str] = {
        "metrics": "install_pkg:prometheus_client|prometheus-client>=0.19",
        "ingest_queue": "install_pkg:arq|arq>=0.25,<0.27",
    }

    out: List[HealthCheck] = []
    for c in raw_checks:
        out.append(HealthCheck(
            id=f"scale.{c.key}",
            category="scale",
            title=c.title,
            severity=c.severity,
            summary=c.summary,
            impact=c.impact or "",
            fix_hint=c.fix_hint or "",
            snippet=c.snippet or "",
            snippet_lang=c.snippet_lang or "yaml",
            fixer_id=fixer_by_key.get(c.key, ""),
        ))
    return out


# ---------------------------------------------------------------------------
# Fixer 注册表
# ---------------------------------------------------------------------------

def _fix_ensure_panel_credentials() -> Dict[str, Any]:
    from chayuan.server.config_panel.bootstrap import ensure_panel_credentials
    res = ensure_panel_credentials()
    return {
        "ok": True,
        "changed": res.changed_fields,
        "message": (
            "已补齐 " + ", ".join(res.changed_fields)
            if res.changed_fields else "所有面板凭据已就绪，无需修改。"
        ),
        "generated_password": bool(res.generated_password),
    }


def _fix_make_data_dirs() -> Dict[str, Any]:
    from chayuan.settings import Settings
    Settings.basic_settings.make_dirs()
    return {"ok": True, "message": "已调用 basic_settings.make_dirs()（幂等）。"}


def _fix_create_kb_tables() -> Dict[str, Any]:
    from chayuan.init_database import create_tables
    create_tables()
    return {"ok": True, "message": "已调用 init_database.create_tables()。"}


def _fix_install_pkg(arg: str) -> Dict[str, Any]:
    """``arg`` 形如 ``"redis|redis>=5.0,<6.0"``；支持仅 ``import_name`` 不带版本。"""
    import_name, _, requirement = arg.partition("|")
    import_name = import_name.strip()
    requirement = (requirement or import_name).strip()
    from chayuan.server.shared.deps import ensure_pkg
    ok = ensure_pkg(import_name, requirement, auto_install=True)
    return {
        "ok": bool(ok),
        "message": (
            f"{import_name} 安装成功（{requirement}）。"
            if ok else
            f"{import_name} 自动安装失败；可手动 `pip install '{requirement}'`。"
        ),
    }


# Fixer 注册表：key 可以是 ``"name"`` 或 ``"name:arg"``，后者会把 ``:arg`` 透传。
FIXERS: Dict[str, Dict[str, Any]] = {
    "ensure_panel_credentials": {
        "label": "补齐配置面板缺失凭据",
        "fn": lambda _: _fix_ensure_panel_credentials(),
        "danger": False,
    },
    "make_data_dirs": {
        "label": "创建数据目录（logs / media / temp / kb）",
        "fn": lambda _: _fix_make_data_dirs(),
        "danger": False,
    },
    "create_kb_tables": {
        "label": "初始化 KB 元数据表",
        "fn": lambda _: _fix_create_kb_tables(),
        "danger": False,
    },
    "install_pkg": {
        "label": "pip 安装缺失依赖",
        "fn": lambda arg: _fix_install_pkg(arg),
        "danger": False,
    },
}


def _split_fixer(fixer_id: str) -> Tuple[str, str]:
    name, _, arg = fixer_id.partition(":")
    return name, arg


def run_fixer(fixer_id: str) -> Dict[str, Any]:
    """执行单个 fixer；失败不抛异常，返回统一结构。"""
    if not fixer_id:
        return {"ok": False, "message": "fixer_id 为空"}
    name, arg = _split_fixer(fixer_id)
    entry = FIXERS.get(name)
    if not entry:
        return {"ok": False, "message": f"未注册的 fixer: {name}"}
    try:
        result = entry["fn"](arg)
        if not isinstance(result, dict):
            result = {"ok": bool(result), "message": str(result)}
        result.setdefault("ok", True)
        result.setdefault("fixer_id", fixer_id)
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception("run_fixer %s failed", fixer_id)
        return {
            "ok": False,
            "fixer_id": fixer_id,
            "message": f"{type(e).__name__}: {e}",
        }


def run_fixers(fixer_ids: Iterable[str]) -> List[Dict[str, Any]]:
    """按顺序执行 fixer_ids，返回 per-fixer 结果列表。同一个 fixer_id 只跑一次。"""
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for fid in fixer_ids:
        if fid in seen:
            continue
        seen.add(fid)
        out.append(run_fixer(fid))
    return out


# ---------------------------------------------------------------------------
# 入口：build_report
# ---------------------------------------------------------------------------

_ALL_CATEGORIES = tuple(CATEGORIES.keys())


def build_report(categories: Optional[Iterable[str]] = None) -> HealthReport:
    """跑一次完整体检。

    - ``categories``：只跑指定分类（如 ``["config", "connectivity"]``）；None 代表全部。
    - 该函数**不抛异常**：任何检查内部失败会被包成一条 severity=warning 的 `HealthCheck`，
      确保 UI / CLI 能把错误展示出来。
    """
    from chayuan.settings import Settings

    bs = Settings.basic_settings
    kb = Settings.kb_settings
    model = Settings.model_settings

    t0 = time.time()
    selected = set(categories) if categories else set(_ALL_CATEGORIES)

    checks: List[HealthCheck] = []

    def _safe(category: str, fn: Callable[..., Any], *args, list_mode: bool = False) -> None:
        if category not in selected:
            return
        try:
            result = fn(*args)
        except Exception as e:  # noqa: BLE001
            logger.exception("health check %s failed", fn.__name__)
            checks.append(HealthCheck(
                id=f"{category}.{fn.__name__}",
                category=category,
                title=fn.__name__,
                severity="warning",
                summary=f"检查内部错误：{type(e).__name__}: {e}",
            ))
            return
        if list_mode:
            checks.extend(result or [])
        else:
            checks.append(result)

    # --- config ---
    _safe("config", _check_chayuan_root, bs)
    _safe("config", _check_data_dirs, bs)
    _safe("config", _check_yaml_syntax)
    _safe("config", _check_panel_credentials, bs)
    _safe("config", _check_default_llm, model)
    _safe("config", _check_default_embedding, model)

    # --- resource ---
    _safe("resource", _check_disk, bs)
    _safe("resource", _check_memory)
    _safe("resource", _check_cpu)
    _safe("resource", _check_logs_size, bs)

    # --- runtime ---
    _safe("runtime", _check_python_version)
    _safe("runtime", _check_packages, bs, kb, model, list_mode=True)

    # --- connectivity ---
    _safe("connectivity", _check_database_connect, bs)
    _safe("connectivity", _check_redis_connect, bs)
    _safe("connectivity", _check_vector_store_connect, kb)
    _safe("connectivity", _check_llm_platforms_connect, model, list_mode=True)

    # --- api ---
    _safe("api", _check_service_port, bs, "API_SERVER",   62581, "API 服务",    "api.core")
    _safe("api", _check_service_port, bs, "CONFIG_SERVER", 8502, "配置面板", "api.config_panel")
    _safe("api", _check_healthz, bs)
    _safe("api", _check_readyz, bs)

    # --- scale（静态体检，复用 scalability.py）---
    if "scale" in selected:
        try:
            checks.extend(_collect_scalability_checks())
        except Exception as e:  # noqa: BLE001
            logger.exception("collect_scalability_checks failed")
            checks.append(HealthCheck(
                id="scale.collect",
                category="scale",
                title="静态体检收集",
                severity="warning",
                summary=f"失败：{type(e).__name__}: {e}",
            ))

    # 稳定排序：分类顺序 → 严重度 → id
    checks.sort(key=lambda c: (
        CATEGORIES.get(c.category, {}).get("order", 99),
        SEVERITY_ORDER.get(c.severity, 99),
        c.id,
    ))

    mode = (getattr(bs, "DEPLOYMENT_MODE", "dev") or "dev").lower()
    return HealthReport(
        checks=checks,
        mode=mode,
        generated_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        elapsed_ms=int((time.time() - t0) * 1000),
    )


__all__ = [
    "HealthCheck",
    "HealthReport",
    "CATEGORIES",
    "SEVERITY_ORDER",
    "SEVERITY_META",
    "FIXERS",
    "build_report",
    "run_fixer",
    "run_fixers",
]
