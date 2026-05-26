"""阿里百炼(DashScope)文生视频 Connector — wanx 异步轮询模式。

上游协议(异步任务)
====================

1. 提交任务(含 ``X-DashScope-Async: enable`` 头):
::

    POST {api_base}/api/v1/services/aigc/video-generation/video-synthesis
    Authorization: Bearer <api_key>
    X-DashScope-Async: enable
    Content-Type: application/json
    {
      "model": "wanx2.1-t2v-turbo",
      "input": {"prompt": "..."},
      "parameters": {"size": "1280*720", "duration": 5}
    }

   响应(立即返回 task_id):
::

    {
      "request_id": "...",
      "output": {"task_id": "task-xxx", "task_status": "PENDING"}
    }

2. 轮询任务状态:
::

    GET {api_base}/api/v1/tasks/{task_id}
    Authorization: Bearer <api_key>

   响应(SUCCEEDED):
::

    {
      "request_id": "...",
      "output": {
        "task_id": "...",
        "task_status": "SUCCEEDED",
        "video_url": "https://.../video.mp4",
        "submit_time": "...", "scheduled_time": "...", "end_time": "..."
      },
      "usage": {"video_duration": 5, "video_ratio": "16:9", "video_count": 1}
    }

   响应(FAILED):
::

    {"output": {"task_status": "FAILED", "code": "...", "message": "..."}}

策略
====
- 轮询间隔 5s,上限 10min(超时则 yield error_event)
- 每次轮询 yield ``data-task-progress`` 让前端进度条动起来
- ``task_status`` 状态机:PENDING → RUNNING → SUCCEEDED/FAILED/CANCELED/UNKNOWN
- 用户取消(``req.cancelled``)时立即返回,**不**主动撤销上游任务(DashScope
  无 cancel API);PR-6 引 TaskManager 后用单独的 cancel endpoint 通知后端继续
  消费这个 task_id

SSE 长连接限制
==============
单机 Tauri 直连本地后端,无中间代理,SSE 60s+ 没问题。
生产环境 / Nginx 后续上 TaskManager 才需要"断开重连续播",本期不接。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import AsyncIterator, Dict, Optional

import httpx

from chayuan.server.modality.router.artifacts import save_bytes
from chayuan.server.modality.router.connectors.base import Connector, register
from chayuan.server.modality.router.protocol import Capability, GenerateReq
from chayuan.server.modality.router.sse_v5 import (
    data_part,
    error_event,
    file_part,
    text_delta,
    text_end,
    text_start,
)

logger = logging.getLogger("chayuan.modality.connectors.video_gen.dashscope")


# ──────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────


# wanx t2v 各版本默认尺寸 / 时长
_DEFAULT_SIZE_BY_FAMILY: Dict[str, str] = {
    "wanx2.1-t2v":     "1280*720",
    "wanx-t2v":        "1280*720",
    "wanxiang-t2v":    "1280*720",
}
_DEFAULT_DURATION_S: int = 5         # 大部分版本最长 5s
_POLL_INTERVAL_S: float = 5.0
_MAX_WAIT_S: float = 600.0           # 10 分钟硬上限


def _default_size_for(model: str) -> str:
    low = model.lower()
    for prefix, size in _DEFAULT_SIZE_BY_FAMILY.items():
        if low.startswith(prefix):
            return size
    return "1280*720"


def _normalize_size(raw: Optional[str], model: str) -> str:
    if not raw:
        return _default_size_for(model)
    if "x" in raw and "*" not in raw:
        raw = raw.replace("x", "*")
    if re.fullmatch(r"\d+\*\d+", raw):
        return raw
    return _default_size_for(model)


def _build_endpoints(api_base: str) -> tuple[str, str]:
    """返回 (submit_url, tasks_url_prefix)。

    api_base 通常配 ``compatible-mode/v1``,要还原成 native ``/api/v1``。
    """
    base = (api_base or "").rstrip("/")
    base = re.sub(r"/compatible-mode/v\d+$", "", base)
    base = re.sub(r"/v\d+$", "", base)
    submit = f"{base}/api/v1/services/aigc/video-generation/video-synthesis"
    tasks = f"{base}/api/v1/tasks"
    return submit, tasks


def _estimate_percent(elapsed_s: float, status: str) -> int:
    """根据已耗时 + 任务状态估算进度百分比。

    上游不返实际进度,只能凭经验:
      - PENDING 0-10%
      - RUNNING:elapsed/60 → 25-90%,上限 90 留 10% 给"下载视频"阶段
      - 其它状态返 0
    """
    if status == "PENDING":
        return min(10, int(elapsed_s * 2))
    if status == "RUNNING":
        # 假设平均 60s 出片;90% 上限确保 UI 不会卡 100% 等下载
        return min(90, 25 + int(elapsed_s / 0.7))
    return 0


# ──────────────────────────────────────────────────────────────
# Connector
# ──────────────────────────────────────────────────────────────


@register(Capability.T2V, "dashscope")
class DashScopeT2VConnector(Connector):
    """wanx-* 异步文生视频。"""

    async def generate(self, req: GenerateReq) -> AsyncIterator[Dict]:
        prompt = (req.prompt or "").strip()
        if not prompt:
            yield error_event("文生视频需要输入提示词描述", code="empty_prompt")
            return

        size = _normalize_size(req.params.get("size"), req.model)
        try:
            duration = max(2, min(10, int(req.params.get("duration") or _DEFAULT_DURATION_S)))
        except (TypeError, ValueError):
            duration = _DEFAULT_DURATION_S

        text_id = "t0"
        yield text_start(text_id)
        yield text_delta(text_id, f"视频生成中({size}, {duration}s),约需 30-90 秒,可关闭窗口稍后回来…")
        yield text_end(text_id)
        yield data_part("task-progress", {"percent": 2, "message": "提交任务"})

        submit_url, tasks_url_prefix = _build_endpoints(req.api_base)
        body: Dict = {
            "model": req.model,
            "input": {"prompt": prompt},
            "parameters": {"size": size, "duration": duration},
        }
        seed = req.params.get("seed")
        if seed is not None:
            try:
                body["parameters"]["seed"] = int(seed)
            except (TypeError, ValueError):
                pass
        headers_submit = {
            "Authorization": f"Bearer {req.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        headers_poll = {"Authorization": f"Bearer {req.api_key}"}

        # 1. 提交
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.post(submit_url, json=body, headers=headers_submit)
            except httpx.RequestError as e:
                yield error_event(f"无法连接 DashScope 视频接口: {e}", code="upstream_unreachable")
                return

            if resp.status_code != 200:
                try:
                    err_body = resp.json()
                except ValueError:
                    err_body = {"raw": resp.text[:500]}
                yield error_event(
                    f"DashScope {resp.status_code}: "
                    f"{err_body.get('message') or err_body}",
                    code=f"upstream_{resp.status_code}",
                )
                return

            submit_data = resp.json()
            task_id = (submit_data.get("output") or {}).get("task_id")
            if not task_id:
                yield error_event(
                    f"上游未返回 task_id: {submit_data}",
                    code="no_task_id",
                )
                return

            logger.info("[wanx.t2v] task submitted: id=%s model=%s", task_id, req.model)
            yield data_part(
                "task-progress",
                {"percent": 5, "message": "任务已排队", "task_id": task_id},
            )

            # 2. 轮询 + 下载 — 抽到 _poll_and_download,resume 路径复用
            async for ev in self._poll_and_download(
                client=client,
                req=req,
                upstream_task_id=task_id,
                tasks_url_prefix=tasks_url_prefix,
                headers_poll=headers_poll,
                size=size,
                duration=duration,
                prompt=prompt,
            ):
                yield ev

    # ──────────────────────────────────────────────────────────────
    # PR-9 E:服务重启后接现有 upstream_task_id,跳过 submit。
    # ──────────────────────────────────────────────────────────────
    async def resume(self, req: GenerateReq, upstream_task_id: str) -> AsyncIterator[Dict]:
        size = _normalize_size(req.params.get("size"), req.model)
        try:
            duration = max(2, min(10, int(req.params.get("duration") or _DEFAULT_DURATION_S)))
        except (TypeError, ValueError):
            duration = _DEFAULT_DURATION_S
        prompt = (req.prompt or "").strip()

        _submit_url, tasks_url_prefix = _build_endpoints(req.api_base)
        headers_poll = {"Authorization": f"Bearer {req.api_key}"}

        text_id = "t0"
        yield text_start(text_id)
        yield text_delta(
            text_id,
            f"接回中断的视频任务({upstream_task_id})…",
        )
        yield text_end(text_id)
        yield data_part(
            "task-progress",
            {"percent": 5, "message": "继续轮询", "task_id": upstream_task_id},
        )

        timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async for ev in self._poll_and_download(
                client=client,
                req=req,
                upstream_task_id=upstream_task_id,
                tasks_url_prefix=tasks_url_prefix,
                headers_poll=headers_poll,
                size=size,
                duration=duration,
                prompt=prompt,
            ):
                yield ev

    # ──────────────────────────────────────────────────────────────
    # 轮询 + 下载产物的共享实现 — generate / resume 都走这里
    # ──────────────────────────────────────────────────────────────
    async def _poll_and_download(
        self,
        *,
        client: httpx.AsyncClient,
        req: GenerateReq,
        upstream_task_id: str,
        tasks_url_prefix: str,
        headers_poll: Dict[str, str],
        size: str,
        duration: int,
        prompt: str,
    ) -> AsyncIterator[Dict]:
            task_id = upstream_task_id
            poll_url = f"{tasks_url_prefix}/{task_id}"
            started = time.monotonic()
            last_status = ""
            video_url: Optional[str] = None
            usage_info: Dict = {}
            pdata: Dict = {}

            while True:
                if req.cancelled is not None and req.cancelled.is_set():
                    yield error_event("用户已取消(上游任务可能继续跑完,不退费)", code="cancelled")
                    return
                elapsed = time.monotonic() - started
                if elapsed > _MAX_WAIT_S:
                    yield error_event(
                        f"等待超时({int(elapsed)}s,任务可能仍在跑,task_id={task_id})",
                        code="poll_timeout",
                    )
                    return
                await asyncio.sleep(_POLL_INTERVAL_S)

                try:
                    poll = await client.get(poll_url, headers=headers_poll)
                except httpx.RequestError as e:
                    # 临时网络抖动,不立即放弃,下一轮再试
                    logger.warning("[wanx.t2v] poll transient error: %r", e)
                    continue

                if poll.status_code != 200:
                    yield error_event(
                        f"轮询失败 {poll.status_code}: {poll.text[:200]}",
                        code=f"poll_{poll.status_code}",
                    )
                    return

                pdata = poll.json()
                output = pdata.get("output") or {}
                status = output.get("task_status") or "UNKNOWN"
                if status != last_status:
                    logger.info("[wanx.t2v] task=%s status %s → %s", task_id, last_status, status)
                    last_status = status

                if status in ("PENDING", "RUNNING"):
                    yield data_part(
                        "task-progress",
                        {
                            "percent": _estimate_percent(elapsed, status),
                            "message": "排队中" if status == "PENDING" else "生成中",
                            "task_id": task_id,
                            "eta_s": max(0, int(60 - elapsed)),
                        },
                    )
                    continue

                if status == "SUCCEEDED":
                    video_url = (
                        output.get("video_url")
                        or (output.get("results", [{}])[0].get("url") if isinstance(output.get("results"), list) else None)
                    )
                    usage_info = pdata.get("usage") or {}
                    break

                if status in ("FAILED", "CANCELED", "UNKNOWN"):
                    err_msg = output.get("message") or output.get("code") or "上游任务失败"
                    yield error_event(
                        f"任务 {status}: {err_msg}",
                        code=f"task_{status.lower()}",
                    )
                    return

            if not video_url:
                yield error_event(
                    f"任务完成但未返回视频 URL: {pdata}",
                    code="no_video_url",
                )
                return

            # 3. 下载视频 + 落 artifacts
            yield data_part("task-progress", {"percent": 92, "message": "下载视频"})
            try:
                # 视频可能 ≥ 几十 MB,timeout 单独放大
                vresp = await client.get(video_url, timeout=httpx.Timeout(120.0))
                vresp.raise_for_status()
            except Exception as e:  # noqa: BLE001
                yield error_event(f"下载生成视频失败: {e}", code="download_failed")
                return
            vbytes = vresp.content
            mime = (vresp.headers.get("content-type") or "video/mp4").split(";")[0].strip()
            if not mime.startswith("video/"):
                mime = "video/mp4"
            saved = save_bytes(vbytes, mime)

            # 4. width/height 估算
            w, h = None, None
            try:
                w, h = (int(x) for x in size.split("*"))
            except (ValueError, AttributeError):
                pass

            yield file_part(
                media_type=mime,
                url=saved["url"],
                metadata={
                    "sha256": saved["sha256"],
                    "size_bytes": saved["size"],
                    "width": w,
                    "height": h,
                    "duration_s": usage_info.get("video_duration") or duration,
                    "prompt": prompt,
                    "model": req.model,
                    "platform": req.platform_name,
                    "task_id": task_id,
                },
            )
            yield data_part(
                "usage",
                {
                    "video_count": usage_info.get("video_count") or 1,
                    "video_duration": usage_info.get("video_duration") or duration,
                    "video_ratio": usage_info.get("video_ratio"),
                    "model": req.model,
                    "task_id": task_id,
                },
            )
            yield data_part("task-progress", {"percent": 100, "message": "完成"})
