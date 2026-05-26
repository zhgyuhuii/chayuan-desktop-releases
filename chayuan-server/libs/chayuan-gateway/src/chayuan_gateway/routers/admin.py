"""Admin endpoints used by the UI/CLI to add/import/enable/disable models.

These mirror the `chayuan model …` CLI commands so the frontend doesn't have
to shell out.

Also exposes ``/v1/admin/doctor`` — a system self-check endpoint that wraps
``chayuan_preflight.run_all`` so the desktop AiPlatformPanel and the CLI
share a single source of truth for "我的机器能跑这个 AI 平台吗"。
"""
from __future__ import annotations

import asyncio
import json
import logging
import platform
import sys
import threading
import time
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from chayuan_core.events import TOPIC_MODEL_DOWNLOAD, get_bus
from chayuan_gateway.deps import get_repo
from chayuan_gateway.services.model_locator import ModelLocation, locate as _locate_model
from chayuan_modelmgr import (
    RecommendedModel,
    get_default_for_capability,
    get_recommended,
    import_model,
    list_capabilities,
    pull,
)
from chayuan_registry import ModelRepository

logger = logging.getLogger("chayuan_gateway.admin")

router = APIRouter(prefix="/v1/admin", tags=["admin"])

_DOWNLOAD_TASKS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _trigger_post_download_scan(repo_name: str) -> None:
    """下载完成后触发模型库 rescan + 框架自动接入。

    单点钩子让 chayuan-server 的 ``LocalModelIndex`` 立刻发现新文件，
    再让 ``chayuan-discovery`` 把它和 runtime 配上号；前端 SSE 订阅会
    通过 ``TOPIC_MODEL_ADDED`` 收到通知，自动刷新模型库。

    所有依赖 best-effort import，缺一个不影响下载本身的成功状态。
    """
    try:
        # 1) chayuan-server 的本地索引重扫
        from chayuan.server.model_registry.local_index import (  # type: ignore
            get_local_index,
        )
        idx = get_local_index()
        if idx is not None and hasattr(idx, "scan_once"):
            idx.scan_once()
    except Exception as e:  # noqa: BLE001
        logger.debug("[post-download] local_index rescan failed: %r", e)

    try:
        # 2) chayuan-discovery 重新跑一次规则匹配
        from chayuan_discovery import discover_now  # type: ignore
        discover_now()
    except Exception as e:  # noqa: BLE001
        logger.debug("[post-download] discovery rerun failed: %r", e)

    try:
        # 3) 配上 runtime 后立即把发现结果 broadcast 到事件总线
        get_bus().publish(TOPIC_MODEL_DOWNLOAD, {
            "repo": repo_name, "state": "rescanned", "ts": time.time(),
        })
    except Exception:
        pass


@router.post("/models/pull")
def pull_model(payload: dict = Body(...)):
    """启动一次模型下载（异步），返回 ``task_id``。

    * 进度通过 ``TOPIC_MODEL_DOWNLOAD`` 事件推到 ``EventBus``，前端可走
      ``GET /v1/admin/models/pull/{task_id}/stream`` 订阅 SSE。
    * 完成后自动 ``LocalModelIndex.scan_once`` + ``discovery.discover_now``，
      再发一次 ``TOPIC_MODEL_DOWNLOAD state=rescanned``，前端模型库列表能
      立刻刷新（并自动配上 runtime 适配器）。
    """
    repo_name = payload.get("repo")
    if not repo_name:
        raise HTTPException(400, "repo required")
    category = payload.get("category")
    mirror = payload.get("mirror")
    # 同一个 repo 可能反复点击；用 ``task_id`` 唯一化避免覆盖前一次状态
    task_id = f"{repo_name}#{uuid.uuid4().hex[:8]}"
    with _LOCK:
        _DOWNLOAD_TASKS[task_id] = {
            "state": "running", "repo": repo_name, "started": time.time(),
        }

    def _bg() -> None:
        try:
            res = pull(repo_name, category=category, mirror=mirror)
            with _LOCK:
                _DOWNLOAD_TASKS[task_id] = {
                    "state": "done", "repo": repo_name,
                    "dest": str(res.dest), "bytes_total": res.bytes_total,
                    "ended": time.time(),
                }
            # 关键：下载完成后自动重新扫描 + discovery，让模型在前端"立刻可用"
            _trigger_post_download_scan(repo_name)
        except Exception as e:
            logger.warning("[pull] %s failed: %r", repo_name, e)
            with _LOCK:
                _DOWNLOAD_TASKS[task_id] = {
                    "state": "failed", "repo": repo_name, "error": str(e),
                    "ended": time.time(),
                }
            try:
                get_bus().publish(TOPIC_MODEL_DOWNLOAD, {
                    "repo": repo_name, "state": "error", "message": str(e),
                    "ts": time.time(),
                })
            except Exception:
                pass

    threading.Thread(target=_bg, daemon=True).start()
    return {"task_id": task_id, "state": "queued", "repo": repo_name}


