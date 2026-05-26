"""`chayuan stop` 的核心原语——跨平台、幂等、可按 role 粒度停。

两级停服策略：
1. **精确路径**：从 ``runtime_meta.children`` 拿到 role→pid，直接 kill
2. **端口兜底**：meta 缺失 / pid 已死但端口仍在，按端口找占用进程杀（跨平台）

所有函数**不抛**致命异常；返回结构化 ``StopOutcome``，CLI 层负责渲染。

跨平台实现全部走 ``chayuan.server.shared.process_utils``：
- ``is_pid_alive`` / ``terminate_pid`` / ``find_pids_listening_on``
Windows 用 ``taskkill`` + ``netstat``；Linux/macOS 用 POSIX signal + ``ss``/``lsof``。
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from chayuan.server.config_panel.restart import (
    list_children, load_runtime, runtime_meta_path, unregister_child,
)
from chayuan.server.shared.process_utils import (
    find_pids_listening_on as _find_pids_listening_on,
    is_pid_alive as _is_pid_alive_impl,
    terminate_pid as _terminate_pid,
)

logger = logging.getLogger("chayuan.stop")


# 已知 role 的 canonical 名称 + 默认端口标签；用于端口兜底时给用户更友好的提示
KNOWN_ROLES: Dict[str, Dict[str, str]] = {
    "api":    {"label": "API 服务",            "settings_key": "API_SERVER"},
    "config": {"label": "配置面板",            "settings_key": "CONFIG_SERVER"},
}


@dataclass
class RoleOutcome:
    role: str
    label: str
    status: str  # "stopped" | "not_running" | "failed" | "skipped"
    pid: Optional[int] = None
    port: Optional[int] = None
    detail: str = ""


@dataclass
class StopOutcome:
    parent_stopped: bool = False
    parent_pid: Optional[int] = None
    parent_detail: str = ""
    roles: List[RoleOutcome] = field(default_factory=list)

    @property
    def any_action(self) -> bool:
        return self.parent_stopped or any(
            r.status == "stopped" for r in self.roles
        )


# ---------------------------------------------------------------------------
# 进程活性 / 杀进程 / 查端口 —— 跨平台代理到 shared.process_utils
# ---------------------------------------------------------------------------
# 保留下划线前缀的老名字，避免老测试 / 调用方断开。

def _pid_alive(pid: int) -> bool:
    return _is_pid_alive_impl(pid)


def _kill_pid(pid: int, *,
              term_timeout: float = 5.0,
              force: bool = False) -> Tuple[bool, str]:
    return _terminate_pid(
        pid, force=force, term_timeout=term_timeout, kill_timeout=2.0,
    )


def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """纯 socket TCP 连接探测——跨平台最可靠。"""
    if not port:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            return s.connect_ex((host, int(port))) == 0
        except OSError:
            return False


def _find_pid_by_port(port: int) -> List[int]:
    return _find_pids_listening_on(port)


def _resolve_port_for_role(role: str) -> Optional[int]:
    """当 meta 里没记端口时，从 Settings 读默认值。"""
    try:
        from chayuan.settings import Settings
        key = KNOWN_ROLES.get(role, {}).get("settings_key")
        if not key:
            return None
        srv = getattr(Settings.basic_settings, key, {}) or {}
        p = srv.get("port") if isinstance(srv, dict) else None
        return int(p) if p else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------

def stop_role(role: str, *, force: bool = False) -> RoleOutcome:
    """按 role 停服。优先用 meta 里的 pid；失败回落端口搜索。"""
    label = KNOWN_ROLES.get(role, {}).get("label") or role
    children = list_children()
    info = children.get(role) or {}
    pid = int(info.get("pid") or 0)
    port = info.get("port")
    if not port:
        port = _resolve_port_for_role(role)

    # 1) 按 meta pid 杀
    if pid and _pid_alive(pid):
        ok, detail = _kill_pid(pid, force=force)
        # 清理 meta 记录（成功 / 失败都先清，避免下次再误认）
        try:
            unregister_child(role)
        except Exception:  # noqa: BLE001
            pass
        if ok:
            # 再等一下端口释放
            if port:
                for _ in range(10):
                    if not _port_in_use(int(port)):
                        break
                    time.sleep(0.2)
            return RoleOutcome(role=role, label=label, status="stopped",
                                 pid=pid, port=port, detail=detail)
        # 失败 → 尝试端口兜底
        logger.warning(
            "通过 pid 停 %s 失败：%s；将尝试端口兜底", role, detail,
        )

    # 2) 端口兜底
    if port and _port_in_use(int(port)):
        found = _find_pid_by_port(int(port))
        if not found:
            return RoleOutcome(
                role=role, label=label, status="failed",
                pid=None, port=port,
                detail=f"端口 {port} 被占用，但未能定位进程（请手动处理）",
            )
        killed_any = False
        errs: List[str] = []
        for p in found:
            ok, det = _kill_pid(int(p), force=force)
            if ok:
                killed_any = True
            else:
                errs.append(f"pid={p}: {det}")
        # 再观察端口
        for _ in range(10):
            if not _port_in_use(int(port)):
                break
            time.sleep(0.2)
        try:
            unregister_child(role)
        except Exception:  # noqa: BLE001
            pass
        if killed_any and not _port_in_use(int(port)):
            return RoleOutcome(
                role=role, label=label, status="stopped",
                pid=(found[0] if found else None), port=port,
                detail="port fallback",
            )
        return RoleOutcome(
            role=role, label=label, status="failed",
            pid=(found[0] if found else None), port=port,
            detail=("; ".join(errs) or "port still in use after kill"),
        )

    # 3) 啥都没找到
    try:
        unregister_child(role)
    except Exception:  # noqa: BLE001
        pass
    return RoleOutcome(
        role=role, label=label, status="not_running",
        pid=(pid or None), port=port, detail="未发现进程或端口占用",
    )


def stop_parent(*, force: bool = False,
                  term_timeout: float = 6.0) -> Tuple[bool, Optional[int], str]:
    """停 ``chayuan start`` 的父进程（它会级联清理所有子进程）。"""
    meta = load_runtime()
    if not meta:
        return False, None, "未找到运行时元数据；请用 --api/-w/-c 单独停；或 chayuan 未通过 `chayuan start` 启动"
    try:
        pid = int(meta.get("pid") or 0)
    except (TypeError, ValueError):
        return False, None, "runtime meta 中 pid 字段异常"
    if pid <= 0:
        return False, None, "runtime meta 中 pid 字段为空"
    if pid == os.getpid():
        return False, pid, "不能 stop 自身进程"
    if not _pid_alive(pid):
        return True, pid, "父进程已退出"
    ok, detail = _kill_pid(pid, force=force, term_timeout=term_timeout)
    return ok, pid, detail


def stop_all(*, force: bool = False) -> StopOutcome:
    """整体 stop：优先杀父进程；再逐 role 做端口兜底（捕漏）。"""
    out = StopOutcome()
    ok, pid, detail = stop_parent(force=force)
    out.parent_stopped = ok
    out.parent_pid = pid
    out.parent_detail = detail

    # 父进程杀完后，逐 role 兜底：确认端口都释放；如有残留单独处理
    for role in KNOWN_ROLES.keys():
        port = _resolve_port_for_role(role)
        if port and _port_in_use(int(port)):
            # 还有人占端口 → 说明子进程没随父进程走（pyinstaller 独立子、daemon 等）
            r = stop_role(role, force=force)
            out.roles.append(r)
        else:
            out.roles.append(RoleOutcome(
                role=role,
                label=KNOWN_ROLES[role]["label"],
                status="stopped" if ok else "not_running",
                port=port,
                detail="随父进程退出" if ok else "未检测到占用",
            ))

    # 清 meta children 记录（运维视角：父进程已停，继续留着会误导）
    if ok:
        try:
            for role in list_children().keys():
                unregister_child(role)
        except Exception:  # noqa: BLE001
            pass
    return out


# ---------------------------------------------------------------------------
# 状态查询（status 命令 / stop --dry-run 预览用）
# ---------------------------------------------------------------------------

def status_snapshot() -> Dict[str, Dict[str, object]]:
    """返回每个 role 的综合运行态：pid、alive、port 是否监听。"""
    children = list_children()
    out: Dict[str, Dict[str, object]] = {}
    for role, meta_info in KNOWN_ROLES.items():
        c = children.get(role) or {}
        pid = int(c.get("pid") or 0)
        port = c.get("port") or _resolve_port_for_role(role)
        port_listen = bool(port and _port_in_use(int(port)))
        pid_alive = bool(pid and _pid_alive(pid))
        out[role] = {
            "label": meta_info["label"],
            "pid": pid or None,
            "pid_alive": pid_alive,
            "port": int(port) if port else None,
            "port_listen": port_listen,
            "running": bool(pid_alive or port_listen),
        }
    return out
