"""Port allocation with stable-preferred semantics.

The allocator owns the range `[low, high]` (default 16000–65000). Two modes:

  * `allocate()` — pick any free port in range (random-ish: linear search).
  * `allocate(preferred=N)` — try N first; if occupied, try N+1, N+2, ... up to
    `high`; then wrap to `low` and search up to N-1. The first hit wins and is
    remembered as "used" for the rest of the supervisor lifetime.

The "preferred" path is what gives a service a **stable, uncommon** default
port that survives across restarts (because it's persisted by the caller via
`RuntimeInfo`) but **automatically bumps** when the slot is taken by something
else (rogue process, conflicting install, etc).
"""
from __future__ import annotations

import socket
import threading
from typing import Iterable


class PortAllocator:
    def __init__(self, low: int = 16000, high: int = 65000) -> None:
        if low < 1024 or high > 65535 or low >= high:
            raise ValueError(f"invalid port range [{low}, {high}]")
        self.low = low
        self.high = high
        self._used: set[int] = set()
        self._lock = threading.Lock()

    def reserve(self, p: int) -> None:
        with self._lock:
            self._used.add(p)

    def release(self, p: int) -> None:
        with self._lock:
            self._used.discard(p)

    def used(self) -> Iterable[int]:
        return tuple(sorted(self._used))

    # ---- internals -----------------------------------------------------

    def _bind_check(self, p: int) -> bool:
        if p in self._used:
            return False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
                s.bind(("127.0.0.1", p))
                return True
        except OSError:
            return False

    def _try_range(self, start: int, stop: int) -> int | None:
        for p in range(start, stop + 1):
            if self._bind_check(p):
                self._used.add(p)
                return p
        return None

    # ---- public --------------------------------------------------------

    def allocate(self, preferred: int | None = None) -> int:
        with self._lock:
            if preferred is not None:
                pref = max(self.low, min(self.high, preferred))
                # Search forward from preferred → high, then wrap low → pref-1
                got = self._try_range(pref, self.high)
                if got is not None:
                    return got
                got = self._try_range(self.low, pref - 1)
                if got is not None:
                    return got
                raise RuntimeError(
                    f"no free port found starting at preferred={preferred} "
                    f"within [{self.low}, {self.high}]"
                )
            got = self._try_range(self.low, self.high)
            if got is not None:
                return got
            raise RuntimeError(f"no free port in [{self.low}, {self.high}]")

    def allocate_many(self, count: int) -> list[int]:
        return [self.allocate() for _ in range(count)]
