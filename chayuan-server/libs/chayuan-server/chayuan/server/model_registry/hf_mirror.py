"""强制 huggingface_hub 走 hf-mirror 镜像。

国内直连 ``huggingface.co`` 常 ``[Errno 101] Network is unreachable``。
huggingface_hub 在**首次 import 时**就把 ``HF_ENDPOINT`` 读进
``constants.ENDPOINT`` 定死,且各子模块 ``from .constants import ENDPOINT``
各自缓存了一份。进程别处(模型扫描等)早已 import 过它 —— 此时再设环境
变量已无效。

:func:`force_hf_mirror_endpoint` 在下载前调用,运行时遍历所有已加载的
``huggingface_hub*`` 子模块,把 ``ENDPOINT`` 全部改成镜像。install_model /
install_job 等所有 HF 下载路径都应先调它。
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional

logger = logging.getLogger("chayuan.model_registry.hf_mirror")

HF_MIRROR = "https://hf-mirror.com"


_HF_DEFAULT = "https://huggingface.co"


def force_hf_mirror_endpoint(endpoint: Optional[str] = None) -> str:
    """把本进程 huggingface_hub 的下载端点强制改成镜像,返回最终端点。

    ⚠ 关键:``hf_hub_url`` 真正用来拼下载 URL 的是
    ``constants.HUGGINGFACE_CO_URL_TEMPLATE`` —— 这个模板在 import 时就把
    端点拼成字符串定死了。**只改 ``ENDPOINT`` 不够**,必须连模板一起改,
    否则 hf_hub_download / snapshot_download 仍直连 huggingface.co。

    Args:
        endpoint: 显式指定端点;不传则尊重已有 ``HF_ENDPOINT`` 环境变量,
            再不行用 :data:`HF_MIRROR`。

    Returns:
        实际生效的端点 URL。
    """
    ep = (endpoint or os.environ.get("HF_ENDPOINT") or HF_MIRROR).rstrip("/")
    os.environ["HF_ENDPOINT"] = ep
    patched = 0
    for name, mod in list(sys.modules.items()):
        if not name.startswith("huggingface_hub"):
            continue
        try:
            if getattr(mod, "ENDPOINT", None):
                mod.ENDPOINT = ep
                patched += 1
            # 真正决定下载 URL 的模板 —— 必须改
            tmpl = getattr(mod, "HUGGINGFACE_CO_URL_TEMPLATE", None)
            if isinstance(tmpl, str) and _HF_DEFAULT in tmpl:
                mod.HUGGINGFACE_CO_URL_TEMPLATE = tmpl.replace(_HF_DEFAULT, ep)
                patched += 1
        except Exception:  # noqa: BLE001
            pass
    logger.info("[hf-mirror] HF endpoint → %s(覆盖 %d 处)", ep, patched)
    return ep
