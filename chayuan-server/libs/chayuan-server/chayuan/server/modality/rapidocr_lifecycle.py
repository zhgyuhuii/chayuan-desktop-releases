"""RapidOCR sidecar daemon 生命周期管理。

为什么需要这个模块
==================
``rapidocr_server.py`` 是一个独立的 Python 进程(``uvicorn`` 起的 ``/v1/ocr``
HTTP 服务,端口 18380),**不**走 ``LocalRuntimeRegistry`` 管的 5 个
capability(那个管 llama-server / whisper-server / Infinity 的生命周期)。

之前它只有两种启动方式:
  1) 用户在 UI 上点「设置 → 运行时框架 → RapidOCR → 启动」
  2) 用户在命令行手动跑 ``python -m chayuan.server.modality.rapidocr_server``

两种都要用户主动操作,结果用户上传图片就拿 503 "OCR sidecar not ready"。
本模块封装 ``start / stop / status``,给:
  - ``server_app.py`` 的 startup hook(进程起来时自动拉起)
  - ``modality_routes.py`` 的 ``/sidecar/ocr/*`` 路由(给前端 UI 启停)

端口固定 18380,跟 ``ocr_client._OCR_PORTS`` / ``install_task_manager``
recipe 的硬编码对齐。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("chayuan.modality.rapidocr_lifecycle")

# 跟 install_task_manager._START_RECIPES["rapidocr"] / ocr_client._OCR_PORTS 对齐
RAPIDOCR_PORT = 18380
RAPIDOCR_HOST = "127.0.0.1"
# 启动后等多久 TCP 探测到 ready;超过这个时间认作启动失败(典型 cold start 2-4s)
_READY_TIMEOUT_SEC = 10.0
_READY_PROBE_INTERVAL_SEC = 0.3

# auto-start 持久化:用户在「设置 → 本地模型服务」UI 切换 → 写本文件 →
# 下次 chayuan-server 启动时读这里决定要不要拉起 RapidOCR。
# 默认 True(开箱即用),只有用户显式关闭才写 false。
_SETTINGS_FILE_NAME = "sidecar_settings.json"
_SETTINGS_LOCK = threading.Lock()


def _settings_path() -> Path:
    """<CHAYUAN_ROOT>/data/sidecar_settings.json — 跟 LOG_PATH 同级,跨重启持久。"""
    from chayuan.settings import Settings
    base = Path(Settings.basic_settings.DATA_PATH)
    base.mkdir(parents=True, exist_ok=True)
    return base / _SETTINGS_FILE_NAME


def _read_settings() -> Dict[str, Any]:
    p = _settings_path()
    if not p.is_file():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        logger.warning("[ocr-sidecar] 读 %s 失败: %r;视作空配置", p, e)
        return {}


def _write_settings(data: Dict[str, Any]) -> None:
    p = _settings_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def get_auto_start() -> bool:
    """RapidOCR sidecar 是否在 chayuan-server 启动时自动拉起。默认 True。

    委托给通用 auto_start_store(key="rapidocr"),跟 5 个 capability 共用同一份
    持久化文件 + schema。
    """
    from chayuan.server.runtime.auto_start_store import get as _get
    return _get("rapidocr")


def set_auto_start(enabled: bool) -> bool:
    """保存 auto-start 开关。返回写入后的值。"""
    from chayuan.server.runtime.auto_start_store import set_ as _set
    return _set("rapidocr", bool(enabled))


def _log_path() -> str:
    """跟 install_task_manager._bg_log_path('rapidocr') 同一路径,方便用户排查。"""
    if os.name == "nt":
        base = (
            os.environ.get("TEMP")
            or os.environ.get("TMP")
            or "C:\\Windows\\Temp"
        )
        return os.path.join(base, "chayuan_rapidocr.log")
    return "/tmp/chayuan_rapidocr.log"


def _port_listening(port: int = RAPIDOCR_PORT, host: str = RAPIDOCR_HOST,
                       timeout: float = 0.3) -> bool:
    """TCP 探测 host:port 在不在监听。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, int(port))) == 0
    except OSError:
        return False


def _registered_child() -> Optional[Dict[str, Any]]:
    """从 runtime meta 读 "ocr" role 的注册记录;不存在返 None。"""
    try:
        from chayuan.server.config_panel.restart import list_children
        return list_children().get("ocr")
    except Exception:  # noqa: BLE001
        return None


def _is_pid_alive(pid: int) -> bool:
    try:
        from chayuan.server.shared.process_utils import is_pid_alive
        return bool(is_pid_alive(pid))
    except Exception:  # noqa: BLE001
        return False


def ocr_sidecar_status() -> Dict[str, Any]:
    """看 RapidOCR sidecar 当前状态。

    判断顺序:
      1) 18380 端口在监听 → ``"ready"``
      2) 端口没听 + meta 里有 "ocr" 注册 + pid 还活 → ``"starting"``(在拉起 / 异常)
      3) 都没 → ``"stopped"``

    返回字段:
      - ``state``      : ready / starting / stopped
      - ``pid``        : meta 里登记的 pid(可能为 None)
      - ``port``       : 18380
      - ``log_path``   : 后台日志路径,UI 给用户复制粘贴用
      - ``listening``  : bool,真实 TCP probe 结果
    """
    listening = _port_listening()
    meta = _registered_child() or {}
    pid = int(meta.get("pid") or 0) or None

    if listening:
        state = "ready"
    elif pid and _is_pid_alive(pid):
        state = "starting"
    else:
        state = "stopped"

    return {
        "state": state,
        "pid": pid,
        "port": RAPIDOCR_PORT,
        "log_path": _log_path(),
        "listening": listening,
        "auto_start": get_auto_start(),
    }