@router.get("/models/pull/{task_id}")
def pull_status(task_id: str):
    with _LOCK:
        t = _DOWNLOAD_TASKS.get(task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    return t


@router.get("/models/pull/{task_id}/stream")
async def pull_stream(task_id: str, request: Request) -> StreamingResponse:
    """以 SSE 方式订阅一次下载的进度。

    每一帧形如::

        data: {"task_id":"...","repo":"...","state":"running","percent":42.7,
               "bytes_done":...,"bytes_total":...,"filename":"..."}

    最后一帧 ``state=done`` 或 ``state=error``，再跟一行 ``data: [DONE]``。

    实现：``ProgressSink`` 已经把每个进度事件推到 ``TOPIC_MODEL_DOWNLOAD``，
    本 endpoint 订阅事件总线、按 ``repo`` 过滤、转 OpenAI 风格 SSE。
    """
    with _LOCK:
        snapshot = _DOWNLOAD_TASKS.get(task_id)
    if snapshot is None:
        raise HTTPException(404, "task not found")
    repo_name = snapshot.get("repo") or ""

    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    loop = asyncio.get_event_loop()

    def _on_event(ev) -> None:
        # ProgressEvent.to_dict 已 publish，但 chayuan-core EventBus 的事件可能
        # 是 chayuan_core.events.Event 包装；统一转成 dict
        try:
            payload = getattr(ev, "data", None)
            if payload is None and hasattr(ev, "to_dict"):
                payload = ev.to_dict().get("data") or ev.to_dict()
            if payload is None:
                payload = ev if isinstance(ev, dict) else {}
            if not isinstance(payload, dict):
                return
            if repo_name and payload.get("repo") != repo_name:
                return
            loop.call_soon_threadsafe(queue.put_nowait, payload)
        except Exception:
            pass

    bus = get_bus()
    bus.subscribe(_on_event, topics=[TOPIC_MODEL_DOWNLOAD])

    async def _gen() -> AsyncIterator[bytes]:
        try:
            # 第一帧：当前状态快照（兼容刚刚已经完成的 task）
            first = {**snapshot, "task_id": task_id}
            yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n".encode()
            terminal = first.get("state") in ("done", "failed", "error")
            while not terminal:
                if await request.is_disconnected():
                    return
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    payload = {**payload, "task_id": task_id}
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
                    if payload.get("state") in ("done", "rescanned", "error", "failed"):
                        terminal = True
                except asyncio.TimeoutError:
                    yield b": ping\n\n"  # SSE comment as keepalive
        finally:
            try:
                bus.unsubscribe(_on_event)
            except Exception:
                pass
            yield b"data: [DONE]\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/models/import")
def import_local(payload: dict = Body(...)):
    src = payload.get("src")
    if not src:
        raise HTTPException(400, "src required")
    dest, meta = import_model(
        src,
        repo=payload.get("repo"),
        category=payload.get("category"),
        move=bool(payload.get("move", False)),
        hardlink=bool(payload.get("hardlink", False)),
    )
    return {"dest": str(dest), "meta": meta.to_payload() if meta else None}


@router.post("/models/{model_id:path}/enable")
def enable(model_id: str, repo: ModelRepository = Depends(get_repo)):
    if not repo.set_enabled(model_id, True):
        raise HTTPException(404, "model not found")
    return {"ok": True}


@router.post("/models/{model_id:path}/disable")
def disable(model_id: str, repo: ModelRepository = Depends(get_repo)):
    if not repo.set_enabled(model_id, False):
        raise HTTPException(404, "model not found")
    return {"ok": True}


@router.delete("/models/{model_id:path}")
def remove(model_id: str, repo: ModelRepository = Depends(get_repo)):
    if not repo.hard_remove(model_id):
        raise HTTPException(404, "model not found")
    return {"ok": True}


@router.post("/models/default")
def set_default(payload: dict = Body(...), repo: ModelRepository = Depends(get_repo)):
    cat, mid = payload.get("category"), payload.get("model")
    if not cat or not mid:
        raise HTTPException(400, "category and model required")
    if not repo.set_default(cat, mid):
        raise HTTPException(404, "model/category mismatch")
    return {"ok": True}


# -- doctor / self-check ------------------------------------------------------
#
# 这一组接口的设计原则是：
#   1) 任何依赖（chayuan_preflight, chayuan_runtime, chayuan-server runtime_info）
#      都做 best-effort import；缺包/缺数据时仍然返回结构化结果而不是 500，方便
#      前端面板"看到底缺什么"。
#   2) 报告分三层：preflight（OS/AV/端口/GPU）+ runtime（vendor 子进程探针）+
#      adapter（每个 11 类适配器是否能 ping 通），合一为一份 JSON。
#   3) 修复动作放在 ``/doctor/fix/{check}``，目前只返回"如何修"的人类指引；
#      未来 chayuan_preflight.fixers 实现自动修复时再升级。
# -----------------------------------------------------------------------------


def _runtime_endpoints_summary() -> dict:
    """读 chayuan-server 的 runtime.json，把 vendor 子进程的 host/port/状态汇总。

    chayuan-server 不存在或 runtime.json 还没生成时返回空 dict（不是错误）。
    """
    try:
        from chayuan.server.runtime.runtime_info import get_runtime_info  # type: ignore
    except Exception:
        return {}
    try:
        info = get_runtime_info()
    except Exception as e:  # noqa: BLE001
        return {"_error": repr(e)}
    services = (info._data or {}).get("services") or {}  # type: ignore[attr-defined]
    out: dict[str, dict] = {}
    for name, raw in services.items():
        if not isinstance(raw, dict):
            continue
        ep = info.get_endpoint(name)
        masked = ep.masked() if ep else {}
        out[name] = {
            "host": masked.get("host"),
            "port": masked.get("port"),
            "url":  masked.get("url"),
            "kind": masked.get("kind"),
            "status": "configured",
        }
    return out


