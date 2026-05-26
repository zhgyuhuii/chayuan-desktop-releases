#!/usr/bin/env python3
"""为 ``perf-locust.yml`` 拉起一个真 chayuan-gateway 进程。

* 复用 ``smoke_ollama_chat.py`` 的注入策略：内存 ``ModelRepository`` +
  真 ``OllamaAdapter`` 指向本地 ollama；
* 通过 uvicorn 暴露在 ``127.0.0.1:8088``；
* locust 的请求都从这里走。

为什么不直接 ``uvicorn chayuan_gateway.app:app``？
* gateway 默认启动后 ``ModelRepository`` 是空的，``/v1/chat/completions`` 拿不到
  注册的 model。我们这里在启动前注入一条 ``qwen2:0.5b``，让 locust 第一秒就能打。
"""
from __future__ import annotations

import os

import uvicorn

DEFAULT_MODEL = os.environ.get("CHAYUAN_PERF_MODEL", "qwen2:0.5b")
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")


def _bootstrap() -> None:
    """启动前把 fixture 模型 + adapter 注入到 gateway。"""
    from chayuan_gateway.deps import get_repo
    from chayuan_registry import ModelRepository
    from chayuan_runtime.adapters.ollama_adapter import OllamaAdapter
    from chayuan_runtime.registry import default_registry

    # repo 注入
    repo: ModelRepository = get_repo()
    if repo.get(DEFAULT_MODEL) is None:
        repo.register({
            "id": DEFAULT_MODEL,
            "category": "chat",
            "runtime": "ollama",
            "format": "gguf",
            "is_default": True,
        })

    # 用真 ollama 地址替换 mock adapter
    real = OllamaAdapter(base_url=OLLAMA_URL, mock=False)
    default_registry().register(real, replace=True)


def main() -> None:
    _bootstrap()
    uvicorn.run(
        "chayuan_gateway.app:app",
        host="127.0.0.1",
        port=int(os.environ.get("CHAYUAN_PERF_PORT", "8088")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
