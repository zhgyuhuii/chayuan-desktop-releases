"""运行时编排 HTTP API（``/runtime/*``）。

为前端"系统服务面板"与 ``chayuan service info`` CLI 提供同源数据：

* ``GET  /runtime/services``        所有受管服务 endpoint + 凭据（默认掩码）
* ``GET  /runtime/services/{name}`` 单个服务详情
* ``POST /runtime/services/recheck`` 重新走一遍 PortAllocator + endpoint 落库
* ``GET  /runtime/vendor``          扫描 vendor/ 目录
* ``POST /runtime/models/scan``     强制扫描本地模型目录
* ``GET  /runtime/models``          返回当前 local_index 的所有条目
* ``GET  /runtime/models/events``   SSE：本地模型增/改/删事件流（前端模型广场实时刷新）

所有写接口需要管理员权限（沿用 ``require_admin`` 依赖）；只读接口默认放行
（鉴权由全局 ``AuthMiddleware`` 控制；后端开发时 ``AUTH_REQUIRED=false``
不需要 token，生产开启后由 middleware 兜底）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from chayuan.server.model_registry.local_index import (
    LocalModelEntry,
    ScanDelta,  # noqa: F401  保留类型，供 _on_change 签名使用
    get_local_index,
    scan_once,
)


# watcher 包已在单机版重构中删除（原"全网模型目录"链路的一部分）。
# 这里保留 SSE 事件流接口，但仅推送 model.snapshot；后续如需实时增量，
# 单机版改用 file watchdog 直接订阅 local_index 目录变更即可。
def start_default_watcher() -> None:  # type: ignore[misc]
    """No-op stub。watcher 已移除；前端可手动触发 ``POST /runtime/models/scan``。"""
    return None


def subscribe(_callback) -> "callable":  # type: ignore[misc]
    """No-op stub。返回的 unsubscribe 也是 no-op。"""
    def _unsub() -> None:
        return None
    return _unsub
from chayuan.server.runtime import (
    allocate_core_ports,
    discover_vendor,
    get_runtime_info,
    known_runtimes,
    known_services,
)

logger = logging.getLogger("chayuan.api.runtime")

runtime_router = APIRouter(prefix="/runtime", tags=["Runtime / Services / Models"])


def _ep_to_payload(ep, *, reveal: bool) -> Dict[str, Any]:
    if ep is None:
        return {}
    return dict(ep) if reveal else dict(ep.masked())


def _entry_to_payload(e: LocalModelEntry) -> Dict[str, Any]:
    return e.to_dict()


# ---------------------------------------------------------------------------
# /runtime/services
# ---------------------------------------------------------------------------

@runtime_router.get("/services", summary="列出所有受管服务的运行时端点 + 凭据（默认密码掩码）")
async def list_services(reveal: bool = Query(False, description="reveal=true 返回明文密码（仅管理员）")) -> Dict[str, Any]:
    ri = get_runtime_info()
    out: List[Dict[str, Any]] = [_ep_to_payload(e, reveal=reveal) for e in ri.list_endpoints()]
    return {
        "code": 0,
        "data": {
            "updated_at": ri._data.get("updated_at"),  # noqa: SLF001 - 内部协议固定
            "services": out,
            "warning": (
                "返回字段已对密码做 **** 掩码；如需明文请加 ?reveal=true 并在生产环境慎用。"
                if not reveal else "已返回明文密码，请勿外发。"
            ),
        },
    }


@runtime_router.get("/services/{name}", summary="单个受管服务的端点详情")
async def get_service(name: str, reveal: bool = Query(False)) -> Dict[str, Any]:
    ri = get_runtime_info()
    ep = ri.get_endpoint(name)
    if ep is None:
        raise HTTPException(404, f"service not found: {name}")
    return {"code": 0, "data": _ep_to_payload(ep, reveal=reveal)}


@runtime_router.post("/services/recheck", summary="重新分配 API/配置面板端口（端口被占自动 bump）")
async def recheck_services() -> Dict[str, Any]:
    try:
        result = allocate_core_ports()
    except Exception as e:  # noqa: BLE001
        logger.exception("recheck_services failed")
        raise HTTPException(500, f"allocate_core_ports failed: {e}") from e
    return {"code": 0, "data": result.to_dict()}


# ---------------------------------------------------------------------------
# /runtime/vendor
# ---------------------------------------------------------------------------

@runtime_router.get("/vendor", summary="扫描 <CHAYUAN_ROOT>/vendor 与仓库 vendor/ 目录")
async def list_vendor() -> Dict[str, Any]:
    layout = discover_vendor()
    return {
        "code": 0,
        "data": {
            **layout.to_dict(),
            "known_services": known_services(),
            "known_runtimes": known_runtimes(),
        },
    }


# ---------------------------------------------------------------------------
# /runtime/models
# ---------------------------------------------------------------------------

@runtime_router.get("/models", summary="本地磁盘模型索引（local_models.json 的内存视图）")
async def list_local_models(
    capability: Optional[str] = Query(None, description="按 capability 过滤；支持 chat/text-embedding/...")
) -> Dict[str, Any]:
    idx = get_local_index()
    items = idx.list_entries()
    if capability:
        items = [e for e in items if e.capability == capability]
    return {
        "code": 0,
        "data": {
            "total": len(items),
            "items": [_entry_to_payload(e) for e in items],
        },
    }


@runtime_router.post("/models/scan", summary="强制扫描本地模型目录，返回新增/更新/删除差异")
async def trigger_scan() -> Dict[str, Any]:
    delta = scan_once()
    # 同时唤起 watcher（如果还没启动）
    try:
        start_default_watcher()
    except Exception as e:  # noqa: BLE001
        logger.debug("start_default_watcher noop: %s", e)
    return {
        "code": 0,
        "data": {
            "added":   [_entry_to_payload(e) for e in delta.added],
            "updated": [_entry_to_payload(e) for e in delta.updated],
            "removed": list(delta.removed),
        },
    }


@runtime_router.get(
    "/models/events",
    summary="SSE：本地模型变化事件流，前端模型广场可订阅以实时刷新",
)
async def models_events_stream(request: Request):
    """`text/event-stream` 长连接；服务侧通过 :func:`subscribe` 推送 ``ScanDelta``。

    与 SSE 客户端契约：
      - 每条事件 ``event: model.changed``，``data: {"added":[...], ...}``；
      - 每 25s 一条 ``event: ping`` 心跳，避免 nginx 60s 空闲断流。
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=128)
    loop = asyncio.get_event_loop()

    def _on_change(delta: ScanDelta) -> None:
        payload = {
            "added":   [_entry_to_payload(e) for e in delta.added],
            "updated": [_entry_to_payload(e) for e in delta.updated],
            "removed": list(delta.removed),
            "ts": int(time.time()),
        }
        try:
            loop.call_soon_threadsafe(queue.put_nowait, payload)
        except RuntimeError:
            # 客户端已断开，事件循环没了；忽略
            pass

    unsubscribe = subscribe(_on_change)
    # 确保 watcher 已经在跑
    try:
        start_default_watcher()
    except Exception as e:  # noqa: BLE001
        logger.debug("start_default_watcher noop: %s", e)

    async def _gen():
        try:
            # 立刻吐一条全量初始快照，避免前端首次连接拿不到东西
            idx = get_local_index()
            yield {
                "event": "model.snapshot",
                "data": json.dumps(
                    {"items": [_entry_to_payload(e) for e in idx.list_entries()]},
                    ensure_ascii=False,
                ),
            }
            last_ping = time.monotonic()
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=5.0)
                    yield {
                        "event": "model.changed",
                        "data": json.dumps(payload, ensure_ascii=False),
                    }
                except asyncio.TimeoutError:
                    pass
                if time.monotonic() - last_ping > 25.0:
                    yield {"event": "ping", "data": str(int(time.time()))}
                    last_ping = time.monotonic()
        finally:
            unsubscribe()

    return EventSourceResponse(_gen())


