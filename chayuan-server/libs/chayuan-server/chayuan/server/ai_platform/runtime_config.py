"""根据 chayuan-server 的 ``runtime.json`` 配置 chayuan_runtime adapters。

为什么单独一个模块？
====================

* ``chayuan_runtime`` 的 11 个 adapter 默认 ``mock=True``、``base_url="http://127.0.0.1:<vendor-default>"``，
  在合并版里我们希望它们：
  - 用真实 ``mock=False`` 模式；
  - ``base_url`` 来自 chayuan-server 的 ``runtime.json``（``server.runtime.runtime_info``），
    那里写了 vendor 子进程实际占用的端口（35432/36379/...）；
  - 找不到端点时优雅降级（adapter 仍可用，只是请求会 404）。

主入口：
* :func:`apply_runtime_config` —— 在 chayuan-server FastAPI 启动时调一次。
"""
from __future__ import annotations

import logging
from typing import Dict

logger = logging.getLogger("chayuan.ai_platform.runtime_config")


# 名字映射：chayuan_runtime adapter 名 → chayuan-server runtime_info service 名。
# 当某个 service 名不存在时，回退到环境变量 / 默认值。
_ADAPTER_TO_SERVICE: Dict[str, str] = {
    "ollama":      "ollama",
    "vllm":        "vllm",
    "llamacpp":    "llamacpp",
    "infinity":    "infinity",
    "comfyui":     "comfyui",
    "whispercpp":  "whispercpp",
    "funasr":      "funasr",
    "piper":       "piper",
    "cosyvoice":   "cosyvoice",
    "rapidocr":    "rapidocr",
    "paddleocr":   "paddleocr",
}

# 缺 runtime.json 时用的"看起来合理的本地默认"。这些端口与 vendor/README.md 对齐。
_FALLBACK_PORTS: Dict[str, int] = {
    "ollama":      31434,
    "vllm":        38000,
    "llamacpp":    38086,
    "infinity":    37997,
    "comfyui":     38188,
    "whispercpp":  38090,
    "funasr":      38180,
    "piper":       38088,
    "cosyvoice":   38280,
    "rapidocr":    38089,
    "paddleocr":   38091,
}


def _adapter_base_url(adapter_name: str) -> str:
    """从 chayuan-server runtime_info 读端口；找不到就用 fallback。

    优先级：
    1) ``runtime.json::services.<name>.url``（含 schema/host/port）
    2) ``runtime.json::services.<name>.host`` + ``port``
    3) ``http://127.0.0.1:<fallback_port>``
    """
    svc_name = _ADAPTER_TO_SERVICE.get(adapter_name, adapter_name)
    host = "127.0.0.1"
    port = _FALLBACK_PORTS.get(adapter_name, 0)

    try:
        from chayuan.server.runtime.runtime_info import get_runtime_info
        info = get_runtime_info()
        ep = info.get_endpoint(svc_name)
        if ep:
            url = ep.get("url")
            if url and isinstance(url, str) and url.startswith(("http://", "https://")):
                return url.rstrip("/")
            host = ep.get("host") or host
            try:
                port = int(ep.get("port") or port)
            except (TypeError, ValueError):
                pass
    except Exception as e:  # noqa: BLE001
        logger.debug("[runtime_config] runtime_info 不可用：%r", e)

    if not port:
        return ""   # 让 adapter 的内置默认接管
    return f"http://{host}:{port}"


def apply_runtime_config(*, mock: bool = False) -> Dict[str, str]:
    """重置 chayuan_runtime registry，把每个 adapter 的 base_url 指向 runtime.json。

    Args:
        mock: True 强制保持 mock；生产应传 False。

    Returns:
        ``{adapter_name: base_url}`` 应用结果摘要。
    """
    try:
        from chayuan_runtime.registry import get_registry
    except Exception as e:  # noqa: BLE001
        logger.warning("[runtime_config] chayuan_runtime 未安装：%r", e)
        return {}

    registry = get_registry(mock=mock)
    out: Dict[str, str] = {}
    for adapter in registry.all():
        url = _adapter_base_url(adapter.name)
        if url:
            adapter.base_url = url
        adapter.mock = mock
        out[adapter.name] = adapter.base_url or "<adapter default>"
    logger.info("[runtime_config] applied: mock=%s adapters=%d", mock, len(out))
    for k, v in out.items():
        logger.debug("  · %s → %s", k, v)
    return out


__all__ = ["apply_runtime_config"]
