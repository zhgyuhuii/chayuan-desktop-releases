"""ComfyUI runtime adapter.

为什么要单独写 workflow 模板而不是直接透传 user 的 ``workflow``？
================================================================

* ``/v1/images/generations`` 是 OpenAI 风格 API，调用方只关心
  ``prompt`` / ``size`` / ``n``，不应该被迫学 ComfyUI DAG。
* ComfyUI 自身不接 OpenAI 协议，必须把字段翻译成节点 graph。
* 翻译逻辑要可单测、可在离线环境（没装 ComfyUI）跑通。

调用流程：

1. ``render_workflow`` 把 OpenAI payload 渲染成 ComfyUI 节点 dict
2. ``POST /prompt`` 提交，拿 ``prompt_id``
3. 短轮询 ``GET /history/{prompt_id}`` 直到 ``outputs`` 出现
4. 用 ``GET /view`` 把图片字节读回，base64 编码成 data URL 返给调用方

环境变量：

* ``CHAYUAN_COMFYUI_POLL_INTERVAL_SEC`` (default ``0.5``)
* ``CHAYUAN_COMFYUI_POLL_TIMEOUT_SEC`` (default ``300``)
* ``CHAYUAN_COMFYUI_OUTPUT_PREFIX`` (default ``"chayuan/output"``)

高级用户：可以通过 ``payload.workflow`` 直接传完整 ComfyUI graph 跳过模板。
"""
from __future__ import annotations

import base64
import logging
import os
import time
import uuid
from typing import Any

from chayuan_runtime.adapters._comfyui_workflows import (
    AVAILABLE_TEMPLATES,
    render_workflow,
)
from chayuan_runtime.adapters._http import get_client
from chayuan_runtime.base import (
    AdapterCapabilities,
    AdapterRequest,
    AdapterResponse,
    RuntimeAdapter,
)

logger = logging.getLogger(__name__)


def _poll_interval() -> float:
    try:
        return float(os.environ.get("CHAYUAN_COMFYUI_POLL_INTERVAL_SEC", "0.5"))
    except ValueError:
        return 0.5


def _poll_timeout() -> float:
    try:
        return float(os.environ.get("CHAYUAN_COMFYUI_POLL_TIMEOUT_SEC", "300"))
    except ValueError:
        return 300.0


