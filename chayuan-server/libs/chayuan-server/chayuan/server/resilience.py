"""弹性：LLM 调用重试 / 超时 / 可选语义缓存。

- ``llm_retry()`` 装饰器：指数退避重试，支持同步 & 异步；缺 ``tenacity`` 时降级为
  一次手写的退避循环，保证行为一致。
- ``SemanticCache``：基于 Redis 的 embedding 哈希缓存，存在命中则直接返回历史响应。
  依赖 ``REDIS_URL`` 已配置 + ``embed_fn`` 传入；否则所有操作变成 no-op，
  透明穿透到真正的 LLM 调用。

注：真正要不要打开语义缓存，要结合业务判断："问答"类非严格一致场景收益高；
涉及时间 / 实时数据的场景可能"缓存脏数据"反而更糟。
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import random
import threading
import time
from typing import Any, Awaitable, Callable, Iterable, Optional, Type, TypeVar, Union

logger = logging.getLogger("chayuan.resilience")

F = TypeVar("F", bound=Callable[..., Any])

_DEFAULT_RETRY_EXCEPTIONS: tuple = (Exception,)


def _retry_cfg() -> tuple[int, float, float]:
    try:
        from chayuan.settings import Settings
        bs = Settings.basic_settings
        attempts = int(getattr(bs, "LLM_RETRY_ATTEMPTS", 3) or 0)
        wait_max = float(getattr(bs, "LLM_RETRY_WAIT_MAX", 8.0) or 0.0)
        timeout = float(getattr(bs, "LLM_TIMEOUT_SECONDS", 120.0) or 0.0)
    except Exception:
        attempts, wait_max, timeout = 3, 8.0, 120.0
    return attempts, wait_max, timeout


# ---------------------------------------------------------------------------
# Circuit breaker（P2-7）
# ---------------------------------------------------------------------------
#
# 设计目标：LLM / embedding / SQL 后端挂掉时，不要一直傻等；短时间内连续失败 N 次
# 后主动"打开"，接下来所有请求**立即**抛 BreakerOpenError，避免 worker 池被拖死。
# 冷却一段时间后进入半开 —— 放过第一个请求做探针，成功则关闭，失败则再度打开。
#
# 三态：
#   closed   —— 正常放行；成功清零计数器，失败 +1；失败数 >= threshold ⇒ open
#   open     —— 立即失败；直到 now >= opened_at + cool_down ⇒ half_open
#   half_open—— 只放 1 个探针；成功 ⇒ closed，失败 ⇒ 重新 open
#
# 进程内单例字典按 provider 名查找；跨 worker 不共享（有意为之：避免 Redis 依赖；
# 多 worker 各自独立判断 —— 坏的 provider 整体失败率高会让每个 worker 都开熔断）。


class BreakerOpenError(RuntimeError):
    """熔断器打开期间抛出，供上层捕获后快速失败。"""

    def __init__(self, provider: str, retry_after: float) -> None:
        super().__init__(
            f"circuit breaker open for provider={provider!r}, retry after {retry_after:.1f}s"
        )
        self.provider = provider
        self.retry_after = retry_after


class ProviderBreaker:
    """单个 provider 的三态熔断器。线程安全。"""

    STATE_CLOSED = "closed"
    STATE_OPEN = "open"
    STATE_HALF_OPEN = "half_open"

    def __init__(
        self,
        provider: str,
        *,
        failure_threshold: int = 5,
        cool_down_seconds: float = 60.0,
    ) -> None:
        self.provider = provider
        self.failure_threshold = max(1, int(failure_threshold))
        self.cool_down = max(1.0, float(cool_down_seconds))
        self._state = self.STATE_CLOSED
        self._fail_count = 0
        self._opened_at = 0.0
        self._half_open_in_flight = False
        self._lock = threading.Lock()

    # ----- 对外 API -----

    def before_call(self) -> None:
        """放行前检查。半开态只允许 1 个探针进入。"""
        with self._lock:
            now = time.time()
            if self._state == self.STATE_OPEN:
                if now - self._opened_at >= self.cool_down:
                    self._state = self.STATE_HALF_OPEN
                    self._half_open_in_flight = False
                    self._emit_state()
                else:
                    raise BreakerOpenError(
                        self.provider,
                        retry_after=max(0.0, self.cool_down - (now - self._opened_at)),
                    )
            if self._state == self.STATE_HALF_OPEN:
                if self._half_open_in_flight:
                    raise BreakerOpenError(self.provider, retry_after=0.5)
                self._half_open_in_flight = True

    def on_success(self) -> None:
        with self._lock:
            if self._state == self.STATE_HALF_OPEN:
                self._state = self.STATE_CLOSED
                self._half_open_in_flight = False
                self._fail_count = 0
                self._emit_state()
            elif self._state == self.STATE_CLOSED:
                self._fail_count = 0

    def on_failure(self) -> None:
        with self._lock:
            if self._state == self.STATE_HALF_OPEN:
                # 探针失败 → 重新打开，计时重置
                self._state = self.STATE_OPEN
                self._opened_at = time.time()
                self._half_open_in_flight = False
                self._emit_state()
                return
            self._fail_count += 1
            if self._state == self.STATE_CLOSED and self._fail_count >= self.failure_threshold:
                self._state = self.STATE_OPEN
                self._opened_at = time.time()
                self._emit_state()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "provider": self.provider,
                "state": self._state,
                "fail_count": self._fail_count,
                "opened_at": self._opened_at,
                "cool_down": self.cool_down,
            }

    # ----- 内部 -----

    def _emit_state(self) -> None:
        try:
            from chayuan.server.observability.metrics import set_breaker_state
            set_breaker_state(self.provider, self._state)
        except Exception:  # noqa: BLE001
            logger.debug("set_breaker_state failed", exc_info=True)


_BREAKERS: dict[str, ProviderBreaker] = {}
_BREAKERS_LOCK = threading.Lock()


def get_breaker(
    provider: str,
    *,
    failure_threshold: Optional[int] = None,
    cool_down_seconds: Optional[float] = None,
) -> ProviderBreaker:
    """按 provider 名取或建一个 breaker；热参数从 Settings 读。"""
    name = (provider or "default").strip() or "default"
    with _BREAKERS_LOCK:
        b = _BREAKERS.get(name)
        if b is not None:
            return b
        # 默认读 Settings，缺则 5 次 / 60 秒
        try:
            from chayuan.settings import Settings
            bs = Settings.basic_settings
            ft = int(
                failure_threshold
                if failure_threshold is not None
                else getattr(bs, "LLM_BREAKER_FAILURE_THRESHOLD", 5) or 5
            )
            cd = float(
                cool_down_seconds
                if cool_down_seconds is not None
                else getattr(bs, "LLM_BREAKER_COOLDOWN_SECONDS", 60.0) or 60.0
            )
        except Exception:
            ft, cd = int(failure_threshold or 5), float(cool_down_seconds or 60.0)
        b = ProviderBreaker(name, failure_threshold=ft, cool_down_seconds=cd)
        _BREAKERS[name] = b
        try:
            from chayuan.server.observability.metrics import set_breaker_state
            set_breaker_state(name, b.STATE_CLOSED)
        except Exception:  # noqa: BLE001
            pass
        return b


def breaker_state_snapshot() -> list[dict]:
    """诊断端点用：返回当前全部 breaker 快照。"""
    with _BREAKERS_LOCK:
        return [b.snapshot() for b in _BREAKERS.values()]


def llm_retry(
    *,
    attempts: Optional[int] = None,
    wait_max: Optional[float] = None,
    retry_on: Iterable[Type[BaseException]] = _DEFAULT_RETRY_EXCEPTIONS,
    provider: Optional[str] = None,
) -> Callable[[F], F]:
    """指数退避重试装饰器。同时支持同步 / async 函数。

    - 参数缺省时读 ``BasicSettings`` 配置，便于运行时热更；
    - 失败会 ``logger.warning`` + 计入 Prometheus；
    - 最后一次失败抛原异常。
    - ``provider`` 非空时挂接 ``ProviderBreaker``：每次进入函数前 ``before_call``，
      成功/失败回调 on_success / on_failure；熔断打开时 BreakerOpenError 不重试直接抛。
    """

    def decorator(func: F) -> F:
        is_coro = asyncio.iscoroutinefunction(func)

        def _should_retry(exc: BaseException) -> bool:
            # BreakerOpenError 不重试：熔断打开时继续 retry 只会叠加噪声
            if isinstance(exc, BreakerOpenError):
                return False
            return isinstance(exc, tuple(retry_on))

        def _sleep_for(i: int, w_max: float) -> float:
            # 0.5 * 2^i + jitter[0,0.25)，封顶 w_max
            base = min(0.5 * (2 ** i), w_max)
            return base + random.random() * 0.25

        _breaker = get_breaker(provider) if provider else None

        if is_coro:
            @functools.wraps(func)
            async def awrapper(*args: Any, **kwargs: Any) -> Any:
                total, w_max, _timeout = _retry_cfg()
                if attempts is not None:
                    total = int(attempts)
                if wait_max is not None:
                    w_max = float(wait_max)

                last_exc: Optional[BaseException] = None
                for i in range(total + 1):
                    if _breaker is not None:
                        _breaker.before_call()
                    try:
                        res = await func(*args, **kwargs)
                        if _breaker is not None:
                            _breaker.on_success()
                        return res
                    except BaseException as e:  # noqa: BLE001
                        if _breaker is not None and not isinstance(e, BreakerOpenError):
                            _breaker.on_failure()
                        last_exc = e
                        if not _should_retry(e) or i >= total:
                            raise
                        wait = _sleep_for(i, w_max)
                        logger.warning(
                            "LLM call failed (attempt %d/%d): %r; retry in %.2fs",
                            i + 1, total + 1, e, wait,
                        )
                        await asyncio.sleep(wait)
                assert last_exc is not None
                raise last_exc

            return awrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def swrapper(*args: Any, **kwargs: Any) -> Any:
            total, w_max, _timeout = _retry_cfg()
            if attempts is not None:
                total = int(attempts)
            if wait_max is not None:
                w_max = float(wait_max)

            last_exc: Optional[BaseException] = None
            for i in range(total + 1):
                if _breaker is not None:
                    _breaker.before_call()
                try:
                    res = func(*args, **kwargs)
                    if _breaker is not None:
                        _breaker.on_success()
                    return res
                except BaseException as e:  # noqa: BLE001
                    if _breaker is not None and not isinstance(e, BreakerOpenError):
                        _breaker.on_failure()
                    last_exc = e
                    if not _should_retry(e) or i >= total:
                        raise
                    wait = _sleep_for(i, w_max)
                    logger.warning(
                        "LLM call failed (attempt %d/%d): %r; retry in %.2fs",
                        i + 1, total + 1, e, wait,
                    )
                    time.sleep(wait)
            assert last_exc is not None
            raise last_exc

        return swrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# 语义缓存（可选）
# ---------------------------------------------------------------------------

class SemanticCache:
    """最朴素的 embedding 哈希缓存：

    - 用 ``embed_fn(text)`` 得到向量；
    - 按 bucket (向量分量正负号 → 32bit 指纹) + 原文 SHA1 做 key；
    - 命中直接返回；未命中由调用方真正算，然后 ``set`` 写回。

    只推荐：FAQ / 百科 / 客服类场景。业务侧如果接入，必须在 cache key
    里加入租户 / 角色，防串权限。
    """

    def __init__(self, namespace: str = "chayuan:semcache", ttl_seconds: int = 3600):
        self.namespace = namespace
        self.ttl = int(ttl_seconds)

    def _key(self, text: str, embedding: Optional[list[float]] = None) -> str:
        h = hashlib.sha1()
        h.update(text.encode("utf-8"))
        if embedding:
            bits = "".join("1" if v >= 0 else "0" for v in embedding[:64])
            h.update(bits.encode("ascii"))
        return f"{self.namespace}:{h.hexdigest()}"

    async def get(self, text: str, embedding: Optional[list[float]] = None) -> Optional[str]:
        client = await self._client()
        if client is None:
            return None
        try:
            raw = await client.get(self._key(text, embedding))
            if raw is None:
                return None
            return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        except Exception:  # noqa: BLE001
            logger.debug("SemanticCache.get failed", exc_info=True)
            return None

    async def set(
        self,
        text: str,
        response: str,
        embedding: Optional[list[float]] = None,
    ) -> None:
        client = await self._client()
        if client is None:
            return
        try:
            await client.set(self._key(text, embedding), response, ex=self.ttl)
        except Exception:  # noqa: BLE001
            logger.debug("SemanticCache.set failed", exc_info=True)

    async def _client(self):
        try:
            from chayuan.server.shared import get_redis

            return get_redis()
        except Exception:
            return None


# ---------------------------------------------------------------------------
# 业务侧对外：纯同步 get/set（chat / kb_chat 在路由层调用）
# ---------------------------------------------------------------------------

def _sem_enabled() -> bool:
    try:
        from chayuan.settings import Settings
        return bool(getattr(Settings.basic_settings, "SEMANTIC_CACHE_ENABLED", False))
    except Exception:
        return False


def _sem_namespace() -> str:
    try:
        from chayuan.settings import Settings
        return str(
            getattr(Settings.basic_settings, "SEMANTIC_CACHE_NAMESPACE", "chayuan:semcache")
            or "chayuan:semcache"
        )
    except Exception:
        return "chayuan:semcache"


def _sem_ttl() -> int:
    try:
        from chayuan.settings import Settings
        return int(getattr(Settings.basic_settings, "SEMANTIC_CACHE_TTL_SECONDS", 900) or 0)
    except Exception:
        return 900


def _sem_min_len() -> int:
    try:
        from chayuan.settings import Settings
        return int(getattr(Settings.basic_settings, "SEMANTIC_CACHE_MIN_LEN", 8) or 0)
    except Exception:
        return 8


def _sem_scope_key(
    *, query: str, user_id: Optional[Any], kb: Optional[str], model: Optional[str],
    extra: Optional[dict] = None,
) -> str:
    """把租户 / KB / 模型 / 问题都编进 key；串权限会直接 miss。"""
    h = hashlib.sha1()
    h.update(("u=" + str(user_id or "")).encode("utf-8"))
    h.update(("|kb=" + str(kb or "")).encode("utf-8"))
    h.update(("|m=" + str(model or "")).encode("utf-8"))
    if extra:
        try:
            h.update(("|x=" + json.dumps(extra, ensure_ascii=False, sort_keys=True)).encode("utf-8"))
        except Exception:
            pass
    h.update(b"|q=")
    h.update((query or "").strip().encode("utf-8"))
    return f"{_sem_namespace()}:{h.hexdigest()}"


def semcache_get(
    *, query: str, user_id: Optional[Any], kb: Optional[str], model: Optional[str],
    extra: Optional[dict] = None,
) -> Optional[dict]:
    """同步读：未命中 / 未启用 / 未连 redis 一律返回 None。

    启用了语义缓存但 Redis 连不上时，会以 5 分钟一次的节流发一条 warning，
    附上「请到配置面板配置 Redis」的指引（见 shared.redis_health）。
    """
    if not _sem_enabled():
        return None
    if not query or len(query.strip()) < _sem_min_len():
        return None

    from chayuan.server.shared.redis_health import warn_redis_unavailable

    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("redis", "redis>=5.0,<6.0")
        import redis  # type: ignore
        from chayuan.settings import Settings
        url = (getattr(Settings.basic_settings, "REDIS_URL", "") or "").strip()
        if not url:
            warn_redis_unavailable("semcache", reason="REDIS_URL 未配置")
            return None
        client = redis.Redis.from_url(
            url, decode_responses=True,
            socket_connect_timeout=1.0, socket_timeout=1.0,
        )
        key = _sem_scope_key(query=query, user_id=user_id, kb=kb, model=model, extra=extra)
        raw = client.get(key)
        if not raw:
            return None
        data = json.loads(raw)
        data["_cached"] = True
        return data
    except Exception as e:  # noqa: BLE001
        warn_redis_unavailable("semcache", reason=f"{type(e).__name__}: {e}")
        logger.debug("semcache_get failed: %r", e)
        return None


def semcache_set(
    *,
    query: str,
    response: dict,
    user_id: Optional[Any],
    kb: Optional[str],
    model: Optional[str],
    extra: Optional[dict] = None,
) -> None:
    if not _sem_enabled():
        return
    if not query or len(query.strip()) < _sem_min_len():
        return

    from chayuan.server.shared.redis_health import warn_redis_unavailable

    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("redis", "redis>=5.0,<6.0")
        import redis  # type: ignore
        from chayuan.settings import Settings
        url = (getattr(Settings.basic_settings, "REDIS_URL", "") or "").strip()
        if not url:
            warn_redis_unavailable("semcache", reason="REDIS_URL 未配置")
            return
        client = redis.Redis.from_url(
            url, decode_responses=True,
            socket_connect_timeout=1.0, socket_timeout=1.0,
        )
        key = _sem_scope_key(query=query, user_id=user_id, kb=kb, model=model, extra=extra)
        client.set(key, json.dumps(response, ensure_ascii=False, default=str), ex=_sem_ttl())
    except Exception as e:  # noqa: BLE001
        warn_redis_unavailable("semcache", reason=f"{type(e).__name__}: {e}")
        logger.debug("semcache_set failed: %r", e)
