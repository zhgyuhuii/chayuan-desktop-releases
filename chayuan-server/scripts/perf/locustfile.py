"""Locust task：流式 chat baseline。

跑法
====

    locust -f scripts/perf/locustfile.py --host http://127.0.0.1:8088

配套见 ``.github/workflows/perf-locust.yml``。

* 默认 100% 流量打 chat；
* 走 OpenAI 兼容流式（``stream=True``，按 ``data:`` 行解析）；
* 每条请求记录 token-time 和 first-byte-time，方便和 ``95%`` 对照。

"""
from __future__ import annotations

import json
import time

from locust import HttpUser, between, task


_PROMPT = "用一句话告诉我 4B 量化语言模型在 CPU 上常见的瓶颈。"


class ChatUser(HttpUser):
    wait_time = between(0.5, 2.0)

    @task
    def chat_stream(self) -> None:
        body = {
            "model": "qwen2:0.5b",
            "messages": [{"role": "user", "content": _PROMPT}],
            "stream": True,
            "max_tokens": 64,
        }
        start = time.perf_counter()
        ttfb = None
        chunks = 0
        with self.client.post(
            "/v1/chat/completions",
            json=body,
            headers={"Authorization": "Bearer sk-chayuan-dev"},
            name="POST /v1/chat/completions (stream)",
            catch_response=True,
            stream=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"http {resp.status_code}: {resp.text[:200]}")
                return
            for raw in resp.iter_lines():
                if not raw:
                    continue
                line = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
                if not line.startswith("data:"):
                    continue
                if ttfb is None:
                    ttfb = time.perf_counter() - start
                if line.strip() == "data: [DONE]":
                    break
                try:
                    json.loads(line[len("data:"):].strip())
                    chunks += 1
                except Exception:
                    pass
            if chunks == 0:
                resp.failure("no SSE chunks received")
            else:
                resp.success()
        # 把 ttfb 当一条独立 metric 记进去，方便看
        if ttfb is not None:
            self.environment.events.request.fire(
                request_type="stream",
                name="ttfb /v1/chat/completions",
                response_time=ttfb * 1000.0,
                response_length=chunks,
                exception=None,
                context={},
            )
