"""知识源三层缓存（P0-2）。

分层：
1. SchemaCache       — 热：Redis(TTL 1h)；冷：knowledge_source_schema_cache 表
2. Text2XTemplateCache — Redis 精确 key（sha1(q + source + schema_hash)）；TTL 24h
3. ResultCache       — Redis 短 TTL（默认 60s）；只对"非时间敏感"查询开启

核心 guard：
- **时间敏感词检测**：查询里出现"今天/现在/刚才/last N/latest/now/today"等词 → 自动跳过 2/3 层
- **Redis 不可用 fail-open**：所有 get 返回 None、所有 set 静默成功；不拖业务路径
- **用户维度隔离**：key 可带 user_id / kb_scope，避免多租户串数据

集成点（已在 graph_text2sql / orchestrator 注入）：
- graph_text2sql.node_generate 入口 → template cache 命中则跳过 LLM 直接填 sql
- graph_text2sql 末尾 → 写 result cache（非时间敏感）
- sql/connector._load_schema → schema hot cache（Redis）
- vector_adapter 检索结果 → result cache
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("chayuan.knowledge_source.cache")


# 时间敏感词（中英混合）；任何一个命中就跳过缓存的 2/3 层
_TIME_SENSITIVE_PATTERNS = [
    r"\b(today|yesterday|tomorrow|now|latest|current|recent|this\s+(hour|day|week|month|year))\b",
    r"\b(last\s+\d+\s+(minute|hour|day|week|month|year)s?)\b",
    r"\b(past\s+\d+\s+(minute|hour|day|week|month|year)s?)\b",
    r"今天|昨天|明天|现在|刚才|刚刚|实时|当前|最近|近期|本日|本周|本月|本年",
    r"过去\s*\d+\s*(分钟|小时|天|周|月|年)",
    r"最近\s*\d+\s*(分钟|小时|天|周|月|年)",
]
_TIME_RE = re.compile("|".join(_TIME_SENSITIVE_PATTERNS), flags=re.IGNORECASE)


def is_time_sensitive(query: str) -> bool:
    if not query:
        return False
    return bool(_TIME_RE.search(query))


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Redis client（尽量复用项目已有 resilience.py 的单例）
# ---------------------------------------------------------------------------

_REDIS_CLIENT = None
_REDIS_CHECKED = False


def _get_redis():
    """懒加载 Redis 客户端；失败（或未配置）返回 None，所有缓存操作 fail-open。

    单例按 REDIS_URL 构建；短 timeout 避免拖慢主路径。
    """
    global _REDIS_CLIENT, _REDIS_CHECKED
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    if _REDIS_CHECKED:
        return None
    _REDIS_CHECKED = True
    try:
        from chayuan.settings import Settings
        import redis  # type: ignore
        url = (getattr(Settings.basic_settings, "REDIS_URL", "") or "").strip()
        if not url:
            return None
        _REDIS_CLIENT = redis.Redis.from_url(
            url, decode_responses=False,
            socket_connect_timeout=1.0, socket_timeout=1.0,
        )
        _REDIS_CLIENT.ping()
        return _REDIS_CLIENT
    except Exception as e:  # noqa: BLE001
        logger.debug("Redis 未就绪，三层缓存 fail-open：%r", e)
        _REDIS_CLIENT = None
        return None


def _rget(key: str) -> Optional[bytes]:
    r = _get_redis()
    if r is None:
        return None
    try:
        return r.get(key)
    except Exception as e:  # noqa: BLE001
        logger.debug("redis get 失败：%r", e)
        return None


def _rset(key: str, value: bytes, ttl_sec: int) -> None:
    r = _get_redis()
    if r is None:
        return
    try:
        r.set(key, value, ex=int(max(1, ttl_sec)))
    except Exception as e:  # noqa: BLE001
        logger.debug("redis set 失败：%r", e)


def _rdel(pattern: str) -> None:
    r = _get_redis()
    if r is None:
        return
    try:
        # 用 scan 迭代删除，避免 KEYS 阻塞
        for k in r.scan_iter(match=pattern, count=500):
            r.delete(k)
    except Exception as e:  # noqa: BLE001
        logger.debug("redis scan+del 失败：%r", e)


# ===========================================================================
# Layer 1. Schema Hot Cache
# ===========================================================================

_SCHEMA_TTL = 3600  # 1 小时


def schema_cache_get(source_id: int) -> Optional[Dict[str, Any]]:
    raw = _rget(f"ks:schema:{int(source_id)}")
    _metric_cache("schema", hit=bool(raw))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


def _metric_cache(layer: str, hit: bool, bypass_reason: str = "") -> None:
    try:
        from chayuan.server.observability.ks_metrics import (
            CACHE_HIT, CACHE_MISS, CACHE_BYPASS,
        )
        if bypass_reason:
            CACHE_BYPASS.labels(layer=layer, reason=bypass_reason).inc()
        elif hit:
            CACHE_HIT.labels(layer=layer).inc()
        else:
            CACHE_MISS.labels(layer=layer).inc()
    except Exception:  # noqa: BLE001
        pass


def schema_cache_set(source_id: int, snapshot_dict: Dict[str, Any], ttl: int = _SCHEMA_TTL) -> None:
    try:
        _rset(f"ks:schema:{int(source_id)}", json.dumps(snapshot_dict, ensure_ascii=False, default=str).encode("utf-8"), ttl_sec=ttl)
    except Exception as e:  # noqa: BLE001
        logger.debug("schema_cache_set 失败：%r", e)


def schema_cache_invalidate(source_id: int) -> None:
    _rdel(f"ks:schema:{int(source_id)}")


# ===========================================================================
# Layer 2. Text2X Template Cache（LLM 生成结果）
# ===========================================================================

_TEMPLATE_TTL = 24 * 3600  # 24 小时


def _schema_hash(schema_like: Any) -> str:
    """对 schema 做稳定 hash；schema 改变时 key 自然失效。"""
    try:
        if isinstance(schema_like, dict):
            s = json.dumps(schema_like, sort_keys=True, ensure_ascii=False, default=str)
        else:
            # SchemaSnapshot 对象：取表名 + 列名签名
            parts = []
            for t in getattr(schema_like, "tables", []) or []:
                cols = ",".join(c.name for c in (t.columns or []))
                parts.append(f"{t.name}[{cols}]")
            s = "|".join(parts)
        return _sha1(s)
    except Exception:  # noqa: BLE001
        return "nohash"


def template_key(source_id: int, query: str, schema_like: Any, kind: str = "sql") -> str:
    return f"ks:t2x:{kind}:{int(source_id)}:{_schema_hash(schema_like)}:{_sha1(query or '')}"


def template_cache_get(source_id: int, query: str, schema_like: Any, kind: str = "sql") -> Optional[Dict[str, Any]]:
    if is_time_sensitive(query):
        _metric_cache("template", hit=False, bypass_reason="time_sensitive")
        return None
    raw = _rget(template_key(source_id, query, schema_like, kind))
    _metric_cache("template", hit=bool(raw))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


def template_cache_set(
    source_id: int, query: str, schema_like: Any, payload: Dict[str, Any],
    kind: str = "sql", ttl: int = _TEMPLATE_TTL,
) -> None:
    if is_time_sensitive(query):
        return
    try:
        _rset(
            template_key(source_id, query, schema_like, kind),
            json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
            ttl_sec=ttl,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("template_cache_set 失败：%r", e)


def template_cache_invalidate(source_id: int) -> None:
    _rdel(f"ks:t2x:*:{int(source_id)}:*")


# ===========================================================================
# Layer 3. Result Cache（短 TTL 查询结果）
# ===========================================================================

_RESULT_TTL = 60  # 1 分钟；时间敏感查询直接 bypass


def _result_key(kind: str, sid: int, query: str, user_id: Optional[int] = None) -> str:
    user_part = f":u{int(user_id)}" if user_id else ""
    return f"ks:result:{kind}:{int(sid)}{user_part}:{_sha1(query or '')}"


def result_cache_get(
    kind: str, source_id: int, query: str, user_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if is_time_sensitive(query):
        _metric_cache("result", hit=False, bypass_reason="time_sensitive")
        return None
    raw = _rget(_result_key(kind, source_id, query, user_id))
    _metric_cache("result", hit=bool(raw))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


def result_cache_set(
    kind: str, source_id: int, query: str, payload: Dict[str, Any],
    user_id: Optional[int] = None, ttl: int = _RESULT_TTL,
) -> None:
    if is_time_sensitive(query):
        return
    try:
        _rset(
            _result_key(kind, source_id, query, user_id),
            json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
            ttl_sec=ttl,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("result_cache_set 失败：%r", e)


def invalidate_result_cache_by_source(source_id: int) -> None:
    _rdel(f"ks:result:*:{int(source_id)}*")


def invalidate_result_cache_by_kb(kb_name: str) -> None:
    """向量源按 kb_name 存；这里仅清向量源 kind=vector 的全局条目（简化）。"""
    _rdel(f"ks:result:vector:*")


# ===========================================================================
# 统一健康视图（供 /metrics / 管理面板用）
# ===========================================================================

def cache_health() -> Dict[str, Any]:
    r = _get_redis()
    return {
        "redis_ok": r is not None,
        "layers": ["schema(1h)", "template(24h)", "result(60s)"],
        "time_sensitive_bypass": True,
    }
