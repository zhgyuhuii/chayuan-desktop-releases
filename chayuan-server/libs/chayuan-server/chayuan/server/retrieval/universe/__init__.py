"""Typed Knowledge Universe retrieval service."""

from .service import (
    block_to_chunks,
    result_to_chunk,
    search_ku_blocks,
    search_ku_chunks,
)

__all__ = [
    "block_to_chunks",
    "result_to_chunk",
    "search_ku_blocks",
    "search_ku_chunks",
]