def _adapter_probes() -> list[dict]:
    """对 chayuan_runtime 注册的每个适配器做 1 次轻量级 ping。"""
    out: list[dict] = []
    try:
        from chayuan_runtime.registry import get_registry
    except Exception:
        return out
    try:
        reg = get_registry()
    except Exception as e:  # noqa: BLE001
        return [{"name": "<registry>", "ok": False, "detail": repr(e)}]
    for ad in reg.all():
        rec: dict = {
            "name": ad.name,
            "base_url": getattr(ad, "base_url", "") or "",
            "mock": bool(getattr(ad, "mock", True)),
            # subprocess 类适配器（whisper.cpp / piper）没有 HTTP 端点；前端面板靠这个字段
            # 显示成"本地子进程"而不是"未连通"。
            "kind": "subprocess" if getattr(ad, "is_subprocess", False) else "http",
        }
        # 优先用各 adapter 自己的 health_url（``/api/tags`` 之于 ollama 等）；
        # 没声明的回退到 base_url；subprocess 类适配器直接跳过。
        probe_url = ""
        try:
            probe_url = ad.health_url() if hasattr(ad, "health_url") else rec["base_url"]
        except Exception:  # noqa: BLE001
            probe_url = rec["base_url"]
        rec["probe_url"] = probe_url

        if rec["mock"]:
            rec["ok"] = True
            rec["detail"] = "mock-mode"
            out.append(rec)
            continue
        if rec["kind"] == "subprocess":
            rec["ok"] = True   # subprocess 适配器只看二进制存在，doctor 不主动起进程
            rec["detail"] = "subprocess adapter (no HTTP probe)"
            out.append(rec)
            continue
        if not probe_url:
            rec["ok"] = False
            rec["detail"] = "no base_url configured"
            out.append(rec)
            continue
        try:
            import httpx
            with httpx.Client(timeout=httpx.Timeout(2.0)) as c:
                # 用 GET 而不是 HEAD：很多本地推理服务（ollama/comfyui）不实现 HEAD。
                r = c.get(probe_url)
                rec["ok"] = r.status_code < 500
                rec["status_code"] = r.status_code
        except Exception as e:  # noqa: BLE001
            rec["ok"] = False
            rec["detail"] = repr(e)
        out.append(rec)
    return out


@router.get("/doctor")
def doctor(*, with_adapters: bool = True, with_runtime: bool = True) -> dict:
    """系统自检报告 —— 给桌面/CLI 用一份。

    Query 参数：
    * ``with_adapters`` (默认 ``true``)：是否对 11 个 adapter 做 ping
    * ``with_runtime`` (默认 ``true``)：是否汇总 ``runtime.json`` 中的 vendor 端点

    返回 schema（节选）::

        {
          "host": {"os": "linux", "python": "3.12.x", "machine": "x86_64"},
          "preflight": {"summary": {"fatal":0,"warn":1,"ok":7}, "checks":[...]},
          "runtime":   {"postgres": {...}, "redis": {...}, ...},
          "adapters":  [{"name":"ollama", "ok":true, "base_url":"http://..."}],
        }
    """
    report: dict = {
        "host": {
            "os":      sys.platform,
            "python":  platform.python_version(),
            "machine": platform.machine(),
            "system":  platform.system(),
        },
        "preflight": None,
        "runtime":   None,
        "adapters":  None,
    }

    try:
        from chayuan_preflight import run_all
        rep = run_all()
        report["preflight"] = rep.to_dict()
    except Exception as e:  # noqa: BLE001
        report["preflight"] = {"error": repr(e)}

    if with_runtime:
        report["runtime"] = _runtime_endpoints_summary()
    if with_adapters:
        report["adapters"] = _adapter_probes()
    return report


# -- 运行时框架（adapter）卡片 + 默认模型选择 -------------------------------
#
# 设计目标：
#   设置面板顶部一行"模型框架"卡片：每张卡 = 一个适配器（ollama / vllm /
#   infinity / comfyui / funasr / piper / cosyvoice / rapidocr / paddleocr /
#   llama.cpp / whisper.cpp）。卡片立刻显示：
#     - 是否健康（绿/橙/灰）
#     - 它能服务的 capability 列表（"对话 / 文本嵌入 / 图像嵌入"）
#     - 已经在它上面跑的本地模型数量
#     - URL
#     - "怎么装"按钮（→ 引导到 /v1/admin/services/topology 同款命令）
#     - 一键自动安装（Ollama / pip-installable 的 runtime 才有）
# -----------------------------------------------------------------------------


# adapter.categories 是 chayuan_runtime 的内部词汇；前端展示用更口语化的标签
_RUNTIME_CATEGORY_LABELS: dict[str, str] = {
    "chat": "对话",
    "embedding": "文本嵌入",
    "clip": "图像嵌入",
    "rerank": "重排",
    "t2i": "文生图",
    "t2v": "文生视频",
    "tts": "语音合成",
    "asr": "语音识别",
    "ocr": "图像识别文字",
}


def _runtime_install_kind(name: str) -> str:
    """每个 runtime 的"自动安装能力等级"。

    * ``one-click`` —— 单条命令在所有平台都能装；后端可以 subprocess 跑（Ollama）
    * ``pip``      —— ``pip install <pkg>``；后端可以 subprocess（infinity / piper /
                       cosyvoice / funasr / rapidocr / paddleocr / whispercpp）
    * ``docker``   —— 必须 docker（vllm 在 macOS / Windows、ComfyUI 推荐路径）
    * ``manual``   —— 用户必须手动（裸机 GPU / 系统级配置）
    """
    if name == "ollama":
        return "one-click"
    if name in {"infinity", "piper", "cosyvoice", "funasr", "rapidocr", "paddleocr", "whispercpp"}:
        return "pip"
    if name in {"comfyui", "vllm", "llamacpp"}:
        return "docker"
    return "manual"


def _runtime_default_pip_pkg(name: str) -> str | None:
    return {
        "infinity":   "infinity-emb[all]",
        "piper":      "piper-tts",
        "cosyvoice":  "cosyvoice",
        "funasr":     "funasr",
        "rapidocr":   "rapidocr-onnxruntime",
        "paddleocr":  "paddleocr paddlepaddle",
        "whispercpp": "whisper-cpp-python",
    }.get(name)


