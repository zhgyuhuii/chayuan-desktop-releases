"""主业务数据库配置卡片。

设计要点
--------
- 项目的主业务数据库靠 ``SQLALCHEMY_DATABASE_URI`` 驱动 SQLAlchemy
  （见 ``chayuan/server/db/base.py``）；配置面板原来只暴露一个 URI
  文本框，用户手敲很容易写错。
- 这里把「支持的数据库类型 + 对应的连接字段」结构化：
    * ``sqlite``     — 仅一个本地文件路径；Python stdlib 自带驱动；
    * ``postgresql`` — host/port/database/schema/user/password；
                       驱动 = psycopg2-binary（已列在 pyproject.toml）；
    * ``mysql``      — host/port/database/user/password/charset；
                       驱动 = pymysql（已列在 pyproject.toml）。
  这是仓库当前真正能 ``create_engine`` 起来的三种方言；其它方言
  （Oracle / SQL Server / 达梦等）若要加，需要同步在 pyproject 补驱动，
  并在 ``DB_TYPES`` 里加一条即可。
- 表单采用「类型下拉 + 动态字段」模式：切换下拉会刷新下方字段区，
  避免把三种方言的字段堆在一个大表里互相干扰。
- 「验证连接」按钮直接调 ``create_engine(uri).connect()`` + ``SELECT 1``，
  完全复用真实运行时路径，不会假跑，不写磁盘。
- 「保存」统一走 ``yaml_store.save_updates`` 把最终 URI 落到
  ``basic_settings.yaml`` 的 ``SQLALCHEMY_DATABASE_URI``；若是 sqlite
  还会同步把文件路径写到 ``DB_ROOT_PATH``（向后兼容旧代码）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

from chayuan.server.config_panel import yaml_store


# ---------------------------------------------------------------------------
# 库不存在识别 & 自动建库：sql 模板放在 chayuan/server/db/init_scripts/。
# 错误文案的识别是基于 psycopg2 / pymysql 常见英文错误消息做的穷举匹配，
# 能覆盖 95% 线上场景；极少数被本地化了的错误会退化成通用 "连接失败"，
# 此时用户仍然可以手动在数据库里建库再重试。
# ---------------------------------------------------------------------------

_INIT_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "db" / "init_scripts"


_DB_MISSING_PATTERNS = (
    # PostgreSQL：psycopg2.OperationalError: FATAL:  database "xxx" does not exist
    re.compile(r'database\s+"[^"]+"\s+does\s+not\s+exist', re.IGNORECASE),
    re.compile(r"database\s+.+\s+does\s+not\s+exist", re.IGNORECASE),
    # MySQL / MariaDB：OperationalError: (1049, "Unknown database 'xxx'")
    re.compile(r"unknown\s+database", re.IGNORECASE),
    # 某些 ODBC 驱动抛 "Cannot open database"
    re.compile(r"cannot\s+open\s+database", re.IGNORECASE),
)


def _looks_like_db_missing(err_text: str) -> bool:
    """粗粒度判断错误文本是否属于「目标库不存在」。"""
    if not err_text:
        return False
    for pat in _DB_MISSING_PATTERNS:
        if pat.search(err_text):
            return True
    return False


# 业务库名白名单：字母/数字/下划线，首字符不能是数字；最长 63 字符（PG 限制最严）。
# CREATE DATABASE 无法用参数化，这里只能在 Python 侧做严校验，坚决杜绝 SQL 注入。
_IDENTIFIER_PAT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _assert_safe_identifier(name: str) -> None:
    if not name or not _IDENTIFIER_PAT.match(name):
        raise ValueError(
            f"非法数据库名 {name!r}：仅允许字母/数字/下划线，首字符非数字，长度 ≤63"
        )


def _render_create_sql(db_type_key: str, database: str) -> str:
    """读 init_scripts/<key>.sql 并把 {{database}} 替换为校验过的库名。"""
    _assert_safe_identifier(database)
    path = _INIT_SCRIPTS_DIR / f"{db_type_key}.sql"
    if not path.exists():
        raise FileNotFoundError(f"找不到建库脚本：{path}")
    tpl = path.read_text(encoding="utf-8")
    return tpl.replace("{{database}}", database)


# ---------------------------------------------------------------------------
# 支持的数据库方言：**务必和 pyproject.toml 已安装的驱动保持同步**，否则
# 「验证连接」那步会因 ModuleNotFoundError 直接崩，用户体验很差。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DbType:
    key: str
    label: str
    scheme: str
    driver: Optional[str]
    default_port: Optional[int]
    default_db: str
    default_schema: Optional[str]
    default_charset: Optional[str]
    has_host: bool
    has_schema: bool
    has_charset: bool
    driver_hint: str
    install_hint: str


DB_TYPES: List[DbType] = [
    DbType(
        key="sqlite",
        label="SQLite（本地文件，仅限开发/单机）",
        scheme="sqlite",
        driver=None,
        default_port=None,
        default_db="info.db",
        default_schema=None,
        default_charset=None,
        has_host=False,
        has_schema=False,
        has_charset=False,
        driver_hint=(
            "Python 标准库自带 sqlite3 驱动，无需额外安装。"
            "高并发下会出现 'database is locked'，仅推荐单机/开发。"
        ),
        install_hint="",
    ),
    DbType(
        key="postgresql",
        label="PostgreSQL（推荐生产）",
        scheme="postgresql+psycopg2",
        driver="psycopg2",
        default_port=5432,
        default_db="chayuan",
        default_schema="public",
        default_charset=None,
        has_host=True,
        has_schema=True,
        has_charset=False,
        driver_hint=(
            "通过 SQLAlchemy + psycopg2 驱动连接；支持独立 schema（默认 public）。"
            "生产多用户场景首选。"
        ),
        install_hint="pip install 'psycopg2-binary>=2.9'",
    ),
    DbType(
        key="mysql",
        label="MySQL / MariaDB",
        scheme="mysql+pymysql",
        driver="pymysql",
        default_port=3306,
        default_db="chayuan",
        default_schema=None,
        default_charset="utf8mb4",
        has_host=True,
        has_schema=False,
        has_charset=True,
        driver_hint=(
            "通过 SQLAlchemy + pymysql 驱动连接；MySQL/MariaDB 兼容。"
            "默认字符集 utf8mb4，避免 emoji / 生僻字报错。"
        ),
        install_hint="pip install 'pymysql>=1.1'",
    ),
]


def db_type_by_key(key: str) -> Optional[DbType]:
    for t in DB_TYPES:
        if t.key == key:
            return t
    return None


# ---------------------------------------------------------------------------
# URI <-> 结构化字段
# ---------------------------------------------------------------------------


@dataclass
class DbFormValues:
    """表单值快照。仅保留我们真正暴露在 UI 上的字段。"""

    db_type: str
    # sqlite
    sqlite_path: str = ""
    # 网络型
    host: str = ""
    port: str = ""  # 允许空；保存时再 cast
    database: str = ""
    schema: str = ""
    username: str = ""
    password: str = ""
    charset: str = ""

    def get_field(self, name: str) -> str:
        return getattr(self, name, "") or ""


def _default_sqlite_path() -> str:
    """当前 CHAYUAN_ROOT 下的 sqlite 默认文件路径。"""
    try:
        from chayuan.paths import resolve_chayuan_root
        root = resolve_chayuan_root().path
    except Exception:
        root = Path.home() / ".chayuan"
    return str(Path(root) / "data" / "knowledge_base" / "info.db")


def defaults_for(t: DbType) -> DbFormValues:
    """给定方言返回一份可直接显示的默认值（host=127.0.0.1, 默认端口等）。"""
    if t.key == "sqlite":
        return DbFormValues(db_type="sqlite", sqlite_path=_default_sqlite_path())
    return DbFormValues(
        db_type=t.key,
        host="127.0.0.1",
        port=str(t.default_port or ""),
        database=t.default_db or "",
        schema=(t.default_schema or "") if t.has_schema else "",
        username="",
        password="",
        charset=(t.default_charset or "") if t.has_charset else "",
    )


def parse_uri(uri: str) -> DbFormValues:
    """把 ``SQLALCHEMY_DATABASE_URI`` 逆向解析成表单值。

    无法识别的 URI 回落到 sqlite 默认表单，避免崩 UI。
    """
    uri = (uri or "").strip()
    if not uri:
        return defaults_for(db_type_by_key("sqlite"))  # type: ignore[arg-type]

    # SQLAlchemy 的 make_url 能兼容 `sqlite:///`、相对路径、query 参数等。
    try:
        from sqlalchemy.engine.url import make_url
        u = make_url(uri)
    except Exception:
        return defaults_for(db_type_by_key("sqlite"))  # type: ignore[arg-type]

    backend = (u.get_backend_name() or "").lower()

    if backend == "sqlite":
        # SQLAlchemy 把 ``sqlite:///D:/chayuan_data/x.sqlite`` 的 ``u.database``
        # 还原成 ``D:/chayuan_data/x.sqlite`` —— 已经是 Windows 绝对路径,这里
        # 不能再前面拼 ``/``,否则变 ``/D:/chayuan_data/...`` 触发
        # ``[WinError 123] 文件名、目录名或卷标语法不正确`` 启动崩。
        # 用 os.path.isabs 同时正确处理 Windows(``D:\\``、``D:/``)和 POSIX(``/``)
        # 两种绝对路径形式;只在路径**真的相对**时才补斜杠。
        import os
        path = u.database or ""
        if path and len(path) > 1 and not os.path.isabs(path) and not path.startswith("/"):
            path = "/" + path.lstrip("/")
        return DbFormValues(
            db_type="sqlite",
            sqlite_path=path or _default_sqlite_path(),
        )

    if backend == "postgresql":
        schema = ""
        opts = (u.query or {}).get("options", "")
        if isinstance(opts, str) and "search_path" in opts:
            for token in opts.split():
                if token.startswith("-csearch_path="):
                    schema = token[len("-csearch_path="):]
                    break
        return DbFormValues(
            db_type="postgresql",
            host=u.host or "127.0.0.1",
            port=str(u.port or 5432),
            database=u.database or "chayuan",
            schema=schema or "public",
            username=u.username or "",
            password=u.password or "",
        )

    if backend == "mysql":
        charset = (u.query or {}).get("charset", "") if isinstance(u.query, dict) else ""
        return DbFormValues(
            db_type="mysql",
            host=u.host or "127.0.0.1",
            port=str(u.port or 3306),
            database=u.database or "chayuan",
            username=u.username or "",
            password=u.password or "",
            charset=charset or "utf8mb4",
        )

    return defaults_for(db_type_by_key("sqlite"))  # type: ignore[arg-type]


def build_uri(values: DbFormValues) -> str:
    """表单值 → SQLAlchemy URI。保存前统一走这里，保证 URI 合法。"""
    t = db_type_by_key(values.db_type)
    if t is None:
        raise ValueError(f"未知数据库类型：{values.db_type!r}")

    if t.key == "sqlite":
        path = (values.sqlite_path or "").strip() or _default_sqlite_path()
        # 统一成 posix；sqlalchemy 对 `sqlite:///<abs_posix>` 兼容性最好。
        path = path.replace("\\", "/")
        if not path.startswith("/"):
            path = "/" + path
        return f"sqlite://{path}"

    host = (values.host or "").strip() or "127.0.0.1"
    try:
        port = int((values.port or "").strip() or (t.default_port or 0))
    except ValueError:
        raise ValueError(f"端口必须是整数：{values.port!r}")
    db = (values.database or "").strip() or (t.default_db or "")
    user = (values.username or "").strip()
    pwd = values.password or ""

    # 密码里可能含 @ / : / ? / /，要 URL-encode；同理用户名里也可能有 @ 等。
    userpart = quote(user, safe="")
    if pwd:
        userpart += ":" + quote(pwd, safe="")
    auth = (userpart + "@") if userpart else ""

    base = f"{t.scheme}://{auth}{host}:{port}/{db}"

    params: List[str] = []
    if t.has_schema:
        schema = (values.schema or "").strip()
        if schema and schema != (t.default_schema or ""):
            # psycopg2 接 URL 时 search_path 要藏在 `options` 里；SQLAlchemy 会原样透传。
            params.append("options=" + quote(f"-csearch_path={schema}", safe=""))
        elif schema:
            # 即使是默认 schema 也显式写出来，方便运维一眼看出
            params.append("options=" + quote(f"-csearch_path={schema}", safe=""))
    if t.has_charset:
        charset = (values.charset or "").strip() or (t.default_charset or "")
        if charset:
            params.append(f"charset={quote(charset, safe='')}")

    if params:
        base += "?" + "&".join(params)
    return base


def human_summary(values: DbFormValues) -> str:
    """给 UI 顶部显示的一行摘要，隐藏密码。"""
    t = db_type_by_key(values.db_type)
    if t is None:
        return "（未配置）"
    if t.key == "sqlite":
        return f"SQLite · {values.sqlite_path or _default_sqlite_path()}"
    user = values.username or "<空>"
    host = values.host or "127.0.0.1"
    port = values.port or str(t.default_port or "")
    db = values.database or t.default_db
    extra = ""
    if t.has_schema and values.schema:
        extra = f" · schema={values.schema}"
    return f"{t.label.split('（')[0]} · {user}@{host}:{port}/{db}{extra}"


# ---------------------------------------------------------------------------
# 连接验证
# ---------------------------------------------------------------------------


def validate_connection(uri: str, timeout: int = 5) -> Dict[str, Any]:
    """阻塞地验证 URI 是否能连通。返回 ``{"ok", "message", "detail", "db_missing"}``。

    - 用 ``create_engine`` + ``SELECT 1`` 完整走一遍 driver，和线上 engine 同路径；
    - 为防挂住 UI 进程，``connect_args.connect_timeout`` 硬设 timeout（driver 支持时）；
    - sqlite 走本地文件，忽略 timeout；
    - 若错误文本匹配「库不存在」的规则，额外返回 ``db_missing=True``，上层 UI 会据此
      让用户一键创建。
    """
    uri = (uri or "").strip()
    if not uri:
        return {"ok": False, "message": "URI 为空", "detail": "", "db_missing": False}

    try:
        from sqlalchemy import create_engine, text
    except ImportError as e:
        return {
            "ok": False,
            "message": "未安装 sqlalchemy",
            "detail": repr(e),
            "db_missing": False,
        }

    connect_args: Dict[str, Any] = {}
    if uri.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    elif uri.startswith("postgresql"):
        connect_args["connect_timeout"] = int(timeout)
    elif uri.startswith("mysql"):
        connect_args["connect_timeout"] = int(timeout)

    try:
        engine = create_engine(uri, connect_args=connect_args, pool_pre_ping=True)
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "message": f"引擎构建失败：{type(e).__name__}",
            "detail": str(e),
            "db_missing": False,
        }

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        cls = type(e).__name__
        err_text = str(e) or ""
        detail = err_text.strip().splitlines()[-1] if err_text.strip() else ""
        db_missing = _looks_like_db_missing(err_text)

        tip = ""
        el = err_text.lower()
        if db_missing:
            tip = "目标数据库不存在——点右边「立即创建」或保存后自动创建"
        elif "password authentication failed" in el:
            tip = "用户名/密码错误"
        elif "could not translate host name" in el or "name or service not known" in el:
            tip = "host 无法解析"
        elif "connection refused" in el:
            tip = "host/port 不可达；确认数据库已启动、防火墙放行"
        elif "timeout" in el:
            tip = f"超时（{timeout}s）"
        elif "access denied" in el:
            tip = "账号权限不足"

        return {
            "ok": False,
            "message": f"连接失败：{cls}" + (f"（{tip}）" if tip else ""),
            "detail": detail or repr(e),
            "db_missing": db_missing,
        }
    finally:
        try:
            engine.dispose()
        except Exception:
            pass

    return {
        "ok": True,
        "message": "连接成功（SELECT 1 已通过）",
        "detail": uri,
        "db_missing": False,
    }


# ---------------------------------------------------------------------------
# 建库：从用户填的表单值里拆出「管理库连接 + 业务库名」，连到管理库执行 SQL。
# ---------------------------------------------------------------------------


def _admin_uri_for(values: DbFormValues) -> Tuple[str, str]:
    """返回 (admin_uri, database_name)：

    - PostgreSQL：连默认的 ``postgres`` 管理库；
    - MySQL：连空库（URI 末尾不带 database）；
    - SQLite：无所谓 admin，这里返回原 URI + 文件路径。

    这里故意不复用 ``build_uri``：后者会在 database 为空时回退到 ``t.default_db``，
    而「回退的值」恰好就是目标业务库——会出现自己连自己建自己的死循环。
    """
    t = db_type_by_key(values.db_type)
    if t is None:
        raise ValueError(f"未知数据库类型：{values.db_type!r}")

    if t.key == "sqlite":
        path = (values.sqlite_path or "").strip() or _default_sqlite_path()
        return (build_uri(values), path)

    host = (values.host or "").strip() or "127.0.0.1"
    try:
        port = int((values.port or "").strip() or (t.default_port or 0))
    except ValueError as e:
        raise ValueError(f"端口必须是整数：{values.port!r}") from e

    user = (values.username or "").strip()
    pwd = values.password or ""
    userpart = quote(user, safe="")
    if pwd:
        userpart += ":" + quote(pwd, safe="")
    auth = (userpart + "@") if userpart else ""

    db_name = (values.database or "").strip() or (t.default_db or "")

    if t.key == "postgresql":
        # 管理库 postgres 一定存在；其它连接参数保持一致。
        admin_uri = f"{t.scheme}://{auth}{host}:{port}/postgres"
    elif t.key == "mysql":
        # MySQL 允许连接时不带 database；注意 pymysql charset 参数保留。
        admin_uri = f"{t.scheme}://{auth}{host}:{port}/"
        charset = (values.charset or "").strip() or (t.default_charset or "")
        if charset:
            admin_uri += f"?charset={quote(charset, safe='')}"
    else:
        raise ValueError(f"方言 {t.key} 暂不支持自动建库")

    return (admin_uri, db_name)


def create_database(values: DbFormValues, timeout: int = 5) -> Dict[str, Any]:
    """自动创建业务库。返回 ``{"ok", "created", "message", "detail"}``。

    - SQLite：创建父目录并 touch 空库文件；
    - PostgreSQL：连 ``postgres`` 管理库 + AUTOCOMMIT 执行 ``CREATE DATABASE``；
    - MySQL：连空库执行 ``CREATE DATABASE IF NOT EXISTS ... CHARACTER SET utf8mb4``。
    """
    t = db_type_by_key(values.db_type)
    if t is None:
        return {
            "ok": False,
            "created": False,
            "message": f"未知数据库类型：{values.db_type!r}",
            "detail": "",
        }

    # ---- SQLite：文件 touch ------------------------------------------------
    if t.key == "sqlite":
        path = (values.sqlite_path or "").strip() or _default_sqlite_path()
        p = Path(path)
        was_present = p.exists()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            import sqlite3
            # sqlite3.connect 本身就是「不存在即创建」的语义，不强依赖 touch。
            conn = sqlite3.connect(str(p))
            conn.close()
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "created": False,
                "message": f"SQLite 文件创建失败：{type(e).__name__}",
                "detail": str(e),
            }
        return {
            "ok": True,
            "created": not was_present,
            "message": (
                f"SQLite 库文件已创建：{p}" if not was_present
                else f"SQLite 库文件已存在：{p}"
            ),
            "detail": str(p),
        }

    # ---- PG / MySQL：连管理库执行 CREATE DATABASE --------------------------
    try:
        from sqlalchemy import create_engine, text
    except ImportError as e:
        return {
            "ok": False,
            "created": False,
            "message": "未安装 sqlalchemy",
            "detail": repr(e),
        }

    try:
        admin_uri, db_name = _admin_uri_for(values)
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "created": False,
            "message": f"管理 URI 组装失败：{type(e).__name__}",
            "detail": str(e),
        }

    try:
        sql = _render_create_sql(t.key, db_name)
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "created": False,
            "message": f"建库脚本准备失败：{type(e).__name__}",
            "detail": str(e),
        }

    connect_args: Dict[str, Any] = {}
    if t.key in ("postgresql", "mysql"):
        connect_args["connect_timeout"] = int(timeout)

    try:
        admin_engine = create_engine(admin_uri, connect_args=connect_args)
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "created": False,
            "message": f"连接管理库失败：{type(e).__name__}",
            "detail": str(e),
        }

    try:
        if t.key == "postgresql":
            # PostgreSQL 的 CREATE DATABASE 不能在事务里；切 AUTOCOMMIT 隔离级别。
            with admin_engine.connect() as conn:
                conn = conn.execution_options(isolation_level="AUTOCOMMIT")
                # 先探一下：已存在就直接 ok，避免重复报 "already exists"。
                exists = conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :n"),
                    {"n": db_name},
                ).first()
                if exists:
                    return {
                        "ok": True,
                        "created": False,
                        "message": f"PostgreSQL 库 {db_name} 已存在",
                        "detail": admin_uri,
                    }
                conn.execute(text(sql))
        elif t.key == "mysql":
            with admin_engine.begin() as conn:
                conn.execute(text(sql))
        else:
            return {
                "ok": False,
                "created": False,
                "message": f"方言 {t.key} 暂不支持自动建库",
                "detail": "",
            }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "created": False,
            "message": f"建库失败：{type(e).__name__}",
            "detail": str(e),
        }
    finally:
        try:
            admin_engine.dispose()
        except Exception:
            pass

    return {
        "ok": True,
        "created": True,
        "message": f"已创建 {t.label.split('（')[0]} 数据库 {db_name}",
        "detail": "",
    }


def ensure_database_from_uri(uri: str, timeout: int = 5) -> Dict[str, Any]:
    """启动链路专用的 bootstrap 入口：给定 URI，保证目标库存在并连通。

    返回 ``{"ok", "created", "message", "detail"}``：
    - ``ok=True`` 且 ``created=False``：库原本就存在、直接能连；
    - ``ok=True`` 且 ``created=True``：检测到缺库，已经按 init_scripts 建好；
    - ``ok=False``：出了其它错（host 不通 / 账号不对 / ……），启动流程应把
      明确错误抛出来而不是继续走，免得下游 `create_all` 再报一次同样的错。

    这层刻意做成「幂等 + 能跳就跳」：能连通就立刻返回，不做无谓的网络往返。
    """
    uri = (uri or "").strip()
    if not uri:
        return {"ok": False, "created": False, "message": "URI 为空", "detail": ""}

    # SQLite 特判：touch 文件 + mkdir 父目录完全幂等且零副作用，直接前置执行。
    # 这样就不用再去 parse SQLAlchemy 的 "unable to open database file" 错误文本。
    if uri.startswith("sqlite"):
        try:
            values = parse_uri(uri)
            cres = create_database(values, timeout=timeout)
            if not cres.get("ok"):
                return cres
            v = validate_connection(uri, timeout=timeout)
            if v.get("ok"):
                return {
                    "ok": True,
                    "created": bool(cres.get("created")),
                    "message": cres.get("message", "SQLite 文件已就绪"),
                    "detail": uri,
                }
            return {
                "ok": False,
                "created": bool(cres.get("created")),
                "message": v.get("message", "SQLite 验证失败"),
                "detail": v.get("detail", ""),
            }
        except Exception as e:  # noqa: BLE001
            return {
                "ok": False,
                "created": False,
                "message": f"SQLite bootstrap 异常：{type(e).__name__}",
                "detail": str(e),
            }

    # 第一步：尝试直接连。大多数场景这里就通过了，零副作用。
    res = validate_connection(uri, timeout=timeout)
    if res.get("ok"):
        return {
            "ok": True,
            "created": False,
            "message": "数据库已存在且可连通",
            "detail": uri,
        }

    # 非「库不存在」错误直接透传——这些错误自动建库也解决不了。
    if not res.get("db_missing"):
        return {
            "ok": False,
            "created": False,
            "message": res.get("message", "连接失败"),
            "detail": res.get("detail", ""),
        }

    # 库不存在 → 逆解析 URI 为表单值，走 create_database。
    try:
        values = parse_uri(uri)
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "created": False,
            "message": f"URI 解析失败：{type(e).__name__}",
            "detail": str(e),
        }

    cres = create_database(values, timeout=timeout)
    if not cres.get("ok"):
        return cres

    # 建完再验一次，确保真的通了（可能有权限没给够之类的坑）。
    v2 = validate_connection(uri, timeout=timeout)
    if v2.get("ok"):
        return {
            "ok": True,
            "created": bool(cres.get("created")),
            "message": cres.get("message", "已创建数据库"),
            "detail": uri,
        }
    return {
        "ok": False,
        "created": bool(cres.get("created")),
        "message": f"{cres.get('message','')}；但验证仍失败：{v2.get('message','')}",
        "detail": v2.get("detail", ""),
    }


# ---------------------------------------------------------------------------
# UI：主业务数据库卡片
# ---------------------------------------------------------------------------


def _copy_to_clipboard(ui, text: str) -> None:
    import json as _json
    js = f"navigator.clipboard.writeText({_json.dumps(text)})"
    try:
        ui.run_javascript(js)
        ui.notify("已复制", color="positive", timeout=1500)
    except Exception:
        ui.notify("复制失败：浏览器不支持 clipboard API", color="warning")


def render_main_db_card(
    ui,
    *,
    mark_restart_needed: Optional[Callable[[], None]] = None,
) -> None:
    """在当前 NiceGUI 容器内渲染「主业务数据库」卡。

    ``mark_restart_needed`` 用于通知外层「有需要重启的改动」——SQLALCHEMY_DATABASE_URI
    必须重启才生效，保存成功后会调用一次。
    """
    result = yaml_store.load_yaml("basic_settings.yaml")
    current_uri = str(yaml_store.get_by_path(result.doc, "SQLALCHEMY_DATABASE_URI", "") or "")
    values = parse_uri(current_uri)

    # 控件引用留一份，便于「切换方言」时按字段名选择性设值。
    inputs: Dict[str, Any] = {}
    fields_container = None
    summary_label = None
    result_row = None
    result_icon = None
    result_text = None
    result_detail = None

    def _refresh_summary() -> None:
        if summary_label is not None:
            summary_label.text = human_summary(_collect())

    def _collect() -> DbFormValues:
        """读取当前所有控件值，拼成 DbFormValues。"""
        dv = DbFormValues(db_type=type_select.value)
        for name, el in inputs.items():
            try:
                raw = el.value
            except Exception:
                raw = ""
            if raw is None:
                raw = ""
            setattr(dv, name, str(raw) if not isinstance(raw, str) else raw)
        return dv

    def _render_fields_for(key: str, initial: DbFormValues) -> None:
        """按方言刷新下方字段区。initial 是控件的初值。"""
        nonlocal inputs
        inputs = {}
        fields_container.clear()
        t = db_type_by_key(key)
        if t is None:
            return
        with fields_container:
            # 驱动提示（弱信息）
            hint = t.driver_hint
            if t.install_hint:
                hint += f"\n驱动安装：{t.install_hint}"
            ui.label(hint).classes("text-xs text-grey-7 q-mb-sm").style(
                "white-space: pre-line"
            )

            if t.key == "sqlite":
                el = (
                    ui.input(label="SQLite 文件路径", value=initial.sqlite_path or _default_sqlite_path())
                    .props("outlined dense")
                    .classes("w-full")
                )
                el.tooltip(
                    "绝对路径。留空将使用 $CHAYUAN_ROOT/data/knowledge_base/info.db。"
                )
                el.on("update:model-value", lambda _=None: _refresh_summary())
                inputs["sqlite_path"] = el
                return

            # 网络型方言：两列网格
            with ui.grid(columns=2).classes("w-full q-gutter-sm"):
                host_el = (
                    ui.input(label="主机 host", value=initial.host or "127.0.0.1")
                    .props("outlined dense")
                )
                host_el.tooltip("数据库服务器 IP 或域名")
                inputs["host"] = host_el

                port_el = (
                    ui.input(label="端口 port", value=initial.port or str(t.default_port or ""))
                    .props("outlined dense")
                )
                port_el.tooltip(f"默认 {t.default_port}")
                inputs["port"] = port_el

                db_el = (
                    ui.input(label="数据库名 database", value=initial.database or (t.default_db or ""))
                    .props("outlined dense")
                )
                db_el.tooltip("要写入业务表的目标数据库；必须预先创建")
                inputs["database"] = db_el

                if t.has_schema:
                    schema_el = (
                        ui.input(label="模式 schema", value=initial.schema or (t.default_schema or ""))
                        .props("outlined dense")
                    )
                    schema_el.tooltip(
                        "PostgreSQL schema（namespace）。默认 public；"
                        "非默认时会通过 options=-csearch_path=<schema> 注入到 URI。"
                    )
                    inputs["schema"] = schema_el

                user_el = (
                    ui.input(label="用户名 username", value=initial.username or "")
                    .props("outlined dense")
                )
                user_el.tooltip("建议专用账号，仅授予本库最小权限")
                inputs["username"] = user_el

                pwd_el = (
                    ui.input(label="密码 password", value=initial.password or "", password=True, password_toggle_button=True)
                    .props("outlined dense")
                )
                pwd_el.tooltip("密码里的特殊字符会自动 URL-encode，无需手动转义")
                inputs["password"] = pwd_el

                if t.has_charset:
                    charset_el = (
                        ui.input(label="字符集 charset", value=initial.charset or (t.default_charset or ""))
                        .props("outlined dense")
                    )
                    charset_el.tooltip("MySQL 建议 utf8mb4，支持 emoji / 生僻字")
                    inputs["charset"] = charset_el

            for el in inputs.values():
                el.classes("w-full")
                el.on("update:model-value", lambda _=None: _refresh_summary())

    def _on_type_change(_=None) -> None:
        new_key = type_select.value
        # 切换方言时：若新类型匹配当前解析出来的配置，沿用；否则给一份默认。
        initial = values if values.db_type == new_key else defaults_for(
            db_type_by_key(new_key) or DB_TYPES[0]
        )
        _render_fields_for(new_key, initial)
        _refresh_summary()
        _clear_result()

    # 引用占位：库不存在时的「立即创建」按钮会被 _show_result 按需显隐。
    create_btn_holder: Dict[str, Any] = {"btn": None}

    def _clear_result() -> None:
        if result_row is not None:
            result_row.visible = False
        if create_btn_holder["btn"] is not None:
            create_btn_holder["btn"].visible = False

    def _show_result(
        ok: bool,
        message: str,
        detail: str = "",
        *,
        db_missing: bool = False,
        icon_override: Optional[str] = None,
        color_override: Optional[str] = None,
    ) -> None:
        if result_row is None:
            return
        result_row.visible = True
        if icon_override or color_override:
            result_icon.props(
                f"name={icon_override or 'info'} color={color_override or 'primary'}"
            )
        elif ok:
            result_icon.props("name=check_circle color=positive")
        elif db_missing:
            result_icon.props("name=add_circle color=warning")
        else:
            result_icon.props("name=error color=negative")
        result_text.text = message
        result_detail.text = detail or ""
        result_detail.visible = bool(detail)
        # 仅在「库不存在」时显式展示「立即创建」按钮，避开其它错误场景。
        if create_btn_holder["btn"] is not None:
            create_btn_holder["btn"].visible = bool(db_missing)

    def _on_validate() -> None:
        try:
            uri = build_uri(_collect())
        except Exception as e:  # noqa: BLE001
            _show_result(False, f"URI 组装失败：{type(e).__name__}", str(e))
            return
        _show_result(
            False, "正在验证连接……", uri,
            icon_override="hourglass_top", color_override="primary",
        )
        # 同步阻塞验证，5s 超时；用户可以接受。
        res = validate_connection(uri, timeout=5)
        _show_result(
            bool(res.get("ok")),
            str(res.get("message", "")),
            str(res.get("detail", "")),
            db_missing=bool(res.get("db_missing")),
        )

    def _on_create_db() -> None:
        values = _collect()
        _show_result(
            False, "正在创建数据库……", "",
            icon_override="hourglass_top", color_override="primary",
        )
        res = create_database(values, timeout=5)
        if not res.get("ok"):
            _show_result(
                False, str(res.get("message", "建库失败")), str(res.get("detail", "")),
                db_missing=True,  # 保留「立即创建」以便用户改参数后重试
            )
            return
        # 建库成功后再自动跑一次连接验证，给用户一个闭环反馈。
        created = bool(res.get("created"))
        try:
            uri = build_uri(values)
        except Exception:
            uri = ""
        vres = validate_connection(uri, timeout=5) if uri else {"ok": False, "message": "URI 组装失败", "detail": ""}
        if vres.get("ok"):
            prefix = "已创建并连通" if created else "已存在且连通"
            _show_result(
                True, f"{prefix}：{res.get('message', '')}", uri,
            )
        else:
            _show_result(
                False,
                f"{res.get('message', '')}；但验证连接仍失败：{vres.get('message')}",
                str(vres.get("detail", "")),
                db_missing=bool(vres.get("db_missing")),
            )

    def _on_save() -> None:
        values = _collect()
        try:
            uri = build_uri(values)
        except Exception as e:  # noqa: BLE001
            ui.notify(f"URI 组装失败：{type(e).__name__}: {e}", color="negative")
            return

        updates: Dict[str, Any] = {"SQLALCHEMY_DATABASE_URI": uri}
        # sqlite 额外同步 DB_ROOT_PATH，保持和旧代码的路径期望一致（很多旧工具读它）。
        if uri.startswith("sqlite://"):
            p = uri[len("sqlite://"):]
            updates["DB_ROOT_PATH"] = p

        try:
            _path, _bak, changes = yaml_store.save_updates("basic_settings.yaml", updates)
        except Exception as e:  # noqa: BLE001
            ui.notify(f"保存失败：{type(e).__name__}: {e}", color="negative")
            return

        if changes:
            ui.notify(
                f"已保存到 basic_settings.yaml（{len(changes)} 项）。"
                "SQLALCHEMY_DATABASE_URI 需要重启服务后生效。",
                color="positive", timeout=6000,
            )
            if mark_restart_needed is not None:
                try:
                    mark_restart_needed()
                except Exception:
                    pass
        else:
            ui.notify("配置未变化；继续检查目标库是否存在", color="info", timeout=3000)

        # —— 保存完成后：立即判断目标库是否存在，不存在就自动建库。—— #
        vres = validate_connection(uri, timeout=5)
        if vres.get("ok"):
            _show_result(True, "已保存并连通：" + str(vres.get("message", "")), uri)
            return

        if not vres.get("db_missing"):
            _show_result(
                False,
                "已保存，但连接验证失败：" + str(vres.get("message", "")),
                str(vres.get("detail", "")),
                db_missing=False,
            )
            return

        # 库不存在 → 自动建库（sqlite 会 touch 文件；PG/MySQL 走 init_scripts SQL）。
        ui.notify("目标数据库不存在，正在自动创建……", color="info", timeout=3000)
        cres = create_database(values, timeout=5)
        if not cres.get("ok"):
            _show_result(
                False,
                "已保存，但自动建库失败：" + str(cres.get("message", "")),
                str(cres.get("detail", "")),
                db_missing=True,
            )
            return

        # 建库后再验一次
        vres2 = validate_connection(uri, timeout=5)
        if vres2.get("ok"):
            ui.notify(cres.get("message", "已自动创建数据库"), color="positive", timeout=6000)
            _show_result(True, "已保存并自动创建：" + str(cres.get("message", "")), uri)
        else:
            _show_result(
                False,
                f"{cres.get('message','')}；但验证仍失败：{vres2.get('message','')}",
                str(vres2.get("detail", "")),
                db_missing=bool(vres2.get("db_missing")),
            )

    # === 布局 =================================================================
    with ui.card().classes("w-full q-mb-md q-pa-md"):
        # 行 1：标题 + 方言下拉 + 摘要
        with ui.row().classes("items-center w-full no-wrap q-mb-sm").style("gap:12px"):
            ui.label("主业务数据库").classes("text-base font-semibold")
            type_select = (
                ui.select(
                    {t.key: t.label for t in DB_TYPES},
                    label="数据库类型",
                    value=values.db_type if db_type_by_key(values.db_type) else "sqlite",
                )
                .props("outlined dense")
                .style("min-width: 260px")
            )
            type_select.tooltip(
                "切换后下方字段会跟着变；保存前请点「验证连接」确认可连。"
            )
            type_select.on("update:model-value", _on_type_change)
            ui.space()
            summary_label = ui.label(human_summary(values)).classes(
                "text-xs text-grey-7 font-mono"
            ).style("word-break: break-all; max-width: 360px; text-align: right")

        # 行 2：动态字段区（按方言刷新）
        fields_container = ui.column().classes("w-full q-gutter-xs")
        _render_fields_for(type_select.value, values)

        # 行 3：结果 + 操作按钮
        with ui.row().classes("items-center w-full no-wrap q-mt-sm").style("gap:8px"):
            result_row = ui.row().classes("items-center").style(
                "flex: 1; min-width: 0; gap: 6px;"
            )
            result_row.visible = False
            with result_row:
                result_icon = ui.icon("info").style("font-size: 20px")
                with ui.column().classes("q-gutter-none").style("min-width: 0"):
                    result_text = ui.label("").classes("text-sm")
                    result_detail = ui.label("").classes(
                        "text-xs text-grey-7 font-mono"
                    ).style("word-break: break-all")
                    result_detail.visible = False

            ui.space()
            ui.button("复制 URI", icon="content_copy", on_click=lambda: _copy_to_clipboard(ui, build_uri(_collect()))).props("flat dense").tooltip(
                "把当前表单组装出的完整 SQLAlchemy URI 复制到剪贴板（密码已 URL-encode）"
            )
            create_btn = ui.button(
                "立即创建", icon="add_circle", on_click=_on_create_db,
            ).props("color=warning dense").tooltip(
                "直接连上数据库服务器执行 CREATE DATABASE（脚本在 chayuan/server/db/init_scripts/）"
            )
            create_btn.visible = False
            create_btn_holder["btn"] = create_btn
            ui.button("验证连接", icon="bolt", on_click=_on_validate).props("color=secondary dense").tooltip(
                "实际发起一次 SELECT 1；超时 5 秒。若目标库不存在会提示并显出「立即创建」"
            )
            ui.button("保存", icon="save", on_click=_on_save).props("color=primary dense").tooltip(
                "写入 basic_settings.yaml；保存后会自动检测目标库——不存在则立即创建"
            )
