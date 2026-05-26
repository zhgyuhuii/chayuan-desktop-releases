"""运行时服务(sidecar 二进制)下载编排 — 「运行时服务双保险」的应用内补回侧。

为什么需要
==========

本地模型运行时依赖两个 sidecar 二进制:

* ``llama-server``  —— chat / embedding / rerank 引擎(llama.cpp)
* ``whisper-server`` —— 语音识别引擎(whisper.cpp)

它们按平台放在 ``chayuan-server/vendor/services/<engine>-server/<platform>/``,
完整安装包构建时由 ``scripts/install-vendor.ps1`` 预下载、再被打进 Tauri
resources。但有两种情况会导致装机后**缺二进制**:

* 用户拿到的是轻量版安装包(不含 services);
* 完整包构建时开发者忘了先跑 install-vendor(已由 build-desktop.ps1 的
  fail-fast 校验拦住,但历史包仍可能有此坑)。

本模块就是「应用内按平台下载补回」入口 —— 跟 ``model_registry/install_model.py``
同构:异步 job + 逐步进度 + 取消 + 诊断日志。前端「本地模型服务」设置页
的「运行时服务」区调它。

设计要点
========

* **In-process,不 subprocess 脚本**:``scripts/install-llama-server.*`` /
  ``install-whisper-server.sh`` 只在开发仓库里,**不随安装包发布**(Tauri
  resources 只含 ``services/**`` 和 ``bundled_models/**``)。所以装机后无法
  shell out 跑那些脚本,必须在 sidecar 进程内直接下载 + 解压。
* **下载来源 / 镜像表对齐脚本**:GitHub release URL schema、版本号、镜像
  自动探测清单(gh-proxy.com / ghfast.top)都跟 ``install-llama-server.*``
  一致 —— 真源集中在本模块顶部常量,脚本侧注释指回这里,避免维护两份。
* **平台识别复用后端已有逻辑**:``local_runtime._platform_subdir_candidates()``
  —— 不在本模块或前端硬编码平台名。
* **whisper-server 只 Windows 有预编译**:upstream 不发 Linux/Mac whisper
  二进制(``install-whisper-server.sh`` 走 docker/brew/源码)。本模块只在
  当前平台是 win-x64 / win-arm64 时支持下载 whisper-server,其它平台返回
  明确的「不支持自动下载」诊断,不静默假装成功。
* **写入运行时真正查找的 services 目录**:落到 ``find_server_exe`` 会扫到的
  第一个可写 services 根下的 ``<engine>-server/<platform>/`` 子目录。
* **每个 engine 同时只能一个 running 任务**:重复请求拒绝,避免硬盘竞争。
* **进度 / 诊断,失败不静默**:逐步骤更新 ``progress_pct``,失败把 URL /
  HTTP 状态 / 异常都写进 ``log_tail``。
"""
from __future__ import annotations

import io
import logging
import os
import threading
import time
import uuid
import zipfile
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Deque, Dict, List, Optional

logger = logging.getLogger("chayuan.runtime.install_service")


