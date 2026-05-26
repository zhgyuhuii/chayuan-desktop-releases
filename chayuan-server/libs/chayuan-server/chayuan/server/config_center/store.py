"""ConfigStore —— DB + Redis + yaml 三级读写。

读取顺序
--------
1. **Redis**（命中即返回，TTL 默认 300s + Pub/Sub 失效事件主动清除）
2. **DB**（miss 时回源，读到后回填 Redis）
3. **yaml snapshot**（仅 DB 也不可达时作为只读降级）

写入顺序
--------
1. DB 事务：UPSERT ``chayuan_config`` + INSERT ``chayuan_config_history``
2. Redis：`SET` 新值 + `PUBLISH` 到 ``chayuan:config:events``
3. yaml snapshot（可选，便于离线备份 / dr 快照）

并发模型
--------
- ``ConfigStore`` 无状态（仅握 engine），可跨线程 / asyncio 使用；
- 真正的热更新由 ``subscribe.py`` 的后台 Redis 订阅 task + 本地 ``register_callback``
  协作完成；每个业务 store（apps / custom_tools / ws_endpoints）注册一个回调，
  事件到达时清自己进程内缓存即可。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ConfigEntry, ConfigHistory


def _maybe_coroutine(obj: Any):
    """如果 obj 是 awaitable 返回它，否则返回 None。"""
    if inspect.iscoroutine(obj) or inspect.isawaitable(obj):
        return obj
    return None


def _sync_awaitable_or_none(obj: Any) -> Any:
    """同步接口里兼容误传入的 async Redis 客户端。

    - 当前线程没有运行中的事件循环：临时跑完 coroutine,拿到真实返回值；
    - 已在 NiceGUI/FastAPI 事件循环中：同步函数不能阻塞 await,直接关闭 coroutine
      并返回 None,让调用方走 DB/YAML 兜底,同时避免 RuntimeWarning。
    """
    aw = _maybe_coroutine(obj)
    if aw is None:
        return obj
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            return asyncio.run(aw)
        except Exception:  # noqa: BLE001
            return None
    if loop.is_running():
        close = getattr(aw, "close", None)
        if callable(close):
            close()
        return None
    try:
        return loop.run_until_complete(aw)
    except Exception:  # noqa: BLE001
        return None


def _session():
    """每次获取 SessionLocal；便于单测 reload ``chayuan.server.db.base`` 后立刻生效。"""
    from chayuan.server.db.base import SessionLocal as _SL
    return _SL()


logger = logging.getLogger("chayuan.config_center")


REDIS_KEY_PREFIX = "chayuan:config:"
REDIS_CHANNEL = "chayuan:config:events"
REDIS_TTL_SECONDS = 300


class ConfigStore:
    """线程安全；惰性握 Redis 连接，拿不到 Redis 也能纯走 DB。"""

    def __init__(self) -> None:
        self._local_lock = threading.Lock()

    # --- helpers ---

    @staticmethod
    def _redis_key(namespace: str, key: str) -> str:
        return f"{REDIS_KEY_PREFIX}{namespace}:{key}"

    @staticmethod
    def _redis() -> Any:
        try:
            from chayuan.server.shared import get_redis
            return get_redis()  # 可能返回 None
        except Exception:  # noqa: BLE001
            return None

    # --- 读 ---

    def get(
        self, namespace: str, key: str, default: Any = None,
        *, allow_yaml_fallback: bool = True, yaml_fallback_path: Optional[Path] = None,
    ) -> Any:
        # 1) Redis
        r = self._redis()
        if r is not None:
            try:
                cached = _sync_awaitable_or_none(r.get(self._redis_key(namespace, key)))
            except Exception:  # noqa: BLE001
                cached = None
            if cached is not None:
                try:
                    return json.loads(cached)
                except Exception:  # noqa: BLE001
                    pass  # 格式坏掉继续回源

        # 2) DB
        try:
            with _session() as session:  # type: Session
                row: Optional[ConfigEntry] = session.execute(
                    select(ConfigEntry)
                    .where(ConfigEntry.namespace == namespace,
                           ConfigEntry.key == key)
                ).scalar_one_or_none()
        except Exception as e:  # noqa: BLE001
            logger.warning("ConfigStore.get DB failed ns=%s key=%s err=%r",
                           namespace, key, e)
            row = None

        if row is not None:
            try:
                val = json.loads(row.value) if row.value is not None else None
            except Exception:  # noqa: BLE001
                val = None
            self._warm_redis(namespace, key, val)
            return val

        # 3) yaml fallback（只读）
        if allow_yaml_fallback and yaml_fallback_path is not None:
            try:
                if yaml_fallback_path.is_file():
                    from chayuan.pydantic_settings_file import import_yaml
                    with open(yaml_fallback_path, "r", encoding="utf-8") as f:
                        doc = import_yaml().load(f) or {}
                    if isinstance(doc, dict) and key in doc:
                        return doc[key]
            except Exception:  # noqa: BLE001
                logger.debug("yaml fallback failed", exc_info=True)

        return default

    def get_namespace(
        self, namespace: str,
        *, yaml_fallback_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """一次返回该 namespace 下全部 key-value 的 dict，供业务 store 组装出
        原本「一个 yaml 文件」的结构。"""
        try:
            with _session() as session:
                rows = session.execute(
                    select(ConfigEntry)
                    .where(ConfigEntry.namespace == namespace)
                ).scalars().all()
                out: Dict[str, Any] = {}
                for r in rows:
                    try:
                        out[r.key] = json.loads(r.value) if r.value else None
                    except Exception:  # noqa: BLE001
                        out[r.key] = None
                if out:
                    return out
        except Exception as e:  # noqa: BLE001
            logger.warning("ConfigStore.get_namespace DB failed ns=%s err=%r",
                           namespace, e)

        # DB 空或挂掉：yaml fallback
        if yaml_fallback_path and yaml_fallback_path.is_file():
            try:
                from chayuan.pydantic_settings_file import import_yaml
                with open(yaml_fallback_path, "r", encoding="utf-8") as f:
                    doc = import_yaml().load(f) or {}
                return dict(doc) if isinstance(doc, dict) else {}
            except Exception:  # noqa: BLE001
                logger.debug("yaml fallback failed", exc_info=True)
        return {}

    # --- 写 ---

    def set(
        self, namespace: str, key: str, value: Any,
        *, updated_by: str = "system", comment: str = "",
    ) -> int:
        """返回新版本号；把 DB / Redis / Pub/Sub 一起更新。"""
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
        try:
            with _session() as session:  # type: Session
                row: Optional[ConfigEntry] = session.execute(
                    select(ConfigEntry)
                    .where(ConfigEntry.namespace == namespace,
                           ConfigEntry.key == key)
                ).scalar_one_or_none()
                if row is None:
                    row = ConfigEntry(
                        namespace=namespace, key=key, value=raw,
                        version=1, updated_by=updated_by, comment=comment,
                    )
                    session.add(row)
                else:
                    row.value = raw
                    row.version = (row.version or 0) + 1
                    row.updated_by = updated_by
                    row.comment = comment
                session.add(ConfigHistory(
                    namespace=namespace, key=key, value=raw,
                    version=row.version, updated_by=updated_by, comment=comment,
                ))
                session.commit()
                new_version = row.version
        except Exception as e:  # noqa: BLE001
            logger.error("ConfigStore.set DB failed ns=%s key=%s err=%r",
                         namespace, key, e)
            raise

        # Redis 侧：设缓存 + 发布失效事件
        self._publish_update(namespace, key, value, new_version)
        return new_version

    def delete(
        self, namespace: str, key: str,
        *, updated_by: str = "system", comment: str = "deleted",
    ) -> bool:
        try:
            with _session() as session:
                row = session.execute(
                    select(ConfigEntry)
                    .where(ConfigEntry.namespace == namespace,
                           ConfigEntry.key == key)
                ).scalar_one_or_none()
                if row is None:
                    return False
                session.add(ConfigHistory(
                    namespace=namespace, key=key, value=row.value,
                    version=(row.version or 0) + 1,
                    updated_by=updated_by, comment=f"DELETE: {comment}",
                ))
                session.delete(row)
                session.commit()
        except Exception as e:  # noqa: BLE001
            logger.error("ConfigStore.delete DB failed ns=%s key=%s err=%r",
                         namespace, key, e)
            return False

        # Redis 侧：del + 通知
        r = self._redis()
        if r is not None:
            try:
                _sync_awaitable_or_none(r.delete(self._redis_key(namespace, key)))
            except Exception:  # noqa: BLE001
                pass
        self._publish_event({
            "op": "delete", "namespace": namespace, "key": key,
            "ts": int(time.time()),
        })
        return True

    # --- 历史 ---

    def history(
        self, namespace: str, key: str, limit: int = 20,
    ) -> List[Dict[str, Any]]:
        try:
            with _session() as session:
                rows = session.execute(
                    select(ConfigHistory)
                    .where(ConfigHistory.namespace == namespace,
                           ConfigHistory.key == key)
                    .order_by(ConfigHistory.version.desc())
                    .limit(int(limit))
                ).scalars().all()
                return [
                    {
                        "version": r.version,
                        "updated_at": (r.updated_at.isoformat()
                                        if r.updated_at else None),
                        "updated_by": r.updated_by,
                        "comment": r.comment,
                        "value": (json.loads(r.value)
                                  if r.value else None),
                    }
                    for r in rows
                ]
        except Exception as e:  # noqa: BLE001
            logger.warning("history failed ns=%s key=%s err=%r",
                           namespace, key, e)
            return []

    # --- 内部 ---

    def _warm_redis(self, namespace: str, key: str, value: Any) -> None:
        r = self._redis()
        if r is None:
            return
        try:
            _sync_awaitable_or_none(r.setex(
                self._redis_key(namespace, key),
                REDIS_TTL_SECONDS,
                json.dumps(value, ensure_ascii=False, sort_keys=True),
            ))
        except Exception:  # noqa: BLE001
            pass

    def _publish_update(
        self, namespace: str, key: str, value: Any, version: int,
    ) -> None:
        self._warm_redis(namespace, key, value)
        self._publish_event({
            "op": "set", "namespace": namespace, "key": key,
            "version": version, "ts": int(time.time()),
        })

    def _publish_event(self, evt: Dict[str, Any]) -> None:
        # **无条件**本地派发一次（写入方本进程立即感知）；然后尝试 Redis publish
        # 通知其它副本。Redis 订阅 task 只处理跨副本消息时才有额外价值；回调本身
        # 幂等（reload 两次无副作用），所以即便同时本地 + Redis 来一份也安全。
        from .subscribe import dispatch_local
        dispatch_local(evt)
        r = self._redis()
        if r is None:
            return
        try:
            # redis-py 同步 / 异步客户端的 publish 行为不同；异步返回 coroutine
            # 这里用 `getattr` 避免同步调 async 方法
            pub = r.publish(REDIS_CHANNEL, json.dumps(evt, ensure_ascii=False))
            if asyncio_coroutine := _maybe_coroutine(pub):
                # 异步客户端：fire-and-forget 调度到事件循环
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.ensure_future(asyncio_coroutine)
                    else:
                        loop.run_until_complete(asyncio_coroutine)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass


_STORE: Optional[ConfigStore] = None


def get_store() -> ConfigStore:
    global _STORE
    if _STORE is None:
        _STORE = ConfigStore()
    return _STORE