@router.get("/runtimes")
def list_runtimes(repo: ModelRepository = Depends(get_repo)) -> dict:
    """每个适配器（11 个）的运行时卡片数据 —— 前端 ``RuntimeFrameworkCards`` 用。

    响应::

        {
          "runtimes": [{
              "name": "ollama",
              "label": "Ollama",
              "categories": ["chat"],
              "category_labels": ["对话"],
              "url": "http://127.0.0.1:11434",
              "health": "healthy" | "configured" | "missing",
              "models_served": 3,
              "install_kind": "one-click",
              "install_recipes": {...},      # 同 topology 的 recipes
              "subprocess": false,
              "needs_gpu": false,
          }, ...],
          "host_os": "linux"
        }
    """
    from chayuan_runtime import get_registry  # type: ignore
    reg = get_registry()
    probes = {p["name"]: p for p in _adapter_probes()}
    rt_endpoints = _runtime_endpoints_summary()
    recipes_all = _platform_install_recipes()
    host_os = _detect_local_os()

    # 反向 index：repo 中 runtime → 数量
    served: dict[str, int] = {}
    try:
        for m in repo.list():
            r = (getattr(m, "runtime", "") or "").lower()
            if r:
                served[r] = served.get(r, 0) + 1
    except Exception as e:  # noqa: BLE001
        logger.debug("[runtimes] repo.list() failed: %r", e)

    out = []
    for a in reg.all():
        cats = list(a.capabilities.categories or ())
        ep = rt_endpoints.get(a.name) or {}
        probe = probes.get(a.name) or {}
        health = (
            "healthy" if probe.get("ok")
            else ("configured" if (ep or a.base_url) else "missing")
        )
        out.append({
            "name": a.name,
            "label": _runtime_label(a.name),
            "categories": cats,
            "category_labels": [_RUNTIME_CATEGORY_LABELS.get(c, c) for c in cats],
            "url": ep.get("url") or a.base_url or None,
            "host": ep.get("host"),
            "port": ep.get("port"),
            "health": health,
            "probe": {
                "ok": probe.get("ok"),
                "kind": probe.get("kind"),
                "url": probe.get("probe_url"),
                "detail": probe.get("detail"),
            },
            "models_served": served.get(a.name, 0),
            "install_kind": _runtime_install_kind(a.name),
            "install_recipes": recipes_all.get(a.name, {}),
            "subprocess": bool(getattr(a, "is_subprocess", False)),
            "needs_gpu": bool(getattr(a.capabilities, "needs_gpu", False)),
            "default_pip_package": _runtime_default_pip_pkg(a.name),
        })

    # 按 capability 数 desc + name asc 给一个稳定顺序
    out.sort(key=lambda r: (-len(r["categories"]), r["name"]))
    return {"runtimes": out, "host_os": host_os}


def _runtime_label(name: str) -> str:
    return {
        "ollama": "Ollama",
        "vllm": "vLLM",
        "llamacpp": "llama.cpp",
        "infinity": "Infinity",
        "comfyui": "ComfyUI",
        "funasr": "FunASR",
        "cosyvoice": "CosyVoice",
        "piper": "Piper TTS",
        "rapidocr": "RapidOCR",
        "paddleocr": "PaddleOCR",
        "whispercpp": "whisper.cpp",
    }.get(name, name)


# -- 一键安装运行时（Ollama / pip） ------------------------------------------

_INSTALL_TASKS: dict[str, dict] = {}
_INSTALL_LOCK = threading.Lock()


def _spawn_install(name: str) -> dict:
    """启动一次 runtime 自动安装；返回 ``{task_id, kind, started}``。

    具体实现：
      * Ollama：``curl ollama.com/install.sh | sh`` (Linux/macOS) /
        ``winget install Ollama.Ollama`` (Windows)
      * pip-installable：``pip install <pkg>``
      * 其它：直接拒绝（前端应展示 docker / 手动命令而不是自动安装）
    """
    import shlex
    import subprocess

    task_id = f"install-{name}-{uuid.uuid4().hex[:8]}"
    kind = _runtime_install_kind(name)
    host_os = _detect_local_os()

    cmd: list[str] | None = None
    if name == "ollama":
        if host_os == "win":
            cmd = ["winget", "install", "-e", "--id", "Ollama.Ollama"]
        else:
            cmd = ["bash", "-lc", "curl -fsSL https://ollama.com/install.sh | sh"]
    elif kind == "pip":
        pkg = _runtime_default_pip_pkg(name)
        if pkg:
            cmd = [sys.executable, "-m", "pip", "install", *shlex.split(pkg)]

    if cmd is None:
        raise HTTPException(
            status_code=400,
            detail=f"runtime '{name}' 不支持自动安装（kind={kind}）；请使用 install_recipes 中的命令。",
        )

    with _INSTALL_LOCK:
        _INSTALL_TASKS[task_id] = {
            "task_id": task_id, "name": name, "state": "running",
            "kind": kind, "started": time.time(), "log": [],
        }

    def _bg() -> None:
        try:
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                with _INSTALL_LOCK:
                    t = _INSTALL_TASKS.get(task_id)
                    if t is not None:
                        t["log"].append(line.rstrip())
                        # SSE/事件推一份，前端可以订阅
                        try:
                            get_bus().publish("runtime.install", {
                                "task_id": task_id, "name": name,
                                "state": "running", "line": line.rstrip(),
                                "ts": time.time(),
                            })
                        except Exception:
                            pass
            rc = proc.wait()
            with _INSTALL_LOCK:
                t = _INSTALL_TASKS.get(task_id)
                if t is not None:
                    t["state"] = "done" if rc == 0 else "failed"
                    t["return_code"] = rc
                    t["ended"] = time.time()
            try:
                get_bus().publish("runtime.install", {
                    "task_id": task_id, "name": name,
                    "state": "done" if rc == 0 else "failed",
                    "return_code": rc, "ts": time.time(),
                })
            except Exception:
                pass
        except Exception as e:  # noqa: BLE001
            with _INSTALL_LOCK:
                _INSTALL_TASKS[task_id] = {
                    **_INSTALL_TASKS.get(task_id, {}),
                    "state": "failed", "error": str(e), "ended": time.time(),
                }
            try:
                get_bus().publish("runtime.install", {
                    "task_id": task_id, "name": name,
                    "state": "failed", "message": str(e), "ts": time.time(),
                })
            except Exception:
                pass

    threading.Thread(target=_bg, daemon=True).start()
    return {"task_id": task_id, "kind": kind, "name": name, "state": "queued"}


