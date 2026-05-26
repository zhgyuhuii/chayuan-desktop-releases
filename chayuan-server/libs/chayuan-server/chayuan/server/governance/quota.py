"""配额：Redis 令牌桶（QPS） + 日累计（token_budget）。

设计：
- **QPS 令牌桶**：Redis 原子 INCR + TTL；进图第一道 node_quota_check 调用 check_and_reserve
- **日 token 预算**：Redis 天级 INCRBY；node_finalize.record_usage 累加
- Redis 不可用 → **fail-open**（不拒请求；记 warn）
- 策略从 governance.policy 读取：user-level → role-level → global

两个入口：
- ``check_and_reserve(user_id, user_role)`` → (ok, reason)
- ``record_usage(user_id, tokens, mode)`` → None
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional, Tuple

logger = logging.getLogger("chayuan.governance.quota")


_REDIS_CLIENT = None
_REDIS_CHECKED = False


def _get_redis():
    """复用 knowledge_source.cache 的 Redis 单例；若未建则自己新建。"""
    global _REDIS_CLIENT, _REDIS_CHECKED
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    if _REDIS_CHECKED:
        return None
    _REDIS_CHECKED = True
    try:
        # 先尝试用已有 ks cache 的 redis（统一连接池）
        from chayuan.server.knowledge_source.cache import _get_redis as _ks_redis
        r = _ks_redis()
        if r is not None:
            _REDIS_CLIENT = r
            return r
    except Exception:  # noqa: BLE001
        pass
    # 自建
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
        logger.debug("quota redis 不可用（fail-open）：%r", e)
        return None


# ---------------------------------------------------------------------------
# Entry: check_and_reserve（进图第一道）
# ---------------------------------------------------------------------------

def check_and_reserve(user_id: int, user_role: str = "") -> Tuple[bool, str]:
    """返回 (ok, reason)。fail-open：策略读取 / Redis 异常都放行。"""
    try:
        from chayuan.server.governance.policy import get_policy
        policy = get_policy(user_id=user_id, user_role=user_role)
    except Exception as e:  # noqa: BLE001
        logger.debug("policy 读取失败（放行）：%r", e)
        return True, ""

    # 1) 日 token 预算（软限）：仅在超额且 budget>0 时拒
    if (policy.daily_token_budget or -1) > 0:
        r = _get_redis()
        if r is not None:
            try:
                key = _daily_token_key(user_id)
                used = r.get(key)
                used = int(used or 0) if used is not None else 0
                if used >= int(policy.daily_token_budget):
                    return False, f"日 token 预算已用尽（{used}/{policy.daily_token_budget}）"
            except Exception:  # noqa: BLE001
                pass

    # 2) QPS 令牌桶
    if (policy.qps or -1) > 0:
        r = _get_redis()
        if r is not None:
            try:
                if not _token_bucket_take(r, user_id, rate=int(policy.qps)):
                    return False, f"QPS 超限（上限 {policy.qps}/s）"
            except Exception:  # noqa: BLE001
                pass

    return True, ""


# ---------------------------------------------------------------------------
# Entry: record_usage（finalize 节点）
# ---------------------------------------------------------------------------

def record_usage(user_id: Optional[int], tokens: int, mode: str = "") -> None:
    if user_id is None or tokens <= 0:
        return
    r = _get_redis()
    if r is None:
        return
    try:
        key = _daily_token_key(int(user_id))
        pipe = r.pipeline()
        pipe.incrby(key, int(tokens))
        # 48h TTL，余量即第二日失效
        pipe.expire(key, 48 * 3600)
        pipe.execute()
    except Exception as e:  # noqa: BLE001
        logger.debug("record_usage 失败（忽略）：%r", e)


# ---------------------------------------------------------------------------
# 令牌桶实现（简化版：秒粒度；够用于 QPS 级别）
# ---------------------------------------------------------------------------

def _token_bucket_take(r, user_id: int, rate: int) -> bool:
    """在 1 秒窗口里发 rate 个令牌；用 INCR + 1s TTL 简化实现。

    适合 QPS ≤ 10 的场景；高 QPS 需换 Redis Lua 脚本做漏桶。
    """
    key = f"gov:qps:{int(user_id)}:{int(time.time())}"
    try:
        val = r.incr(key)
        if int(val) == 1:
            r.expire(key, 2)
        return int(val) <= int(rate)
    except Exception:  # noqa: BLE001
        return True


def _daily_token_key(user_id: Optional[int]) -> str:
    d = datetime.utcnow().strftime("%Y%m%d")
    return f"gov:tokens:{int(user_id or 0)}:{d}"


# ---------------------------------------------------------------------------
# 可视化支持：今日已用 token / 剩余额度
# ---------------------------------------------------------------------------

def today_usage(user_id: int, user_role: str = "") -> dict:
    used = 0
    r = _get_redis()
    if r is not None:
        try:
            v = r.get(_daily_token_key(user_id))
            used = int(v or 0) if v is not None else 0
        except Exception:  # noqa: BLE001
            pass
    budget = -1
    try:
        from chayuan.server.governance.policy import get_policy
        budget = int(get_policy(user_id=user_id, user_role=user_role).daily_token_budget)
    except Exception:  # noqa: BLE001
        pass
    return {"used": used, "budget": budget,
            "remaining": (budget - used) if budget > 0 else -1}