def _wait_until_ready(timeout: float = _READY_TIMEOUT_SEC) -> bool:
    """轮询 18380 直到 ready 或超时。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_listening():
            return True
        time.sleep(_READY_PROBE_INTERVAL_SEC)
    return False


def start_ocr_sidecar() -> Dict[str, Any]:
    """spawn RapidOCR daemon(若未在跑),返 ocr_sidecar_status()。

    幂等:已 ready 直接返;有 stale 注册但端口没听 → 清掉再起。

    实现方式:**复用** install_task_manager.start_service("rapidocr") —
    跟「设置 → 运行时框架」上的手动「启动」按钮走同一条路径。

    为什么不自己直接 Popen:
      * PyInstaller 打包后 sys.executable = chayuan.exe,**不**支持 `-m module`,
        所以直接 ``subprocess.Popen([sys.executable, "-m", ...], ...)`` 会让
        chayuan.exe 立刻退出 — 用户报"手动行,自动不行"就是这个根因。
      * install_task_manager 在 Windows 用 outer Python helper 内联 spawn
        (``[sys.executable, "-c", "import subprocess; subprocess.Popen(...)"]``)
        + DETACHED_PROCESS flag,绕过 cmd shell + PyInstaller entry 限制,
        手动启动验证过能跑。
    """
    # 已经在跑 → 直接返
    if _port_listening():
        logger.info("[ocr-sidecar] 已在 %d 监听,无需重启", RAPIDOCR_PORT)
        return ocr_sidecar_status()

    # 清掉 stale 注册(meta 里有 ocr 但端口没听,通常是上次崩了或被外部杀掉)
    stale = _registered_child()
    if stale:
        stale_pid = int(stale.get("pid") or 0)
        if stale_pid and _is_pid_alive(stale_pid):
            logger.info(
                "[ocr-sidecar] 已注册 pid=%d 但 %d 未监听,等待 ready",
                stale_pid, RAPIDOCR_PORT,
            )
            if _wait_until_ready(timeout=3.0):
                return ocr_sidecar_status()
        try:
            from chayuan.server.config_panel.restart import unregister_child
            unregister_child("ocr")
        except Exception:  # noqa: BLE001
            pass

    # 走 install_task_manager 的 start_service 路径(跟 UI 手动按钮一致)
    try:
        from chayuan.server.config_panel.install_task_manager import (
            get_install_manager,
        )
        task = get_install_manager().start_service(framework="rapidocr")
    except Exception as e:  # noqa: BLE001
        logger.exception("[ocr-sidecar] start_service 调用失败: %r", e)
        return {
            "state": "failed",
            "pid": None,
            "port": RAPIDOCR_PORT,
            "log_path": _log_path(),
            "listening": False,
            "auto_start": get_auto_start(),
            "error": f"{type(e).__name__}: {e}",
        }
    if task is None:
        logger.warning(
            "[ocr-sidecar] start_service 返回 None — _START_RECIPES 缺 rapidocr "
            "条目,或 _derive_start_recipe 派生失败"
        )
        return {
            **ocr_sidecar_status(),
            "error": "no start recipe for rapidocr",
        }
    logger.info(
        "[ocr-sidecar] 已调 install_task_manager.start_service(rapidocr) "
        "task_id=%s, 等 TCP %d ready",
        task.task_id, RAPIDOCR_PORT,
    )

    # 等 ready;超时也不报错,前端会看到 starting 状态
    if not _wait_until_ready():
        logger.warning(
            "[ocr-sidecar] %ds 内未 ready;子进程仍在跑,看 %s 排查",
            int(_READY_TIMEOUT_SEC), _log_path(),
        )

    return ocr_sidecar_status()


def stop_ocr_sidecar() -> Dict[str, Any]:
    """停 RapidOCR daemon。返 stop 之前的 pid + 当前 status。"""
    meta = _registered_child() or {}
    pid = int(meta.get("pid") or 0) or None
    stopped = False
    detail = ""

    if pid and _is_pid_alive(pid):
        try:
            from chayuan.server.shared.process_utils import terminate_pid
            stopped, detail = terminate_pid(pid, force=False, term_timeout=3.0)
        except Exception as e:  # noqa: BLE001
            logger.exception("[ocr-sidecar] terminate_pid 失败: %r", e)
            detail = f"{type(e).__name__}: {e}"
    else:
        detail = "no registered pid or already dead"

    try:
        from chayuan.server.config_panel.restart import unregister_child
        unregister_child("ocr")
    except Exception:  # noqa: BLE001
        pass

    # 等端口释放
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if not _port_listening():
            break
        time.sleep(0.2)

    return {
        **ocr_sidecar_status(),
        "stopped_pid": pid,
        "stopped": stopped,
        "detail": detail,
    }