@router.post("/runtimes/{name}/install")
def install_runtime(name: str) -> dict:
    """启动一次 runtime 自动安装（异步）。返回 ``task_id``。

    支持的 ``name``:
      * ``ollama``    —— 全平台一键
      * ``infinity`` / ``piper`` / ``cosyvoice`` / ``funasr`` / ``rapidocr`` /
        ``paddleocr`` / ``whispercpp`` —— ``pip install``

    其它 runtime 一律返 400；前端应展示 ``install_recipes`` 里的命令让用户手动跑。
    """
    return _spawn_install(name)


@router.get("/runtimes/install/{task_id}")
def install_status(task_id: str) -> dict:
    with _INSTALL_LOCK:
        t = _INSTALL_TASKS.get(task_id)
    if t is None:
        raise HTTPException(404, "install task not found")
    return t


# -- 9 类 capability 的"默认模型"统一读 / 写 ---------------------------------


_DEFAULTS_CAPABILITIES = (
    "chat", "embedding", "clip", "rerank", "t2i", "t2v", "tts", "asr", "ocr",
)


def _capability_to_repo_category(cap: str) -> str:
    """前端 capability ←→ ModelRepository.category 的映射。"""
    return {
        "text-embedding":  "embedding",
        "image-embedding": "clip",
        "text-to-image":   "t2i",
        "text-to-video":   "t2v",
        "text-to-speech":  "tts",
        "asr":             "asr",
        "ocr":             "ocr",
        "rerank":          "rerank",
        "chat":            "chat",
    }.get(cap, cap)


@router.get("/defaults")
def list_defaults(repo: ModelRepository = Depends(get_repo)) -> dict:
    """9 类 capability 的"默认模型 + 候选模型清单"——前端 ``DefaultModelsRow`` 用。

    响应::

        {
          "defaults": {"chat": "qwen2.5:4b", "embedding": "bge-small-zh-v1.5", ...},
          "candidates": {
              "chat": [{"id": "qwen2.5:4b", "runtime": "ollama", ...}, ...],
              "embedding": [...]
          },
          "capabilities": ["chat", "embedding", ...]
        }

    候选只列 ``enabled=true`` 的本地模型；如果某 capability 没装任何模型，
    candidates 是空数组，前端展示"未安装 → 去推荐"。
    """
    defaults: dict[str, str | None] = {c: None for c in _DEFAULTS_CAPABILITIES}
    candidates: dict[str, list[dict]] = {c: [] for c in _DEFAULTS_CAPABILITIES}
    try:
        for m in repo.list(enabled=True):
            cat = getattr(m, "category", None)
            if cat not in defaults:
                continue
            # ``public_id`` 才是 OpenAI / 前端用的稳定 string id；ORM ``.id`` 是 PK 整数
            mid = getattr(m, "public_id", None) or getattr(m, "id", None)
            if mid is None:
                continue
            candidates[cat].append({
                "id": mid,
                "runtime": getattr(m, "runtime", None),
                "format": getattr(m, "format", None),
                "size_bytes": getattr(m, "size_bytes", 0),
                "is_default": bool(getattr(m, "is_default", False)),
            })
            if getattr(m, "is_default", False):
                defaults[cat] = mid
    except Exception as e:  # noqa: BLE001
        logger.debug("[defaults] repo.list() failed: %r", e)

    return {
        "capabilities": list(_DEFAULTS_CAPABILITIES),
        "defaults": defaults,
        "candidates": candidates,
    }


@router.post("/defaults")
def set_defaults(payload: dict = Body(...), repo: ModelRepository = Depends(get_repo)) -> dict:
    """批量设置默认模型。``payload`` 形如::

        {"chat": "qwen2.5:4b", "embedding": "bge-small-zh-v1.5"}

    只接收上面 9 类 capability 之一的键；其它键被忽略；逐条调
    ``repo.set_default(category, model_id)``。任何一条失败都不影响其他条。
    """
    results: dict[str, dict] = {}
    for cap_in, mid in (payload or {}).items():
        cat = _capability_to_repo_category(cap_in)
        if cat not in _DEFAULTS_CAPABILITIES:
            results[cap_in] = {"ok": False, "error": "unknown capability"}
            continue
        if not mid or not isinstance(mid, str):
            results[cap_in] = {"ok": False, "error": "model id required"}
            continue
        try:
            ok = bool(repo.set_default(cat, mid))
            results[cap_in] = {"ok": ok, "model": mid}
        except Exception as e:  # noqa: BLE001
            results[cap_in] = {"ok": False, "error": str(e)}
    return {"results": results}


# -- 模型文件定位（"这个模型的文件在哪儿"） ------------------------------------


