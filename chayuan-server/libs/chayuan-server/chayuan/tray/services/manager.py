"""企业版 embedded 服务的顶层编排。

被 :class:`chayuan.tray.supervisor.Backend` 在 ``start()`` / ``stop()`` 里
调用。职责：

1. 探活 bundle 里是否有内嵌服务二进制；没有就退化为 no-op；
2. 首次启动：``initdb`` + 启动 Postgres + 启动 Redis；
3. 把解析出的 ``SQLALCHEMY_DATABASE_URI`` / ``REDIS_URL`` / Milvus-Lite DB
   路径 patch 回 ``basic_settings.yaml`` / ``kb_settings.yaml``，再跑
   ``chayuan init`` / 后端服务时都能读到正确地址；
4. 把运行态（端口号、数据库名）落到 ``~/.chayuan/services/ports.json``，方便
   排障。

对外只暴露 ``ensure_up()`` / ``shutdown()`` 两个函数。
"""
from __future__ import annotations

import json
import os
import logging
from pathlib import Path
from typing import Dict

from chayuan.tray.services import should_enable
from chayuan.tray.services import postgres as _pg
from chayuan.tray.services import redis as _redis

logger = logging.getLogger("chayuan.tray.services.manager")


STATE_FILE = Path.home() / ".chayuan" / "services" / "state.json"


def ensure_up() -> Dict[str, str]:
    """拉起 embedded 服务；幂等。返回写入 settings 的值。

    非企业版 / 没 bundle / 被 env 关闭 时返回空 dict，调用方不要改 settings。
    """
    if not should_enable():
        return {}

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    try:
        did_initdb = _pg.ensure_initdb()
        _pg.start()
        _pg.ensure_database()
        pg_uri = _pg.sqlalchemy_uri()
        logger.info("embedded postgres up (initdb=%s) -> %s", did_initdb, _pg.data_dir())
    except Exception as e:
        logger.exception("embedded postgres 启动失败：%s", e)
        raise

    try:
        port = _redis.start()
        redis_url = _redis.url(port)
        logger.info("embedded redis up on %d", port)
    except Exception as e:
        logger.exception("embedded redis 启动失败：%s", e)
        # PG 已经起来了，尽量关干净再抛
        try:
            _pg.stop()
        except Exception:  # noqa: BLE001
            pass
        raise

    # Milvus-Lite：不需要单独起进程，用 file-based URI 让 pymilvus 自动
    # 单进程 embed。路径放到 ~/.chayuan/services/milvus-lite.db。
    milvus_lite_path = Path.home() / ".chayuan" / "services" / "milvus-lite.db"

    result = {
        "SQLALCHEMY_DATABASE_URI": pg_uri,
        "REDIS_URL": redis_url,
        "MILVUS_LITE_URI": str(milvus_lite_path),
        "PG_SOCKET": str(_pg.socket_dir()),
        "REDIS_PORT": str(port),
    }

    STATE_FILE.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result


def shutdown() -> None:
    """tray 退出时调用；幂等。"""
    try:
        _redis.stop()
    except Exception:  # noqa: BLE001
        logger.warning("redis stop 异常", exc_info=True)
    try:
        _pg.stop()
    except Exception:  # noqa: BLE001
        logger.warning("postgres stop 异常", exc_info=True)


def apply_to_settings_yaml() -> None:
    """把 ``ensure_up()`` 得到的 URI 回写到 basic_settings.yaml / kb_settings.yaml。

    只在值还是"默认占位"（prod profile 写的 ``postgres:5432`` / ``redis:6379``）
    或空字符串时才覆盖；用户如果已经手动把它改成外部服务地址，保持不动。
    """
    if not STATE_FILE.is_file():
        return
    try:
        overrides = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return

    try:
        from chayuan.server.config_panel import yaml_store
    except Exception:  # noqa: BLE001
        return

    # basic_settings 里的 DB / Redis
    basic = yaml_store.load_yaml("basic_settings.yaml")
    basic_doc = basic.doc
    changes: Dict[str, object] = {}

    cur_uri = (yaml_store.get_by_path(basic_doc, "SQLALCHEMY_DATABASE_URI") or "").strip()
    if _is_prod_default_pg_uri(cur_uri):
        changes["SQLALCHEMY_DATABASE_URI"] = overrides["SQLALCHEMY_DATABASE_URI"]

    cur_redis = (yaml_store.get_by_path(basic_doc, "REDIS_URL") or "").strip()
    if _is_prod_default_redis_url(cur_redis):
        changes["REDIS_URL"] = overrides["REDIS_URL"]

    if changes:
        yaml_store.save_updates("basic_settings.yaml", changes)

    # kb_settings 里的 Milvus 连接 → Milvus-Lite URI
    # Milvus 2.6 / langchain-milvus 0.3+ 后 uri 是头等公民；同时清理旧 host/port 字段
    # 避免与 uri 共存导致 _normalize_connection_args 优先级困惑。
    kb = yaml_store.load_yaml("kb_settings.yaml")
    kb_doc = kb.doc
    kb_changes: Dict[str, object] = {}

    milvus_uri = (yaml_store.get_by_path(kb_doc, "kbs_config.milvus.uri") or "").strip()
    milvus_host = (yaml_store.get_by_path(kb_doc, "kbs_config.milvus.host") or "").strip()
    is_default = (
        milvus_uri in ("", "http://127.0.0.1:19530", "http://localhost:19530", "http://milvus:19530")
        and milvus_host in ("", "milvus", "127.0.0.1", "localhost")
    )
    if is_default:
        # MilvusClient(uri=file:./xxx.db) 让 pymilvus 切到 Milvus-Lite 嵌入模式。
        kb_changes["kbs_config.milvus.uri"] = overrides["MILVUS_LITE_URI"]
        kb_changes["kbs_config.milvus.host"] = ""
        kb_changes["kbs_config.milvus.port"] = ""
    if kb_changes:
        yaml_store.save_updates("kb_settings.yaml", kb_changes)


def _is_prod_default_pg_uri(uri: str) -> bool:
    # init_prod_profile 默认：postgresql+psycopg2://chayuan:chayuan@postgres:5432/chayuan
    if not uri:
        return True
    return "@postgres:5432" in uri or "@postgres/" in uri


def _is_prod_default_redis_url(url: str) -> bool:
    if not url:
        return True
    return "@redis:" in url or "://redis:" in url
