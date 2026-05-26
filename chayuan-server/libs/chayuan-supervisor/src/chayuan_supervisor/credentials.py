"""Credential auto-generation and runtime endpoint persistence.

Why a separate module?
----------------------
Subprocesses like Postgres / MinIO / Redis (with `requirepass`) need a
username + password. We don't want the developer to write or remember them
nor accidentally commit them. So:

  1. On first start of a service, we generate a random password
     (`secrets.token_urlsafe(16)`) and a stable user name
     (defaults to `chayuan` unless overridden in the spec).
  2. The pair is persisted to `<CHAYUAN_HOME>/data/runtime.json` so the
     **same credentials** are used on every subsequent restart — the user can
     write them down once and keep using them.
  3. The same file also stores the **final port + bind address** picked by
     the supervisor (after preferred-port + auto-bump), so the user has a
     single source of truth they can `cat` to find connection info.

`runtime.json` schema:

    {
      "credentials": {
        "postgres": {"user": "chayuan", "password": "<random>"},
        "minio":    {"user": "chayuan_admin", "password": "<random>"},
        ...
      },
      "endpoints": {
        "postgres": {"host": "127.0.0.1", "port": 35432, "scheme": "postgresql",
                     "url": "postgresql://chayuan:<pwd>@127.0.0.1:35432/chayuan"},
        "redis":    {"host": "127.0.0.1", "port": 36379, "scheme": "redis",
                     "url": "redis://:<pwd>@127.0.0.1:36379/0"},
        "minio":    {"host": "127.0.0.1", "port": 39000, "console_port": 39001,
                     "url": "http://127.0.0.1:39000"},
        "milvus":   {"host": "127.0.0.1", "port": 39530, "scheme": "grpc",
                     "url": "127.0.0.1:39530"},
        "ollama":   {"host": "127.0.0.1", "port": 31434, "url": "http://127.0.0.1:31434"},
        ...
      },
      "updated_at": "2026-05-02T13:00:00Z"
    }
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chayuan_core import ensure_dirs

RUNTIME_FILE = "runtime.json"


class RuntimeInfo:
    """Threadsafe wrapper over `<CHAYUAN_HOME>/data/runtime.json`."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {"credentials": {}, "endpoints": {}}
        self._load()

    # ---- I/O ----------------------------------------------------------

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data["credentials"] = dict(raw.get("credentials", {}) or {})
                self._data["endpoints"] = dict(raw.get("endpoints", {}) or {})
        except Exception:
            pass

    def _save(self) -> None:
        self._data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    # ---- credentials --------------------------------------------------

    def credentials(self, name: str) -> dict[str, str]:
        with self._lock:
            return dict(self._data["credentials"].get(name, {}))

    def set_credentials(self, name: str, *, user: str, password: str) -> None:
        with self._lock:
            self._data["credentials"][name] = {"user": user, "password": password}
            self._save()

    # ---- endpoints ----------------------------------------------------

    def endpoint(self, name: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._data["endpoints"].get(name, {}))

    def set_endpoint(self, name: str, **fields: Any) -> None:
        with self._lock:
            cur = dict(self._data["endpoints"].get(name, {}))
            cur.update(fields)
            self._data["endpoints"][name] = cur
            self._save()

    def all_endpoints(self) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._data["endpoints"].items()}

    def all_credentials(self) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._data["credentials"].items()}

    def to_dict(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data))


_INFO: RuntimeInfo | None = None
_INFO_LOCK = threading.Lock()


def get_runtime_info() -> RuntimeInfo:
    global _INFO
    if _INFO is None:
        with _INFO_LOCK:
            if _INFO is None:
                p = ensure_dirs().data / RUNTIME_FILE
                _INFO = RuntimeInfo(p)
    return _INFO


def reset_for_tests(path: Path | None = None) -> RuntimeInfo:
    """Test helper: drop the singleton and re-init from a clean file.

    同时强制 ``runtime_adapter`` 走 supervisor 后端 —— 避免 chayuan-server 在场
    时把 manager.plan 的写入分流到 chayuan-server 的 runtime.json，让既有
    test_supervisor.py 之类的"独立 supervisor"测试用例继续读 supervisor schema。
    """
    global _INFO
    with _INFO_LOCK:
        target = path or (ensure_dirs().data / RUNTIME_FILE)
        if target.exists() and path is None:
            try:
                target.unlink()
            except OSError:
                pass
        _INFO = RuntimeInfo(target)
    # 同步把 runtime_adapter 锁定到 supervisor 后端
    try:
        import chayuan_supervisor.runtime_adapter as ra_mod
        ra_mod._BACKEND = "supervisor"
    except Exception:
        pass
    return _INFO


# ---- credential helpers ----------------------------------------------------


def _random_password() -> str:
    return secrets.token_urlsafe(16)


def ensure_credentials(
    name: str,
    spec_creds: dict[str, str] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (env_vars, credential_record).

    spec_creds may declare:
        user_env:          name of env var to populate with user (e.g. "PG_USER")
        password_env:      name of env var to populate with password
        user:              fixed user name override (default: "chayuan")
        password:          fixed password override (default: random, persisted)
        no_auth:           if true, skip credential generation entirely
    """
    spec_creds = spec_creds or {}
    if spec_creds.get("no_auth"):
        return ({}, {})
    info = get_runtime_info()
    existing = info.credentials(name)
    user = (
        spec_creds.get("user")
        or existing.get("user")
        or "chayuan"
    )
    password = (
        spec_creds.get("password")
        or existing.get("password")
        or _random_password()
    )
    env: dict[str, str] = {}
    if u_env := spec_creds.get("user_env"):
        env[u_env] = user
    if p_env := spec_creds.get("password_env"):
        env[p_env] = password
    if existing.get("user") != user or existing.get("password") != password:
        info.set_credentials(name, user=user, password=password)
    return env, {"user": user, "password": password}
