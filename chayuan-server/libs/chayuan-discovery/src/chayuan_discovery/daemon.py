"""Top-level discovery daemon.

Wraps a ModelTreeWatcher (event-driven) plus a periodic poller (safety net).
Run as `python -m chayuan_discovery` or import & start.
"""
from __future__ import annotations

import threading
import time

from chayuan_core import get_logger, load_config
from chayuan_discovery.poller import poll_once
from chayuan_discovery.watcher import ModelTreeWatcher

logger = get_logger("chayuan_discovery.daemon")


class DiscoveryDaemon:
    def __init__(self, poll_interval_sec: int | None = None) -> None:
        cfg = load_config()
        self.poll_interval = poll_interval_sec or cfg.discovery.poll_interval_sec
        self.watcher = ModelTreeWatcher(debounce_ms=cfg.discovery.debounce_ms)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.watcher.start()
        self._stop.clear()
        # immediate first sweep
        try:
            poll_once()
        except Exception as e:
            logger.warning("daemon.bootstrap_poll_error", err=str(e))
        self._thread = threading.Thread(target=self._loop, name="chayuan-discovery-poller", daemon=True)
        self._thread.start()
        logger.info("daemon.started", interval_sec=self.poll_interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.watcher.stop()
        logger.info("daemon.stopped")

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self.poll_interval)
            if self._stop.is_set():
                return
            try:
                poll_once()
            except Exception as e:
                logger.warning("daemon.poll_error", err=str(e))


def run_once() -> dict:
    """One-shot sync — used by CLI/tests."""
    return poll_once()


def main() -> None:
    daemon = DiscoveryDaemon()
    daemon.start()
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:  # pragma: no cover
        daemon.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
