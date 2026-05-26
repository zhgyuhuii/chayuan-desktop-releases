"""所有 diagnose check 函数。每个函数纯函数化,无 IO 副作用 (除了读盘 / 网络)。

约定:任何函数都不允许抛异常,内部用 try/except 包好;失败时返回
severity=fail + detail 描述。
"""
from __future__ import annotations

import os
import shutil
import yaml
import psutil
from pathlib import Path

from chayuan.server.diagnose.types import DiagnoseCheck
from chayuan.server.model_registry.local_index import get_local_index
from chayuan.server.model_registry.local_runtime import LlamaRuntimeManager


def check_sidecar_healthz() -> DiagnoseCheck:
    """sidecar 进程跑起来就视为 ok (路由调用本函数 = 路由可达 = healthz 一定通)。

    单独保留这个 check 是给报告里留个明确正信号:用户看到「sidecar.healthz: ✓」
    就知道至少 HTTP 层是通的,后面其它 check 失败可以锁定到具体子系统。
    """
    return DiagnoseCheck(
        name="sidecar.healthz",
        ok=True,
        severity="ok",
        detail="sidecar HTTP 层就绪 (routing layer responsive)",
    )


def check_vendor_llama_server_binary() -> DiagnoseCheck:
    """检查 vendor/services/llama-server/llama-server(.exe) 是否存在 + 读 VERSION。"""
    try:
        # 复用 manager 的查找逻辑;chayuan_root 这里只是占位,find_llama_server_exe
        # 不依赖 chayuan_root
        mgr = LlamaRuntimeManager(chayuan_root=Path("/tmp"))
        exe = mgr.find_llama_server_exe()
    except Exception as e:
        return DiagnoseCheck(
            name="vendor.llama-server.binary",
            ok=False,
            severity="fail",
            detail=f"查找二进制时异常:{type(e).__name__}: {e}",
        )

    if exe is None:
        return DiagnoseCheck(
            name="vendor.llama-server.binary",
            ok=False,
            severity="fail",
            detail="未找到 llama-server 二进制;检查集成版是否完整 / 开发机是否跑过 install-llama-server",
        )

    version = "unknown"
    version_file = exe.parent / "VERSION"
    if version_file.is_file():
        try:
            version = version_file.read_text(encoding="utf-8").splitlines()[0].strip()
        except Exception:
            pass

    try:
        size_mb = exe.stat().st_size / 1024 / 1024
    except Exception:
        size_mb = 0.0

    return DiagnoseCheck(
        name="vendor.llama-server.binary",
        ok=True,
        severity="ok",
        detail=f"{exe} ({version}, {size_mb:.1f} MB)",
        context={"path": str(exe), "version": version, "size_bytes": exe.stat().st_size},
    )


def check_bundled_models_chat() -> DiagnoseCheck:
    """bundled_models 是否至少有 1 个 chat .gguf 模型 (local_index 扫得到)。"""
    try:
        idx = get_local_index()
        cands = idx.by_capability("chat")
    except Exception as e:
        return DiagnoseCheck(
            name="vendor.bundled_models.chat",
            ok=False,
            severity="fail",
            detail=f"local_index 查询失败:{type(e).__name__}: {e}",
        )

    if not cands:
        return DiagnoseCheck(
            name="vendor.bundled_models.chat",
            ok=False,
            severity="fail",
            detail="bundled_models/ 下未扫到任何 chat 模型",
        )

    names = ", ".join(getattr(c, "model_id", "<?>") for c in cands[:3])
    more = "" if len(cands) <= 3 else f" 等 {len(cands)} 个"
    return DiagnoseCheck(
        name="vendor.bundled_models.chat",
        ok=True,
        severity="ok",
        detail=f"{len(cands)} 个 chat 模型: {names}{more}",
        context={"count": len(cands), "model_ids": [getattr(c, "model_id", None) for c in cands]},
    )


def check_chayuan_root_writable(chayuan_root: Path) -> DiagnoseCheck:
    """尝试在 chayuan_root 创建 + 删除 .diagnose-test 文件"""
    if not chayuan_root.is_dir():
        return DiagnoseCheck(
            name="chayuan_root.writable",
            ok=False,
            severity="fail",
            detail=f"chayuan_root 不存在: {chayuan_root}",
        )
    probe = chayuan_root / ".diagnose-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as e:
        return DiagnoseCheck(
            name="chayuan_root.writable",
            ok=False,
            severity="fail",
            detail=f"无法写入 {chayuan_root}: {type(e).__name__}: {e}",
        )
    return DiagnoseCheck(
        name="chayuan_root.writable",
        ok=True,
        severity="ok",
        detail=f"可写: {chayuan_root}",
    )