# ---------------------------------------------------------------------------
# /runtime/models/import
# ---------------------------------------------------------------------------

@runtime_router.post(
    "/models/import",
    summary="把本地任意路径上的目录或单文件软链接进 <CHAYUAN_ROOT>/models/custom/，并立即扫描入库",
)
async def import_local_model(
    path: str = Body(..., embed=True, description="要导入的本地路径"),
    name: Optional[str] = Body(None, embed=True, description="可选别名；默认取目录/文件名"),
) -> Dict[str, Any]:
    import os
    import shutil
    from pathlib import Path

    src = Path(path).expanduser()
    if not src.exists():
        raise HTTPException(400, f"路径不存在：{src}")
    from chayuan.settings import CHAYUAN_ROOT
    target_root = Path(CHAYUAN_ROOT) / "models" / "custom"
    target_root.mkdir(parents=True, exist_ok=True)

    target_name = (name or src.name).strip()
    target = target_root / target_name
    if target.exists():
        raise HTTPException(409, f"目标已存在：{target}（请改名或先删除）")

    # 优先用符号链接（不占额外磁盘）；不支持符号链接的 OS / 文件系统时退化为复制
    try:
        os.symlink(src, target)
        action = "symlink"
    except (OSError, NotImplementedError):
        if src.is_dir():
            shutil.copytree(src, target)
        else:
            shutil.copy2(src, target)
        action = "copy"

    # 立即扫描，让前端马上能看到
    delta = scan_once()
    return {
        "code": 0,
        "data": {
            "action": action,
            "target": str(target),
            "scan_delta_added": len(delta.added),
        },
    }


