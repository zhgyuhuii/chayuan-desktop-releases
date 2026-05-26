"""证据收集 + telemetry 上报 — 取证给法务用。

* 检测到改动后,本地 ``~/.chayuan/brandlock.evidence.jsonl`` 持久化日志(永不删)
* 若有网络,异步上报到察元 telemetry endpoint(可选,客户离线部署时静默)
* 上报内容包含:fingerprint / failed_files / 时间 / 主机信息

法务用时:从客户 chayuan_data 目录捞 ``brandlock.evidence.jsonl`` 即铁证 —
hash 不匹配 + Ed25519 签名链证明产物来自察元发版。
"""
from __future__ import annotations

import json
import logging
import os
import platform
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("chayuan.brandlock.evidence")


def _evidence_log_path() -> Path:
    """证据日志路径(用户改不了 — 即使改了 JSON 行,前面所有 append 行还在)。"""
    base = os.environ.get("CHAYUAN_ROOT") or str(
        Path.home() / ".chayuan"
    )
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p / "brandlock.evidence.jsonl"


def _host_fingerprint() -> Dict[str, str]:
    """收集主机指纹(用于追溯具体是哪个客户端篡改)。"""
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": platform.python_version(),
    }


def record_evidence(evidence: Dict[str, Any]) -> None:
    """append 一条 jsonl;同步 IO,失败不抛。

    每次启动 / 定时审计调一次。永不删历史 — 法务证据。
    """
    if not evidence.get("tampered"):
        return  # 完好状态不记录,只记篡改事件,减少噪音
    try:
        record = {
            "ts": time.time(),
            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **evidence,
            "host": _host_fingerprint(),
        }
        with _evidence_log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.debug("[brandlock] record_evidence failed: %r", e)


def report_async(evidence: Dict[str, Any]) -> None:
    """异步上报 telemetry — 失败静默,不阻塞业务。

    端点:由 ``CHAYUAN_TELEMETRY_URL`` 环境变量指定;未设置时跳过。
    商家发版时设置自己的接收端;客户离线部署时无侵入。
    """
    endpoint = os.environ.get("CHAYUAN_TELEMETRY_URL", "").strip()
    if not endpoint or not evidence.get("tampered"):
        return

    def _send() -> None:
        try:
            import urllib.request

            payload = {
                **evidence,
                "host": _host_fingerprint(),
                "ts": time.time(),
            }
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "User-Agent": "chayuan-brandlock/1.0"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5.0)
        except Exception:  # noqa: BLE001
            pass  # 静默失败 — 客户网络不通时不该影响业务

    threading.Thread(target=_send, daemon=True).start()
