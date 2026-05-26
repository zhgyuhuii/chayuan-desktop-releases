"""Structured logging.

Uses structlog for nice console/JSON output and the stdlib RotatingFileHandler
for per-process log files under <CHAYUAN_HOME>/logs/<name>.log.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import structlog

from chayuan_core.paths import ensure_dirs

_CONFIGURED = False


def setup_logging(
    name: str = "chayuan",
    level: str = "INFO",
    json_console: bool = False,
    log_dir: Path | None = None,
) -> structlog.stdlib.BoundLogger:
    global _CONFIGURED
    p = log_dir or ensure_dirs().logs

    root = logging.getLogger()
    if not _CONFIGURED:
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
        for h in list(root.handlers):
            root.removeHandler(h)

        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(sh)

        renderer = (
            structlog.processors.JSONRenderer()
            if json_console
            else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
        )
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                renderer,
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, level.upper(), logging.INFO)
            ),
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
        _CONFIGURED = True

    fh_path = p / f"{name}.log"
    if not any(
        isinstance(h, logging.handlers.RotatingFileHandler) and Path(h.baseFilename) == fh_path
        for h in root.handlers
    ):
        fh = logging.handlers.RotatingFileHandler(
            fh_path, maxBytes=100 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(fh)

    return structlog.get_logger(name)


def get_logger(name: str = "chayuan") -> structlog.stdlib.BoundLogger:
    if not _CONFIGURED:
        setup_logging(name)
    return structlog.get_logger(name)