def check_runtime_json_writable(status_path: Path) -> DiagnoseCheck:
    """检查 runtime.json 父目录可写 (LlamaRuntimeManager._persist_status 写它)。"""
    parent = status_path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        probe = parent / ".diagnose-runtime-json-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as e:
        return DiagnoseCheck(
            name="runtime_json.writable",
            ok=False,
            severity="fail",
            detail=f"runtime.json 父目录不可写 ({parent}): {type(e).__name__}: {e}",
        )
    return DiagnoseCheck(
        name="runtime_json.writable",
        ok=True,
        severity="ok",
        detail=f"可写: {status_path}",
    )


def check_local_runtime_yaml_readable(yaml_path: Path) -> DiagnoseCheck:
    """local_runtime.yaml 不存在不算 fail (= 用默认),存在则解析必须成功。"""
    if not yaml_path.is_file():
        return DiagnoseCheck(
            name="local_runtime_yaml.readable",
            ok=True,
            severity="ok",
            detail=f"{yaml_path} 不存在,使用默认配置",
        )
    try:
        with open(yaml_path, "rb") as f:
            raw = f.read()
        yaml.safe_load(raw.decode("utf-8", errors="strict"))
    except Exception as e:
        return DiagnoseCheck(
            name="local_runtime_yaml.readable",
            ok=False,
            severity="warn",
            detail=f"yaml 解析失败 ({yaml_path}): {type(e).__name__}: {e}",
        )
    return DiagnoseCheck(
        name="local_runtime_yaml.readable",
        ok=True,
        severity="ok",
        detail=f"{yaml_path} 解析成功",
    )


def check_disk_free_gb(chayuan_root: Path) -> DiagnoseCheck:
    """磁盘剩余空间:>= 2 GB ok / 500MB-2GB warn / < 500MB fail"""
    try:
        du = shutil.disk_usage(chayuan_root if chayuan_root.exists() else chayuan_root.parent)
    except Exception as e:
        return DiagnoseCheck(
            name="disk.chayuan_root.free_gb",
            ok=False,
            severity="warn",
            detail=f"无法查询磁盘空间:{type(e).__name__}: {e}",
        )
    free_gb = du.free / 2**30
    if free_gb < 0.5:
        sev: str = "fail"
        ok = False
    elif free_gb < 2.0:
        sev = "warn"
        ok = True
    else:
        sev = "ok"
        ok = True
    return DiagnoseCheck(
        name="disk.chayuan_root.free_gb",
        ok=ok,
        severity=sev,  # type: ignore[arg-type]
        detail=f"{free_gb:.1f} GB 可用",
        context={"free_bytes": du.free, "total_bytes": du.total},
    )


def check_port_62582() -> DiagnoseCheck:
    """检查端口 62582 是否被占。

    被本进程 (chayuan-server) 占 = 也算 ok (sidecar 自己拿着 62581;
      llama-server 拿 62582 是预期)。
    被其它进程占 = warn (会让 start 时 _allocate_port 往上 bump 到 62583)。
    """
    try:
        conns = psutil.net_connections(kind="inet")
    except Exception as e:
        return DiagnoseCheck(
            name="port.62582",
            ok=True,
            severity="warn",
            detail=f"psutil 调用失败:{type(e).__name__}: {e};无法验证端口占用",
        )

    listeners = [
        c for c in conns
        if getattr(c, "status", None) == "LISTEN"
        and getattr(c.laddr, "port", None) == 62582
    ]
    if not listeners:
        return DiagnoseCheck(
            name="port.62582",
            ok=True,
            severity="ok",
            detail="62582 空闲",
        )
    owner = listeners[0]
    owner_pid = getattr(owner, "pid", None)
    owner_name = "<unknown>"
    if owner_pid:
        try:
            owner_name = psutil.Process(owner_pid).name()
        except Exception:
            pass
    if owner_pid == os.getpid():
        return DiagnoseCheck(
            name="port.62582",
            ok=True,
            severity="ok",
            detail=f"62582 被本进程 ({owner_name}, pid {owner_pid}) 占用",
        )
    return DiagnoseCheck(
        name="port.62582",
        ok=True,
        severity="warn",
        detail=f"62582 被其它进程占用: pid={owner_pid} ({owner_name})",
        context={"owner_pid": owner_pid, "owner_name": owner_name},
    )


def check_chayuan_server_process() -> DiagnoseCheck:
    """当前 sidecar 进程的 pid / 启动时间 / RSS。"""
    try:
        p = psutil.Process(os.getpid())
        rss_mb = p.memory_info().rss / 1024 / 1024
        from datetime import datetime as _dt
        started = _dt.fromtimestamp(p.create_time()).isoformat(timespec="seconds")
        return DiagnoseCheck(
            name="chayuan_server.process",
            ok=True,
            severity="ok",
            detail=f"pid={p.pid}, RSS={rss_mb:.1f} MB, started_at={started}",
            context={"pid": p.pid, "rss_bytes": p.memory_info().rss, "started_at": started},
        )
    except Exception as e:
        return DiagnoseCheck(
            name="chayuan_server.process",
            ok=False,
            severity="warn",
            detail=f"psutil 读不到本进程:{type(e).__name__}: {e}",
        )