class ComfyUIAdapter(RuntimeAdapter):
    """ComfyUI for T2I & T2V via its REST + websocket API."""

    name = "comfyui"
    capabilities = AdapterCapabilities(
        categories=("t2i", "t2v"),
        formats=("checkpoint", "diffusers", "safetensors"),
        needs_gpu=True,
    )
    health_path = "/system_stats"

    def __init__(self, *, base_url: str = "http://127.0.0.1:18188", mock: bool = True) -> None:
        super().__init__(base_url=base_url, mock=mock)

    # ------------------------------------------------------------------
    # main entry
    # ------------------------------------------------------------------

    def call(self, req: AdapterRequest) -> AdapterResponse:
        if self.mock:
            body = self._mock_body(req)
            return AdapterResponse(op=req.op, model_id=req.model.public_id, body=body)

        # 1) 选 workflow：用户传了完整 workflow 就直接用，否则模板渲染
        workflow = req.payload.get("workflow")
        if not workflow:
            workflow = render_workflow(
                op=req.op,
                model_id=req.model.public_id,
                model_format=getattr(req.model, "format", None),
                payload=req.payload,
            )

        # 2) 提交
        client_id = uuid.uuid4().hex
        prompt_id = self._submit(workflow, client_id=client_id)

        # 3) 轮询
        outputs = self._wait_until_done(prompt_id)

        # 4) 取图（取第一张）；视频走 ``SaveAnimatedWEBP`` 输出 .webp / .gif，逻辑一致
        files = self._collect_files(outputs)
        primary = files[0] if files else None

        if primary is None:
            return AdapterResponse(
                op=req.op,
                model_id=req.model.public_id,
                body={
                    "prompt_id": prompt_id,
                    "client_id": client_id,
                    "outputs": outputs,
                    "warning": "no output image found in ComfyUI history",
                },
            )

        data_url = self._fetch_as_data_url(primary)
        body = {
            "prompt_id": prompt_id,
            "client_id": client_id,
            "model": req.model.public_id,
            "url": data_url,
            "filename": primary.get("filename"),
            "type": primary.get("type"),
            "subfolder": primary.get("subfolder"),
            "outputs": outputs,
        }
        # 多张图：把所有 data URL 也带回去（OpenAI ``n``）
        if len(files) > 1:
            body["images"] = [
                {
                    "url": self._fetch_as_data_url(f),
                    "filename": f.get("filename"),
                }
                for f in files
            ]
        return AdapterResponse(op=req.op, model_id=req.model.public_id, body=body)

    # ------------------------------------------------------------------
    # mock / offline helpers
    # ------------------------------------------------------------------

    def _mock_body(self, req: AdapterRequest) -> dict[str, Any]:
        # mock 也把渲染好的 workflow 暴露出来，方便单测验证字段拼接
        try:
            wf = render_workflow(
                op=req.op,
                model_id=req.model.public_id,
                model_format=getattr(req.model, "format", None),
                payload=req.payload,
            )
        except Exception:  # 极端情况下 render 不应该抛，但 mock 路径不要 break 调用方
            wf = None
        body = {
            "model": req.model.public_id,
            "prompt": req.payload.get("prompt"),
            "url": "data:image/png;base64,iVBORw0KGgo=",
            "task_id": "mock-task-1",
            "available_templates": list(AVAILABLE_TEMPLATES),
        }
        if wf is not None:
            body["workflow"] = wf
        return body

    # ------------------------------------------------------------------
    # network helpers (only called when ``mock=False``)
    # ------------------------------------------------------------------

    def _submit(self, workflow: dict[str, Any], *, client_id: str) -> str:
        with get_client() as c:
            r = c.post(
                f"{self.base_url}/prompt",
                json={"prompt": workflow, "client_id": client_id},
            )
            r.raise_for_status()
            data = r.json()
        prompt_id = data.get("prompt_id") or data.get("promptId") or data.get("id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI /prompt did not return prompt_id: {data}")
        return str(prompt_id)

    def _wait_until_done(self, prompt_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + _poll_timeout()
        interval = _poll_interval()
        last_err: Exception | None = None
        with get_client() as c:
            while time.monotonic() < deadline:
                try:
                    r = c.get(f"{self.base_url}/history/{prompt_id}")
                    if r.status_code == 200:
                        data = r.json()
                        node = data.get(prompt_id) or {}
                        # ComfyUI 完成时 ``status.completed = True`` 且 ``outputs`` 非空
                        status = node.get("status") or {}
                        outputs = node.get("outputs") or {}
                        if status.get("completed") or outputs:
                            return outputs
                except Exception as e:  # pragma: no cover
                    last_err = e
                time.sleep(interval)
        raise TimeoutError(
            f"ComfyUI prompt {prompt_id} did not complete within "
            f"{_poll_timeout()}s (last_err={last_err!r})"
        )

    def _collect_files(self, outputs: dict[str, Any]) -> list[dict[str, Any]]:
        """ComfyUI ``outputs`` 形如：
            {
                "7": {"images": [{"filename": "...", "subfolder": "...", "type": "output"}, ...]},
                "15": {"gifs":   [{...}]},
            }
        我们扁平化一下取所有可下载文件。
        """
        files: list[dict[str, Any]] = []
        for node_id, node_out in (outputs or {}).items():
            if not isinstance(node_out, dict):
                continue
            for key in ("images", "gifs", "videos", "files"):
                arr = node_out.get(key)
                if not arr:
                    continue
                for f in arr:
                    if isinstance(f, dict) and f.get("filename"):
                        files.append({**f, "_node": node_id})
        return files

    def _fetch_as_data_url(self, f: dict[str, Any]) -> str:
        params = {
            "filename": f.get("filename"),
            "subfolder": f.get("subfolder", ""),
            "type": f.get("type", "output"),
        }
        with get_client() as c:
            r = c.get(f"{self.base_url}/view", params=params)
            r.raise_for_status()
            content = r.content
            mime = r.headers.get("content-type", "image/png").split(";")[0].strip()
        b64 = base64.b64encode(content).decode("ascii")
        return f"data:{mime};base64,{b64}"
