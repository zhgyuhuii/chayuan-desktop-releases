"""Layered configuration: defaults < yaml < env vars.

Search order for the yaml file:
    1. $CHAYUAN_CONFIG (if set, must exist)
    2. <CHAYUAN_HOME>/config/default.yaml
    3. <workspace>/config/default.yaml (devmode)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from chayuan_core.paths import CHAYUAN_HOME, get_paths


class GatewayConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 38080
    api_keys: list[str] = Field(default_factory=lambda: ["sk-chayuan-dev"])
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    rate_limit_qpm: int = 600


class DiscoveryConfig(BaseModel):
    poll_interval_sec: int = 60
    debounce_ms: int = 500
    enabled: bool = True


class RegistryConfig(BaseModel):
    url: str = ""  # populated lazily to <CHAYUAN_HOME>/data/registry.sqlite
    echo: bool = False


class SupervisorConfig(BaseModel):
    spec_path: str = ""  # populated to config/supervisor.yaml
    port_range: tuple[int, int] = (16000, 65000)
    max_restarts: int = 5
    restart_backoff_base_sec: float = 1.0


class MirrorsConfig(BaseModel):
    primary: str = "https://hf-mirror.com"
    fallback: list[str] = Field(default_factory=lambda: ["https://huggingface.co"])
    modelscope: str = "https://www.modelscope.cn"


class AppConfig(BaseModel):
    home: str = ""
    log_level: str = "INFO"
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    registry: RegistryConfig = Field(default_factory=RegistryConfig)
    supervisor: SupervisorConfig = Field(default_factory=SupervisorConfig)
    mirrors: MirrorsConfig = Field(default_factory=MirrorsConfig)


_ENV_PREFIX = "CHAYUAN_"


def _coerce(value: str) -> Any:
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _model_fields(model_cls) -> dict[str, type]:
    return {name: f.annotation for name, f in model_cls.model_fields.items()}


def _try_set(target: dict, model_cls, key_lower: str, raw: str) -> bool:
    """Walk the pydantic model tree and assign target[...] = raw if possible.

    Greedy longest-prefix match handles flat fields like `poll_interval_sec`.
    """
    fields = _model_fields(model_cls)
    for name in sorted(fields, key=len, reverse=True):
        ann = fields[name]
        # nested pydantic model section?
        if hasattr(ann, "model_fields"):
            section_prefix = name + "_"
            if key_lower == name or key_lower.startswith(section_prefix):
                rest = key_lower[len(section_prefix):] if key_lower != name else ""
                section = target.setdefault(name, {})
                if not isinstance(section, dict):
                    continue
                if rest and _try_set(section, ann, rest, raw):
                    return True
        elif key_lower == name:
            ann_str = str(ann)
            if "list" in ann_str or "List" in ann_str or "tuple" in ann_str:
                # comma-separated list parsing for env-overrides
                items = [x.strip() for x in raw.split(",") if x.strip()]
                target[name] = items
            else:
                target[name] = _coerce(raw)
            return True
    return False


def _apply_env_overrides(d: dict) -> dict:
    """Apply CHAYUAN_* env overrides, e.g.

        CHAYUAN_GATEWAY_PORT=12345                -> gateway.port = 12345
        CHAYUAN_DISCOVERY_POLL_INTERVAL_SEC=30    -> discovery.poll_interval_sec = 30
        CHAYUAN_LOG_LEVEL=DEBUG                   -> log_level = "DEBUG"
    """
    for key, raw in os.environ.items():
        if not key.startswith(_ENV_PREFIX):
            continue
        rest = key[len(_ENV_PREFIX):].lower()
        _try_set(d, AppConfig, rest, raw)
    return d


def _resolve_yaml_path() -> Path | None:
    if env := os.environ.get("CHAYUAN_CONFIG"):
        p = Path(env).expanduser()
        return p if p.is_file() else None
    candidates = [
        get_paths().config / "default.yaml",
        CHAYUAN_HOME / "config" / "default.yaml",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def load_config(yaml_path: Path | None = None) -> AppConfig:
    raw: dict[str, Any] = {}
    p = yaml_path or _resolve_yaml_path()
    if p and p.is_file():
        loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            raw = loaded
    raw = _apply_env_overrides(raw)
    raw.setdefault("home", str(CHAYUAN_HOME))
    cfg = AppConfig.model_validate(raw)
    if not cfg.registry.url:
        cfg.registry.url = f"sqlite:///{(get_paths().data / 'registry.sqlite').as_posix()}"
    if not cfg.supervisor.spec_path:
        cfg.supervisor.spec_path = (get_paths().config / "supervisor.yaml").as_posix()
    return cfg
