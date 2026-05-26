"""Unified knowledge query engine.

This package keeps retrieval orchestration out of API route modules so legacy
routes and new `/api/v1/kb-query/*` endpoints can share one execution path.
"""

from chayuan.server.retrieval.query.orchestrator import search

__all__ = ["search"]
