"""Exponential-backoff restart policy with hard ceiling."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RestartPolicy:
    max_restarts: int = 5
    base_sec: float = 1.0
    cap_sec: float = 60.0
    silence_after_max: bool = True

    def delay_for(self, attempt: int) -> float:
        if attempt <= 0:
            return 0.0
        return min(self.cap_sec, self.base_sec * (2 ** (attempt - 1)))

    def should_restart(self, attempt: int) -> bool:
        return attempt < self.max_restarts
