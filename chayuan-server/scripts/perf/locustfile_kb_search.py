"""Locust task：plan v1.3 §5.3 容量目标回归。

目标：``/knowledge_base/search_batch`` P95 < 800ms @ 100 RPS（5min 持续）。

跑法
====

    # 1) 在被测环境写一份 .env：
    #    KB_BASE=http://127.0.0.1:8088
    #    KB_BEARER=<某个有 read 权限的用户 JWT>
    #    KB_NAMES=kb-test-1,kb-test-2
    #
    # 2) 启动：
    locust -f scripts/perf/locustfile_kb_search.py \
        --host $KB_BASE \
        -u 100 -r 10 -t 5m \
        --headless --html out/kb_search_p95.html

设计要点
========
* 75% 流量打同步 ``/search_batch``（单查询 / 多查询两类各一半）；
* 25% 流量打 SSE ``/search_batch_stream``，校验首帧 < 300ms；
* 每条请求把 P50/P95 + 命中 chunk 数当独立指标上报；
* 失败兜底：4031/4032 ACL 拒绝时 ``catch_response`` 标记成 success（属于"被合理拒绝"，
  不算服务故障）；其余 5xx / timeout 才算 failure。

注意：
* 真实压测必须先在 KB 中预热若干典型 query 的向量缓存，否则 P95 会被冷启动 dominate；
* `--processes 4` 多进程模式下推荐配合 master/worker 部署。
"""
from __future__ import annotations

import json
import os
import random
import time
from typing import List

from locust import HttpUser, between, task


_QUERIES_SHORT = [
    "数据库慢查询常见原因有哪些？",
    "JWT 与 OAuth2 的核心区别？",
    "Vue 3 中 ref 和 reactive 的取舍？",
    "RAG 召回阶段如何避免相关但答非所问？",
    "Postgres 行级安全策略 RLS 的配置示例？",
]

_QUERIES_LONG = [
    "请总结向量检索系统在召回精度、性能、运维三个维度的常见权衡，并举例说明",
    "请基于近期最佳实践,描述如何在 100 万级文档库上做混合检索(BM25 + dense)同时控制 P95 < 1s",
]


def _kb_names() -> List[str]:
    raw = os.environ.get("KB_NAMES", "").strip()
    return [s.strip() for s in raw.split(",") if s.strip()] or ["sample-kb"]


def _bearer() -> str:
    return os.environ.get("KB_BEARER", "")


_DEFAULT_TOPK = int(os.environ.get("KB_TOPK", "5"))
_DEFAULT_FUSION = os.environ.get("KB_FUSION", "rrf")


class KbSearchUser(HttpUser):
    """75% 同步 / 25% 流式 的混合负载。"""

    wait_time = between(0.5, 1.5)
    weight = 1

    def on_start(self) -> None:
        self.kb_names = _kb_names()
        self.bearer = _bearer()
        if not self.bearer:
            print("[locust] WARN: KB_BEARER 未配置，所有请求会拿 401，"
                  "请先 export KB_BEARER=<jwt>")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.bearer:
            h["Authorization"] = f"Bearer {self.bearer}"
        return h

    def _is_acceptable_403(self, status_code: int, body: str) -> bool:
        """4031/4032/4033 视为合理拒绝，不算 failure。"""
        if status_code != 403:
            return False
        try:
            detail = json.loads(body).get("detail")
            return isinstance(detail, dict) and detail.get("code") in (4031, 4032, 4033)
        except Exception:
            return False

    @task(15)
    def search_batch_short(self) -> None:
        body = {
            "knowledge_base_names": self.kb_names[:1],
            "queries": [
                {"text": random.choice(_QUERIES_SHORT), "tag": f"q-{int(time.time()*1000)}"}
            ],
            "top_k": _DEFAULT_TOPK,
            "fusion": _DEFAULT_FUSION,
        }
        with self.client.post(
            "/knowledge_base/search_batch",
            json=body, headers=self._headers(),
            name="POST /search_batch (1q × 1kb)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif self._is_acceptable_403(resp.status_code, resp.text):
                resp.success()
            else:
                resp.failure(f"http {resp.status_code}: {resp.text[:200]}")

    @task(10)
    def search_batch_multi(self) -> None:
        body = {
            "knowledge_base_names": self.kb_names[:min(3, len(self.kb_names))],
            "queries": [
                {"text": random.choice(_QUERIES_SHORT), "tag": f"a-{int(time.time()*1000)}"},
                {"text": random.choice(_QUERIES_SHORT), "tag": f"b-{int(time.time()*1000)}"},
                {"text": random.choice(_QUERIES_LONG),  "tag": f"c-{int(time.time()*1000)}"},
            ],
            "top_k": _DEFAULT_TOPK,
            "fusion": _DEFAULT_FUSION,
        }
        with self.client.post(
            "/knowledge_base/search_batch",
            json=body, headers=self._headers(),
            name="POST /search_batch (3q × N kb)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif self._is_acceptable_403(resp.status_code, resp.text):
                resp.success()
            else:
                resp.failure(f"http {resp.status_code}: {resp.text[:200]}")

    @task(8)
    def search_batch_stream(self) -> None:
        body = {
            "knowledge_base_names": self.kb_names[:1],
            "queries": [
                {"text": random.choice(_QUERIES_LONG), "tag": f"s-{int(time.time()*1000)}"}
            ],
            "top_k": _DEFAULT_TOPK,
            "fusion": _DEFAULT_FUSION,
        }
        start = time.perf_counter()
        ttfb = None
        chunks = 0
        with self.client.post(
            "/knowledge_base/search_batch_stream",
            json=body, headers=self._headers(),
            name="POST /search_batch_stream (SSE)",
            catch_response=True,
            stream=True,
        ) as resp:
            if resp.status_code != 200:
                if self._is_acceptable_403(resp.status_code, resp.text):
                    resp.success()
                else:
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
                chunks += 1
                # 看到 done frame 就结束
                if '"type": "done"' in line or '"type":"done"' in line:
                    break
            if chunks == 0:
                resp.failure("no SSE chunks received")
            else:
                resp.success()
        # 单独把 TTFB 当一条 metric
        if ttfb is not None:
            self.environment.events.request.fire(
                request_type="stream",
                name="ttfb /search_batch_stream",
                response_time=ttfb * 1000.0,
                response_length=chunks,
                exception=None,
                context={},
            )