class ServiceJobStatus(str, Enum):
    """任务状态机 —— queued → running → (succeeded | failed | cancelled)。"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ────────────────────────────────────────────────────────────────────────
# 下载规格 —— 真源。scripts/install-llama-server.{ps1,sh} /
# install-whisper-server.{ps1,sh} 必须跟这里保持一致(改一边记得同步另一边)。
# ────────────────────────────────────────────────────────────────────────

# llama.cpp release tag。与 install-llama-server.ps1 的 $Version 默认值对齐。
_LLAMA_VERSION = "b9174"
# whisper.cpp release tag。与 install-whisper-server.* 的 VERSION 默认值对齐。
_WHISPER_VERSION = "v1.7.6"

# GitHub release URL 用的 repo 路径(跟 install-*.{ps1,sh} 的 URL 一致;
# ggerganov/* 会被 GitHub 永久重定向到 ggml-org/*,沿用脚本里的写法)。
_LLAMA_REPO = "ggerganov/llama.cpp"
_WHISPER_REPO = "ggerganov/whisper.cpp"

# GitHub 镜像自动探测清单 —— 跟 install-vendor.ps1 的 $AUTO_GH_MIRRORS 一致。
# 墙内 github.com 不通时按顺序探一个能用的。2026-05 实测可用。
_GH_MIRRORS = ("https://gh-proxy.com/", "https://ghfast.top/")

# vendor 平台子目录名(local_runtime._platform_subdir_candidates 给的) →
# llama.cpp release 的 win/linux/macos zip 资产名片段。
# b5400 起 win 把 avx/avx2/avx512/noavx 合并成单一 -cpu-x64.zip(运行时 SIMD
# dispatch);默认版本 b9174 ≥ 5400 走新 schema。
def _llama_asset_for(platform: str, version: str) -> Optional[str]:
    """vendor 平台名 → llama.cpp release zip 资产名。无映射返回 None。"""
    ver_num = 0
    if version.startswith("b") and version[1:].isdigit():
        ver_num = int(version[1:])
    new_schema = ver_num >= 5400
    if platform in ("win-x64", "win-x64-avx", "win-x64-avx512", "win-x64-noavx"):
        return (
            f"llama-{version}-bin-win-cpu-x64.zip"
            if new_schema
            else f"llama-{version}-bin-win-avx2-x64.zip"
        )
    if platform == "win-arm64":
        return (
            f"llama-{version}-bin-win-cpu-arm64.zip"
            if new_schema
            else f"llama-{version}-bin-win-llvm-arm64.zip"
        )
    if platform == "linux-x64":
        return f"llama-{version}-bin-ubuntu-x64.zip"
    if platform == "macos-x64":
        return f"llama-{version}-bin-macos-x64.zip"
    if platform == "macos-arm64":
        return f"llama-{version}-bin-macos-arm64.zip"
    # linux-arm64:upstream 没发 zip(install-llama-server.sh 同样拒绝)
    return None


def _whisper_asset_for(platform: str, version: str) -> Optional[str]:
    """vendor 平台名 → whisper.cpp release zip 资产名。

    whisper.cpp upstream **只发 Windows x64 预编译 zip**(``whisper-bin-x64.zip``,
    v1.7.6+ 才有 —— 与 install-whisper-server.ps1 的 $Asset 一致)。Linux / macOS /
    Windows ARM64 都没有预编译,需 docker / brew / 源码(install-whisper-server.sh
    走那条路)。所以本函数只对 win x64 返回资产名,其它平台返回 None。
    """
    _ = version  # whisper win zip 资产名跟版本无关
    if platform in ("win-x64", "win-x64-avx", "win-x64-avx512", "win-x64-noavx"):
        return "whisper-bin-x64.zip"
    # win-arm64 / linux-* / macos-* —— upstream 无预编译
    return None


# engine → (binary 主名 base, release repo, 资产名解析函数, 版本)
_ENGINE_SPECS = {
    "llama-server": {
        "binary": "llama-server",
        "repo": _LLAMA_REPO,
        "version": _LLAMA_VERSION,
        "asset_for": _llama_asset_for,
    },
    "whisper-server": {
        "binary": "whisper-server",
        "repo": _WHISPER_REPO,
        "version": _WHISPER_VERSION,
        "asset_for": _whisper_asset_for,
    },
}

_ALLOWED_ENGINES = tuple(_ENGINE_SPECS.keys())

# 解压后要从 zip 里挑出来的文件(主 binary + 共享库 + Metal shader),
# 跟 install-llama-server.* 的 find/copy 过滤规则一致。
_KEEP_SUFFIXES = (".dll", ".so", ".dylib", ".metal")
_KEEP_EXACT = ("ggml-metal.metal",)


def _platform_candidates() -> List[str]:
    """当前 OS/架构的 vendor 平台子目录候选 —— 复用 local_runtime 已有逻辑。"""
    from chayuan.server.model_registry.local_runtime import (
        _platform_subdir_candidates,
    )

    return _platform_subdir_candidates()


def _current_platform() -> Optional[str]:
    """当前平台的首选 vendor 子目录名(候选列表第一个)。"""
    cands = _platform_candidates()
    return cands[0] if cands else None


def _services_root() -> Optional[Path]:
    """挑一个运行时真正查找、且可写的 services 根目录。

    ``local_runtime._default_install_services_dirs()`` 返回若干候选(集成版
    install dir、dev 仓库 vendor/services 等)。下载补回时优先写「已经存在」
    的目录(集成版装机后那个);都不存在时取第一个候选并创建。
    """
    from chayuan.server.model_registry.local_runtime import (
        _default_install_services_dirs,
    )

    cands = _default_install_services_dirs()
    # 优先已存在的(集成版 install dir/services 或 dev vendor/services)
    for d in cands:
        if d.is_dir():
            return d
    # 都不存在 —— 取第一个候选,稍后 mkdir
    return cands[0] if cands else None


def _is_main_binary(name: str, binary_base: str) -> bool:
    """判断 zip 内某文件是否是 engine 主 binary(``foo`` / ``foo.exe``)。"""
    return name in (binary_base, f"{binary_base}.exe")


def _should_keep(name: str, binary_base: str) -> bool:
    """zip 内某文件是否要拷进 services 目录(主 binary / 共享库 / shader)。"""
    if _is_main_binary(name, binary_base):
        return True
    if name in _KEEP_EXACT:
        return True
    lower = name.lower()
    if lower.endswith(_KEEP_SUFFIXES):
        return True
    # 版本化共享库:libggml.so.0.11.1
    return ".so." in lower


@dataclass
class ServiceInstallJob:
    """单次 sidecar 二进制下载任务的运行时记录。

    Attributes:
        id: UUID4 字符串。
        engine: ``llama-server`` / ``whisper-server``。
        platform: 目标 vendor 平台子目录名(如 ``win-x64``)。
        mirror: 实际使用的 GitHub 镜像前缀(``''`` = 直连)。
        status: 状态机当前值。
        started_at / ended_at: epoch 秒。
        exit_code: 0 成功 / 1 失败 / -1 取消。
        step: ``resolving`` / ``downloading`` / ``extracting`` / ``verifying`` / ``done``。
        progress_pct: 0-100。
        progress_message: 前端 banner 直接显示。
        target_dir: 解压落地的 services 子目录。
        log_tail: 诊断行缓冲(最多 200 行)。
    """

    id: str
    engine: str
    platform: str
    status: ServiceJobStatus
    started_at: float
    mirror: str = ""
    ended_at: Optional[float] = None
    exit_code: Optional[int] = None
    step: str = ""
    progress_pct: float = 0.0
    progress_message: str = ""
    target_dir: Optional[str] = None
    log_tail: Deque[str] = field(default_factory=lambda: deque(maxlen=200))
    _cancel_requested: bool = field(default=False, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "engine": self.engine,
            "platform": self.platform,
            "mirror": self.mirror,
            "status": self.status.value,
            "step": self.step,
            "started_at": float(self.started_at),
            "ended_at": float(self.ended_at) if self.ended_at is not None else None,
            "exit_code": int(self.exit_code) if self.exit_code is not None else None,
            "progress_pct": round(float(self.progress_pct), 2),
            "progress_message": self.progress_message,
            "target_dir": self.target_dir,
            "log_tail": list(self.log_tail),
        }


class _Cancelled(Exception):
    """内部信号:任务被请求取消。"""


class ServiceInstallJobManager:
    """线程安全的 sidecar 二进制下载任务注册中心。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: Dict[str, ServiceInstallJob] = {}

    # ── public API ────────────────────────────────────────────────

    def start(self, *, engine: str, platform: Optional[str] = None) -> ServiceInstallJob:
        """启动新下载任务。

        Args:
            engine: ``llama-server`` / ``whisper-server``。
            platform: 目标 vendor 平台;``None`` = 当前 host 平台(后端探测)。

        Raises:
            ValueError: engine 不在白名单,或平台无法识别 / 无可下载资产。
            RuntimeError: 同 engine 已有 queued / running 任务。
        """
        engine = (engine or "").strip()
        if engine not in _ALLOWED_ENGINES:
            raise ValueError(
                f"unsupported engine {engine!r}; allowed: {list(_ALLOWED_ENGINES)}"
            )

        plat = (platform or "").strip() or _current_platform()
        if not plat:
            raise ValueError(
                "无法识别当前平台;后端不支持此 OS/架构的运行时服务自动下载"
            )

        spec = _ENGINE_SPECS[engine]
        asset = spec["asset_for"](plat, spec["version"])  # type: ignore[operator]
        if not asset:
            if engine == "whisper-server":
                raise ValueError(
                    f"whisper-server 在 {plat} 上无预编译版本(upstream 只发 Windows)。"
                    "Linux/macOS 需通过 docker / brew / 源码安装 —— "
                    "见 scripts/install-whisper-server.sh。"
                )
            raise ValueError(
                f"{engine} 在 {plat} 上无可下载的预编译资产"
            )

        with self._lock:
            for j in self._jobs.values():
                if j.engine == engine and j.status in (
                    ServiceJobStatus.QUEUED,
                    ServiceJobStatus.RUNNING,
                ):
                    raise RuntimeError(
                        f"{engine} 已有运行中任务 {j.id} (status={j.status.value})"
                    )
            job = ServiceInstallJob(
                id=str(uuid.uuid4()),
                engine=engine,
                platform=plat,
                status=ServiceJobStatus.QUEUED,
                started_at=time.time(),
            )
            self._jobs[job.id] = job

        t = threading.Thread(
            target=self._run,
            args=(job,),
            daemon=True,
            name=f"install-svc-{engine}-{job.id[:8]}",
        )
        t.start()
        return job

    def get(self, job_id: str) -> Optional[ServiceInstallJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> List[ServiceInstallJob]:
        """按 started_at 倒序返回所有已知任务。"""
        with self._lock:
            return sorted(
                self._jobs.values(), key=lambda j: j.started_at, reverse=True
            )

    def cancel(self, job_id: str) -> ServiceInstallJob:
        """请求中止 ``job_id``。终态 job 为 no-op。

        Raises:
            KeyError: job_id 未知。
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"unknown job_id {job_id!r}")
            if job.status in (
                ServiceJobStatus.SUCCEEDED,
                ServiceJobStatus.FAILED,
                ServiceJobStatus.CANCELLED,
            ):
                return job
            job._cancel_requested = True
        return job

    # ── internals ─────────────────────────────────────────────────

    def _run(self, job: ServiceInstallJob) -> None:
        """后台线程:解析镜像 → 下载 zip → 解压 → 校验。"""
        with self._lock:
            job.status = ServiceJobStatus.RUNNING
            job.step = "resolving"
            job.progress_pct = 2.0
            job.progress_message = "解析下载地址"
            job.log_tail.append(
                f"[install_service] start engine={job.engine} platform={job.platform}"
            )

        try:
            spec = _ENGINE_SPECS[job.engine]
            version = spec["version"]
            asset = spec["asset_for"](job.platform, version)  # type: ignore[operator]
            binary_base = spec["binary"]
            repo = spec["repo"]
            raw_url = (
                f"https://github.com/{repo}/releases/download/{version}/{asset}"
            )

            self._check_cancel(job)

            # 镜像探测:github.com 直连优先,不通就探内置镜像清单。
            mirror = self._resolve_mirror(job)
            url = (mirror.rstrip("/") + "/" + raw_url) if mirror else raw_url
            with self._lock:
                job.mirror = mirror
                job.log_tail.append(f"[install_service] 下载 URL: {url}")

            # 下载 zip 到内存(llama/whisper zip 几十 MB,内存可承受)。
            with self._lock:
                job.step = "downloading"
                job.progress_pct = 15.0
                job.progress_message = f"下载 {asset}"
            zip_bytes = self._download(job, url)

            self._check_cancel(job)

            # 解压到目标 services 子目录。
            with self._lock:
                job.step = "extracting"
                job.progress_pct = 70.0
                job.progress_message = "解压并写入运行时目录"
            target_dir = self._extract(job, zip_bytes, binary_base, version)

            self._check_cancel(job)

            # 校验主 binary 落地。
            with self._lock:
                job.step = "verifying"
                job.progress_pct = 92.0
                job.progress_message = "校验二进制"
            self._verify(job, target_dir, binary_base)

        except _Cancelled:
            self._set_cancelled(job)
            return
        except Exception as e:  # noqa: BLE001
            logger.exception("install_service %s 下载失败", job.engine)
            self._fail(job, f"下载失败: {type(e).__name__}: {e}")
            return

        with self._lock:
            if job._cancel_requested:
                self._set_cancelled(job)
                return
            job.step = "done"
            job.status = ServiceJobStatus.SUCCEEDED
            job.exit_code = 0
            job.progress_pct = 100.0
            job.progress_message = "运行时服务已就绪"
            job.ended_at = time.time()
            job.log_tail.append("[install_service] success")

    def _resolve_mirror(self, job: ServiceInstallJob) -> str:
        """决定用哪个 GitHub 镜像。

        优先级跟 install-vendor.ps1 一致:
          1. 已配置的 ``GITHUB_MIRROR`` 环境变量(显式)
          2. github.com 直连可达 → 不用镜像
          3. 自动探内置镜像清单(gh-proxy.com / ghfast.top)
        """
        env_mirror = os.environ.get("GITHUB_MIRROR", "").strip()
        if env_mirror:
            with self._lock:
                job.log_tail.append(
                    f"[install_service] 使用 GITHUB_MIRROR 环境变量: {env_mirror}"
                )
            return env_mirror

        if self._url_reachable("https://github.com/"):
            with self._lock:
                job.log_tail.append("[install_service] github.com 直连可达")
            return ""

        with self._lock:
            job.log_tail.append(
                "[install_service] github.com 不可达,自动探测镜像…"
            )
        for cand in _GH_MIRRORS:
            if self._url_reachable(cand):
                with self._lock:
                    job.log_tail.append(
                        f"[install_service] 自动选用镜像: {cand}"
                    )
                return cand
        # 都不通 —— 仍返回空,让下载阶段直连失败给出明确诊断
        with self._lock:
            job.log_tail.append(
                "[install_service] 内置镜像均不可达,将直连 github.com(可能失败)"
            )
        return ""

    @staticmethod
    def _url_reachable(url: str, timeout: float = 6.0) -> bool:
        """HEAD/GET 探一个 URL 是否可达。4xx/5xx 也算「网络通了」。"""
        try:
            import httpx

            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.head(url)
                return resp.status_code < 600
        except Exception:  # noqa: BLE001
            return False

    def _download(self, job: ServiceInstallJob, url: str) -> bytes:
        """流式下载 zip 到内存,带进度更新。"""
        import httpx

        buf = io.BytesIO()
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"下载返回 HTTP {resp.status_code}(URL: {url})"
                    )
                total = int(resp.headers.get("content-length") or 0)
                got = 0
                for chunk in resp.iter_bytes(chunk_size=256 * 1024):
                    self._check_cancel(job)
                    buf.write(chunk)
                    got += len(chunk)
                    if total > 0:
                        # 下载阶段占 15% → 68% 的进度区间
                        frac = got / total
                        with self._lock:
                            job.progress_pct = round(15.0 + frac * 53.0, 2)
        data = buf.getvalue()
        with self._lock:
            job.log_tail.append(
                f"[install_service] 下载完成 {len(data) / 1024 / 1024:.1f} MB"
            )
        return data

    def _extract(
        self,
        job: ServiceInstallJob,
        zip_bytes: bytes,
        binary_base: str,
        version: str,
    ) -> Path:
        """解压 zip,把主 binary + 共享库拷进 services/<engine>/<platform>/。"""
        root = _services_root()
        if root is None:
            raise RuntimeError("无法定位运行时 services 目录")
        target_dir = root / job.engine / job.platform
        target_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            job.target_dir = str(target_dir)
            job.log_tail.append(f"[install_service] 目标目录: {target_dir}")

        # 清旧 binary / 共享库(保留 LICENSE 等),跟 install 脚本一致。
        for old in target_dir.iterdir():
            if old.is_file() and _should_keep(old.name, binary_base):
                try:
                    old.unlink()
                except OSError:
                    pass

        n_copied = 0
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except zipfile.BadZipFile as e:
            raise RuntimeError(f"下载到的不是有效 zip: {e}") from e

        with zf:
            members = [m for m in zf.namelist() if not m.endswith("/")]
            for member in members:
                name = Path(member).name
                if not _should_keep(name, binary_base):
                    continue
                dst = target_dir / name
                with zf.open(member) as src, open(dst, "wb") as out:
                    out.write(src.read())
                # 主 binary / Linux/Mac 二进制补可执行位
                if _is_main_binary(name, binary_base) or name == binary_base:
                    try:
                        os.chmod(dst, 0o755)
                    except OSError:
                        pass
                n_copied += 1

        if n_copied == 0:
            raise RuntimeError(
                f"zip 内未找到 {binary_base} 主 binary 或共享库 —— "
                "资产结构可能已变"
            )

        # 写版本元数据(跟 install 脚本的 VERSION 文件一致)
        try:
            (target_dir / "VERSION").write_text(
                f"{version}\n{time.strftime('%Y-%m-%d')}\n",
                encoding="utf-8",
            )
        except OSError:
            pass

        with self._lock:
            job.log_tail.append(
                f"[install_service] 解压完成,写入 {n_copied} 个文件"
            )
        return target_dir

    def _verify(
        self, job: ServiceInstallJob, target_dir: Path, binary_base: str
    ) -> None:
        """校验主 binary 确实落地。"""
        for name in (f"{binary_base}.exe", binary_base):
            p = target_dir / name
            if p.is_file() and p.stat().st_size > 0:
                with self._lock:
                    job.log_tail.append(
                        f"[install_service] 校验通过: {p} "
                        f"({p.stat().st_size / 1024 / 1024:.1f} MB)"
                    )
                return
        raise RuntimeError(
            f"校验失败:{binary_base} 主 binary 未出现在 {target_dir}"
        )

    def _check_cancel(self, job: ServiceInstallJob) -> None:
        with self._lock:
            if job._cancel_requested:
                raise _Cancelled()

    def _set_cancelled(self, job: ServiceInstallJob) -> None:
        with self._lock:
            job.status = ServiceJobStatus.CANCELLED
            job.exit_code = -1
            job.ended_at = time.time()
            job.log_tail.append("[install_service] cancelled")

    def _fail(self, job: ServiceInstallJob, msg: str) -> None:
        with self._lock:
            job.log_tail.append(f"[install_service] {msg}")
            job.status = ServiceJobStatus.FAILED
            job.exit_code = 1
            job.ended_at = time.time()


# ─────────────────────── 进程内单例 ───────────────────────

_SINGLETON: Optional[ServiceInstallJobManager] = None
_SINGLETON_LOCK = threading.Lock()


def get_install_service_manager() -> ServiceInstallJobManager:
    """全局唯一 :class:`ServiceInstallJobManager`。"""
    global _SINGLETON
    if _SINGLETON is None:
        with _SINGLETON_LOCK:
            if _SINGLETON is None:
                _SINGLETON = ServiceInstallJobManager()
    return _SINGLETON


def _reset_manager_for_test() -> None:
    """测试 hook:清空单例。"""
    global _SINGLETON
    with _SINGLETON_LOCK:
        _SINGLETON = None


def _locate_engine_binary(binary_base: str) -> Optional[Path]:
    """在所有候选 services 目录里找 ``<engine>-server`` 主 binary。

    走 ``find_server_exe`` 同一套查找规则:平台子目录优先,扁平 layout 兜底。
    跟它共用 ``_default_install_services_dirs`` / ``_platform_subdir_candidates``,
    不另写一套查找逻辑。
    """
    from chayuan.server.model_registry.local_runtime import (
        _default_install_services_dirs,
        _platform_subdir_candidates,
    )

    names = (f"{binary_base}.exe", binary_base)
    plats = _platform_subdir_candidates()
    for d in _default_install_services_dirs():
        engine_dir = d / binary_base
        for plat in plats:
            for name in names:
                p = engine_dir / plat / name
                if p.is_file():
                    return p
        # 扁平 layout 兜底(集成版装机后 sync_services 的落点)
        for name in names:
            p = engine_dir / name
            if p.is_file():
                return p
    return None


def service_status() -> Dict[str, object]:
    """汇总当前平台两个 sidecar 引擎的就绪状态 —— 给前端「运行时服务」区。

    Returns:
        ``{platform, engines: [{engine, binary, present, downloadable, path,
        version, reason}]}``。

        * ``present``      —— 当前平台已有可用 binary。
        * ``downloadable`` —— 缺失时是否支持应用内下载补回(whisper 在非 Win
          平台为 False)。
        * ``reason``       —— present=False 时的人类可读说明。
    """
    plat = _current_platform()
    engines: List[Dict[str, object]] = []
    for engine, spec in _ENGINE_SPECS.items():
        binary_base = spec["binary"]
        exe_path: Optional[str] = None
        version: Optional[str] = None
        try:
            found = _locate_engine_binary(binary_base)
            if found is not None:
                exe_path = str(found)
                v_file = found.parent / "VERSION"
                if v_file.is_file():
                    try:
                        version = v_file.read_text(
                            encoding="utf-8"
                        ).splitlines()[0].strip()
                    except OSError:
                        version = None
        except Exception as e:  # noqa: BLE001
            logger.debug("[install_service] service_status find_exe 失败: %r", e)

        present = exe_path is not None
        asset = (
            spec["asset_for"](plat, spec["version"])  # type: ignore[operator]
            if plat
            else None
        )
        downloadable = asset is not None
        reason: Optional[str] = None
        if not present:
            if not plat:
                reason = "无法识别当前平台,不支持自动下载"
            elif not downloadable:
                reason = (
                    f"{engine} 在 {plat} 上无预编译版本(upstream 只发 Windows),"
                    "需通过 docker / brew / 源码安装"
                )
            else:
                reason = "未随安装包内置,可一键下载补回"
        engines.append(
            {
                "engine": engine,
                "binary": binary_base,
                "present": present,
                "downloadable": downloadable,
                "path": exe_path,
                "version": version,
                "reason": reason,
            }
        )
    return {"platform": plat, "engines": engines}


__all__ = [
    "ServiceInstallJob",
    "ServiceInstallJobManager",
    "ServiceJobStatus",
    "get_install_service_manager",
    "service_status",
]