# ---------------------------------------------------------------------------
# /runtime/services/{name}/install — 一键安装可选服务
# ---------------------------------------------------------------------------

# 支持一键安装的服务白名单。
# Key 是服务名(supervisor.yaml 里的服务键),value 是 docker image。
# 增加新服务只需加一行,不必改路由。单机版默认无可安装服务,保留路由供未来扩展。
_INSTALLABLE_SERVICES: Dict[str, Dict[str, str]] = {}


@runtime_router.post(
    "/services/{name}/install",
    summary="一键安装可选服务 (docker pull + 写 supervisor override)",
)
async def install_service(name: str) -> Any:
    """流式输出 docker pull 进度。

    返回 SSE 事件流,每行 JSON:
        {"stage": "pull" | "compose" | "ready" | "error", "line": "...", "progress": 0-100?}

    成功后会在 ``<CHAYUAN_ROOT>/supervisor.local.override.yaml`` 中追加该服务,
    用户手动 ``chayuan service start <name>`` 即可启动。
    """
    if name not in _INSTALLABLE_SERVICES:
        raise HTTPException(400, f"该服务不支持一键安装: {name}")

    spec = _INSTALLABLE_SERVICES[name]

    async def _emit() -> Any:
        import shutil as _sh
        from chayuan.settings import CHAYUAN_ROOT

        docker_bin = _sh.which("docker")
        if not docker_bin:
            yield {
                "event": "message",
                "data": json.dumps({
                    "stage": "error",
                    "line": "未找到 docker;请先安装 Docker Desktop / Engine",
                    "fix": "https://docs.docker.com/engine/install/",
                }),
            }
            return

        # 1) docker pull (流式)
        proc = await asyncio.create_subprocess_exec(
            docker_bin, "pull", spec["image"],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip()
            yield {
                "event": "message",
                "data": json.dumps({"stage": "pull", "line": line}),
            }
        rc = await proc.wait()
        if rc != 0:
            yield {
                "event": "message",
                "data": json.dumps({
                    "stage": "error",
                    "line": f"docker pull 失败: rc={rc}",
                }),
            }
            return

        # 2) 写 supervisor.local.override.yaml
        try:
            override_path = _write_supervisor_override(name, spec, CHAYUAN_ROOT)
            yield {
                "event": "message",
                "data": json.dumps({
                    "stage": "compose",
                    "line": f"已写入 {override_path}",
                }),
            }
        except Exception as e:  # noqa: BLE001
            logger.exception("write supervisor override failed")
            yield {
                "event": "message",
                "data": json.dumps({
                    "stage": "error",
                    "line": f"写 supervisor override 失败: {e}",
                }),
            }
            return

        # 3) 完成
        yield {
            "event": "message",
            "data": json.dumps({
                "stage": "ready",
                "line": (
                    f"{name} 已就绪;"
                    f"运行 `chayuan service start {name}` 启动 (端口 "
                    f"{spec['preferred_host_port']})"
                ),
            }),
        }

    return EventSourceResponse(_emit())


def _write_supervisor_override(name: str, spec: Dict[str, str], chayuan_root: str) -> str:
    """把 ``name`` 服务写到 ``supervisor.local.override.yaml``。

    用 minimal yaml 拼接,不引 PyYAML 强依赖(顶层已用 yaml,但避免在该路径
    再 import 一次)。
    """
    import os as _os
    from pathlib import Path as _Path

    override_path = _Path(chayuan_root) / "supervisor.local.override.yaml"
    existing = override_path.read_text(encoding="utf-8") if override_path.exists() else ""

    # 已存在该服务条目则跳过(等用户主动改 yaml)
    if f"\n{name}:" in existing or existing.startswith(f"{name}:"):
        return str(override_path)

    block = (
        f"# auto-generated by /runtime/services/{name}/install\n"
        f"{name}:\n"
        f"  kind: docker\n"
        f"  image: {spec['image']}\n"
        f"  ports:\n"
        f"    - host: ${{CHAYUAN_RUNTIME_{name.upper()}_PORT:-{spec['preferred_host_port']}}}\n"
        f"      container: {spec['container_port']}\n"
        f"  volumes:\n"
        f"    - source: ${{CHAYUAN_DATA}}/vendor-data/{name}/data\n"
        f"      target: {spec['vol_data']}\n"
        f"    - source: ${{CHAYUAN_DATA}}/vendor-data/{name}/log\n"
        f"      target: {spec['vol_log']}\n"
        f"  preferred_port: {spec['preferred_host_port']}\n"
    )
    new_content = (existing.rstrip() + "\n\n" + block) if existing else block
    override_path.parent.mkdir(parents=True, exist_ok=True)
    override_path.write_text(new_content, encoding="utf-8")
    try:
        _os.chmod(override_path, 0o600)
    except OSError:
        pass
    return str(override_path)


# ───────────────────────── /runtime/llama/* ─────────────────────────
# 本地 LLM runtime (llama-server.exe) 控制面。详见
# docs/superpowers/specs/2026-05-15-local-llm-runtime-integration-design.md §4.3。


def _llama_manager():
    """惰性 import 避免循环依赖"""
    from chayuan.server.model_registry.local_runtime import get_manager
    return get_manager()


def _ok(data) -> Dict[str, Any]:
    return {"code": 0, "data": data}


@runtime_router.get("/llama/status")
def llama_status() -> Dict[str, Any]:
    return _ok(_llama_manager().status.to_dict())


@runtime_router.post("/llama/start")
async def llama_start(body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    model_id = body.get("model_id") if isinstance(body, dict) else None
    status = await _llama_manager().start(model_id=model_id)
    return _ok(status.to_dict())


@runtime_router.post("/llama/stop")
async def llama_stop() -> Dict[str, Any]:
    await _llama_manager().stop()
    return _ok(_llama_manager().status.to_dict())


@runtime_router.post("/llama/restart")
async def llama_restart(body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    model_id = body.get("model_id") if isinstance(body, dict) else None
    status = await _llama_manager().restart(model_id=model_id)
    return _ok(status.to_dict())


@runtime_router.get("/llama/config")
def llama_config_get() -> Dict[str, Any]:
    import dataclasses as _dc
    return _ok(_dc.asdict(_llama_manager().settings))


@runtime_router.post("/llama/config")
def llama_config_post(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """部分更新设置。不立即重启,需要 /runtime/llama/restart 让端口 / API key 生效"""
    if not isinstance(body, dict) or not body:
        raise HTTPException(status_code=400, detail="body 必须是非空 dict")
    # 端口合法性校验:类型 + 范围
    if "port" in body and body["port"] is not None:
        try:
            port_int = int(body["port"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="port 必须是整数")
        if not (1024 <= port_int <= 65535):
            raise HTTPException(status_code=422, detail="port 必须在 1024-65535")
    new_settings = _llama_manager().set_config(body)
    import dataclasses as _dc
    return _ok(_dc.asdict(new_settings))


@runtime_router.get("/llama/install-info")
def llama_install_info() -> Dict[str, Any]:
    """透出关键路径给设置页"模型存放路径" section 显示。

    新字段(2026-05-16):
      - ``is_lite_build``  当前安装包是否轻量版(installer 没嵌全部 cap)
      - ``bundled_caps``   随包嵌入的 capability 短名列表(空 = 全空 / 即 lite 模式
                           但用户后续手动补装过)
      - ``missing_caps``   FULL_CAPS - bundled_caps,前端可据此提示"需先下载"
    """
    from pathlib import Path
    m = _llama_manager()
    exe = m.find_llama_server_exe()  # 受 _INSTALL_SERVICES_DIRS monkeypatch 影响,测试可注入
    version = None
    if exe is not None and exe.parent.is_dir():
        v_path = exe.parent / "VERSION"
        if v_path.is_file():
            version = v_path.read_text(encoding="utf-8").splitlines()[0].strip()

    # lite/full 判定:扫 bundled_models 根目录的 cap 子目录;
    # 出现 chat/image 子目录 = full build。lite 也允许后续用户手动补装,届时跟 full 视觉等同。
    bundled_root = m.chayuan_root / "models" / "bundled"
    # 兼容 Tauri install_dir/bundled_models/(packaging 阶段拷过来的随包模型)
    alt_roots: List[Path] = []
    try:
        from chayuan.server.model_registry.local_runtime import _default_install_services_dirs
        for svc_dir in _default_install_services_dirs():
            alt_roots.append(svc_dir.parent / "bundled_models")
    except Exception:  # noqa: BLE001
        pass

    bundled_caps: List[str] = []
    for root in [bundled_root] + alt_roots:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if entry.is_dir() and not entry.name.startswith(".") and entry.name not in bundled_caps:
                # 只算非空目录(.gitkeep 不算真有模型)
                if any(p.is_file() and p.name != ".gitkeep" for p in entry.rglob("*")):
                    bundled_caps.append(entry.name)

    # 单一真源:packaging/bundle_manifest.py
    full_caps: tuple = ()
    try:
        import sys as _sys
        from pathlib import Path as _P
        pkg = _P(__file__).resolve().parents[3] / "packaging"
        _sys.path.insert(0, str(pkg))
        try:
            from bundle_manifest import FULL_CAPS as _FULL  # type: ignore[import-not-found]
            full_caps = tuple(_FULL)
        finally:
            _sys.path.pop(0)
    except Exception:  # noqa: BLE001
        # 兜底:6 项硬编码,避免老 checkout 缺 bundle_manifest 时整接口炸
        full_caps = ("chat", "embedding", "rerank", "asr", "ocr", "image")

    missing = [c for c in full_caps if c not in bundled_caps]
    # is_lite_build:严格定义为「不嵌 chat」(chat 是大头);
    # 即使用户后续手装了 chat 也保留 is_lite_build=true 直观告知"原始 installer 是 lite"
    # —— 但有 chat 嵌入即视为 full(用户拿到的就是 full installer 或他主动等价)。
    is_lite_build = "chat" not in bundled_caps

    return _ok({
        "chayuan_root": str(m.chayuan_root),
        "models_root": str(m.chayuan_root / "models"),
        "bundled_models_root": str(m.chayuan_root / "models" / "bundled"),
        "llama_server_exe": str(exe) if exe else None,
        "build_version": version,
        "is_lite_build": is_lite_build,
        "bundled_caps": bundled_caps,
        "missing_caps": missing,
    })


@runtime_router.get("/diagnose")
def runtime_diagnose() -> Dict[str, Any]:
    """跑所有本地 runtime 健康检查,返报告 JSON。"""
    from chayuan.server.diagnose import run_all_checks
    report = run_all_checks()
    return _ok(report.to_dict())


# ─────── /runtime/llama/{capability}/* — Plan 3B 多 capability ───────

_VALID_CAPABILITIES = {"chat", "embedding", "rerank", "asr", "image-embedding"}


def _validate_capability(capability: str) -> None:
    if capability not in _VALID_CAPABILITIES:
        raise HTTPException(status_code=400, detail=f"unknown capability: {capability!r}")


def _llama_registry():
    """惰性 import 避免循环依赖"""
    from chayuan.server.model_registry.local_runtime_registry import get_registry
    return get_registry()


@runtime_router.get("/llama/registry")
def llama_registry_status() -> Dict[str, Any]:
    """一次拿 3 个 capability 状态;前端 LocalRuntimePanel mount 用。"""
    reg = _llama_registry()
    return _ok({cap: st.to_dict() for cap, st in reg.all_statuses().items()})


@runtime_router.get("/llama/{capability}/status")
def llama_capability_status(capability: str) -> Dict[str, Any]:
    _validate_capability(capability)
    return _ok(_llama_registry().get(capability).status.to_dict())


@runtime_router.post("/llama/{capability}/start")
async def llama_capability_start(
    capability: str, body: Dict[str, Any] = Body(default={})
) -> Dict[str, Any]:
    _validate_capability(capability)
    model_id = body.get("model_id") if isinstance(body, dict) else None
    status = await _llama_registry().get(capability).start(model_id=model_id)
    return _ok(status.to_dict())


@runtime_router.post("/llama/{capability}/stop")
async def llama_capability_stop(capability: str) -> Dict[str, Any]:
    _validate_capability(capability)
    mgr = _llama_registry().get(capability)
    await mgr.stop()
    return _ok(mgr.status.to_dict())


@runtime_router.post("/llama/{capability}/restart")
async def llama_capability_restart(
    capability: str, body: Dict[str, Any] = Body(default={})
) -> Dict[str, Any]:
    _validate_capability(capability)
    model_id = body.get("model_id") if isinstance(body, dict) else None
    status = await _llama_registry().get(capability).restart(model_id=model_id)
    return _ok(status.to_dict())


# ─────────────────────── PyTorch 检测与安装 ───────────────────────


@runtime_router.get(
    "/pytorch/status",
    summary="PyTorch 安装状态 + GPU 检测",
)
def pytorch_status() -> Dict[str, Any]:
    """返回当前 env 的 torch / torchvision 版本、import 链是否健康、是否有
    NVIDIA GPU、推荐安装 variant(auto 用)。前端「设置 → 本地模型服务」的
    PyTorch 行据此渲染按钮(已就绪/CPU 安装/CUDA 安装)。"""
    from chayuan.server.runtime.pytorch_installer import detect_status
    return _ok(detect_status().to_dict())


@runtime_router.get(
    "/pytorch/scan",
    summary="扫描 py_packages/ 看用户手动放好的 torch 在不在、版本、CPU/CUDA",
)
def pytorch_scan() -> Dict[str, Any]:
    """对应「模型下载对话框的扫描挂载」体验 —— 用户把手动下好 / 解压好的 torch
    放进 ``<CHAYUAN_ROOT>/py_packages/`` 后,点「扫描」让本接口识别。

    纯文件探测(不 import torch):读 ``py_packages/torch/version.py`` 拿
    ``__version__`` + ``cuda`` 字段判断 CPU / CUDA 版。返回字段见
    :func:`pytorch_installer.scan_installed_torch`,其中 ``target_dir`` 就是
    用户应当放置 torch 的绝对路径,前端可直接展示 + 提供「打开文件夹」。
    """
    from chayuan.server.runtime.pytorch_installer import scan_installed_torch
    return _ok(scan_installed_torch())


@runtime_router.get(
    "/pytorch/locations",
    summary="跨目录扫 torch:CPU 版 / GPU 版各自是否存在 + 当前选用配置",
)
def pytorch_locations() -> Dict[str, Any]:
    """扫多个候选目录(py_packages/、用户指定目录、系统 Python site-packages),
    汇总「CPU 版」「GPU(CUDA)版」torch 各自是否存在,以及当前 torch 选用
    配置(auto / 指定目录 / 不使用)与生效目录。

    前端「PyTorch」行据此:
      - 同时检测到 CPU + GPU 两个版本 → 让用户选用哪个;
      - 也支持选「不使用 PyTorch」(image-embedding 等会不可用)。
    返回字段见 :func:`pytorch_installer.scan_torch_locations`。
    """
    from chayuan.server.runtime.pytorch_installer import scan_torch_locations
    return _ok(scan_torch_locations())


@runtime_router.post(
    "/pytorch/validate-dir",
    summary="验证用户指定的目录里有没有合法的 torch(可采纳为外部已装 torch)",
)
def pytorch_validate_dir(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """body: ``{"path": "<目录绝对路径>"}``。

    用户可复制粘贴一个路径 / 选择一个目录,指向「已经装好 torch 的目录」
    (系统 Python 的 site-packages,或之前解压过 torch wheel 的目录)。
    本接口纯文件探测目录里是否有合法 torch,返回版本 / CPU-CUDA 判定,
    **不写配置**(写配置走 POST /runtime/pytorch/select with mode=path)。
    """
    from chayuan.server.runtime.pytorch_installer import validate_torch_dir
    path = (body or {}).get("path")
    if not isinstance(path, str) or not path.strip():
        raise HTTPException(status_code=400, detail="`path` 必填且为非空字符串")
    return _ok(validate_torch_dir(path.strip()))


@runtime_router.get(
    "/pytorch/select",
    summary="读当前 torch 选用配置(auto / 指定目录 / 不使用)",
)
def pytorch_select_get() -> Dict[str, Any]:
    from chayuan.server.runtime.pytorch_installer import (
        get_torch_selection, resolve_active_torch_dir,
    )
    sel = get_torch_selection()
    return _ok({"selection": sel, "active_dir": resolve_active_torch_dir(selection=sel)})


@runtime_router.post(
    "/pytorch/select",
    summary="设置 torch 选用配置:auto / 指定外部目录 / 不使用 PyTorch",
)
def pytorch_select_set(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """body: ``{"mode": "auto"|"path"|"disabled", "path": "<目录>"?, "prefer": "cpu"|"cuda"?}``。

    - ``mode=auto``     :自动用扫到的 torch;``prefer`` 在 CPU + GPU 两版都装时
      决定优先用哪个(缺省优先 GPU)。
    - ``mode=path``     :强制用 ``path`` 指定的外部已装 torch 目录(写前会校验)。
    - ``mode=disabled`` :不使用 PyTorch —— image-embedding 等依赖功能不可用。

    sidecar(image-embedding / infinity)下次启动按此决定 PYTHONPATH。
    """
    from chayuan.server.runtime.pytorch_installer import (
        resolve_active_torch_dir, set_torch_selection,
    )
    mode = (body or {}).get("mode")
    if not isinstance(mode, str):
        raise HTTPException(status_code=400, detail="`mode` 必填(auto/path/disabled)")
    path = (body or {}).get("path")
    prefer = (body or {}).get("prefer")
    try:
        cfg = set_torch_selection(
            mode=mode,
            path=str(path) if path else None,
            prefer=str(prefer) if prefer else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _ok({"selection": cfg, "active_dir": resolve_active_torch_dir(selection=cfg)})


@runtime_router.post(
    "/pytorch/install",
    summary="启动 PyTorch 安装(异步),返 task_id 供轮询进度",
)
def pytorch_install(
    body: Dict[str, Any] = Body(default={}),
) -> Dict[str, Any]:
    """body: ``{"variant": "cpu" | "cu118" | "cu121" | "cu124" | "cu126" | "auto"}``。

    异步跑 ``pip install --upgrade torch torchvision --index-url <wheel-url>``;
    日志通过 ``GET /runtime/pytorch/install/{task_id}`` 拉。
    复用 install_task_manager 的现有 task 基础设施。
    """
    from chayuan.server.runtime.pytorch_installer import start_install
    variant = (body or {}).get("variant", "auto")
    if not isinstance(variant, str):
        raise HTTPException(status_code=400, detail="`variant` must be a string")
    # prefer_offline=true(默认)— 有内置 wheel 优先用,无网也能装上
    # 用户显式要"强制在线"(更新 wheel 版本)时传 prefer_offline=false
    prefer_offline = bool((body or {}).get("prefer_offline", True))
    # force=true(重装)— 解压前清空 py_packages/,去掉残留旧版本,避免
    # 新 torch 叠在旧 torchvision 上版本对不上。UI 安装/重装按钮都传 true。
    force = bool((body or {}).get("force", False))
    try:
        task = start_install(variant, prefer_offline=prefer_offline, force=force)  # type: ignore[arg-type]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _ok(task.to_dict())


@runtime_router.get(
    "/pytorch/install/{task_id}",
    summary="查 PyTorch 安装进度 / 日志",
)
def pytorch_install_status(task_id: str) -> Dict[str, Any]:
    from chayuan.server.config_panel.install_task_manager import get_install_manager
    task = get_install_manager().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    return _ok(task.to_dict())


# ─────────────────────── auto-start 开关 ───────────────────────


@runtime_router.get(
    "/llama/auto-start",
    summary="读 5 个 capability + rapidocr 的自动启动开关",
)
def llama_auto_start_list() -> Dict[str, Any]:
    """返回 {capability: bool} — 控制 chayuan-server 启动时是否自动拉起。
    UI 切换不影响当前进程,只决定下次 server 启动行为。"""
    from chayuan.server.runtime.auto_start_store import list_all
    return _ok(list_all())


@runtime_router.post(
    "/llama/auto-start",
    summary="设置某 capability 的自动启动开关",
)
def llama_auto_start_set(
    body: Dict[str, Any] = Body(...),
) -> Dict[str, Any]:
    """body: ``{"capability": "chat", "enabled": true}``。返回更新后的全集字典。"""
    from chayuan.server.runtime.auto_start_store import KNOWN_KEYS, list_all, set_ as _set
    cap = body.get("capability") if isinstance(body, dict) else None
    enabled = body.get("enabled") if isinstance(body, dict) else None
    if not isinstance(cap, str) or cap not in KNOWN_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown capability: {cap!r};expected one of {KNOWN_KEYS}",
        )
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="`enabled` must be bool")
    _set(cap, enabled)
    return _ok(list_all())


# ─────────────────────── OCR 健康检查 ───────────────────────


@runtime_router.get("/ocr/health", summary="OCR (RapidOCR) 健康检查")
def ocr_health() -> Dict[str, Any]:
    """OCR 走独立 sidecar(rapidocr_server.py uvicorn 跑 18380),不在 chayuan-server
    进程内。

    检查项(全 ready 才返回 ready):
      0. **sidecar 真在 18380 监听**(否则前面两项都通过也不算 ready)
      1. RapidOCR python 包是否可 import(chayuan-server 进程内能 import 才
         意味着 sidecar 也能加载,这里 fail fast 给出清晰 reason)
      2. ``vendor/bundled_models/ocr/RapidOCR/`` 下 det/rec/cls 三件套 ONNX 是否齐全
    任何一项缺失返回 state=failed + reason,前端 footer 灯亮红。

    历史:这条曾经把 OCR 当 chayuan-server 进程内 onnxruntime 用(无端口),
    检查只看 pkg + 模型 → sidecar 没起来时仍返 ready,footer 误绿。改成 OCR
    sidecar 化后(rapidocr_server.py + rapidocr_lifecycle.py),必须先确认
    端口监听。
    """
    from pathlib import Path

    state = "ready"
    reason: Optional[str] = None
    detail: Dict[str, Any] = {}

    # 0) sidecar 监听检测 —— 真源。其它检查再绿,这里不通也是没启动。
    try:
        from chayuan.server.modality.rapidocr_lifecycle import (
            ocr_sidecar_status as _ocr_status,
        )
        status = _ocr_status() or {}
        listening = bool(status.get("listening"))
        detail["sidecar_state"] = status.get("state")
        detail["sidecar_port"] = status.get("port")
        detail["sidecar_pid"] = status.get("pid")
        detail["sidecar_listening"] = listening
        if not listening:
            state = "failed"
            reason = (
                "RapidOCR sidecar 未在 "
                f"{status.get('port', 18380)} 端口监听 —— "
                "「设置 → 本地模型服务 → RapidOCR」点重新启动,或看日志 "
                f"{status.get('log_path') or '%TEMP%/chayuan_rapidocr.log'}"
            )
    except Exception as e:  # noqa: BLE001
        state = "failed"
        reason = f"sidecar 状态查询失败: {e}"
        detail["sidecar_state"] = "unknown"

    # 1) python 包(仅在 sidecar 已 ok 时继续往下,避免一连串 fail 把真错盖掉)
    if state == "ready":
        try:
            import rapidocr_onnxruntime as _rapidocr  # noqa: F401
            detail["rapidocr_pkg"] = "ok"
        except Exception as e:  # noqa: BLE001
            state = "failed"
            reason = f"rapidocr_onnxruntime 不可 import: {e}"
            detail["rapidocr_pkg"] = "missing"

    # 2) bundled model 三件套(找一个候选路径就算 OK,跟 install-bundled-models 落点对齐)
    if state == "ready":
        try:
            from chayuan.server.model_registry.local_runtime import _default_install_services_dirs
            # services 目录的父级就是 vendor 根
            candidates: List[Path] = []
            for svc_dir in _default_install_services_dirs():
                if not svc_dir or not isinstance(svc_dir, Path):
                    continue
                vendor_root = svc_dir.parent  # services/ 的父级
                candidates.append(vendor_root / "bundled_models" / "ocr" / "RapidOCR")
            needed = [
                "PP-OCRv4/ch_PP-OCRv4_det_infer.onnx",
                "PP-OCRv4/ch_PP-OCRv4_rec_infer.onnx",
                "PP-OCRv3/ch_ppocr_mobile_v2.0_cls_train.onnx",
            ]
            hit_dir: Optional[Path] = None
            for c in candidates:
                if all((c / rel).is_file() for rel in needed):
                    hit_dir = c
                    break
            if hit_dir is None:
                state = "failed"
                reason = "OCR 模型 ONNX 缺失;运行 scripts/install-bundled-models.{ps1,sh} -Only ocr"
                detail["models"] = "missing"
                detail["scanned"] = [str(c) for c in candidates]
            else:
                detail["models"] = "ok"
                detail["models_dir"] = str(hit_dir)
        except Exception as e:  # noqa: BLE001
            state = "failed"
            reason = f"模型路径探测失败: {e}"

    return _ok({"state": state, "reason": reason, "detail": detail})


# ─────────────────── 运行时服务(sidecar 二进制)下载 ───────────────────
# 「运行时服务双保险」的应用内补回侧:llama-server / whisper-server 既打包进
# 完整安装包,又支持缺失时按平台一键下载。详见 runtime/install_service.py。
#
# 路径用 /runtime/service-binaries/* 独立命名空间 —— 刻意不挂在
# /runtime/services/* 下:那里已有 GET /runtime/services/{name},会把
# /runtime/services/status 这类静态段当成 name 参数吞掉(FastAPI 按注册顺序
# 匹配,{name} 先注册)。


@runtime_router.get(
    "/service-binaries/status",
    summary="当前平台 llama-server / whisper-server 就绪状态",
)
def service_binaries_status() -> Dict[str, Any]:
    """前端「本地模型服务 → 运行时服务」区据此渲染:已内置 / 缺失 / 可下载。"""
    from chayuan.server.runtime.install_service import service_status

    return _ok(service_status())


@runtime_router.post(
    "/service-binaries/install",
    summary="按当前平台下载 llama-server / whisper-server 补回",
)
def service_binaries_install(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """body: ``{"engine": "llama-server" | "whisper-server", "platform": "win-x64"(可选)}``。

    异步跑下载 + 解压 + 校验,返回 job;前端轮询
    ``GET /runtime/service-binaries/install/{job_id}`` 看进度。
    平台不传 = 后端探测当前 host。
    """
    from chayuan.server.runtime.install_service import get_install_service_manager

    engine = str((body or {}).get("engine") or "").strip()
    platform = (body or {}).get("platform")
    platform = str(platform).strip() if platform else None
    try:
        job = get_install_service_manager().start(engine=engine, platform=platform)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _ok(job.to_dict())


@runtime_router.get(
    "/service-binaries/install/{job_id}",
    summary="查运行时服务下载任务进度 / 日志",
)
def service_binaries_install_status(job_id: str) -> Dict[str, Any]:
    from chayuan.server.runtime.install_service import get_install_service_manager

    job = get_install_service_manager().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job_id {job_id!r}")
    return _ok(job.to_dict())


@runtime_router.post(
    "/service-binaries/install/{job_id}/cancel",
    summary="取消运行时服务下载任务",
)
def service_binaries_install_cancel(job_id: str) -> Dict[str, Any]:
    from chayuan.server.runtime.install_service import get_install_service_manager

    try:
        job = get_install_service_manager().cancel(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown job_id {job_id!r}")
    return _ok(job.to_dict())


__all__ = ["runtime_router"]
