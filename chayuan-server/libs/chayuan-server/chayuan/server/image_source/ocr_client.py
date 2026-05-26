"""调 rapidocr / paddleocr sidecar 的异步瘦客户端。

sidecar 协议见 chayuan/server/modality/rapidocr_server.py:
    POST /v1/ocr {"image": "<base64>"}
    → {"boxes": [{"box":[...], "text":"...", "score":0.x}, ...], "elapsed_ms": N}

端口解析:OCR sidecar 不走 SidecarRuntimeManager(那个只管 chat/embedding/
rerank/asr/image-embedding 5 个 capability — 见 LocalRuntimeRegistry.CAPABILITIES);
OCR 是 install_task_manager 起的独立 daemon,端口固定:
    rapidocr  → 18380
    paddleocr → 18480
``resolve_port()`` 用 TCP probe 找在监听的那个。
"""
from __future__ import annotations

import base64
import logging
import re
import socket
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger("chayuan.image_source.ocr_client")

# 顺序优先:rapidocr 默认更轻量;两个都装则优先用 rapidocr
_OCR_PORTS: tuple[int, ...] = (18380, 18480)

_CN_RE = re.compile(r"[一-鿿]")


@dataclass
class OCRResult:
    text: str = ""
    lang: str = "unknown"
    confidence: float = 0.0
    box_count: int = 0
    elapsed_ms: int = 0
    error: Optional[str] = None
    raw_boxes: list = field(default_factory=list)


async def run_ocr(image_bytes: bytes, *, port: int, timeout: float = 30.0) -> OCRResult:
    """对一张图调 rapidocr,返 OCRResult。永不抛异常,失败信息写 .error。"""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    url = f"http://127.0.0.1:{int(port)}/v1/ocr"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json={"image": b64}, timeout=timeout)
        if resp.status_code >= 400:
            return OCRResult(error=f"{resp.status_code} {resp.text[:200]}")
        data = resp.json() or {}
    except httpx.TimeoutException as e:
        return OCRResult(error=f"timeout: {e}")
    except Exception as e:  # noqa: BLE001
        return OCRResult(error=f"http error: {e}")

    boxes = data.get("boxes") or []
    texts = []
    scores = []
    for b in boxes:
        t = (b.get("text") or "").strip()
        if t:
            texts.append(t)
            try:
                scores.append(float(b.get("score") or 0.0))
            except (TypeError, ValueError):
                pass
    full_text = "\n".join(texts)
    lang = "ch" if _CN_RE.search(full_text) else ("en" if full_text else "unknown")
    confidence = (sum(scores) / len(scores)) if scores else 0.0
    return OCRResult(
        text=full_text, lang=lang, confidence=confidence,
        box_count=len(boxes),
        elapsed_ms=int(data.get("elapsed_ms") or 0),
        raw_boxes=boxes,
    )


def _port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
    """TCP 探测 host:port 是否在监听。0.2s 超时,本机回环够用。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, int(port))) == 0
    except OSError:
        return False


def resolve_port() -> Optional[int]:
    """找在监听的 OCR sidecar 端口;两个都没起返 None。

    历史 bug:之前调 SidecarRuntimeManager.singleton() — 这个方法不存在,
    AttributeError 被 except 吞了,resolve_port 永远返 None,每次 OCR 都 503。
    根因:OCR sidecar 不在 LocalRuntimeRegistry 管的 5 个 capability 里,
    它是 install_task_manager 起的独立 daemon,端口在 install_task_manager 里
    硬编码(rapidocr=18380, paddleocr=18480)。
    """
    probed: list[tuple[int, bool]] = []
    for port in _OCR_PORTS:
        ok = _port_listening(port)
        probed.append((port, ok))
        if ok:
            logger.info("[ocr] resolve_port: %d listening — 用它", port)
            return port
    # 全没起 → 告诉用户去看 sidecar log(install_task_manager._bg_log_path),
    # 那里能看到 rapidocr_server.py 启动失败的真根因(常见:onnx 没装/端口占)。
    logger.warning(
        "[ocr] resolve_port: 无 OCR sidecar 在监听 — probed=%s。"
        "看 /tmp/chayuan_rapidocr.log(Linux/Mac)或 "
        "%%TEMP%%\\chayuan_rapidocr.log(Windows)排查启动失败原因",
        probed,
    )
    return None
