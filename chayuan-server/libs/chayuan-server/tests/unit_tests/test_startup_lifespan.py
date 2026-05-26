"""回归 chayuan/startup.py:_set_app_event 必须真的 fire on_event hook。

历史 bug:`_set_app_event` 把 `app.router.lifespan_context = lifespan` 直接覆盖,
导致 server_app.py 里的 7 个 `@app.on_event("startup")` 全部被静默丢弃
(db 迁移、模型首启 seed、auto-start cap、auto-start rapidocr ...)
→ 装完客户端后本地服务不自启、bundled_models 不 seed。

这个测试保证以后哪怕重写 `_set_app_event`,只要它接收的 app 上有 on_event hook,
它就必须在 lifespan 进入时跑完 startup hook、退出时跑完 shutdown hook。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI

from chayuan.startup import _set_app_event


@pytest.mark.asyncio
async def test_set_app_event_fires_on_startup_and_on_shutdown_hooks():
    app = FastAPI()
    log: list[str] = []

    @app.on_event("startup")
    def _sync_startup():
        log.append("sync_startup")

    @app.on_event("startup")
    async def _async_startup():
        await asyncio.sleep(0)
        log.append("async_startup")

    @app.on_event("shutdown")
    async def _async_shutdown():
        log.append("async_shutdown")

    _set_app_event(app, started_event=None)

    lifespan_ctx = app.router.lifespan_context
    async with lifespan_ctx(app):
        # 进入 lifespan 后,两个 startup hook 都应该已经跑过
        assert log == ["sync_startup", "async_startup"], (
            f"_set_app_event 的 lifespan 必须 await on_startup;实际 log={log!r}"
        )

    # 退出 lifespan 后,shutdown hook 也应该跑了
    assert "async_shutdown" in log, (
        f"_set_app_event 的 lifespan 必须 await on_shutdown;实际 log={log!r}"
    )


@pytest.mark.asyncio
async def test_set_app_event_isolates_hook_failures():
    """单个 hook 抛异常不能阻塞其它 hook;否则一个 hook 挂了全链路都死。"""
    app = FastAPI()
    log: list[str] = []

    @app.on_event("startup")
    def _hook_a():
        log.append("a")

    @app.on_event("startup")
    def _hook_b():
        log.append("b_before_raise")
        raise RuntimeError("boom from b")

    @app.on_event("startup")
    def _hook_c():
        log.append("c")

    _set_app_event(app, started_event=None)
    lifespan_ctx = app.router.lifespan_context
    async with lifespan_ctx(app):
        pass

    assert log == ["a", "b_before_raise", "c"], (
        f"hook 失败应被吞掉、不影响后续 hook;实际 log={log!r}"
    )
