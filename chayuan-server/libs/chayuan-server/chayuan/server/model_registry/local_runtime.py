"""本地 LLM runtime 管理 (vendor 进集成版的 llama-server.exe)。

职责:
- 启动 / 停止 / 重启 vendor/services/llama-server/llama-server.exe 子进程
- 健康检查:轮询 /health,失败重试
- 状态写入 <CHAYUAN_ROOT>/runtime.json (前端读)
- 配置持久化到 <CHAYUAN_ROOT>/model_registry/local_runtime.yaml

设计:整个 chayuan-server 进程内一个 manager 单例,通过 get_manager() 拿。
sidecar lifespan shutdown 必须 await manager.stop() 级联关停 llama-server。
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Literal, Optional

import yaml

from chayuan.server.model_registry import process_args  # noqa: E402 — 供 monkeypatch 拦截

logger = logging.getLogger(__name__)

# 启动等 /health 200 的总超时(秒)。单元测试可 monkeypatch。
HEALTH_READY_TIMEOUT_SEC: float = 60.0
HEALTH_PROBE_INTERVAL_SEC: float = 0.5


# ───────────────────────── 配置 / 状态 dataclass ─────────────────────────

@dataclasses.dataclass
class LocalRuntimeSettings:
    """本地 runtime 用户可配项,持久化到 local_runtime.yaml"""
    preload_on_startup: bool = True
    host: str = "127.0.0.1"
    port: int = 62582
    api_key: str = ""
    expose_lan: bool = False
    default_chat_model: str = ""
    # Plan 3B 多 capability:
    preload_embedding: bool = False
    preload_rerank: bool = False
    default_embedding_model: str = ""
    default_rerank_model: str = ""
    # Plan 3C ASR:
    preload_asr: bool = False
    default_asr_model: str = ""
    # Plan 3D 图像嵌入:
    preload_image_embedding: bool = False
    default_image_embedding_model: str = ""

    @classmethod
    def load(cls, path: Path) -> "LocalRuntimeSettings":
        if not path.is_file():
            return cls()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return cls()
        return cls(**{
            k: data[k]
            for k in dataclasses.asdict(cls()).keys()
            if k in data
        })

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(dataclasses.asdict(self), allow_unicode=True),
            encoding="utf-8",
        )


@dataclasses.dataclass
class RuntimeStatus:
    """RuntimeManager 实时状态,序列化给 API 返回。"""
    state: Literal["stopped", "starting", "ready", "failed", "restarting"]
    endpoint: Optional[str] = None
    pid: Optional[int] = None
    model_id: Optional[str] = None
    model_path: Optional[str] = None
    started_at: Optional[datetime] = None
    last_health_at: Optional[datetime] = None
    last_error: Optional[str] = None

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        for k in ("started_at", "last_health_at"):
            if d[k] is not None:
                d[k] = d[k].isoformat()
        return d


# ─────────────────────── manager ─────────────────────────

# 装机后 services 二进制可能的路径(按优先级排;集成版 / dev / 自定义)
def _default_install_services_dirs() -> list[Path]:
    """运行时定位 services 二进制目录。

    集成版装机后:
      Windows:  <install_dir>\\services\\
      Mac:      <install_dir>/Contents/Resources/services/
      Linux:    <install_dir>/services/

    Tauri 会把 bundle.resources 中的 services/** 解压到运行时
    可执行文件旁边的 resources/ 子目录。我们扫几个常见位置取并集。
    """
    candidates: list[Path] = []
    import sys
    # PyInstaller frozen 时 sys.executable = sidecar exe;
    # exe 同目录里的 services/ 是 Tauri install dir 的 services/
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "services")
        candidates.append(Path(sys.executable).parent.parent / "services")
        # Linux AppImage / Tauri Linux 解包:exe 旁的 lowercase resources/
        candidates.append(Path(sys.executable).parent / "resources" / "services")
        candidates.append(Path(sys.executable).parent.parent / "resources" / "services")
        # Mac .app bundle 结构
        candidates.append(Path(sys.executable).parent.parent / "Resources" / "services")
    # dev mode:从仓库 vendor/services/ 找
    candidates.append(Path(__file__).resolve().parents[5] / "vendor" / "services")
    # 兜底:CHAYUAN_ROOT/services
    return candidates


_INSTALL_SERVICES_DIRS: list[Path] | None = None  # 单元测试可 monkeypatch 覆盖


def _platform_subdir_candidates() -> list[str]:
    """当前 OS / 架构按优先级的 vendor 预编译子目录候选列表。

    `find_server_exe` 依次走这个列表,选第一个真存在 binary 的子目录。

    可通过 env var ``CHAYUAN_VENDOR_PLATFORM`` 强制覆盖(单值);常见用法:
      * Win 老 CPU 没 AVX2:``CHAYUAN_VENDOR_PLATFORM=win-x64-noavx``
      * Win 新 Xeon 想用 AVX-512:``CHAYUAN_VENDOR_PLATFORM=win-x64-avx512``

    默认按 OS / 架构推:
      Win x64   → [win-x64, win-x64-avx, win-x64-noavx]   (默认 AVX2,自动 fallback)
      Win arm64 → [win-arm64]
      macOS ARM → [macos-arm64]
      macOS Intel → [macos-x64]
      Linux x64 → [linux-x64]
      Linux arm64 → [linux-arm64]

    返回空列表表示不识别此平台 — 调用方会 fallback 到扁平 layout(install 脚本现写现拷)。
    """
    import platform as _platform
    override = os.environ.get("CHAYUAN_VENDOR_PLATFORM", "").strip()
    if override:
        return [override]
    machine = _platform.machine().lower()
    if sys.platform == "win32":
        if machine in ("arm64", "aarch64"):
            return ["win-arm64"]
        # x86_64:默认 AVX2 → 老 CPU 没 AVX2 自动 fallback 到 win-x64-noavx
        # (win-x64-avx 是中间档,2011 年 Sandy Bridge ~ 2013 年 Haswell 之间的极少数 CPU 才需要)
        return ["win-x64", "win-x64-noavx"]
    if sys.platform == "darwin":
        return ["macos-arm64"] if machine in ("arm64", "aarch64") else ["macos-x64"]
    if sys.platform.startswith("linux"):
        return ["linux-arm64"] if machine in ("aarch64", "arm64") else ["linux-x64"]
    return []


def _platform_subdir() -> str:
    """Deprecated single-value variant. 新代码用 :func:`_platform_subdir_candidates`。"""
    cands = _platform_subdir_candidates()
    return cands[0] if cands else ""


async def _probe_health(url: str, *, timeout: float = 2.0) -> "object":
    """探一次 /health。返回类 httpx.Response 的对象 (有 .status_code)。

    单元测试 monkeypatch 这个函数。
    """
    import httpx
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.get(url)


# capability → PlatformConfig 字段名;sidecar 注册 platform 时按 cap 把
# model_id 放进对应清单字段(其它字段留空,平台只承载这一个 capability)。
_CAP_TO_PLATFORM_FIELD: Dict[str, str] = {
    "chat":            "llm_models",
    "embedding":       "embed_models",
    "rerank":          "rerank_models",
    "asr":             "speech2text_models",
    "image-embedding": "image2text_models",
}

# capability → local_index 里的 capability 标签。绝大多数同名;image-embedding
# 在 local_index 里登记为 ``image``(跟 bundled_models/ 子目录约定一致)。
_CAP_TO_INDEX_CAP: Dict[str, str] = {
    "image-embedding": "image",
}


def _list_downloaded_models(capability: str) -> list:
    """列出某 capability 下「所有已下载模型」的 model_id。

    项目原则:任何下载好的模型都要能选用 —— 平台清单不能只放当前在跑的
    那一个,否则用户选别的已下载模型会报 ``ModelNotConfigured``。
    从 :func:`local_index.get_local_index` 枚举;失败 / 没装返回空 list,
    调用方自行兜底加入当前 model_id。
    """
    out: list = []
    try:
        from chayuan.server.model_registry.local_index import get_local_index
        idx = get_local_index()
        index_caps = {capability, _CAP_TO_INDEX_CAP.get(capability, capability)}
        for cap in index_caps:
            for entry in idx.by_capability(cap):
                mid = getattr(entry, "model_id", None)
                if mid and mid not in out:
                    out.append(mid)
    except Exception as e:  # noqa: BLE001 — 枚举失败不阻塞注册,退化到只注册在跑的模型
        logger.warning("[local-runtime] 枚举 %s 已下载模型失败: %r", capability, e)
    return out


def _purge_stale_py_packages_yaml(active_dir: str) -> None:
    """清掉残留的 ``py_packages/yaml/``(自愈)。

    torch 安装器已不再把 pyyaml 装进 py_packages(见 pytorch_installer
    ._TORCH_DEP_PINS 注释)。但老版本装过的会留下 ``py_packages/yaml/``,且常
    是半套(``__init__.py`` 在、``error.py`` 等缺)。py_packages 在 sidecar 的
    sys.path 靠前,这个坏 yaml 会盖住外层好的 yaml,让 image-embedding 的
    infinity_server ``import yaml`` 崩(``ModuleNotFoundError: 'yaml.error'``)。

    在把 py_packages 加进 sidecar PYTHONPATH 前清掉它。**只清应用自管的
    py_packages**(== :func:`torch_install_target_dir`),绝不碰用户用「换位置」
    指向的外部 Python 环境。整目录删不掉(个别文件被占用)时部分删除也行 ——
    只要 ``__init__.py`` 没了,import yaml 就会落到外层那份。
    """
    try:
        import shutil
        from chayuan.server.runtime.pytorch_installer import torch_install_target_dir
        managed = torch_install_target_dir()
        if managed is None or Path(active_dir).resolve() != Path(managed).resolve():
            return  # active_dir 是外部环境,不是应用自管 py_packages → 不碰
        yaml_dir = Path(managed) / "yaml"
        if yaml_dir.is_dir():
            shutil.rmtree(yaml_dir, ignore_errors=True)
            logger.info(
                "[local-runtime] 已清理残留的 py_packages/yaml —— import yaml 交还给外层那份",
            )
    except Exception as e:  # noqa: BLE001 — 清理失败不阻塞 sidecar 启动
        logger.warning("[local-runtime] 清理 py_packages/yaml 失败: %r", e)


def _register_local_platform(*, capability: str, host: str, port: int, model_id: Optional[str]) -> None:
    """sidecar ready 时,把它写入 ``model_platform`` DB 表(model_platform_repository)。

    为什么不 mutate ``Settings.model_settings.MODEL_PLATFORMS``:那是 yaml-backed
    pydantic singleton,``settings_property + auto_reload=True``,每次访问都从 yaml
    重新加载 → in-memory mutation 立刻被回滚,平台凭空消失。
    用 DB upsert 才能持久 + ``_resolved_platforms`` 合并 yaml seed + db rows 时会读到。

    platform_name 是 ``local-<capability>`` 唯一标识。前端拼 ``local-<cap>::<model_id>``
    命名空间,chat 路径 ``get_model_info`` 解出 platform_name 命中此条 → api_base_url
    打 sidecar。

    幂等:upsert_platform 已处理 exist→update / not-exist→create + bump_version。
    """
    platform_name = f"local-{capability}"
    api_base_url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/v1"

    # 构造 cap 对应的 model_list 字段
    model_field = _CAP_TO_PLATFORM_FIELD.get(capability)
    fields: Dict[str, Any] = {
        "platform_type": "openai",  # llama-server / infinity 都是 OpenAI 兼容 API
        "api_base_url": api_base_url,
        "api_key": "EMPTY",
        "enabled": True,
        # auto_detect 路径还不通(_detect_oneapi 没实现),先用显式 model_id
        "auto_detect_model": False,
        # 所有 cap 字段先置空(防止之前残留),然后按 cap 填一个
        "llm_models": [],
        "embed_models": [],
        "rerank_models": [],
        "speech2text_models": [],
        "image2text_models": [],
    }
    if model_field:
        # 该 capability 下「所有已下载模型」都进清单,不只当前在跑的那个 ——
        # 否则用户选别的下载好的模型会报 ModelNotConfigured(项目原则:
        # 任何下载的模型都要能选用)。当前在跑的 model_id 兜底确保在列。
        models = _list_downloaded_models(capability)
        if model_id and model_id not in models:
            models = [model_id, *models]
        fields[model_field] = models

    try:
        from chayuan.server.db.repository.model_platform_repository import upsert_platform
        upsert_platform(platform_name=platform_name, fields=fields, by="local-runtime-hook")
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[local-runtime] upsert_platform(%s) failed: %r", platform_name, e,
        )
        return

    logger.info(
        "[local-runtime] registered DB platform: %s @ %s (model=%s)",
        platform_name, api_base_url, model_id,
    )


def _deregister_local_platform(*, capability: str) -> None:
    """sidecar stop/failed 时,把对应 DB platform 行 enabled=False。

    不删:保留 api_base_url 等信息,下次 sidecar 重启时 upsert 直接 toggle 回来。
    enabled=False 时 ``get_config_platforms`` 把它过滤掉,chat / /v1/models 都看不到。
    """
    platform_name = f"local-{capability}"
    try:
        from chayuan.server.db.repository.model_platform_repository import update_platform, get_platform
        if get_platform(platform_name):
            update_platform(platform_name, {"enabled": False}, updated_by="local-runtime-hook")
            logger.info("[local-runtime] disabled DB platform: %s", platform_name)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[local-runtime] update_platform(%s, enabled=False) failed: %r",
            platform_name, e,
        )


def _resolve_args_for(
    capability: str,
    *,
    engine: str = "llama",
    n_ctx: int | None = None,
    n_threads: int | None = None,
):
    """调对应 engine 的 process_args.resolve_*,返回 (resolution, model_path)。

    Plan 3D 起 engine 派发:
      * engine='llama'    → resolve_llamacpp_args(透传 n_ctx/n_threads)
      * engine='whisper'  → resolve_whisper_args(透传 n_threads)
      * engine='infinity' → resolve_image_embedding_args(透传 n_threads;
                            不是 Plan 1 老 resolve_infinity_args)
    """
    if engine == "llama":
        kwargs: dict = {"capability": capability}
        if n_ctx is not None:
            kwargs["n_ctx"] = n_ctx
        if n_threads is not None:
            kwargs["n_threads"] = n_threads
        r = process_args.resolve_llamacpp_args(**kwargs)
    elif engine == "whisper":
        kwargs = {"capability": capability}
        if n_threads is not None:
            kwargs["n_threads"] = n_threads
        r = process_args.resolve_whisper_args(**kwargs)
    elif engine == "infinity":
        kwargs = {"capability": capability}
        if n_threads is not None:
            kwargs["n_threads"] = n_threads
        r = process_args.resolve_image_embedding_args(**kwargs)
    else:
        raise ValueError(f"Unknown engine: {engine!r}")

    if r.missing:
        return r, None
    try:
        i = r.args.index("--model")
        return r, r.args[i + 1]
    except (ValueError, IndexError):
        return r, None


# 旧名兼容(Plan 1 测试用过 _resolve_chat_args);保留 alias 直到 Task 5 后清扫
def _resolve_chat_args(**kw):
    return _resolve_args_for("chat", **kw)


class SidecarRuntimeManager:
    """通用 sidecar (llama / whisper) 生命周期管理。

    Plan 3C 起从 LlamaRuntimeManager 改名,加 ``engine`` 参数:
      * ``engine='llama'``   → spawn llama-server (chat/embedding/rerank)
      * ``engine='whisper'`` → spawn whisper-server (asr)

    Plan 3B 已有的 ``capability`` / ``port_offset`` 等仍兼容。
    """

    # ── process-wide port 分配状态 ─────────────────────────────────
    # 原 _allocate_port 实现是 "bind 测试 → close → return port",bind 测试 close
    # 后到 spawn 子进程之间有个 race window:另一个 mgr 并发 _allocate_port 也能
    # bind 测试通过同一个 port → 两个 mgr 都把这个 port 当 endpoint 记下,但实际
    # 只有先 spawn 的 sidecar 占住物理端口,后 spawn 的撞 EADDRINUSE 或更糟 —
    # 调用方按 endpoint 拨号撞到错的 sidecar 拿到 FastAPI 404(asr 拨到 rerank
    # 的 llama-server,/inference 没路由 → 404 "File Not Found")。
    #
    # 修法:lock + class-level reserved set,allocate 成功就占住,stop 时 release。
    _PORT_LOCK: threading.Lock = threading.Lock()
    _RESERVED_PORTS: t.Set[int] = set()

    def __init__(
        self,
        *,
        chayuan_root: Path,
        engine: Literal["llama", "whisper"] = "llama",
        capability: str = "chat",
        port_offset: int = 0,
    ) -> None:
        self.chayuan_root = chayuan_root
        self.engine = engine
        self.capability = capability
        self.port_offset = port_offset
        # 本 mgr 当前持有的 port reservation;stop 时 discard 让下个 cycle 能复用
        self._allocated_port: Optional[int] = None
        self.settings_path = chayuan_root / "model_registry" / "local_runtime.yaml"
        self.status_path = chayuan_root / "runtime.json"
        self._settings = LocalRuntimeSettings.load(self.settings_path)
        self._status = RuntimeStatus(state="stopped")
        self._process = None  # subprocess.Popen 持有处
        # ── PIPE drain ring buffers + daemon threads(详见 _start_pipe_drainers)──
        # 必修:Popen(stdout=PIPE, stderr=PIPE) 后若不持续读,whisper-server 长
        # 跑会把内核 pipe buffer(Linux 64KB / Win 4KB)写满,子进程 fprintf 阻塞
        # → "用着用着卡死,重启就好"经典 Python subprocess 坑。
        import collections
        self._stdout_buf: collections.deque[str] = collections.deque(maxlen=200)
        self._stderr_buf: collections.deque[str] = collections.deque(maxlen=200)
        self._drainer_threads: list = []
        # ── 健康累计:audio.py 在 ReadTimeout 时调 mark_unhealthy()──
        # 累计 ≥ _UNHEALTHY_THRESHOLD 次或 _UNHEALTHY_WINDOW_SEC 秒内,下次请求
        # 进入 ensure_ready 走 restart()。
        self._unhealthy_events: list[float] = []
        self._unhealthy_lock = threading.Lock()

    @property
    def settings(self) -> LocalRuntimeSettings:
        return self._settings

    @property
    def status(self) -> RuntimeStatus:
        return self._status

    def find_server_exe(self) -> Optional[Path]:
        """跨平台找 vendor 进集成版的 sidecar 二进制。

        Plan 3D 起按 self.engine 选 binary 名和子目录:
          * engine='llama'    → services/llama-server/llama-server[.exe]
          * engine='whisper'  → services/whisper-server/whisper-server[.exe]
          * engine='infinity' → Python 解释器 sys.executable(Python -m 拉 sidecar)

        给路由 (/runtime/llama/install-info) 和 start() 共用。
        """
        if self.engine == "infinity":
            # Python sidecar: 直接用当前解释器拉 `-m chayuan.server.image_source.infinity_server`
            return Path(sys.executable)

        global _INSTALL_SERVICES_DIRS
        dirs = _INSTALL_SERVICES_DIRS if _INSTALL_SERVICES_DIRS is not None else _default_install_services_dirs()
        bin_name = f"{self.engine}-server"
        names = [f"{bin_name}.exe", bin_name]
        cands = _platform_subdir_candidates()
        for d in dirs:
            server_dir = d / bin_name
            # 优先 platform 子目录(预编译 binary 提交进 git 时的布局)
            # 按 candidate 列表顺序找(Win x64 默认 AVX2,缺时退到 noavx)
            for plat in cands:
                for name in names:
                    p = server_dir / plat / name
                    if p.is_file():
                        return p
            # 兜底:扁平布局(install 脚本现拉现写的旧落点)
            for name in names:
                p = server_dir / name
                if p.is_file():
                    return p
        return None

    def find_llama_server_exe(self) -> Optional[Path]:
        """Deprecated: Plan 3C 起改用 find_server_exe();此 alias 留作向后兼容。"""
        return self.find_server_exe()

    def _release_port_reservation(self) -> None:
        """stop 时把本 mgr 占住的 port 从 _RESERVED_PORTS 移除,让下次 start
        能复用。start 失败路径(spawn fail / health timeout)的清理走 _persist_status
        前面那串 stop()-equivalent — 这里只管简单 case。
        """
        if self._allocated_port is None:
            return
        with SidecarRuntimeManager._PORT_LOCK:
            SidecarRuntimeManager._RESERVED_PORTS.discard(self._allocated_port)
        self._allocated_port = None

    def _allocate_port(self, *, preferred: int) -> int:
        """从 preferred 开始往上找空闲端口 (上限 +20)。

        process-wide:锁 _PORT_LOCK + 跳过 _RESERVED_PORTS,bind 测试通过
        + 不在 reserved set 才 return,同时把 port 加进 reserved 集合 + 记到
        self._allocated_port。stop() 时 discard 释放。

        多 mgr 并发 start 时,即便两个 mgr 都从 62582 起 bump,先抢到 lock 的
        把 62582 reserve 掉,另一个会直接跳到 62583 探测,不会得出"两个 mgr
        都拿到 62586 当 endpoint"的错位。
        """
        import socket
        with SidecarRuntimeManager._PORT_LOCK:
            for offset in range(21):
                port = preferred + offset
                if port in SidecarRuntimeManager._RESERVED_PORTS:
                    continue
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    try:
                        s.bind(("127.0.0.1", port))
                    except OSError:
                        continue
                SidecarRuntimeManager._RESERVED_PORTS.add(port)
                self._allocated_port = port
                return port
        raise RuntimeError(f"没找到空闲端口 (从 {preferred} bump 了 20 次都被占)")

    async def start(self, *, model_id: str | None = None) -> RuntimeStatus:
        """spawn llama-server.exe。失败时返回 state=failed 的 RuntimeStatus。"""
        if self._status.state in ("starting", "ready"):
            return self._status

        self._status = RuntimeStatus(state="starting")

        exe = self.find_llama_server_exe()
        if exe is None:
            self._status = RuntimeStatus(
                state="failed",
                last_error="llama-server.exe 不在 vendor/services/llama-server/ 里;集成版未带,或开发环境没跑 install-llama-server.ps1",
            )
            self._persist_status()
            return self._status

        resolution, model_path = _resolve_args_for(self.capability, engine=self.engine)
        if resolution.missing or not model_path:
            self._status = RuntimeStatus(
                state="failed",
                last_error=f"{self.capability} 模型未就绪:{resolution.reason or f'no {self.capability} default'}",
            )
            self._persist_status()
            return self._status

        port = self._allocate_port(preferred=self._settings.port + self.port_offset)
        host = "0.0.0.0" if self._settings.expose_lan else self._settings.host

        if self.engine == "infinity":
            # Python sidecar:命令必须是 `python.exe -m <module> [module-args]`,
            # `-m` 必须紧跟解释器。原来跟 llama 共用拼装,Python 看到
            # `--host` 在 `-m` 之前会当成 Python 自己的 option → unknown option --host
            # 直接退。模块自己接 --host/--port/--model;不加 llama 专属的
            # --log-disable / --api-key / --threads(infinity 不认)。
            args = [str(exe)]
            args.extend(resolution.args)
            args.extend(["--host", host, "--port", str(port)])
        elif self.engine == "whisper":
            # whisper-server (whisper.cpp/examples/server) 支持 --host/--port/--model/--threads,
            # 不支持 --log-disable / --api-key(都是 llama-server 专属)。passing them
            # 会让 whisper-server 启动早期报错或 hang 等输入;避免传。
            args = [str(exe), "--host", host, "--port", str(port)]
            args.extend(resolution.args)
            if "--threads" not in resolution.args:
                args.extend(["--threads", str(min(8, os.cpu_count() or 4))])
        else:
            # llama-server:支持 --log-disable / --api-key,resolution.args 含 --model 等
            args = [str(exe), "--host", host, "--port", str(port), "--log-disable"]
            args.extend(resolution.args)
            if self._settings.api_key:
                args.extend(["--api-key", self._settings.api_key])
            if "--threads" not in resolution.args:
                args.extend(["--threads", str(min(8, os.cpu_count() or 4))])

        # 动态链接的 vendor binary(如 Docker 提取的 whisper-server / linux-arm64
        # llama-server)依赖同目录 .so / .dylib;loader 默认不看 exe 旁路径,需要
        # LD_LIBRARY_PATH(Linux)或 DYLD_LIBRARY_PATH(macOS)显式注入。
        # Win 没此问题(DLL search 默认包含 exe 同目录)。infinity engine 用 sys.executable
        # 跑 Python module,不需要这个。
        proc_env = None
        if self.engine != "infinity" and sys.platform != "win32":
            proc_env = os.environ.copy()
            exe_dir = str(exe.parent)
            var = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
            existing = proc_env.get(var, "")
            proc_env[var] = f"{exe_dir}:{existing}" if existing else exe_dir

        # infinity sidecar(image-embedding)的 CLIP 向量化硬依赖 PyTorch。
        # torch 可能装在本应用 py_packages/、用户指定的外部目录或系统 Python ——
        # 由「PyTorch 选用配置」决定。把生效目录加进子进程 PYTHONPATH;
        # mode=disabled 时直接拒绝启动。frozen 下子进程的 runtime hook
        # ensure_torch_target_on_syspath 也会兜底,这里再注入一次保证非 frozen /
        # 外部目录场景也命中。
        if self.engine == "infinity":
            try:
                from chayuan.server.runtime.pytorch_installer import (
                    get_torch_selection, resolve_active_torch_dir,
                    torch_dir_has_torch,
                )
                _sel = get_torch_selection()
                if _sel.get("mode") == "disabled":
                    self._status = RuntimeStatus(
                        state="failed",
                        last_error=(
                            "已在「设置 → 本地模型服务 → PyTorch」选择「不使用 PyTorch」;"
                            "image-embedding 依赖 PyTorch,无法启动。请改回「自动」"
                            "或指定一个已装好 torch 的目录。"
                        ),
                    )
                    self._persist_status()
                    self._release_port_reservation()
                    return self._status
                _active = resolve_active_torch_dir(selection=_sel)
                if _active and not torch_dir_has_torch(_active):
                    # 半套目录(有 torchvision/ 没 torch/)注进 sidecar PYTHONPATH
                    # 会让 import torch / torchvision 跨目录错配 → torchvision::nms
                    # 不存在。宁可不注,让 sidecar 从它自己的环境解析 torch。
                    logger.warning(
                        "[local-runtime] %s 缺 torch/ 子目录(半套安装),不注入"
                        " sidecar PYTHONPATH —— 避免 torch/torchvision 版本错配。",
                        _active,
                    )
                    _active = None
                if _active:
                    # 加进 sidecar PYTHONPATH 前,清掉老版本残留的 py_packages/yaml
                    # —— 它会盖住外层好的 yaml,让 infinity_server import yaml 崩。
                    _purge_stale_py_packages_yaml(_active)
                    if proc_env is None:
                        proc_env = os.environ.copy()
                    _existing_pp = proc_env.get("PYTHONPATH", "")
                    proc_env["PYTHONPATH"] = (
                        _active + (os.pathsep + _existing_pp if _existing_pp else "")
                    )
            except Exception as e:  # noqa: BLE001 — torch 选用探测失败不阻塞启动
                logger.debug("[local-runtime] torch 选用探测失败: %r", e)

        try:
            self._process = subprocess.Popen(
                args,
                # stdout / stderr 都走 PIPE — llama-server 启动期 fatal(model load
                # failure / OOM / arch mismatch / 不存在的 GGUF 文件等)可能打 stdout
                # 或 stderr,不固定。原来 stdout=DEVNULL 把 stdout 吞了,导致
                # last_error 空白拿不到任何诊断。两边都 PIPE,死时合并读一次。
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=proc_env,
                # Windows 下 CREATE_NO_WINDOW = 0x08000000,避免弹黑框
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
        except Exception as e:
            self._status = RuntimeStatus(state="failed", last_error=f"spawn failed: {e}")
            self._persist_status()
            self._release_port_reservation()
            return self._status

        proc = self._process  # snapshot:外部 stop() 会把 self._process 抢成 None,本地引用还能干净退出

        # 等 health 200(最长 HEALTH_READY_TIMEOUT_SEC 秒)。
        # 引擎不同 health endpoint 也不同:
        #   - llama-server (llama.cpp): GET /health 200 = 模型加载完成,可服务
        #   - whisper-server (whisper.cpp): 没有 /health,GET / 返 HTML UI 200
        #     (whisper-server 启动即加载模型,/ 200 等价于就绪)
        #   - infinity (Python sidecar): /health 200
        endpoint = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}"
        health_path = "/" if self.engine == "whisper" else "/health"
        ready = False
        deadline = datetime.now().timestamp() + HEALTH_READY_TIMEOUT_SEC
        while datetime.now().timestamp() < deadline:
            if self._process is not proc:
                # stop() 已经被 await 过,我们不要再覆盖它写的 stopped 状态
                return self._status
            if proc.poll() is not None:
                # 合并 stdout + stderr,llama-server 早期 fatal 打哪边不固定。
                # 截最后 800 字节避免 last_error 字段被超长堆栈撑爆。
                out_bytes = proc.stdout.read() if proc.stdout else b""
                err_bytes = proc.stderr.read() if proc.stderr else b""
                merged = (out_bytes + err_bytes).decode("utf-8", errors="replace").strip()
                if merged:
                    tail = merged[-800:]
                else:
                    # 一个字节都没捕获到时,至少把命令行给出来 —— 用户/开发者可以
                    # 手动跑这条命令直接看 llama-server 的真实报错(常见:vendored
                    # llama-server 太旧、不认新模型架构如 qwen3 → 静默退出码 1)。
                    _cmdline = " ".join(
                        f'"{a}"' if " " in str(a) else str(a) for a in args
                    )
                    tail = (
                        f"(无 stdout/stderr 输出,退出码 {proc.returncode});"
                        f"手动复现:{_cmdline}"
                    )
                # 二进制错位自检:本该是 llama.cpp / whisper.cpp 的原生 server,
                # 却跑出了 chayuan-server 本体的特征输出(Click Usage /
                # [mp-freeze] / rthook)—— 说明 services/<engine>-server/ 下放的
                # 二进制其实是 chayuan-server 本体(打包损坏 / 被错误替换)。它
                # 是个 Click CLI,收到 --host/--port 不认就打 Usage 退出。直接给
                # 可操作诊断,别甩给用户一坨看不懂的 Click usage。
                if self.engine in ("llama", "whisper") and any(
                    sig in merged for sig in (
                        "chayuan-server [OPTIONS]",
                        "[mp-freeze]",
                        "chayuan-rthook",
                    )
                ):
                    last_error = (
                        f"{self.engine}-server 二进制错位:{exe} 跑起来其实是 "
                        "chayuan-server 本体,而不是 llama.cpp / whisper.cpp 的"
                        "原生 server —— 多半是安装包里该二进制损坏 / 被错误替换。"
                        "请重装应用;若自行构建,确认 "
                        f"vendor/services/{self.engine}-server/<平台>/ 下放的是"
                        "真正的 server 二进制(而非 chayuan-server)后重新打包。"
                    )
                else:
                    last_error = f"llama-server 启动时退出:{tail}"
                self._status = RuntimeStatus(
                    state="failed",
                    last_error=last_error,
                )
                self._process = None
                self._persist_status()
                self._release_port_reservation()
                return self._status
            try:
                resp = await _probe_health(f"{endpoint}{health_path}", timeout=2.0)
                sc = getattr(resp, "status_code", None)
                # whisper-server 没带 static dir,GET / 返 404,但 "返回任何 HTTP 状态码"
                # = 进程起来 + HTTP listener 在听 + model 已加载(whisper-server 同步
                # 加载,listener 起来时 model 已 ready)。只 200 太严。
                # llama-server / infinity:/health 200 才算 model 加载完成,严格判 200。
                ok = (
                    (self.engine == "whisper" and sc is not None and 200 <= sc < 500)
                    or sc == 200
                )
                if ok:
                    ready = True
                    break
            except Exception:
                pass
            await asyncio.sleep(HEALTH_PROBE_INTERVAL_SEC)

        if not ready:
            if self._process is not proc:
                return self._status
            # 超时也 kill 掉别留尸
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                pass
            self._process = None
            self._status = RuntimeStatus(
                state="failed",
                last_error=f"启动 {HEALTH_READY_TIMEOUT_SEC}s 内 {health_path} 没返 200",
            )
            self._persist_status()
            self._release_port_reservation()
            return self._status

        if self._process is not proc:
            return self._status
        resolved_model_id = resolution.resolved_models.get(self.capability)
        self._status = RuntimeStatus(
            state="ready",
            endpoint=endpoint,
            pid=proc.pid,
            # 原来硬编码 "chat" key,导致非 chat sidecar status.model_id 永远 None
            # (诊断报告 embedding/rerank/asr/image-embedding model=None 的来源)。
            model_id=resolved_model_id,
            model_path=model_path,
            started_at=datetime.now(),
            last_health_at=datetime.now(),
        )
        self._persist_status()
        # ready 后立即把 PIPE 交给 daemon 线程持续 drain — 不然 whisper-server 跑一会
        # 就会被自己的 stderr 日志写满 kernel pipe buffer 卡死(本次定位到的根因)。
        self._start_pipe_drainers(proc)
        # 新 sidecar 起来,清掉旧的 unhealthy 计数 — 给个干净的窗口
        with self._unhealthy_lock:
            self._unhealthy_events.clear()
        # sidecar ready → 注册 MODEL_PLATFORMS,让 chat / embedding 调用路径能找到它
        try:
            _register_local_platform(
                capability=self.capability,
                host=host,
                port=port,
                model_id=resolved_model_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[local-runtime] register MODEL_PLATFORMS for %s failed: %r",
                self.capability, e,
            )
        return self._status

    async def stop(self, *, timeout: float = 10.0) -> None:
        if self._process is None:
            self._status = RuntimeStatus(state="stopped")
            self._persist_status()
            self._release_port_reservation()
            try:
                _deregister_local_platform(capability=self.capability)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[local-runtime] deregister MODEL_PLATFORMS for %s failed: %r",
                    self.capability, e,
                )
            return
        proc_local = self._process  # snapshot too,defensive
        try:
            proc_local.terminate()
            proc_local.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc_local.kill()
        except Exception as e:
            logger.warning("[local-runtime] stop terminate/wait error: %r", e)
        finally:
            self._process = None
        self._status = RuntimeStatus(state="stopped")
        self._persist_status()
        self._release_port_reservation()
        try:
            _deregister_local_platform(capability=self.capability)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[local-runtime] deregister MODEL_PLATFORMS for %s failed: %r",
                self.capability, e,
            )

    async def restart(self, *, model_id: str | None = None) -> RuntimeStatus:
        """等价于 stop + start;先写 state=restarting 让观察者看到过渡态。"""
        self._status = RuntimeStatus(state="restarting")
        self._persist_status()
        await self.stop()
        return await self.start(model_id=model_id)

    # ────────────────────────────────────────────────────────────────────
    # 健康自愈:PIPE drain + ReadTimeout 自动重启
    # ────────────────────────────────────────────────────────────────────

    # 在 _UNHEALTHY_WINDOW_SEC 秒内累计 ≥ _UNHEALTHY_THRESHOLD 次 ReadTimeout/异常 → 触发自动重启。
    # 用户报"识别用一会就卡死,重启 sidecar 又能用"的对应自愈门槛 — 2 次内即重启,
    # 不再让用户去设置面板手动点。
    _UNHEALTHY_THRESHOLD = 2
    _UNHEALTHY_WINDOW_SEC = 30.0

    def _start_pipe_drainers(self, proc: "subprocess.Popen") -> None:
        """ready 后用 daemon 线程持续 drain stdout/stderr 到 ring buffer。

        **根因修复**:`subprocess.Popen(stdout=PIPE, stderr=PIPE)` 启动后若不
        持续读 pipe,whisper-server / llama-server 的 inference 日志会把内核
        pipe buffer(Linux 64 KB / Win 4 KB)写满 → 子进程下次 fprintf 阻塞 →
        主线程在打 log 时卡死 → 用户报"录一会就识别不了,kill 重启又能用"。

        ring buffer 保留最后 200 行,任何观察点可以读 ``_stdout_buf / _stderr_buf``
        把日志贴进 last_error / diag — 既消除阻塞,又不丢诊断。
        """
        def _drain(pipe, buf: "collections.deque[str]", tag: str) -> None:
            try:
                for raw in iter(pipe.readline, b""):
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    if line:
                        buf.append(line)
            except Exception as e:  # noqa: BLE001
                logger.debug("[sidecar:%s] %s drain 退出: %r", self.capability, tag, e)

        # 之前 ready 后又重新调用 start → restart 路径会触发,先 join 旧线程避免泄漏
        for t in self._drainer_threads:
            if t.is_alive():
                # daemon=True 进程退出会被回收,这里不强 join 避免卡 ready 路径
                pass
        self._drainer_threads = []
        if proc.stdout is not None:
            t = threading.Thread(
                target=_drain, args=(proc.stdout, self._stdout_buf, "stdout"),
                name=f"sidecar-{self.capability}-stdout-drain", daemon=True,
            )
            t.start()
            self._drainer_threads.append(t)
        if proc.stderr is not None:
            t = threading.Thread(
                target=_drain, args=(proc.stderr, self._stderr_buf, "stderr"),
                name=f"sidecar-{self.capability}-stderr-drain", daemon=True,
            )
            t.start()
            self._drainer_threads.append(t)

    def recent_log_tail(self, n: int = 30) -> str:
        """拿最后 N 行 stdout+stderr — 给诊断 / call_log error 字段用。"""
        lines = list(self._stdout_buf)[-n:] + list(self._stderr_buf)[-n:]
        return "\n".join(lines)

    def mark_unhealthy(self, reason: str = "") -> bool:
        """audio.py / 调用方在 ReadTimeout / 持续 5xx 时调一次。

        累计到 _UNHEALTHY_THRESHOLD 次(_UNHEALTHY_WINDOW_SEC 秒窗口内)→
        把 status.state 改为 ``failed`` + 留 last_error,下次 ensure_ready 进 restart。
        返回:True 表示已触发 unhealthy 状态(caller 可选 trigger 异步 restart)。
        """
        now = time.time()
        with self._unhealthy_lock:
            self._unhealthy_events = [
                t for t in self._unhealthy_events if now - t < self._UNHEALTHY_WINDOW_SEC
            ]
            self._unhealthy_events.append(now)
            count = len(self._unhealthy_events)
        logger.warning(
            "[sidecar:%s] mark_unhealthy #%d/%d in %ds (reason=%s)",
            self.capability, count, self._UNHEALTHY_THRESHOLD,
            int(self._UNHEALTHY_WINDOW_SEC), reason or "n/a",
        )
        if count >= self._UNHEALTHY_THRESHOLD:
            log_tail = self.recent_log_tail(30)
            self._status = RuntimeStatus(
                state="failed",
                last_error=(
                    f"sidecar 在 {int(self._UNHEALTHY_WINDOW_SEC)}s 内累计 "
                    f"{count} 次失败(可能 PIPE 阻塞 / GGML 死锁)。"
                    f"\n--- 最近日志 ---\n{log_tail}"
                ),
            )
            self._persist_status()
            with self._unhealthy_lock:
                self._unhealthy_events.clear()
            return True
        return False

    async def ensure_ready(self, *, model_id: str | None = None) -> RuntimeStatus:
        """调用方进 transcribe 路径前调一次,确保 sidecar 处于 ready。

        语义:
          - state == 'ready'  → 直接返回,no-op
          - state in {'stopped','failed'} → 调 start()(failed 时等价于 restart)
          - state in {'starting','restarting'} → 等到 ready 或 30s 超时

        把"老 audio.py 里手撸的 ``if state != 'ready': loop.run_until_complete(start())``"
        路径搬上来,顺便兼容 ``failed`` 状态自动重启 — mark_unhealthy 之后下次请求即恢复。
        """
        st = self._status
        if st.state == "ready":
            return st
        # failed / stopped → 直接 start(start 内部不区分;若进程还在会先被覆写)
        if st.state in ("failed", "stopped"):
            logger.info(
                "[sidecar:%s] ensure_ready: state=%s → 触发 start (model_id=%s)",
                self.capability, st.state, model_id,
            )
            # failed 时多走一步 stop 把残留进程清掉 — 否则 _process 可能还活着
            if st.state == "failed" and self._process is not None:
                try:
                    await self.stop()
                except Exception as e:  # noqa: BLE001
                    logger.warning("[sidecar:%s] ensure_ready stop 失败: %r", self.capability, e)
            return await self.start(model_id=model_id)
        # starting / restarting → 短暂等待(最多 30s)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self._status.state == "ready":
                return self._status
            if self._status.state in ("failed", "stopped"):
                return await self.start(model_id=model_id)
            await asyncio.sleep(0.5)
        # 超时仍未 ready
        return self._status

    def set_config(self, update: dict) -> LocalRuntimeSettings:
        """部分更新设置 + 持久化 yaml。返回更新后的 settings。

        注意:不立即重启 llama-server,需要前端追一次 /runtime/llama/restart。
        """
        cur = dataclasses.asdict(self._settings)
        for k, v in update.items():
            if k in cur:
                cur[k] = v
        self._settings = LocalRuntimeSettings(**cur)
        self._settings.save(self.settings_path)
        return self._settings

    def _persist_status(self) -> None:
        """状态写 runtime.json,前端读。

        多 capability 共用同一文件,按 capability 分 key:
          {"llama": {"chat": {...}, "embedding": {...}, "rerank": {...}}}
        本 manager 只写自己 capability 那一段,其它段从磁盘 merge 不动。
        """
        try:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            existing: Dict[str, Any] = {}
            if self.status_path.is_file():
                try:
                    existing = json.loads(self.status_path.read_text(encoding="utf-8")) or {}
                except Exception:
                    existing = {}
            llama_section = existing.get("llama") if isinstance(existing.get("llama"), dict) else {}
            # Plan 1 兼容: 旧版本 runtime.json 是 {"llama": {state, endpoint, ...}} 直接展平,
            # 没有 chat/embedding/rerank 分层;检测到这种 shape 时清空重写 (chat 接管)。
            if "state" in llama_section and "chat" not in llama_section:
                llama_section = {}
            llama_section[self.capability] = self._status.to_dict()
            existing["llama"] = llama_section
            self.status_path.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass  # 状态写不进去不致命


# ─────────────────────── 进程内单例 ─────────────────────────

_singleton: Optional[LlamaRuntimeManager] = None  # 保留兼容 monkeypatch (Plan 1 测试用)


def get_manager() -> LlamaRuntimeManager:
    """进程内单例 = registry.get('chat')。

    Plan 1 老调用方拿到 chat manager;Plan 3B 多 capability 调用方
    用 get_registry().get(cap) 取对应 manager。
    """
    global _singleton
    if _singleton is not None:
        # monkeypatch 路径:Plan 1 测试直接给 _singleton 赋值,优先用
        return _singleton
    from chayuan.server.model_registry.local_runtime_registry import get_registry
    return get_registry().get("chat")


# Plan 3C: Plan 1+2+3B 已有 42 个引用都叫 LlamaRuntimeManager。
# 保留同名 thin alias,默认 engine='llama',旧代码零改动。
class LlamaRuntimeManager(SidecarRuntimeManager):
    """Back-compat alias(Plan 3C 起 SidecarRuntimeManager 的 engine='llama' 子类)。"""

    def __init__(self, **kw):
        kw.setdefault("engine", "llama")
        super().__init__(**kw)