@router.get("/models/{model_id:path}/locate")
def locate_model(
    model_id: str,
    runtime: str | None = Query(None, description="未在 repo 注册时手动指定 runtime"),
    repo: ModelRepository = Depends(get_repo),
) -> dict:
    """返回 ``model_id`` 在本机磁盘上的真实文件位置。

    覆盖路径：
        * 自管理模型（chayuan model pull → DB.path 存在）—— 直接返回；
        * Ollama 托管 —— 解析 ``manifests/`` + ``blobs/sha256-...``；
        * Infinity / vLLM / transformers —— HF 缓存；
        * ComfyUI —— ``~/ComfyUI/models/<sub>/<file>``；
        * 其它 —— 兜底返回 DB.path（即便文件已被删，前端也能看到原始位置）。

    响应::

        {
          "model_id": "qwen2:0.5b",
          "runtime": "ollama",
          "found": true,
          "path": "/home/u/.ollama/models/blobs/sha256-abc...",
          "dir": "/home/u/.ollama/models/blobs",
          "size_bytes": 367843201,
          "cache_kind": "ollama-blobs",
          "blobs": [{...}, ...],
          "extra": {"manifest": "..."}
        }
    """
    m = repo.get(model_id)
    rt = (getattr(m, "runtime", None) if m else None) or runtime
    fmt = getattr(m, "format", None) if m else None
    db_path = getattr(m, "path", None) if m else None
    # 兜底嗅探：未注册 + 没传 runtime，但 ``model_id`` 形如 ``foo:tag`` → Ollama
    if rt is None and ":" in model_id and "/" not in model_id:
        rt = "ollama"
    loc: ModelLocation = _locate_model(
        model_id, runtime=rt, db_path=db_path, fmt=fmt,
    )
    out = loc.to_dict()
    # 顺手回传 DB 里有的 ``sha256`` —— 前端可以做校验提示
    if m is not None:
        out["sha256"] = getattr(m, "sha256", "") or None
        out["category"] = getattr(m, "category", None)
    return out


# -- service topology + 安装引导 ---------------------------------------------
#
# 设计目的：
# 设置面板 / CLI 想知道"机器上 11 个服务（postgres / redis / minio / milvus /
# ollama / vllm / comfyui ...）哪些装好了、哪些没装、装的话怎么装"。我们汇总：
#
# 1) supervisor.yaml 声明的 process 列表 → 拿到二进制路径 / 端口
# 2) doctor 的 adapter probe → 已装 / 已启动 / 已健康
# 3) 平台特异化"安装命令"：
#       Linux → apt / pacman / brew(linuxbrew)
#       macOS → brew
#       Windows → winget / choco
# 4) docker fallback：所有服务都给 docker run 备选，没有任何依赖也能跑
# -----------------------------------------------------------------------------


def _platform_install_recipes() -> dict[str, dict[str, list[str]]]:
    """每个服务在三大平台上的"建议安装命令"。

    格式::

        { "<service>": { "<os>": ["cmd1", "cmd2", ...] } }

    os ∈ {linux-apt, linux-pacman, mac-brew, win-winget, docker}
    """
    return {
        "postgres": {
            "linux-apt":   ["sudo apt-get update", "sudo apt-get install -y postgresql"],
            "linux-pacman":["sudo pacman -S --noconfirm postgresql"],
            "mac-brew":    ["brew install postgresql"],
            "win-winget":  ["winget install -e --id PostgreSQL.PostgreSQL"],
            "docker":      ["docker run -d --name chayuan-pg -p 35432:5432 -e POSTGRES_PASSWORD=chayuan postgres:16"],
        },
        "redis": {
            "linux-apt":   ["sudo apt-get install -y redis-server"],
            "linux-pacman":["sudo pacman -S --noconfirm redis"],
            "mac-brew":    ["brew install redis"],
            "win-winget":  ["winget install -e --id Redis.Redis"],
            "docker":      ["docker run -d --name chayuan-redis -p 36379:6379 redis:7"],
        },
        "minio": {
            "linux-apt":   ["wget -qO/usr/local/bin/minio https://dl.min.io/server/minio/release/linux-amd64/minio", "chmod +x /usr/local/bin/minio"],
            "mac-brew":    ["brew install minio/stable/minio"],
            "win-winget":  ["winget install -e --id MinIO.Server"],
            "docker":      ["docker run -d --name chayuan-minio -p 39000:9000 -p 39001:9001 -e MINIO_ROOT_USER=chayuan -e MINIO_ROOT_PASSWORD=chayuan-mi-pwd minio/minio server /data --console-address :9001"],
        },
        "milvus": {
            "docker":      ["bash <(curl -fsSL https://raw.githubusercontent.com/milvus-io/milvus/master/scripts/standalone_embed.sh) start"],
        },
        "ollama": {
            "linux-apt":   ["curl -fsSL https://ollama.com/install.sh | sh"],
            "mac-brew":    ["brew install ollama"],
            "win-winget":  ["winget install -e --id Ollama.Ollama"],
            "docker":      ["docker run -d --name chayuan-ollama -p 11434:11434 ollama/ollama"],
        },
        "vllm": {
            "linux-apt":   ["pip install vllm"],
            "mac-brew":    ["# vLLM 仅支持 Linux + CUDA / ROCm；macOS 请用 llama.cpp / mlx-lm 替代"],
            "docker":      ["docker run --gpus all -p 38000:8000 vllm/vllm-openai:latest --model Qwen/Qwen2.5-7B-Instruct"],
        },
        "comfyui": {
            "linux-apt":   ["git clone https://github.com/comfyanonymous/ComfyUI && cd ComfyUI && pip install -r requirements.txt"],
            "mac-brew":    ["git clone https://github.com/comfyanonymous/ComfyUI && cd ComfyUI && pip install -r requirements.txt"],
            "docker":      ["docker run -d --gpus all -p 18188:8188 yanwk/comfyui-boot:latest"],
        },
        "infinity": {
            "linux-apt":   ["pip install infinity-emb[all]"],
            "mac-brew":    ["pip install infinity-emb[all]"],
            "docker":      ["docker run -d -p 7997:7997 michaelf34/infinity:latest"],
        },
        "funasr":    {"linux-apt": ["pip install funasr"], "docker": ["docker run -d -p 10095:10095 registry.cn-hangzhou.aliyuncs.com/funasr_repo/funasr:funasr-runtime-sdk-online-cpu-0.1.6"]},
        "cosyvoice": {"linux-apt": ["pip install cosyvoice"]},
        "piper":     {"linux-apt": ["pip install piper-tts"], "mac-brew": ["pip install piper-tts"], "win-winget": ["pip install piper-tts"]},
        "rapidocr":  {"linux-apt": ["pip install rapidocr-onnxruntime"]},
        "paddleocr": {"linux-apt": ["pip install paddleocr paddlepaddle"]},
        "docker": {
            "linux-apt":   ["curl -fsSL https://get.docker.com | sh"],
            "mac-brew":    ["brew install --cask docker"],
            "win-winget":  ["winget install -e --id Docker.DockerDesktop"],
        },
    }


