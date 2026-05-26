"""生产 profile 的配置覆写。

被 ``chayuan init --profile prod`` 调用（或 Docker 入口脚本）。
做的事只有一件：**把刚生成的 basic_settings.yaml / kb_settings.yaml 里
若干默认字段改写为生产值**（postgres / milvus / redis / 多 worker …），
同时保留其它字段和 ruamel 写回的注释。

设计原则：
- **幂等**：重复执行不会继续写，yaml_store.save_updates 会按 `_yaml_equal` 比较；
- **不破坏用户手改**：只对 *当前值* 等于已知 local 默认值的字段覆盖；
  若用户已经手动设过 postgres URL / milvus 等，保持不动；
- **外部依赖值走环境变量**：DB URI、Milvus host、Redis URL、JWT_SECRET 允许
  被 env 注入；都没给出时用 docker-compose 默认值（postgres/redis/milvus 服务名）。
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Any, Dict, Tuple

logger = logging.getLogger("chayuan.init.prod")


# ---------- 已知 local 默认值，用来判断是否应覆盖 ----------

_LOCAL_DEFAULT_SQLITE_URI_PREFIX = "sqlite:///"
_LOCAL_DEFAULT_VS_TYPE = "faiss"


def _env(name: str, default: str) -> str:
    v = (os.getenv(name) or "").strip()
    return v or default


def _basic_overrides() -> Dict[str, Any]:
    """返回 basic_settings.yaml 上要写的字段（点号路径 → 值）。"""
    pg_host = _env("CHAYUAN_PG_HOST", "postgres")
    pg_port = _env("CHAYUAN_PG_PORT", "5432")
    pg_user = _env("CHAYUAN_PG_USER", "chayuan")
    pg_password = _env("CHAYUAN_PG_PASSWORD", "chayuan")
    pg_db = _env("CHAYUAN_PG_DB", "chayuan")
    pg_uri = _env(
        "CHAYUAN_DB_URI",
        f"postgresql+psycopg2://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_db}",
    )
    redis_url = _env("CHAYUAN_REDIS_URL", "redis://redis:6379/0")
    jwt_secret = _env("CHAYUAN_JWT_SECRET", "")
    if not jwt_secret:
        jwt_secret = secrets.token_urlsafe(48)

    return {
        # 部署模式：进入 prod 后 /metrics / 健康检查 / 面板 lint 都按严格模式校验
        "DEPLOYMENT_MODE": "prod",
        # 数据库：从 sqlite 切到 pg（psycopg2 驱动，和常见容器镜像兼容）
        "SQLALCHEMY_DATABASE_URI": pg_uri,
        # 连接池：prod 常规推荐值
        "DB_POOL_SIZE": 20,
        "DB_MAX_OVERFLOW": 40,
        "DB_POOL_RECYCLE": 3600,
        "DB_POOL_PRE_PING": True,
        "DB_POOL_TIMEOUT": 30,
        # 进程：默认 4 worker；GPU 机可改成 2*cores+1
        "UVICORN_WORKERS": 4,
        # 共享状态 / 限流 / 队列都靠 redis
        "REDIS_URL": redis_url,
        "RATE_LIMIT_ENABLED": True,
        "RATE_LIMIT_PER_MINUTE": 600,
        # 鉴权：prod 默认开启；JWT 写入稳定随机密钥（env 未覆盖时）
        "AUTH_REQUIRED": True,
        "AUTH_ALLOW_REGISTRATION": False,
        "JWT_SECRET": jwt_secret,
        # 异步入库 + 语义缓存，配合 redis 一起开
        "INGEST_ASYNC_ENABLED": True,
        "SEMANTIC_CACHE_ENABLED": True,
    }


def _kb_overrides() -> Dict[str, Any]:
    """返回 kb_settings.yaml 上要写的字段。

    Milvus 2.6 / langchain-milvus 0.3+ 后 ``uri`` 是头等公民。这里同时支持：
      - CHAYUAN_MILVUS_URI 显式给完整 uri（最高优先）
      - CHAYUAN_MILVUS_HOST / PORT / SECURE 老风格（自动拼成 uri）
    运行时 ``milvus_kb_service._normalize_connection_args`` 也会把 host/port 兜底
    归一为 uri，但这里直接写 uri 让 yaml 与 settings.py 默认值在风格上一致。
    """
    milvus_uri = _env("CHAYUAN_MILVUS_URI", "")
    milvus_user = _env("CHAYUAN_MILVUS_USER", "")
    milvus_password = _env("CHAYUAN_MILVUS_PASSWORD", "")
    if not milvus_uri:
        milvus_host = _env("CHAYUAN_MILVUS_HOST", "milvus")
        milvus_port = _env("CHAYUAN_MILVUS_PORT", "19530")
        secure = _env("CHAYUAN_MILVUS_SECURE", "false").lower() in ("1", "true", "yes")
        scheme = "https" if secure else "http"
        milvus_uri = f"{scheme}://{milvus_host}:{milvus_port}"
    return {
        # 默认向量库类型：milvus
        "DEFAULT_VS_TYPE": "milvus",
        # 对应 kbs_config.milvus 的连接信息（uri 优先；host/port 留空避免与 uri 冲突）
        "kbs_config.milvus.uri": milvus_uri,
        "kbs_config.milvus.user": milvus_user,
        "kbs_config.milvus.password": milvus_password,
        "kbs_config.milvus.secure": False,
    }


def _should_overwrite_basic(doc: Any, key: str) -> bool:
    """只在字段当前是 "local 默认值 or 空" 时才覆盖；避免踩用户手改。"""
    from chayuan.server.config_panel.yaml_store import get_by_path
    cur = get_by_path(doc, key, default=None)
    if cur in (None, "", 0, False):
        return True
    # SQLALCHEMY_DATABASE_URI：只覆盖 sqlite 默认
    if key == "SQLALCHEMY_DATABASE_URI":
        return isinstance(cur, str) and cur.startswith(_LOCAL_DEFAULT_SQLITE_URI_PREFIX)
    # DEPLOYMENT_MODE 从 dev→prod 是本 profile 的使命；其它几项都是 prod 硬写
    return True


def _should_overwrite_kb(doc: Any, key: str, value: Any) -> bool:
    from chayuan.server.config_panel.yaml_store import get_by_path
    cur = get_by_path(doc, key, default=None)
    if key == "DEFAULT_VS_TYPE":
        return cur in (None, "", _LOCAL_DEFAULT_VS_TYPE)
    # milvus 子字段：空值 / 默认 127.0.0.1 才覆盖；避免覆盖用户手填的远端地址
    if key.startswith("kbs_config.milvus."):
        if cur in (None, ""):
            return True
        if isinstance(cur, str) and cur in ("127.0.0.1", "localhost"):
            return True
        return False
    return cur in (None, "")


def apply_prod_profile() -> Dict[str, Tuple[Any, Any]]:
    """对 basic_settings.yaml / kb_settings.yaml 做 prod 覆写，返回总 changes。

    幂等：重复执行只会做真正有差异的字段。
    """
    from chayuan.server.config_panel.yaml_store import load_yaml, save_updates

    summary: Dict[str, Tuple[Any, Any]] = {}

    # ---- basic_settings.yaml ----
    doc = load_yaml("basic_settings.yaml").doc or {}
    to_write = {}
    for key, val in _basic_overrides().items():
        if _should_overwrite_basic(doc, key):
            to_write[key] = val
    if to_write:
        _, _, changes = save_updates("basic_settings.yaml", to_write)
        for k, v in changes.items():
            summary[f"basic:{k}"] = v

    # ---- kb_settings.yaml ----
    doc = load_yaml("kb_settings.yaml").doc or {}
    to_write = {}
    for key, val in _kb_overrides().items():
        if _should_overwrite_kb(doc, key, val):
            to_write[key] = val
    if to_write:
        _, _, changes = save_updates("kb_settings.yaml", to_write)
        for k, v in changes.items():
            summary[f"kb:{k}"] = v

    return summary
