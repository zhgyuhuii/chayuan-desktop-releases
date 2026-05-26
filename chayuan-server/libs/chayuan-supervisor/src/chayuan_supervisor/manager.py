"""Top-level supervisor manager: declarative yaml -> ManagedProcess graph.

New in this revision (port stability + auto credentials):
  * `preferred_port: int`   the well-known uncommon port we'd LIKE to use
  * `port_aliases:`         additional named placeholders (e.g. MinIO console)
  * `credentials:`          spec for auto-generated user/password env vars
  * `expose:`               metadata (kind/scheme/...) used to format a URL
                            and persisted to <CHAYUAN_HOME>/data/runtime.json

The resulting connection info (final port, address, credentials) is
displayed by `chayuan service info` and by `service start --foreground`.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from chayuan_core import ensure_dirs, get_logger, load_config
# 通过 runtime_adapter 让 supervisor 与 chayuan-server 共用一份 runtime.json：
#   - chayuan-server 装上时（合并部署） → 写到 <CHAYUAN_ROOT>/runtime.json
#   - 仅有 supervisor（standalone） → 落 <CHAYUAN_HOME>/data/runtime.json
# 这样前端 AiPlatformPanel 看到的端口/凭据 == supervisor 真正占的。
from chayuan_supervisor.runtime_adapter import (
    ensure_credentials_unified as ensure_credentials,
    get_runtime_info_unified as get_runtime_info,
    make_unified_port_allocator,
)
# 让推理引擎(llamacpp / infinity / ollama)启动时按 chayuan-server 解析的本地模型
# 自动追加 --model 等 args/env;详见 process_args_adapter.py。
from chayuan_supervisor.process_args_adapter import resolve_extra_for
from chayuan_supervisor.health import HealthProbe
from chayuan_supervisor.process import ManagedProcess
from chayuan_supervisor.restart_policy import RestartPolicy

logger = get_logger("chayuan_supervisor.manager")


@dataclass
class ProcessSpec:
    name: str
    binary: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    init: list[list[str]] = field(default_factory=list)
    health: str | None = None
    depends_on: list[str] = field(default_factory=list)
    port: str | None = None
    preferred_port: int | None = None
    port_aliases: dict[str, int] = field(default_factory=dict)
    credentials: dict[str, str] = field(default_factory=dict)
    expose: dict[str, Any] = field(default_factory=dict)
    optional: bool = False


def _interp(s: str, env: dict[str, str]) -> str:
    """Replace ${VAR} with env[VAR] (fallback to os env), leave unknowns alone."""
    def _sub(m: re.Match) -> str:
        key = m.group(1)
        return env.get(key) or os.environ.get(key) or m.group(0)
    return re.sub(r"\$\{([A-Z0-9_]+)\}", _sub, s)


def load_spec(yaml_path: Path | None = None) -> list[ProcessSpec]:
    if yaml_path is None:
        cfg = load_config()
        p = Path(cfg.supervisor.spec_path)
    else:
        p = yaml_path
    if not p.is_file():
        logger.warning("manager.no_spec", path=str(p))
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out: list[ProcessSpec] = []
    for entry in raw.get("processes", []):
        out.append(ProcessSpec(
            name=entry["name"],
            binary=entry["binary"],
            args=[str(a) for a in entry.get("args", [])],
            env={str(k): str(v) for k, v in (entry.get("env") or {}).items()},
            cwd=entry.get("cwd"),
            init=[list(c) for c in (entry.get("init") or [])],
            health=entry.get("health"),
            depends_on=list(entry.get("depends_on") or []),
            port=entry.get("port"),
            preferred_port=entry.get("preferred_port"),
            port_aliases={k: int(v) for k, v in (entry.get("port_aliases") or {}).items()},
            credentials=dict(entry.get("credentials") or {}),
            expose=dict(entry.get("expose") or {}),
            optional=bool(entry.get("optional", False)),
        ))
    return out


def topo_sort(specs: list[ProcessSpec]) -> list[ProcessSpec]:
    incoming = defaultdict(set)
    by_name = {s.name: s for s in specs}
    for s in specs:
        for dep in s.depends_on:
            if dep in by_name:
                incoming[s.name].add(dep)
    out: list[ProcessSpec] = []
    visited: set[str] = set()
    def _visit(name: str, stack: set[str]) -> None:
        if name in visited:
            return
        if name in stack:
            raise RuntimeError(f"dependency cycle through {name}")
        stack.add(name)
        for dep in incoming[name]:
            _visit(dep, stack)
        stack.discard(name)
        visited.add(name)
        out.append(by_name[name])
    for s in specs:
        _visit(s.name, set())
    return out


def _format_url(kind: str, host: str, port: int, *,
                user: str = "", password: str = "",
                extra: dict | None = None) -> str:
    extra = extra or {}
    if kind == "postgres":
        db = extra.get("database", "chayuan")
        if user and password:
            return f"postgresql://{user}:{password}@{host}:{port}/{db}"
        return f"postgresql://{host}:{port}/{db}"
    if kind == "redis":
        if password:
            return f"redis://:{password}@{host}:{port}/0"
        return f"redis://{host}:{port}/0"
    if kind == "milvus":
        return f"{host}:{port}"
    if kind == "minio" or kind == "http":
        return f"http://{host}:{port}"
    if kind == "ollama":
        return f"http://{host}:{port}"
    return f"{kind}://{host}:{port}"


class SupervisorManager:
    def __init__(self, specs: list[ProcessSpec] | None = None) -> None:
        cfg = load_config()
        # PortAllocator 同样走 unified 路径——chayuan-server 在场时它会自动 reserve
        # runtime.json 中已存在的端口，避免新分配抢占。
        low, high = cfg.supervisor.port_range
        self.allocator = make_unified_port_allocator(low, high)
        self.policy = RestartPolicy(
            max_restarts=cfg.supervisor.max_restarts,
            base_sec=cfg.supervisor.restart_backoff_base_sec,
        )
        self.specs = specs or load_spec()
        self._processes: dict[str, ManagedProcess] = {}
        self._port_env: dict[str, str] = {}
        self._endpoints: dict[str, dict] = {}
        self._credentials: dict[str, dict] = {}

    # ---- public --------------------------------------------------------

    def plan(self) -> list[ManagedProcess]:
        ensure_dirs()
        info = get_runtime_info()
        ordered = topo_sort(self.specs)

        # Step 1: resolve ports.
        # If runtime.json already has a "preferred" port persisted from a previous
        # successful start, try that first so external clients keep working.
        for s in ordered:
            if s.port:
                persisted = info.endpoint(s.name).get("port")
                preferred = persisted or s.preferred_port
                p = self.allocator.allocate(preferred=preferred)
                self._port_env[s.port] = str(p)
            for alias_var, alias_pref in s.port_aliases.items():
                persisted_alias = info.endpoint(s.name).get(alias_var.lower())
                pref = persisted_alias or alias_pref
                p = self.allocator.allocate(preferred=pref)
                self._port_env[alias_var] = str(p)

        # Step 2: resolve credentials.
        for s in ordered:
            cred_env, cred_record = ensure_credentials(s.name, s.credentials)
            self._credentials[s.name] = cred_record
            for k, v in cred_env.items():
                self._port_env[k] = v  # reuse the global env table for interpolation

        # Step 2.5: ask chayuan-server (if present) which dynamic args/env to
        # append per process — typically --model paths for llamacpp / infinity
        # resolved against local_index. standalone supervisor returns ([], {}).
        extra_by_name: dict[str, tuple[list[str], dict[str, str]]] = {}
        for s in ordered:
            ea, ee = resolve_extra_for(s.name)
            if ea or ee:
                extra_by_name[s.name] = (ea, ee)

        # Step 3: build ManagedProcess objects.
        for s in ordered:
            interp_env = {**self._port_env, **s.env}
            args = [_interp(a, interp_env) for a in s.args]
            env = {k: _interp(v, interp_env) for k, v in s.env.items()}
            for k, v in self._port_env.items():
                env.setdefault(k, v)
            # Append any dynamically resolved model args/env (Step 2.5).
            # yaml-defined args/env wins on conflict — env.setdefault preserves
            # the original; args are simply appended at the tail (CLI parsers
            # treat the last occurrence as authoritative anyway).
            if s.name in extra_by_name:
                ea, ee = extra_by_name[s.name]
                args.extend(ea)
                for k, v in ee.items():
                    env.setdefault(k, v)
            init_cmds = [[_interp(c, interp_env) for c in cmd] for cmd in s.init]
            health = HealthProbe(_interp(s.health, interp_env)) if s.health else None
            mp = ManagedProcess(
                name=s.name,
                binary=_interp(s.binary, interp_env),
                args=args,
                env=env,
                cwd=s.cwd,
                init_cmds=init_cmds,
                health=health,
                restart_policy=self.policy,
            )
            self._processes[s.name] = mp

        # Step 4: persist endpoints + format URLs.
        for s in ordered:
            if not s.port and not s.expose:
                continue
            host = s.expose.get("host", "127.0.0.1")
            port = int(self._port_env.get(s.port, "0")) if s.port else 0
            kind = s.expose.get("kind") or s.name
            cred = self._credentials.get(s.name, {}) if not s.credentials.get("no_auth") else {}
            url = _format_url(
                kind, host, port,
                user=cred.get("user", ""), password=cred.get("password", ""),
                extra=s.expose,
            ) if port else ""
            ep: dict[str, Any] = {"host": host, "port": port, "scheme": s.expose.get("scheme") or kind, "url": url}
            if cred:
                ep["user"] = cred.get("user", "")
                ep["password"] = cred.get("password", "")
            for alias_var in s.port_aliases:
                ep[alias_var.lower()] = int(self._port_env.get(alias_var, "0"))
            for k, v in s.expose.items():
                if k not in ep:
                    ep[k] = v
            info.set_endpoint(s.name, **ep)
            self._endpoints[s.name] = ep

        return list(self._processes.values())

    def up(self, dry_run: bool = False, only: Iterable[str] | None = None) -> None:
        if not self._processes:
            self.plan()
        names = set(only) if only else None
        for name, p in self._processes.items():
            if names is not None and name not in names:
                continue
            p.start(dry_run=dry_run)

    def down(self) -> None:
        for p in reversed(list(self._processes.values())):
            p.stop()

    def status(self) -> list[dict]:
        return [p.status() for p in self._processes.values()]

    def logs(self, name: str, tail: int = 100) -> list[str]:
        if name not in self._processes:
            return []
        return list(self._processes[name].log_tail)[-tail:]

    def graph(self) -> dict[str, list[str]]:
        return {s.name: list(s.depends_on) for s in self.specs}

    def ports(self) -> dict[str, str]:
        return dict(self._port_env)

    def endpoints(self) -> dict[str, dict]:
        if not self._endpoints:
            self.plan()
        return dict(self._endpoints)

    def credentials_summary(self) -> dict[str, dict]:
        return dict(self._credentials)
