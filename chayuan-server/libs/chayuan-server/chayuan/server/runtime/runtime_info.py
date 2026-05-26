"""``<CHAYUAN_ROOT>/runtime.json`` 的读写门面。

``runtime.json`` 是察元运行时的 **single source of truth**，承载：

* 各 vendor / 内置服务最终绑定的 ``host`` / ``port`` / ``scheme`` / ``url``
  / ``health_url``；
* 自动生成的 ``user`` / ``password``；
* 启动时间戳 / pid（用于重启策略 / `chayuan service info`）；
* vendor 离线包的发现结果摘要（version / binary path）。

它**不取代** yaml：
* yaml 是"用户意图"（接哪个 PG / 用哪个 LLM）；
* runtime.json 是"实际跑成什么样"（端口被占自动改了 / 密码是哪个）。

格式
====

::

    {
      "version": 1,
      "updated_at": 1714654000,
      "services": {
        "postgres": {
          "host": "127.0.0.1",
          "port": 35432,
          "scheme": "postgresql",
          "user": "chayuan_postgres",
          "password": "abcDEF...",
          "database": "chayuan",
          "url": "postgresql://chayuan_postgres:abcDEF...@127.0.0.1:35432/chayuan",
          "health_url": "",
          "started_at": 1714654000,
          "pid": null,
          "extra": {}
        }
      },
      "credentials": {
        "redis": {"user": "", "password": "..."}
      }
    }

线程/多进程安全：写文件用临时文件 + ``os.replace`` 原子替换；多进程并发写
仍可能丢一次更新（但同一 service 不会半写状态），后续可加 ``filelock``。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from chayuan.server.runtime.credentials import mask_password_in_url

logger = logging.getLogger("chayuan.runtime.runtime_info")

_LOCK = threading.Lock()
_SINGLETON: Optional["RuntimeInfo"] = None


class ServiceEndpoint(dict):
    """一条 service 记录。dict 子类便于 JSON 直接序列化。

    使用属性式访问（保留 dict 接口），方便 CLI/router 端不关心字段缺失：

    >>> ep = ServiceEndpoint({"host": "127.0.0.1", "port": 35432})
    >>> ep.port
    35432
    >>> ep["port"]
    35432
    """

    _FIELDS = (
        "name", "host", "port", "scheme", "user", "password", "database",
        "url", "console_url", "health_url",
        "started_at", "pid",
        "kind", "version", "binary",
        "extra",
    )

    def __getattr__(self, item: str):
        if item in self._FIELDS:
            return self.get(item)
        raise AttributeError(item)

    def masked(self) -> "ServiceEndpoint":
        """返回密码被 ``****`` 替换后的副本，用于打印/前端展示。"""
        m = ServiceEndpoint(self)
        if m.get("password"):
            m["password"] = "****"
        if m.get("url"):
            m["url"] = mask_password_in_url(str(m["url"]))
        if m.get("console_url"):
            m["console_url"] = mask_password_in_url(str(m["console_url"]))
        return m


def runtime_info_path() -> Path:
    """``<CHAYUAN_ROOT>/runtime.json``。延迟解析以尊重 ``chayuan init`` 的目录。"""
    from chayuan.settings import CHAYUAN_ROOT
    return Path(CHAYUAN_ROOT) / "runtime.json"


class RuntimeInfo:
    """``runtime.json`` 的内存视图。

    用法：

    >>> ri = get_runtime_info()
    >>> ri.set_endpoint("postgres", host="127.0.0.1", port=35432, scheme="postgresql")
    >>> ri.list_endpoints()  # [ServiceEndpoint, ...]
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or runtime_info_path()
        self._data: Dict[str, Any] = self._load()

    @property
    def path(self) -> Path:
        return self._path

    # -- IO --

    def _load(self) -> Dict[str, Any]:
        if not self._path.is_file():
            return {"version": 1, "updated_at": 0, "services": {}, "credentials": {}}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("runtime.json 顶层不是 object")
            data.setdefault("services", {})
            data.setdefault("credentials", {})
            return data
        except (OSError, ValueError) as e:
            logger.warning("[runtime-info] 解析 %s 失败：%s；按空文件继续", self._path, e)
            return {"version": 1, "updated_at": 0, "services": {}, "credentials": {}}

    def _flush(self) -> None:
        self._data["version"] = 1
        self._data["updated_at"] = int(time.time())
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=f".{self._path.name}.", suffix=".tmp",
                dir=str(self._path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._path)
            except Exception:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
        except OSError as e:
            logger.warning("[runtime-info] 写 %s 失败：%s", self._path, e)

    # -- 凭据 --

    def get_credentials(self, name: str) -> Dict[str, str]:
        return dict(self._data.get("credentials", {}).get(name) or {})

    def set_credentials(self, name: str, user: str, password: str) -> None:
        with _LOCK:
            self._data.setdefault("credentials", {})[name] = {
                "user": user, "password": password,
            }
            self._flush()

    # -- endpoint --

    def get_endpoint(self, name: str) -> Optional[ServiceEndpoint]:
        raw = self._data.get("services", {}).get(name)
        if not isinstance(raw, dict):
            return None
        return ServiceEndpoint(raw, name=name)

    def set_endpoint(
        self, name: str,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        scheme: str = "",
        user: str = "",
        password: str = "",
        database: str = "",
        url: str = "",
        console_url: str = "",
        health_url: str = "",
        kind: str = "",
        version: str = "",
        binary: str = "",
        pid: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> ServiceEndpoint:
        with _LOCK:
            ep = ServiceEndpoint({
                "host": host, "port": int(port) if port else 0,
                "scheme": scheme,
                "user": user, "password": password,
                "database": database,
                "url": url,
                "console_url": console_url,
                "health_url": health_url,
                "kind": kind,
                "version": version,
                "binary": binary,
                "pid": pid,
                "started_at": int(time.time()),
                "extra": dict(extra or {}),
            })
            self._data.setdefault("services", {})[name] = dict(ep)
            self._flush()
            ep["name"] = name
            return ep

    def remove_endpoint(self, name: str) -> None:
        with _LOCK:
            self._data.get("services", {}).pop(name, None)
            self._flush()

    def list_endpoints(self) -> List[ServiceEndpoint]:
        out: List[ServiceEndpoint] = []
        for n, raw in (self._data.get("services") or {}).items():
            if isinstance(raw, dict):
                ep = ServiceEndpoint(raw)
                ep["name"] = n
                out.append(ep)
        return sorted(out, key=lambda e: str(e.get("name") or ""))

    # -- 测试用 --

    def reload(self) -> None:
        """从磁盘重读（测试时换 ``CHAYUAN_ROOT`` 后调用）。"""
        with _LOCK:
            self._data = self._load()


def get_runtime_info(*, force_reload: bool = False) -> RuntimeInfo:
    """全局单例。``force_reload=True`` 用于测试切目录后强制重读。"""
    global _SINGLETON
    if force_reload or _SINGLETON is None or _SINGLETON.path != runtime_info_path():
        with _LOCK:
            _SINGLETON = RuntimeInfo()
    return _SINGLETON


__all__ = [
    "RuntimeInfo",
    "ServiceEndpoint",
    "get_runtime_info",
    "runtime_info_path",
]