def _detect_local_os() -> str:
    """返回 ``mac`` / ``win`` / ``linux``。前端可以拿来挑命令。"""
    if sys.platform.startswith("darwin"): return "mac"
    if sys.platform.startswith("win"):    return "win"
    return "linux"


@router.get("/services/topology")
def services_topology() -> dict:
    """服务拓扑：每个服务的状态 + 安装命令 + docker 兜底。

    响应::

        {
          "host_os": "linux" | "mac" | "win",
          "services": [{
              "name": "postgres", "kind": "postgres",
              "managed": true,                    # 在 supervisor.yaml 中
              "status": "healthy" | "configured" | "missing",
              "host": "127.0.0.1", "port": 35432,
              "binary_present": true,             # vendor/services/<name>/bin/* 存在
              "install": {                        # 按 host_os 排好的命令
                  "preferred": ["sudo apt-get install -y postgresql"],
                  "docker":    ["docker run ..."],
                  "all":       {"linux-apt": [...], "mac-brew": [...], ...}
              },
          }, ...]
        }
    """
    host_os = _detect_local_os()
    recipes_all = _platform_install_recipes()

    # 1) supervisor.yaml 声明的服务（优先来源）
    declared: dict[str, dict] = {}
    try:
        from chayuan_supervisor.manager import load_spec  # type: ignore
        for spec in load_spec():
            declared[spec.name] = {
                "name": spec.name,
                "binary": spec.binary,
                "expose": getattr(spec, "expose", {}) or {},
            }
    except Exception as e:  # noqa: BLE001
        logger.debug("[topology] supervisor spec load failed: %r", e)

    # 2) runtime.json 中已配置的端点
    rt_endpoints = _runtime_endpoints_summary()

    # 3) adapter probe 的真实健康度
    probes = {p["name"]: p for p in _adapter_probes()}

    # 4) vendor 二进制是否就绪（packaging/python312 fetch 完之后才有）
    import os as _os
    vendor_root = _os.environ.get("CHAYUAN_VENDOR_ROOT") or "vendor/services"

    out: list[dict] = []
    seen: set[str] = set()
    for name, proc in declared.items():
        seen.add(name)
        rt_ep = rt_endpoints.get(name) or {}
        probe = probes.get(name) or {}
        bin_path = (proc.get("binary") or "").replace("vendor/services/", f"{vendor_root}/")
        binary_present = bool(bin_path) and _os.path.exists(bin_path)

        if probe.get("ok"):
            status = "healthy"
        elif rt_ep:
            status = "configured"  # 配了端口但 ping 不通
        else:
            status = "missing"

        recipes = recipes_all.get(name, {})
        install = {
            "preferred": recipes.get(f"{host_os}-apt") or recipes.get(f"{host_os}-brew") or recipes.get(f"{host_os}-winget") or [],
            "docker":    recipes.get("docker", []),
            "all":       recipes,
        }

        out.append({
            "name": name,
            "kind": (proc.get("expose") or {}).get("kind") or "service",
            "managed": True,
            "status": status,
            "host": rt_ep.get("host"),
            "port": rt_ep.get("port"),
            "url":  rt_ep.get("url"),
            "binary_present": binary_present,
            "binary_path": bin_path or None,
            "probe": {
                "ok": probe.get("ok"),
                "kind": probe.get("kind"),
                "url": probe.get("probe_url"),
                "detail": probe.get("detail"),
            },
            "install": install,
        })

    # 包含未在 supervisor.yaml 中、但有 install recipe 的（如 docker / vllm）
    for name, recipes in recipes_all.items():
        if name in seen:
            continue
        out.append({
            "name": name, "kind": "service", "managed": False, "status": "missing",
            "binary_present": False, "binary_path": None,
            "install": {
                "preferred": recipes.get(f"{host_os}-apt") or recipes.get(f"{host_os}-brew") or recipes.get(f"{host_os}-winget") or [],
                "docker":    recipes.get("docker", []),
                "all":       recipes,
            },
        })

    return {"host_os": host_os, "services": out}


# -- recommended models -------------------------------------------------------
#
# 设计目的：
# 用户在桌面 / Web 设置面板里点开 "9 类模型库 → 图像嵌入 → 还没装" 时，应该
# **马上**看到推荐清单 + 一键安装。对应 chayuan_modelmgr.recommended SSOT。
# -----------------------------------------------------------------------------


@router.get("/recommended_models")
def recommended_models(
    capability: str | None = Query(None, description="过滤 capability；不传返回全部"),
    repo: ModelRepository = Depends(get_repo),
) -> dict:
    """返回每个 capability 的推荐清单（含已安装态）。

    响应结构::

        {
          "capabilities": ["chat", "image-embedding", ...],
          "recommended": {
              "image-embedding": [{
                  "id": "jina-clip-v1", "runtime": "infinity",
                  "hf_repo": "jinaai/jina-clip-v1", "size_mb": 900,
                  "intent": "...", "installed": false, "default": false,
              }],
              ...
          },
          "default_for_capability": {"chat": "qwen2.5:4b", ...}
        }

    ``installed`` 通过 ``ModelRepository`` 反查，前端可以拿来 toggle 按钮（已装 →
    设为默认；未装 → 安装）。
    """
    items = get_recommended(capability)
    out_by_cap: dict[str, list[dict]] = {}
    installed_by_id: dict[str, bool] = {}
    default_by_cap: dict[str, str | None] = {}

    try:
        all_models = list(repo.list())
        installed_by_id = {
            (getattr(m, "public_id", None) or getattr(m, "id", None)): True for m in all_models
        }
        # default by category
        for m in all_models:
            cat = getattr(m, "category", None)
            if not cat:
                continue
            if getattr(m, "is_default", False):
                default_by_cap[cat] = (
                    getattr(m, "public_id", None) or getattr(m, "id", None)
                )
    except Exception as e:  # noqa: BLE001
        logger.debug("[recommended] repo.list() failed: %r", e)

    def _enrich(m: RecommendedModel) -> dict:
        d = m.to_dict()
        # alias 也算已装：ollama 拉的 ``qwen2.5:4b`` ≡ HF 的 ``Qwen/Qwen2.5-4B``
        candidates = (m.id, *m.id_aliases, m.hf_repo)
        d["installed"] = any(installed_by_id.get(c) for c in candidates)
        d["default"] = default_by_cap.get(m.capability) == m.id
        return d

    for m in items:
        out_by_cap.setdefault(m.capability, []).append(_enrich(m))

    return {
        "capabilities": list(list_capabilities()),
        "recommended": out_by_cap,
        "default_for_capability": {
            c: (get_default_for_capability(c).id if get_default_for_capability(c) else None)
            for c in list_capabilities()
        },
    }


