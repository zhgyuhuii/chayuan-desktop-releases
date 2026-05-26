"""``chayuan-supervisor`` ↔ ``chayuan-server`` 运行时适配层。

为什么要这层？
==============

仓库里此刻并存两套"运行时元数据"代码：

* ``chayuan_supervisor.credentials.RuntimeInfo`` —— supervisor 独立运行所需，
  落 ``<CHAYUAN_HOME>/data/runtime.json``，schema = ``{credentials, endpoints}``；
* ``chayuan.server.runtime.runtime_info.RuntimeInfo`` —— chayuan-server 主进程
  使用，落 ``<CHAYUAN_ROOT>/runtime.json``，schema = ``{services, credentials}``。

合并后我们希望它们 **看到同一份 runtime.json**：避免"前端面板查的端口"
和"supervisor 真正占的端口"两份记账。

策略：本模块在 import 时探测 ``chayuan.server.runtime.*``：

* 探测成功 → 用 chayuan-server 的版本作为 source-of-truth，并把它包成 supervisor
  期待的 ``RuntimeInfo`` API（``credentials/set_credentials/endpoint/set_endpoint``）。
* 探测失败（standalone supervisor）→ 回退到 supervisor 自带实现。

公开 API：
* :func:`get_runtime_info_unified`
* :func:`ensure_credentials_unified`
* :func:`make_unified_port_allocator`

调用方（``manager.py`` / ``credentials.py`` 内部）继续使用既有签名，无感知变化。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional, Tuple

logger = logging.getLogger("chayuan_supervisor.runtime_adapter")

_BACKEND: Optional[str] = None  # "server" | "supervisor"
_LOCK = threading.Lock()


def _detect_backend() -> str:
    """优先用 chayuan-server；否则回退 supervisor 自带。"""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    with _LOCK:
        if _BACKEND is None:
            try:
                from chayuan.server.runtime import runtime_info as _ri  # noqa: F401
                from chayuan.server.runtime import credentials as _cr   # noqa: F401
                _BACKEND = "server"
            except Exception as e:  # noqa: BLE001
                logger.debug("[runtime_adapter] 未发现 chayuan-server，回退 supervisor 模式：%r", e)
                _BACKEND = "supervisor"
    return _BACKEND


# ---------------------------------------------------------------------------
# RuntimeInfo 适配：把 chayuan-server 的接口包成 supervisor 期待的形状
# ---------------------------------------------------------------------------


class _ServerRuntimeInfoAdapter:
    """让 ``chayuan.server.runtime.runtime_info.RuntimeInfo`` 看起来像
    ``chayuan_supervisor.credentials.RuntimeInfo``。

    映射：
    * supervisor.credentials → server.credentials  （字段同名，直接转）
    * supervisor.endpoints   → server.services    （顶层 key 不同，做 alias）
    """

    def __init__(self, ri) -> None:
        self._ri = ri

    @property
    def path(self):
        return self._ri.path

    # ---- credentials ----

    def credentials(self, name: str) -> dict:
        c = self._ri.get_credentials(name) if hasattr(self._ri, "get_credentials") \
            else self._ri._data.get("credentials", {}).get(name, {})
        return dict(c or {})

    def set_credentials(self, name: str, *, user: str, password: str) -> None:
        self._ri.set_credentials(name, user, password)

    def all_credentials(self) -> dict:
        return dict(self._ri._data.get("credentials", {}) or {})

    # ---- endpoints (supervisor 词)  → services (server 词) ----

    def endpoint(self, name: str) -> dict:
        ep = self._ri.get_endpoint(name)
        return dict(ep or {})

    def set_endpoint(self, name: str, **fields: Any) -> None:
        """容错版：把 chayuan-server set_endpoint 不识别的 key 收到 extra 里。

        supervisor 旧 API 的 ``info.set_endpoint(name, **arbitrary)`` 会拼任意
        键值（``console_port``、``metrics_port``、``kind``...）；server 端用
        keyword-only 强约束，本方法把不在白名单的键放到 ``extra={}`` 中。
        """
        known = {
            "host", "port", "scheme", "user", "password", "database",
            "url", "console_url", "health_url", "kind", "version", "binary",
            "pid",
        }
        accepted = {k: v for k, v in fields.items() if k in known}
        extras = {k: v for k, v in fields.items() if k not in known}
        if extras:
            accepted["extra"] = {**accepted.get("extra", {}), **extras}
        # 一些常见别名重定向：alias 端口 → extra.<alias>
        self._ri.set_endpoint(name, **accepted)

    def all_endpoints(self) -> dict:
        return {
            k: dict(v) for k, v in (self._ri._data.get("services", {}) or {}).items()
            if isinstance(v, dict)
        }

    def to_dict(self) -> dict:
        import json
        return json.loads(json.dumps(self._ri._data))


def get_runtime_info_unified():
    """返回一份 ``RuntimeInfo``：优先 chayuan-server，回退 supervisor 自带。"""
    if _detect_backend() == "server":
        from chayuan.server.runtime.runtime_info import get_runtime_info as _server_get
        return _ServerRuntimeInfoAdapter(_server_get())
    # 回退：原 supervisor 实现
    from chayuan_supervisor.credentials import get_runtime_info as _legacy_get
    return _legacy_get()


# ---------------------------------------------------------------------------
# ensure_credentials 适配
# ---------------------------------------------------------------------------


def ensure_credentials_unified(
    name: str,
    spec_creds: Optional[dict],
) -> Tuple[dict, dict]:
    """与 ``chayuan_supervisor.credentials.ensure_credentials`` 同签名。

    优先委托给 ``chayuan.server.runtime.credentials.ensure_credentials``，
    再把它返回的 ``Credentials`` → 拆成 ``(env_vars, record)`` 让 supervisor 用。
    """
    spec_creds = spec_creds or {}
    if spec_creds.get("no_auth"):
        return ({}, {})

    if _detect_backend() == "server":
        from chayuan.server.runtime.credentials import ensure_credentials as _server_ensure
        creds = _server_ensure(
            name,
            user=spec_creds.get("user"),
            no_auth=False,
        )
        env: dict[str, str] = {}
        if u_env := spec_creds.get("user_env"):
            env[u_env] = creds.user
        if p_env := spec_creds.get("password_env"):
            env[p_env] = creds.password
        return env, {"user": creds.user, "password": creds.password}

    # 回退：原 supervisor 实现
    from chayuan_supervisor.credentials import ensure_credentials as _legacy_ensure
    return _legacy_ensure(name, spec_creds)


# ---------------------------------------------------------------------------
# PortAllocator 适配
# ---------------------------------------------------------------------------


def make_unified_port_allocator(low: int, high: int):
    """返回一个 PortAllocator。

    * 当 chayuan-server 装上时：返回 ``chayuan.server.runtime.port_allocator.PortAllocator``，
      并把 runtime.json 中已记录的端口都 ``reserve`` 进来，避免新分配抢老端口。
      ⚠ 该 PortAllocator 的 API（``allocate(*, preferred=, name=, skip=)``）与 supervisor
      的 ``allocate(preferred=)`` 不完全一致；这里返回的实例同样支持 supervisor 的位置参数
      用法（通过 wrapper 转换）。
    * 否则回退 supervisor 自带 ``PortAllocator(low, high)``。
    """
    if _detect_backend() == "server":
        from chayuan.server.runtime.port_allocator import PortAllocator as _ServerPA
        pa = _ServerPA(port_range=(low, high))
        # 把 runtime.json 中已经有 port 的服务先占住
        try:
            ri = get_runtime_info_unified()
            for name, ep in ri.all_endpoints().items():
                p = ep.get("port")
                if isinstance(p, int) and low <= p <= high:
                    pa.reserve(p, name=name)
        except Exception as e:  # noqa: BLE001
            logger.debug("[runtime_adapter] reserve historical ports failed: %r", e)
        return _PortAllocatorShim(pa)

    from chayuan_supervisor.port_allocator import PortAllocator as _LegacyPA
    return _LegacyPA(low, high)


class _PortAllocatorShim:
    """让 chayuan-server 的 PortAllocator 同时支持 supervisor 老 API。

    supervisor 用 ``allocate(preferred=N)`` 而 server 用 ``allocate(preferred=N, name=...)``；
    两者都通过本 shim 透出。
    """

    def __init__(self, inner) -> None:
        self._inner = inner

    def reserve(self, p: int, *, name: Optional[str] = None) -> None:
        self._inner.reserve(p, name=name)

    def release(self, p: int) -> None:
        self._inner.release(p)

    def used(self):
        return tuple(sorted(self._inner.taken))

    def allocate(self, preferred: Optional[int] = None, *, name: Optional[str] = None,
                 skip=None) -> int:
        return self._inner.allocate(preferred=preferred, name=name, skip=skip)

    def allocate_many(self, count: int) -> list[int]:
        return [self.allocate() for _ in range(count)]


__all__ = [
    "get_runtime_info_unified",
    "ensure_credentials_unified",
    "make_unified_port_allocator",
]