def check_runtime_llama_status_for(capability: str) -> DiagnoseCheck:
    """LlamaRuntimeManager 某 capability 当前 state。

    ready    → ok
    failed   → fail (含 last_error)
    其它     → warn
    """
    name = f"runtime.llama.{capability}.status"
    try:
        from chayuan.server.model_registry.local_runtime_registry import get_registry
        mgr = get_registry().get(capability)
        st = mgr.status
    except Exception as e:
        return DiagnoseCheck(
            name=name,
            ok=False,
            severity="warn",
            detail=f"registry 读 status 异常:{type(e).__name__}: {e}",
        )

    state = st.state
    if state == "ready":
        ok, sev = True, "ok"
        detail = f"state=ready, endpoint={st.endpoint}, model={st.model_id}, pid={st.pid}"
    elif state == "failed":
        ok, sev = False, "fail"
        detail = f"state=failed, last_error={st.last_error or '<none>'}"
    else:
        ok, sev = True, "warn"
        detail = f"state={state} (未运行或过渡态)"

    return DiagnoseCheck(
        name=name,
        ok=ok,
        severity=sev,  # type: ignore[arg-type]
        detail=detail,
        context={
            "state": state,
            "endpoint": st.endpoint,
            "pid": st.pid,
            "model_id": st.model_id,
            "last_error": st.last_error,
        },
    )


# 旧名兼容 (保留是给外部老调用方;新代码用 check_runtime_llama_status_for(capability))
def check_runtime_llama_status() -> DiagnoseCheck:
    """Deprecated:Plan 3B 起改用 check_runtime_llama_status_for(capability)。
    仅返 chat 状态,保留向后兼容。"""
    return check_runtime_llama_status_for("chat")


def check_vendor_platform_detected() -> DiagnoseCheck:
    """报当前 host 的 vendor 平台 candidate 列表 + 解析后 binary 实际路径 + env 覆盖。

    主要排查:user 说"找不到 llama-server"时,这一项直接告诉:
    1) 自动检测到的候选(_platform_subdir_candidates)
    2) CHAYUAN_VENDOR_PLATFORM env 是否设了什么
    3) find_server_exe 实际命中哪条(扁平 / 子目录 / None)
    """
    import os
    from chayuan.server.model_registry.local_runtime import (
        SidecarRuntimeManager, _platform_subdir_candidates,
    )

    candidates = _platform_subdir_candidates()
    override = (os.environ.get("CHAYUAN_VENDOR_PLATFORM") or "").strip() or None

    resolved = {}
    for engine in ("llama", "whisper"):
        try:
            mgr = SidecarRuntimeManager(
                chayuan_root=Path("/tmp"),
                engine=engine,
                capability="chat" if engine == "llama" else "asr",
                port_offset=0,
            )
            exe = mgr.find_server_exe()
            resolved[engine] = str(exe) if exe else None
        except Exception as e:
            resolved[engine] = f"<error: {type(e).__name__}: {e}>"

    # llama 是必须的(chat/embedding/rerank 都靠它),没找到就 fail
    llama_ok = bool(resolved.get("llama")) and not str(resolved["llama"]).startswith("<error")
    # whisper 缺只是 warn(Mac/Linux upstream 没发,fallback 到 Python faster-whisper)
    whisper_ok = bool(resolved.get("whisper")) and not str(resolved["whisper"]).startswith("<error")

    if llama_ok and whisper_ok:
        severity: Severity = "ok"
    elif llama_ok:
        severity = "warn"  # whisper 缺,降级 fallback 可用
    else:
        severity = "fail"

    detail_parts = [
        f"candidates={candidates}",
        f"override={override!r}",
        f"llama={resolved.get('llama')}",
        f"whisper={resolved.get('whisper')}",
    ]
    return DiagnoseCheck(
        name="vendor.platform.detected",
        ok=(severity == "ok"),
        severity=severity,
        detail=" | ".join(detail_parts),
        context={
            "candidates": candidates,
            "override": override,
            "resolved": resolved,
        },
    )


def _safe_call(name: str, fn) -> DiagnoseCheck:
    """check 内部如果还能漏出异常,这里再兜一层"""
    try:
        return fn()
    except Exception as e:
        return DiagnoseCheck(
            name=name,
            ok=False,
            severity="fail",
            detail=f"check 抛异常:{type(e).__name__}: {e}",
        )
