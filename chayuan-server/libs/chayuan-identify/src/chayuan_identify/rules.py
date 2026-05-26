"""Hot-reloadable rule set.

A rule file (YAML) lets ops add new model families without releasing a new
chayuan-identify wheel. The file is re-read whenever its mtime changes.

Schema (YAML):

    rules:
      - name: my-special-llm
        category: chat
        runtime: vllm
        format: safetensors
        files: ["mymarker.json"]            # any of these files present
        file_suffix: [".safetensors"]       # optional
        config_arch: ["MyArchForCausalLM"]  # optional
        pipeline_tag: ["text-generation"]   # optional
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from chayuan_core import get_paths
from chayuan_core.events import TOPIC_RULE_RELOADED, get_bus
from chayuan_identify.signatures import BUILTIN_SIGNATURES, Signature


@dataclass
class RuleSet:
    signatures: tuple[Signature, ...]
    source: Path | None = None

    def all(self) -> tuple[Signature, ...]:
        return self.signatures


def _load_yaml(path: Path) -> tuple[Signature, ...]:
    if not path.is_file():
        return ()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[Signature] = []
    for r in raw.get("rules", []):
        out.append(
            Signature(
                name=r.get("name", "user-rule"),
                category=r["category"],
                runtime=r.get("runtime", "auto"),
                format=r.get("format", "unknown"),
                files=tuple(s.lower() for s in r.get("files", [])),
                file_suffix=tuple(s.lower() for s in r.get("file_suffix", [])),
                config_arch=tuple(r.get("config_arch", [])),
                pipeline_tag=tuple(s.lower() for s in r.get("pipeline_tag", [])),
            )
        )
    return tuple(out)


_LOCK = threading.RLock()
_CACHE: dict[str, tuple[float, RuleSet]] = {}


def get_default_ruleset() -> RuleSet:
    if env := os.environ.get("CHAYUAN_RULES_FILE"):
        return load_ruleset(Path(env))
    p = get_paths().config / "model_rules.yaml"
    return load_ruleset(p)


def load_ruleset(path: Path) -> RuleSet:
    with _LOCK:
        mtime = path.stat().st_mtime if path.is_file() else 0.0
        cached = _CACHE.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1]
        user = _load_yaml(path) if path.is_file() else ()
        merged = (*user, *BUILTIN_SIGNATURES)
        rs = RuleSet(signatures=merged, source=path if path.is_file() else None)
        _CACHE[str(path)] = (mtime, rs)
        if cached and cached[0] != mtime:
            get_bus().publish(TOPIC_RULE_RELOADED, {"file": str(path), "count": len(rs.signatures)})
        return rs


def watch_changes(interval_sec: float = 60.0, stop_event: threading.Event | None = None) -> None:
    """Background poller. Discovery service uses this to trigger re-identification."""
    stop = stop_event or threading.Event()
    while not stop.is_set():
        try:
            get_default_ruleset()
        except Exception:
            pass
        stop.wait(interval_sec)


def _t() -> float:
    return time.time()
