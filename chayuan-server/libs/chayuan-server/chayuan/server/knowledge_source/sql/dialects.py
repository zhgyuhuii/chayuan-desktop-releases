"""SQL 方言 URL builder 与驱动探测。

每个方言给出：
- build_url()：根据 ConnectionSpec 生成 SQLAlchemy URL
- drivers：候选驱动列表（优先顺序），缺失会抛 ConnectorError 带友好 pip 提示
- sqlglot_dialect：sqlglot 解析方言名（校验 SQL 用）

这样 Connector 实现就只有一份（sql/connector.py），方言差异收敛到这里。
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable, List, Optional
from urllib.parse import quote_plus

from chayuan.server.knowledge_source.base import ConnectionSpec, ConnectorError


@dataclass
class DialectProfile:
    name: str
    drivers: List[str]               # SQLAlchemy driver name 候选，按优先级
    pip_hint: str                    # 缺驱动时的 pip install 提示
    sqlglot_dialect: str             # sqlglot 解析方言
    default_port: int = 0
    build_url: Optional[Callable[[ConnectionSpec, str], str]] = None


def _pick_driver(profile: DialectProfile) -> str:
    """选可用驱动；全部未装时抛 ConnectorError。"""
    for drv in profile.drivers:
        # SQLAlchemy driver 形如 "pymysql" / "psycopg2" / "oracledb" / "pyodbc"
        # 其中 "pysqlite" 是内建模块名，不单独装包
        modname = drv
        if modname == "pysqlite":
            return drv  # 内建
        try:
            importlib.import_module(modname)
            return drv
        except Exception:  # noqa: BLE001
            continue
    raise ConnectorError(
        f"方言 {profile.name} 的驱动未安装。{profile.pip_hint}",
        code="driver_missing",
        dialect=profile.name,
    )


def _auth(spec: ConnectionSpec) -> str:
    if not spec.username:
        return ""
    if spec.password:
        return f"{quote_plus(spec.username)}:{quote_plus(spec.password)}@"
    return f"{quote_plus(spec.username)}@"


def _mysql_url(spec: ConnectionSpec, driver: str) -> str:
    host = spec.host or "127.0.0.1"
    port = spec.port or 3306
    db = spec.database or ""
    # driver: pymysql（默认）
    return f"mysql+{driver}://{_auth(spec)}{host}:{port}/{db}"


def _postgres_url(spec: ConnectionSpec, driver: str) -> str:
    host = spec.host or "127.0.0.1"
    port = spec.port or 5432
    db = spec.database or "postgres"
    # driver: psycopg2
    return f"postgresql+{driver}://{_auth(spec)}{host}:{port}/{db}"


def _sqlite_url(spec: ConnectionSpec, driver: str) -> str:
    # spec.database 填绝对 / 相对路径；":memory:" 支持
    db = spec.database or ":memory:"
    # sqlite+pysqlite:///relative 或 ////absolute
    if db == ":memory:":
        return "sqlite:///:memory:"
    # 允许 Windows 路径；SQLAlchemy 要求三斜杠 + 绝对路径用四斜杠
    if db.startswith("/") or (len(db) > 2 and db[1] == ":"):
        return f"sqlite:///{db}"
    return f"sqlite:///{db}"


def _mssql_url(spec: ConnectionSpec, driver: str) -> str:
    host = spec.host or "127.0.0.1"
    port = spec.port or 1433
    db = spec.database or ""
    if driver == "pyodbc":
        odbc_driver = spec.options.get("odbc_driver") or "ODBC Driver 18 for SQL Server"
        # 常用参数：Encrypt / TrustServerCertificate
        extra = spec.options.get("odbc_extra") or "Encrypt=no;TrustServerCertificate=yes"
        params = quote_plus(
            f"DRIVER={{{odbc_driver}}};SERVER={host},{port};DATABASE={db};"
            f"UID={spec.username};PWD={spec.password};{extra}"
        )
        return f"mssql+pyodbc:///?odbc_connect={params}"
    # pymssql
    return f"mssql+pymssql://{_auth(spec)}{host}:{port}/{db}"


def _oracle_url(spec: ConnectionSpec, driver: str) -> str:
    host = spec.host or "127.0.0.1"
    port = spec.port or 1521
    service = spec.options.get("service_name") or spec.database or "XE"
    # 新驱动 python-oracledb（原 cx_Oracle 后继），推荐 service_name 连接串
    return f"oracle+{driver}://{_auth(spec)}{host}:{port}/?service_name={service}"


def _clickhouse_url(spec: ConnectionSpec, driver: str) -> str:
    host = spec.host or "127.0.0.1"
    port = spec.port or 8123
    db = spec.database or "default"
    # clickhouse-sqlalchemy: driver name = "http"（默认走 HTTP 接口）
    return f"clickhouse+{driver}://{_auth(spec)}{host}:{port}/{db}"


def _doris_url(spec: ConnectionSpec, driver: str) -> str:
    """Doris 走 MySQL 协议（9030 端口），直接用 pymysql 即可。"""
    host = spec.host or "127.0.0.1"
    port = spec.port or 9030
    db = spec.database or ""
    return f"mysql+{driver}://{_auth(spec)}{host}:{port}/{db}"


def _kingbase_url(spec: ConnectionSpec, driver: str) -> str:
    """人大金仓 KingbaseES 连接串。

    协议层与 PostgreSQL 兼容，三个候选 URL 形态：

    1. ``kingbase+ksycopg2://...`` —— 官方 SQLAlchemy dialect ``sqlalchemy-kingbase``
       搭配金仓官方驱动 ``ksycopg2``。生产优先。
    2. ``kingbase+kingbase8://...`` —— 金仓 V8 官方驱动 ``kingbase8``
       （比 ksycopg2 新，未来演进方向）。
    3. ``postgresql+psycopg2://...`` —— 直接按 PG 协议走。多数金仓版本开启
       PG 兼容模式后可用，作为驱动缺失时的退路。

    端口默认 54321（金仓默认），可以由 spec 覆盖。
    """
    host = spec.host or "127.0.0.1"
    port = spec.port or 54321
    db = spec.database or ""
    if driver == "psycopg2":
        return f"postgresql+psycopg2://{_auth(spec)}{host}:{port}/{db}"
    if driver == "psycopg":
        return f"postgresql+psycopg://{_auth(spec)}{host}:{port}/{db}"
    if driver == "kingbase8":
        return f"kingbase+kingbase8://{_auth(spec)}{host}:{port}/{db}"
    # 默认：kingbase+ksycopg2（官方 dialect）
    return f"kingbase+ksycopg2://{_auth(spec)}{host}:{port}/{db}"


def _dm_url(spec: ConnectionSpec, driver: str) -> str:
    """达梦数据库 DM 连接串。

    协议层与 Oracle 兼容；使用官方驱动 ``dmPython`` +
    官方 SQLAlchemy dialect ``sqlalchemy-dm``（dialect 名 ``dm``）。

    默认端口 5236。spec.options 支持：
    - ``schema``：登录后切默认 schema（达梦 schema 语义 ≈ Oracle schema ≈ user）
    - ``autoCommit``：是否自动提交（默认 False，配合 read_only 更稳）
    """
    host = spec.host or "127.0.0.1"
    port = spec.port or 5236
    # DM 连接串不走 database 槽，连上后按 user 的默认 schema 读取
    # sqlalchemy-dm 的标准 URL：dm+dmPython://user:pass@host:port
    return f"dm+dmPython://{_auth(spec)}{host}:{port}"


def _hive_url(spec: ConnectionSpec, driver: str) -> str:
    """HiveServer2 连接串（通过 PyHive / impyla 的 SQLAlchemy dialect）。

    options 支持：
    - ``auth`` : NONE / NOSASL / LDAP / KERBEROS / CUSTOM（对应 PyHive auth 参数）
    - ``kerberos_service_name``：Kerberos 服务名（默认 hive）
    - ``configuration``：任意 Hive 参数键值对（hive.* 配置），字典形式
    - ``fetch_size``：PyHive cursor fetch 大小（默认 10000，long-scan 流式更稳）
    - ``socket_keepalive``：thrift TCP keepalive 布尔（默认 true，长 session 兜底）

    Hive 标准端口 10000；Impala 21050。

    P2-14-d：默认塞入两条生产关键配置：
      configuration["hive.fetch.task.conversion"]="none"
        → 禁用小结果集的 fetch-task 转换，避免大查询被截断；
      configuration["hive.server2.idle.session.timeout"]="3600000"
        → server-side session 空闲超时提到 1h，与客户端 socket_timeout 配合。
    """
    host = spec.host or "127.0.0.1"
    port = spec.port or 10000
    db = spec.database or "default"
    opts = dict(spec.options or {})
    query_parts: List[str] = []
    auth = (opts.get("auth") or "").upper()
    if auth:
        query_parts.append(f"auth={auth}")
    ksvc = opts.get("kerberos_service_name")
    if ksvc:
        query_parts.append(f"kerberos_service_name={ksvc}")

    # P2-14-d：默认 Hive 配置（可被 options.configuration 覆盖）
    config = dict(opts.get("configuration") or {})
    config.setdefault("hive.fetch.task.conversion", "none")
    config.setdefault("hive.server2.idle.session.timeout", "3600000")
    for k, v in config.items():
        query_parts.append(f"{quote_plus(f'configuration.{k}')}={quote_plus(str(v))}")

    # PyHive URL 参数（非 configuration）：socket_keepalive / fetch_size（若驱动支持）
    sk = opts.get("socket_keepalive")
    if sk is None:
        sk = True
    if bool(sk):
        query_parts.append("socket_keepalive=true")
    fs = opts.get("fetch_size") or 10000
    try:
        query_parts.append(f"fetch_size={int(fs)}")
    except Exception:  # noqa: BLE001
        pass

    suffix = ("?" + "&".join(query_parts)) if query_parts else ""
    # driver: 'hive' (PyHive 的 sqlalchemy-hive)，或 'impala' (impyla 兼容 Hive)
    if driver == "impala":
        return f"impala://{_auth(spec)}{host}:{port}/{db}{suffix}"
    return f"hive://{_auth(spec)}{host}:{port}/{db}{suffix}"


_PROFILES = {
    "mysql": DialectProfile(
        name="mysql",
        drivers=["pymysql"],
        pip_hint="请执行 `pip install pymysql`。",
        sqlglot_dialect="mysql",
        default_port=3306,
        build_url=_mysql_url,
    ),
    "postgres": DialectProfile(
        name="postgres",
        drivers=["psycopg2", "psycopg"],
        pip_hint="请执行 `pip install psycopg2-binary` 或 `pip install psycopg[binary]`。",
        sqlglot_dialect="postgres",
        default_port=5432,
        build_url=_postgres_url,
    ),
    "sqlite": DialectProfile(
        name="sqlite",
        drivers=["pysqlite"],
        pip_hint="SQLite 为 Python 内建，通常无需安装。",
        sqlglot_dialect="sqlite",
        default_port=0,
        build_url=_sqlite_url,
    ),
    "mssql": DialectProfile(
        name="mssql",
        # aioodbc 是异步驱动；同步路径下我们只取 pyodbc / pymssql
        drivers=["pyodbc", "pymssql"],
        pip_hint=(
            "请执行 `pip install pyodbc` 并安装 ODBC Driver 18 for SQL Server；"
            "或 `pip install pymssql`；若要启用异步路径另装 `pip install aioodbc`。"
        ),
        sqlglot_dialect="tsql",
        default_port=1433,
        build_url=_mssql_url,
    ),
    "oracle": DialectProfile(
        name="oracle",
        drivers=["oracledb", "cx_oracle"],
        pip_hint="请执行 `pip install oracledb`（推荐，支持 Thin 模式免 Oracle Client）。",
        sqlglot_dialect="oracle",
        default_port=1521,
        build_url=_oracle_url,
    ),
    "clickhouse": DialectProfile(
        name="clickhouse",
        drivers=["http", "native"],
        pip_hint="请执行 `pip install clickhouse-sqlalchemy clickhouse-driver`。",
        sqlglot_dialect="clickhouse",
        default_port=8123,
        build_url=_clickhouse_url,
    ),
    "doris": DialectProfile(
        name="doris",
        drivers=["pymysql"],
        pip_hint="Doris 兼容 MySQL 协议，`pip install pymysql` 即可。",
        sqlglot_dialect="mysql",
        default_port=9030,
        build_url=_doris_url,
    ),
    # ------------------------------------------------------------------
    # 国产信创数据库
    # ------------------------------------------------------------------
    # 人大金仓 KingbaseES：协议层 PostgreSQL 兼容；sqlglot 走 "postgres"。
    "kingbase": DialectProfile(
        name="kingbase",
        # 优先官方 SQLAlchemy dialect + 金仓官方驱动；依次降级：
        #   ksycopg2 → kingbase8 → psycopg2 → psycopg(v3)
        # 只要装了任何一个就能跑，最糟也能走 PG 协议。
        drivers=["ksycopg2", "kingbase8", "psycopg2", "psycopg"],
        pip_hint=(
            "请按金仓官方指引安装：`pip install ksycopg2 sqlalchemy-kingbase`；"
            "或使用新版驱动 `pip install kingbase8`；"
            "实在没有官方驱动时可装 `pip install psycopg2-binary` 走 PG 兼容模式。"
        ),
        sqlglot_dialect="postgres",
        default_port=54321,
        build_url=_kingbase_url,
    ),
    # 达梦 DM：协议层 Oracle 兼容；sqlglot 走 "oracle"。
    "dm": DialectProfile(
        name="dm",
        drivers=["dmPython"],
        pip_hint=(
            "请按达梦官方指引安装：`pip install dmPython sqlalchemy-dm`；"
            "dmPython 需要达梦客户端动态库（DPI），参见达梦官方手册。"
        ),
        sqlglot_dialect="oracle",
        default_port=5236,
        build_url=_dm_url,
    ),
    "hive": DialectProfile(
        name="hive",
        # 优先 PyHive（社区最成熟），退化到 impyla（Impala 驱动，Hive 协议兼容）
        drivers=["pyhive", "impala"],
        pip_hint=(
            "请执行 `pip install 'pyhive[hive]' sasl thrift thrift-sasl`；"
            "如需 Impala 协议改装 `pip install impyla`；"
            "Kerberos 环境下额外 `pip install pykerberos`。"
        ),
        sqlglot_dialect="hive",
        default_port=10000,
        build_url=_hive_url,
    ),
}


def get_profile(dialect: str) -> DialectProfile:
    key = (dialect or "").lower()
    if key not in _PROFILES:
        raise ConnectorError(f"未知 SQL 方言：{dialect!r}", code="dialect_unsupported", dialect=key)
    return _PROFILES[key]


def build_sqlalchemy_url(spec: ConnectionSpec) -> str:
    """从 ConnectionSpec 生成可连接的 SQLAlchemy URL；驱动缺失会抛 ConnectorError。

    clickhouse 驱动特殊处理：有些版本是 ``clickhouse-driver`` 提供 native，
    有些版本是 ``clickhouse-sqlalchemy`` 内置 http；按候选顺序尝试即可。
    """
    profile = get_profile(spec.dialect)
    # SQLite 不需要装包（pysqlite 是 stdlib），直接用 "pysqlite"
    if profile.name == "sqlite":
        return profile.build_url(spec, "pysqlite")
    # Doris 用 pymysql
    if profile.name == "doris":
        driver = _pick_driver(profile)
        return profile.build_url(spec, driver)
    # Hive：pyhive 的 sqlalchemy-hive dialect 名就是 "hive"；impyla 是 "impala"
    if profile.name == "hive":
        # pyhive 优先；impala 作为备胎
        try:
            importlib.import_module("pyhive")
            return profile.build_url(spec, "hive")
        except Exception:  # noqa: BLE001
            try:
                importlib.import_module("impala")
                return profile.build_url(spec, "impala")
            except Exception:  # noqa: BLE001
                raise ConnectorError(
                    profile.pip_hint, code="driver_missing", dialect=profile.name,
                )
    # 人大金仓 KingbaseES：优先 ksycopg2/kingbase8 + sqlalchemy-kingbase；
    # 降级到 psycopg2/psycopg 直连 PG 协议（需金仓开启 PG 兼容模式）。
    if profile.name == "kingbase":
        # 探测 SQLAlchemy dialect：装了 sqlalchemy-kingbase 才有 "kingbase" 入口
        has_kingbase_dialect = False
        try:
            from sqlalchemy.dialects import registry as _sa_reg  # type: ignore
            _sa_reg.load("kingbase.ksycopg2")
            has_kingbase_dialect = True
        except Exception:  # noqa: BLE001
            try:
                from sqlalchemy.dialects import registry as _sa_reg  # type: ignore
                _sa_reg.load("kingbase.kingbase8")
                has_kingbase_dialect = True
            except Exception:  # noqa: BLE001
                has_kingbase_dialect = False

        if has_kingbase_dialect:
            # 优先 ksycopg2；其次 kingbase8
            for drv in ("ksycopg2", "kingbase8"):
                try:
                    importlib.import_module(drv)
                    return profile.build_url(spec, drv)
                except Exception:  # noqa: BLE001
                    continue
        # 退化：直接按 PostgreSQL 协议走（需金仓开兼容模式）
        for drv in ("psycopg2", "psycopg"):
            try:
                importlib.import_module(drv)
                return profile.build_url(spec, drv)
            except Exception:  # noqa: BLE001
                continue
        raise ConnectorError(
            profile.pip_hint, code="driver_missing", dialect=profile.name,
        )
    # 达梦 DM：只有一条链路，dmPython + sqlalchemy-dm
    if profile.name == "dm":
        try:
            importlib.import_module("dmPython")
        except Exception:  # noqa: BLE001
            raise ConnectorError(
                profile.pip_hint, code="driver_missing", dialect=profile.name,
            )
        # 验证 SQLAlchemy dialect 也已注册；缺失给明确的 pip hint
        try:
            from sqlalchemy.dialects import registry as _sa_reg  # type: ignore
            _sa_reg.load("dm.dmPython")
        except Exception:  # noqa: BLE001
            raise ConnectorError(
                "缺少 SQLAlchemy 达梦方言：" + profile.pip_hint,
                code="driver_missing", dialect=profile.name,
            )
        return profile.build_url(spec, "dmPython")
    # ClickHouse：clickhouse-sqlalchemy 装上后 sqlalchemy dialect 名就是 "clickhouse"
    if profile.name == "clickhouse":
        try:
            importlib.import_module("clickhouse_sqlalchemy")
        except Exception:  # noqa: BLE001
            raise ConnectorError(
                profile.pip_hint, code="driver_missing", dialect=profile.name
            )
        # http 子驱动要求 clickhouse_driver 或 requests；这里直接按 http 走，
        # 实际连接失败会回到 test_connection 的错误提示
        return profile.build_url(spec, "http")
    driver = _pick_driver(profile)
    return profile.build_url(spec, driver)