# -- mirror selection ---------------------------------------------------------
#
# 用户在配置面板能看到 / 切换"模型镜像源"（hf-mirror / huggingface / modelscope
# / 自定义 URL）。后端把当前生效的 endpoint 暴露出来，并允许通过 POST 修改：
#
# * 修改后只对当前进程立即生效（写 ``CHAYUAN_MIRROR`` env）；
# * 同时落到 ``runtime.json``，下次进程起来时 ``modelmgr.mirrors.resolve_mirror``
#   会从环境恢复，避免每次都让用户再选一遍。
# -----------------------------------------------------------------------------


@router.get("/mirror")
def get_mirror() -> dict:
    """返回当前生效的镜像源 + 可选项。"""
    try:
        from chayuan_modelmgr.mirrors import MIRRORS, resolve_mirror
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"chayuan_modelmgr unavailable: {e!r}") from e
    cur = resolve_mirror()
    return {
        "current": {"name": cur.name, "endpoint": cur.endpoint, "kind": cur.kind},
        "available": [
            {"name": m.name, "endpoint": m.endpoint, "kind": m.kind}
            for m in MIRRORS.values()
        ],
        # 让前端能识别"是不是用户自定义的"
        "is_custom": cur.name not in MIRRORS,
    }


@router.post("/mirror")
def set_mirror(payload: dict = Body(...)) -> dict:
    """切换镜像源。

    请求体可以是：
    * ``{"name": "hf-mirror"}`` 选择内置选项（hf-mirror / huggingface / modelscope）
    * ``{"endpoint": "https://my-mirror.com"}`` 自定义 URL（必须 http/https 开头）
    """
    import os as _os
    try:
        from chayuan_modelmgr.mirrors import MIRRORS
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"chayuan_modelmgr unavailable: {e!r}") from e

    name = (payload.get("name") or "").strip()
    endpoint = (payload.get("endpoint") or "").strip()

    if name and name in MIRRORS:
        ep = MIRRORS[name].endpoint
    elif endpoint:
        if not endpoint.startswith(("http://", "https://")):
            raise HTTPException(400, "endpoint must start with http:// or https://")
        ep = endpoint.rstrip("/")
        # 用 endpoint 当 name，让 GET /mirror 能反查
        name = endpoint
    else:
        raise HTTPException(400, "either 'name' or 'endpoint' required")

    # 1) 当前进程立即生效（modelmgr.mirrors.resolve_mirror 优先读环境变量）
    _os.environ["CHAYUAN_MIRROR"] = name
    _os.environ["HF_ENDPOINT"] = ep
    _os.environ["HF_MIRROR_URL"] = ep

    # 2) 持久化到 runtime.json（best-effort，没装 chayuan-server 时跳过）
    try:
        from chayuan.server.runtime.runtime_info import get_runtime_info  # type: ignore
        info = get_runtime_info()
        if hasattr(info, "set_extra"):
            info.set_extra("mirror", {"name": name, "endpoint": ep})
    except Exception:
        pass

    return {"ok": True, "current": {"name": name, "endpoint": ep}}


@router.post("/doctor/fix/{check_name}")
def doctor_fix(check_name: str) -> dict:
    """对某项检查执行修复（暂未实现自动修，返回一份文字指引）。

    后续 ``chayuan_preflight.fixers.<check_name>`` 实装时会优先调用模块化修
    复函数。
    """
    fixers_module = None
    try:
        from chayuan_preflight import fixers  # type: ignore
        fixers_module = fixers
    except Exception:
        pass

    fix_fn = None
    if fixers_module is not None:
        fix_fn = getattr(fixers_module, check_name.replace("-", "_"), None) or \
                 getattr(fixers_module, f"fix_{check_name.replace('-', '_')}", None)

    if callable(fix_fn):
        try:
            result = fix_fn()
            return {"ok": True, "result": result}
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"fix failed: {e!r}") from e

    # 兜底：返回一份"如何手动修"的指引；前端可以渲染成卡片
    hints = {
        "av-windows-defender": (
            "请在 Windows Defender「病毒和威胁防护设置」中，把 chayuan 安装目录加入「排除项」。"
            "然后重启 chayuan，让其下载/释放 ollama 等子进程二进制。"
        ),
        "selinux":  "运行：sudo setsebool -P httpd_can_network_connect 1",
        "appgatekeeper": "运行：sudo spctl --master-disable 仅在 Mac 完成首次签名验证前临时使用。",
        "port-conflict": "运行：lsof -nP -iTCP:35432 | grep LISTEN，找到占用进程后释放或在 runtime.json 改 port。",
    }
    return {
        "ok": False,
        "hint": hints.get(check_name, "尚未实现自动修复，请查阅 docs/install/troubleshooting.md。"),
        "check": check_name,
    }
