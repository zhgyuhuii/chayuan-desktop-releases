"""本地 runtime 诊断模块。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from chayuan import __version__ as _server_version
from chayuan.server.diagnose import checks as _checks
from chayuan.server.diagnose.types import DiagnoseCheck, DiagnoseReport, Severity

__all__ = [
    "DiagnoseCheck",
    "DiagnoseReport",
    "Severity",
    "run_all_checks",
]


def _resolve_chayuan_root() -> Path:
    from chayuan import settings as cy_settings
    return Path(cy_settings.CHAYUAN_ROOT)


def run_all_checks() -> DiagnoseReport:
    """跑 15 项 check,聚合成报告。单个 check 抛异常不阻塞其它 check。"""
    root = _resolve_chayuan_root()
    settings_path = root / "model_registry" / "local_runtime.yaml"
    status_path = root / "runtime.json"

    results = [
        _checks._safe_call("sidecar.healthz", _checks.check_sidecar_healthz),
        _checks._safe_call("vendor.platform.detected", _checks.check_vendor_platform_detected),
        _checks._safe_call("vendor.llama-server.binary", _checks.check_vendor_llama_server_binary),
        _checks._safe_call("vendor.bundled_models.chat", _checks.check_bundled_models_chat),
        _checks._safe_call("chayuan_root.writable", lambda: _checks.check_chayuan_root_writable(root)),
        _checks._safe_call("runtime_json.writable", lambda: _checks.check_runtime_json_writable(status_path)),
        _checks._safe_call("local_runtime_yaml.readable", lambda: _checks.check_local_runtime_yaml_readable(settings_path)),
        _checks._safe_call("disk.chayuan_root.free_gb", lambda: _checks.check_disk_free_gb(root)),
        _checks._safe_call("port.62582", _checks.check_port_62582),
        _checks._safe_call("chayuan_server.process", _checks.check_chayuan_server_process),
        _checks._safe_call("runtime.llama.chat.status",
                           lambda: _checks.check_runtime_llama_status_for("chat")),
        _checks._safe_call("runtime.llama.embedding.status",
                           lambda: _checks.check_runtime_llama_status_for("embedding")),
        _checks._safe_call("runtime.llama.rerank.status",
                           lambda: _checks.check_runtime_llama_status_for("rerank")),
        _checks._safe_call("runtime.llama.asr.status",
                           lambda: _checks.check_runtime_llama_status_for("asr")),
        # image-embedding 已下线(Level C),从 diagnose 也同步移除,
        # 避免读 registry 时 "unknown capability" 噪声。要恢复:
        # _checks._safe_call("runtime.llama.image-embedding.status",
        #                    lambda: _checks.check_runtime_llama_status_for("image-embedding")),
    ]

    summary = {"ok": 0, "warn": 0, "fail": 0}
    for c in results:
        summary[c.severity] += 1

    return DiagnoseReport(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        platform=sys.platform,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        chayuan_root=str(root),
        chayuan_server_version=_server_version,
        checks=results,
        summary=summary,
    )
