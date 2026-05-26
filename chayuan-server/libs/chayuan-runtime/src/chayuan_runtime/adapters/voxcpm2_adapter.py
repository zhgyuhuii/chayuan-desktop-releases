"""VoxCPM 2 runtime adapter — 接入 chayuan_runtime 的 RuntimeAdapter 接口。

VoxCPM 2 是 OpenBMB 开源的轻量 TTS(0.5B 参数,CPU 实时合成中文)。
对应的 HTTP wrapper 在 ``chayuan/server/modality/voxcpm2_server.py``,
此 adapter 通过 OpenAI 兼容协议 ``POST /v1/audio/speech`` 与之对话。

接入后:
* chayuan-runtime registry 自动加载本 adapter
* framework_wiring 把它视为 TTS 类 framework
* model_locator 路由"使用 voxcpm2 跑 TTS"请求到此 adapter
"""
from __future__ import annotations

from chayuan_runtime.adapters._http import get_client
from chayuan_runtime.base import (
    AdapterCapabilities,
    AdapterRequest,
    AdapterResponse,
    RuntimeAdapter,
)


class VoxCpm2Adapter(RuntimeAdapter):
    name = "voxcpm2"
    capabilities = AdapterCapabilities(
        categories=("tts",),
        formats=("pytorch", "safetensors"),
    )
    # voxcpm2_server.py 通用 base 暴露 /v1/models 健康端点
    health_path = "/v1/models"

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:18580",
        mock: bool = True,
    ) -> None:
        super().__init__(base_url=base_url, mock=mock)

    def call(self, req: AdapterRequest) -> AdapterResponse:
        if self.mock:
            return self._mock_simple(req, key="audio")
        with get_client() as c:
            r = c.post(
                f"{self.base_url}/v1/audio/speech",
                json={
                    "model": req.model.public_id,
                    "input": req.payload.get("input"),
                    "voice": req.payload.get("voice", "default"),
                },
            )
            r.raise_for_status()
            return AdapterResponse(
                op=req.op,
                model_id=req.model.public_id,
                body={"audio": r.content, "format": "wav"},
            )
