"""重启辅助：在配置面板中触发服务重启。

设计：
- ``chayuan start`` 启动时，``startup.main`` 会调用本模块的 ``record_runtime``
  把父进程的 ``pid`` / ``argv`` / ``cwd`` / 必要 env（``CHAYUAN_ROOT`` 等）写到
  ``CHAYUAN_ROOT/.chayuan_runtime.json``。
- 面板 UI 调用 ``trigger_restart()`` 时，会 fork 出一个完全脱离当前 chayuan 进程树的
  「守护小脚本」——即 ``restart_helper.py``——这个脚本负责：
    1. 向父进程发送 SIGTERM；
    2. 等父进程退出（或超时 SIGKILL）；
    3. 用原始 argv / cwd / env 启动新的 chayuan。
  这样即便面板本身作为 child 被一起杀死，新实例也能起来。

这里「父进程」指的是启动 chayuan 的那个 CLI 进程（cli.main），而不是面板自己。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from chayuan.settings import CHAYUAN_ROOT

RUNTIME_META_ENV = "CHAYUAN_RUNTIME_META"

_DEFAULT_META_PATH = Path(CHAYUAN_ROOT) / ".chayuan_runtime.json"

# 这些 env 在重启时会被继承（其它保持由新进程继承的全局环境，避免带错无关变量）
_INHERIT_ENV_KEYS = (
    "CHAYUAN_ROOT",
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "POETRY_ACTIVE",
)


def runtime_meta_path() -> Path:
    """运行时元数据文件路径，允许通过 env 覆盖，便于测试。"""
    override = os.environ.get(RUNTIME_META_ENV)
    if override:
        return Path(override)
    return _DEFAULT_META_PATH


def record_runtime(argv: Optional[List[str]] = None) -> Path:
    """把当前进程信息写入元数据文件，供 restart_helper 读取。

    在 ``chayuan.startup.main`` 里调用一次即可。
    """
    data: Dict[str, Any] = {
        "pid": os.getpid(),
        "argv": list(sys.argv if argv is None else argv),
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "env": {k: os.environ[k] for k in _INHERIT_ENV_KEYS if k in os.environ},
        "recorded_at": time.time(),
    }
    path = runtime_meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def load_runtime() -> Optional[Dict[str, Any]]:
    path = runtime_meta_path()
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# children 子进程注册：支持 `chayuan stop --api / -w / -c` 粒度化停服
# ---------------------------------------------------------------------------
#
# 每个 role 的记录：
#   { "pid": int, "port": int | null, "name": str }
#
# 写盘时加 file lock 防止并发；读盘时若文件损坏，回落空 children 字典。


def _atomic_update_runtime(mutator) -> Optional[Dict[str, Any]]:
    """读-改-写 runtime meta；返回新内容（若无 meta 文件则返回 None）。

    ``mutator`` 是一个 ``(meta: dict) -> None`` 函数，会就地修改传入字典。
    """
    path = runtime_meta_path()
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            meta = json.load(f) or {}
    except (OSError, ValueError):
        return None
    meta.setdefault("children", {})
    mutator(meta)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return meta


def register_child(role: str, pid: int, *,
                     port: Optional[int] = None,
                     name: str = "") -> None:
    """把一个子进程登记到 runtime meta，供 `chayuan stop --<role>` 使用。

    :param role: ``api`` / ``config`` / ``worker`` 等。
    :param pid:  子进程 pid。
    :param port: 子进程监听端口（若有）；stop 时若 pid 已失效可按端口兜底找人。
    :param name: 子进程显示名（日志用）。
    """
    if not role or pid <= 0:
        return

    def _mut(meta: Dict[str, Any]) -> None:
        children = meta.setdefault("children", {})
        children[str(role)] = {
            "pid": int(pid),
            "port": int(port) if port else None,
            "name": str(name or role),
            "registered_at": time.time(),
        }

    _atomic_update_runtime(_mut)


def unregister_child(role: str) -> None:
    """从 runtime meta 里删掉某个 role 的记录（进程已退出时调用）。"""
    def _mut(meta: Dict[str, Any]) -> None:
        children = meta.get("children") or {}
        children.pop(str(role), None)

    _atomic_update_runtime(_mut)


def list_children() -> Dict[str, Dict[str, Any]]:
    """返回 ``{role: {pid, port, name, ...}}``。文件不存在时返回 ``{}``。"""
    meta = load_runtime()
    if not meta:
        return {}
    children = meta.get("children") or {}
    return {str(k): dict(v) for k, v in children.items() if isinstance(v, dict)}


def trigger_restart(delay: float = 1.0) -> Dict[str, Any]:
    """fork 出守护脚本，去 kill 父进程并以相同参数重启，立即返回。

    返回描述字典：``{"helper_pid": ..., "target_pid": ..., "argv": [...]}``。
    调用方应在 UI 上提示用户「服务正在重启，几秒后刷新页面」。
    """
    meta = load_runtime()
    if not meta:
        raise RuntimeError(
            f"未找到运行时元数据文件：{runtime_meta_path()}。"
            " 可能是通过非 `chayuan start` 方式启动，或首次启动时写入失败。"
        )
    target_pid = int(meta["pid"])
    argv = list(meta.get("argv") or [])
    if not argv:
        raise RuntimeError("元数据中 argv 为空，无法重启。")

    helper = Path(__file__).with_name("restart_helper.py")
    cmd = [
        sys.executable,
        str(helper),
        "--meta",
        str(runtime_meta_path()),
        "--delay",
        str(delay),
    ]

    # 跨平台 detach；守护脚本必须脱离本会话，这样父进程 (chayuan) 被杀时不受影响。
    from chayuan.server.shared.process_utils import detached_popen_kwargs
    popen_kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(Path(CHAYUAN_ROOT)),
        **detached_popen_kwargs(),
    }
    proc = subprocess.Popen(cmd, **popen_kwargs)
    return {
        "helper_pid": proc.pid,
        "target_pid": target_pid,
        "argv": argv,
    }


# ---------------------------------------------------------------------------
# 单服务 restart：spawn detached 子进程，不影响主 runtime meta.pid
# ---------------------------------------------------------------------------
#
# 用 ``subprocess.Popen(start_new_session=True)`` 起一个完全脱离父进程的子进程，
# 执行 ``python -c "from chayuan.startup import run_<role>; run_<role>()"``。
# 子进程不记 runtime_meta.pid（保留原 `chayuan start -a` 父进程身份），只写
# children.<role>.pid，让 `chayuan stop --<role>` 能找到它。

_ROLE_TO_TARGET = {
    "api":    "run_api_server",
    "config": "run_config_panel",
}


def _spawn_service_detached(role: str) -> int:
    """启动一个独立 subprocess 跑对应 role 的 run_* 函数；返回子 pid。

    子进程完全 detach（start_new_session / close_fds）；stdout / stderr
    重定向到 ``$CHAYUAN_ROOT/logs/restart_<role>.log`` 方便排查。
    """
    target = _ROLE_TO_TARGET.get(role)
    if not target:
        raise ValueError(f"不支持的 role={role!r}")

    log_dir = Path(CHAYUAN_ROOT) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"restart_{role}.log"

    # 为什么用 `python -c "..."` 而不是 `python -m chayuan.startup --api`：
    #  * 后者会触发 _kill_previous_instance（读 runtime_meta 里旧 pid）→ 误杀 `-a` 的
    #    父进程；
    #  * 后者会调 record_runtime → 把 runtime_meta.pid 改成这个独立子进程，等它
    #    退出 meta 就失配；
    #  * 直接调 run_<role>() 只启动对应服务，没有副作用。
    cmd = [
        sys.executable,
        "-c",
        f"from chayuan.startup import {target}; {target}()",
    ]

    # 带上必要环境变量
    env = {k: os.environ[k] for k in os.environ}
    env.setdefault("CHAYUAN_ROOT", str(CHAYUAN_ROOT))

    # 跨平台 detach kwargs：Windows=DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP；
    # POSIX=start_new_session。详见 shared/process_utils.py。
    from chayuan.server.shared.process_utils import detached_popen_kwargs

    log_fd = open(log_path, "ab", buffering=0)
    try:
        popen_kwargs: Dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": log_fd,
            "stderr": subprocess.STDOUT,
            "env": env,
            "cwd": str(Path(CHAYUAN_ROOT)),
            **detached_popen_kwargs(),
        }
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except Exception:
        try:
            log_fd.close()
        finally:
            pass
        raise
    return int(proc.pid)


def restart_role(role: str, *,
                    stop_timeout_sec: float = 6.0,
                    start_wait_sec: float = 30.0,
                    force: bool = False) -> Dict[str, Any]:
    """停某个 role 的现有进程，重新 spawn 一个独立进程。

    :returns: ``{"role", "stopped", "new_pid", "port", "listening", "detail"}``
    """
    from chayuan.server.config_panel.stop import (  # 延迟 import 防循环
        KNOWN_ROLES, _pid_alive, _port_in_use,
        _resolve_port_for_role, stop_role,
    )
    if role not in _ROLE_TO_TARGET:
        raise ValueError(
            f"不支持的 role={role!r}；可选：{list(_ROLE_TO_TARGET)}"
        )

    # 1) stop
    stop_result = stop_role(role, force=force)

    # 2) 等端口释放
    port = stop_result.port or _resolve_port_for_role(role)
    if port:
        deadline = time.time() + stop_timeout_sec
        while time.time() < deadline and _port_in_use(int(port)):
            time.sleep(0.2)

    # 3) spawn 新进程
    new_pid = _spawn_service_detached(role)

    # 4) 落 children 记录（端口留作 port_in_use 兜底；新进程自身不写 meta）
    try:
        register_child(role, new_pid, port=port,
                         name=KNOWN_ROLES.get(role, {}).get("label") or role)
    except Exception:  # noqa: BLE001
        pass

    # 5) 等子进程起来（端口监听 = 成功）
    listening = False
    detail = ""
    if port:
        deadline = time.time() + start_wait_sec
        while time.time() < deadline:
            if _port_in_use(int(port)):
                listening = True
                break
            if not _pid_alive(new_pid):
                detail = f"新进程 pid={new_pid} 起来后立即退出；请查看日志"
                break
            time.sleep(0.3)
    else:
        # 无端口语义（理论上三种 role 都有），只校验 pid 还活着
        time.sleep(0.5)
        listening = _pid_alive(new_pid)

    return {
        "role": role,
        "stopped": {
            "status": stop_result.status,
            "pid": stop_result.pid,
            "detail": stop_result.detail,
        },
        "new_pid": new_pid,
        "port": port,
        "listening": listening,
        "detail": detail or ("端口已监听" if listening
                                 else f"{start_wait_sec}s 内未监听端口 {port}"),
    }


def _roles_from_argv(argv: List[str]) -> List[str]:
    """从保存的 argv 里推断当初启用了哪些 role。

    规则：
    - ``-a / --all`` → API + 配置面板
    - ``--api / -c / --config`` → 按 flag 收集
    - 没有明确 flag 时回落 API + 配置面板
    """
    joined = " ".join(argv).lower()
    if " -a" in f" {joined} " or "--all" in joined:
        return ["api", "config"]
    roles: List[str] = []
    if "--api" in joined:
        roles.append("api")
    if " -c" in f" {joined} " or "--config" in joined:
        roles.append("config")
    return roles or ["api", "config"]


def restart_all(*, delay: float = 1.0, force: bool = False,
                  use_helper: bool = False) -> Dict[str, Any]:
    """整体重启：默认走**逐 role** 路径（和单服务 restart 同一实现，最可靠）。

    :param use_helper: 历史路径——守护脚本（``trigger_restart``）+ 原 argv 重启。
        该路径需要守护进程稳定 spawn 新父进程；在某些 Windows 环境下 DEVNULL 屏蔽
        stderr 会掩盖启动失败，排查困难。默认改为逐 role spawn。

    两条路径都会先 ``stop_all(force)``，保证端口释放；随后：
    - 逐 role 路径：根据 meta.argv 推断原来启了哪些 role，分别 ``restart_role``。
      若无 meta 则全启（api + config）。
    - 守护脚本路径：保持原 ``trigger_restart`` 不变。
    """
    meta = load_runtime()

    # 先清理所有孤儿 / 端口占用
    try:
        from chayuan.server.config_panel.stop import stop_all as _stop_all
        pre_stop = _stop_all(force=force)
    except Exception as e:  # noqa: BLE001
        pre_stop = {"error": f"{type(e).__name__}: {e}"}

    if use_helper and meta:
        try:
            result = trigger_restart(delay=delay)
            return {"mode": "helper", "pre_stop": pre_stop, **result}
        except RuntimeError as e:
            # 守护脚本启动失败 → 降级逐 role
            pass  # 落下来走 per_role

    # 逐 role：按原 argv 推断；无 meta 则全启
    if meta:
        roles = _roles_from_argv(list(meta.get("argv") or []))
    else:
        roles = ["api", "config"]

    out: Dict[str, Any] = {
        "mode": "per_role", "pre_stop": pre_stop, "roles_planned": roles,
        "roles": [],
    }
    for role in roles:
        if role not in _ROLE_TO_TARGET:
            continue
        try:
            out["roles"].append(restart_role(role, force=force))
        except Exception as e:  # noqa: BLE001
            out["roles"].append(
                {"role": role, "error": f"{type(e).__name__}: {e}"}
            )
    return out
