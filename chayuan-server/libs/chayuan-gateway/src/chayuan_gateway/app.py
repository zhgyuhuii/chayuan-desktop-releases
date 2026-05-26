"""FastAPI app factory."""
from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from chayuan_core import load_config, setup_logging
from chayuan_core.events import get_bus
from chayuan_gateway.middlewares.auth import ApiKeyMiddleware
from chayuan_gateway.middlewares.ratelimit import RateLimitMiddleware
from chayuan_gateway.routers import (
    admin,
    audio,
    chat,
    embeddings,
    images,
    models,
    ocr,
    rerank,
    system,
    videos,
)
from chayuan_registry import init_engine


def create_app() -> FastAPI:
    cfg = load_config()
    setup_logging("chayuan_gateway", level=cfg.log_level)
    init_engine()
    app = FastAPI(title="Chayuan Gateway", version="0.1.0", description="OpenAI-compatible multimodal gateway")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.gateway.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RateLimitMiddleware, qpm=cfg.gateway.rate_limit_qpm)
    app.add_middleware(ApiKeyMiddleware)
    for r in (system.router, models.router, chat.router, embeddings.router,
              rerank.router, audio.router, images.router, videos.router,
              ocr.router, admin.router):
        app.include_router(r)

    @app.on_event("startup")
    def _starting() -> None:
        get_bus()  # warm singletons

    return app


app = create_app()  # uvicorn entry point convenience
