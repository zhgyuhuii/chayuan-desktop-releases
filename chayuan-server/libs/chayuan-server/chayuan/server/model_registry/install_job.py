"""模型下载编排 — 给 GUI 一个能直接在 sidecar 内拉模型的入口。

为什么需要
==========

``GET /admin/models/bootstrap`` 已经能告诉前端"缺哪些 capability + 推荐哪个
release",但用户拿到这个提示后:

* 期望:GUI 上点"下载推荐包",server 后台拉下来即可,有进度条。

本模块就是中间这层 —— 接前端 POST 请求,在 server 进程的后台线程里**直接
调 ``huggingface_hub``** 下载模型(2026-05-15 重写,不再 subprocess
``chayuan_packaging``,因为 PyInstaller frozen sidecar 没法 ``-m``),把进度
写进 :class:`InstallJob` 让前端轮询。

设计要点
========

* **In-process,不 subprocess**:之前用 ``sys.executable -m chayuan_packaging``,
  在 PyInstaller frozen 模式下 ``sys.executable`` 是 sidecar exe,根本不支持
  ``-m``;且 ``chayuan_packaging`` 没在 sidecar 包内。直接调
  :func:`huggingface_hub.hf_hub_download` / :func:`snapshot_download` 才能
  在装机后真正可用。
* **逐文件进度**:hf_hub 没暴露 per-byte callback,但每个 release 才 4-7 个
  模型条目,per-file 进度足够前端展示("正在下载 3/7: BAAI/bge-m3,42%")。
* **写入用户数据目录**:模型落到 ``<CHAYUAN_ROOT>/models/bundled/<cap>/``
  (与 :func:`seed_bundled_models` 同根),装完即被 ``scan_once`` 扫到。
  **不**写到 ``vendor/bundled_models/``(那是 Tauri 打包时的暂存,运行时
  通常只读)。
* **每个 release 同时只能一个 running 任务**:重复请求拒绝;避免硬盘竞争。
* **内存状态,无持久化**:server 重启后 in-flight 任务被遗忘 — 已下载的
  文件 ``hf_hub`` 自己的 cache 会跳过,重新请求等价于续跑。
* **镜像白名单**:防止用户传任意 URL 触发 SSRF;只支持 ``hf-mirror`` /
  ``huggingface`` 两条已知路径,通过 ``HF_ENDPOINT`` env 注入。
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger("chayuan.model_registry.install_job")


class JobStatus(str, Enum):
    """任务状态机 —— queued → running → (succeeded | failed | cancelled)。"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ALLOWED_RELEASES = ("lite", "standard", "pro")

# 镜像 → ``HF_ENDPOINT`` 环境变量值。``huggingface_hub`` 通过该 env 切镜像。
# 用户不传 mirror 时默认走 ``hf-mirror`` —— 国内网络下唯一能稳的选择。
_MIRROR_ENDPOINTS = {
    "hf-mirror":   "https://hf-mirror.com",
    "huggingface": "https://huggingface.co",
}


# ── Release 模型清单(与 vendor/bundled_models/README.md 的"推荐清单"对齐) ──
# 每条:
#   * repo:HuggingFace repo id
#   * file:可选;有值时单文件下载,否则整仓 snapshot
#   * cap:capability tag(决定落到 <cap>/ 子目录)
#   * allow_patterns:可选,snapshot 时按 glob 限制下载内容(过滤 png / fp16 等无用大文件)
#
# 所有 repo + file 都已在 hf-mirror.com 实测 HEAD 200(详见
# vendor/bundled_models/README.md「推荐模型清单」)。layout.yaml 是
# chayuan_packaging fetch 用的真源,本表是 in-sidecar 副本,改了任一边
# 记得同步另一边。
_RELEASE_MANIFEST: Dict[str, List[Dict[str, Any]]] = {
    "lite": [
        {
            "repo": "unsloth/Qwen3-4B-Instruct-2507-GGUF",
            "file": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            "cap": "chat",
            "approx_bytes": 2_500_000_000,
        },
        # embedding / rerank 必须是 GGUF 单文件 —— 运行时引擎是 llama-server
        # (--embedding / --reranking),只吃 GGUF;装 BAAI/* 的 safetensors
        # 整仓 sidecar 直接起不来(见 server/utils.py 的报错文案)。
        {
            "repo": "gpustack/bge-m3-GGUF",
            "file": "bge-m3-Q5_K_M.gguf",
            "cap": "embedding",
            "approx_bytes": 468_000_000,
        },
        {
            "repo": "gpustack/bge-reranker-v2-m3-GGUF",
            "file": "bge-reranker-v2-m3-Q4_K_M.gguf",
            "cap": "rerank",
            "approx_bytes": 438_000_000,
        },
        {
            "repo": "ggerganov/whisper.cpp",
            "file": "ggml-tiny.bin",
            "cap": "asr",
            "approx_bytes": 80_000_000,
        },
    ],
    "standard": [
        # standard = lite + OCR + 中文大向量
    ],
    "pro": [
        # pro = standard + 7B chat
    ],
}
# standard = lite + extras
_RELEASE_MANIFEST["standard"] = _RELEASE_MANIFEST["lite"] + [
    {
        "repo": "SWHL/RapidOCR",
        "cap": "ocr",
        "allow_patterns": [
            "PP-OCRv4/ch_PP-OCRv4_det_infer.onnx",
            "PP-OCRv4/ch_PP-OCRv4_rec_infer.onnx",
            "PP-OCRv3/ch_ppocr_mobile_v2.0_cls_train.onnx",
        ],
        "approx_bytes": 16_000_000,
    },
]
# pro = standard 但 chat 换成 7B
_RELEASE_MANIFEST["pro"] = [
    {
        "repo": "bartowski/Qwen2.5-7B-Instruct-GGUF",
        "file": "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        "cap": "chat",
        "approx_bytes": 4_700_000_000,
    },
] + _RELEASE_MANIFEST["standard"][1:]  # 复用 standard 的非 chat 条目


def _release_models(release: str) -> List[Dict[str, Any]]:
    """返回 release 的模型清单(深拷贝,调用方可放心修改)。"""
    if release not in _RELEASE_MANIFEST:
        raise ValueError(f"unknown release {release!r}")
    return [dict(m) for m in _RELEASE_MANIFEST[release]]


def _user_bundled_root() -> Path:
    """``<CHAYUAN_ROOT>/models/bundled/`` —— 下载目标根目录。"""
    from chayuan.settings import CHAYUAN_ROOT
    return Path(CHAYUAN_ROOT) / "models" / "bundled"


def _repo_safe_name(repo: str) -> str:
    """``Org/Model`` → ``Org--Model``,跟 layout.yaml dest 约定对齐。"""
    return repo.replace("/", "--")


@dataclass
class InstallJob:
    """单次下载任务的运行时记录。

    Attributes:
        id: UUID4 字符串。
        release: ``lite`` / ``standard`` / ``pro``。
        mirror: 镜像别名(``hf-mirror`` / ``huggingface``);``None`` 默认 ``hf-mirror``。
        status: 状态机当前值。
        started_at: epoch 秒,任务创建时刻。
        ended_at: epoch 秒,首次进入终态时刻。
        exit_code: 0 / 1 / -1,前端兼容字段(0=成功 / 1=失败 / -1=取消)。
        step: 当前在哪一步,便于前端显示;新版只有 ``downloading`` / ``finalizing`` /
            ``done``。
        progress_pct: 0-100 浮点,逐文件粒度。
        progress_message: 当前正在下载哪个 repo,前端 banner 直接显示。
        files_total / files_done: 文件计数。
        log_tail: 行缓冲(最多 200 行),前端调试用。
    """

    id: str
    release: str
    mirror: Optional[str]
    status: JobStatus
    started_at: float
    ended_at: Optional[float] = None
    exit_code: Optional[int] = None
    step: str = ""
    progress_pct: float = 0.0
    progress_message: str = ""
    files_total: int = 0
    files_done: int = 0
    log_tail: Deque[str] = field(default_factory=lambda: deque(maxlen=200))
    # 内部字段:取消标志。不进 to_dict;由 manager 锁保护读写。
    _cancel_requested: bool = field(default=False, repr=False)
    # 兼容老 cancel 路径(留个字段防 admin route 老引用 _proc 报 AttributeError)。
    _proc: Optional[Any] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "release": self.release,
            "mirror": self.mirror,
            "status": self.status.value,
            "step": self.step,
            "started_at": float(self.started_at),
            "ended_at": float(self.ended_at) if self.ended_at is not None else None,
            "exit_code": (int(self.exit_code) if self.exit_code is not None else None),
            "progress_pct": round(float(self.progress_pct), 2),
            "progress_message": self.progress_message,
            "files_total": int(self.files_total),
            "files_done": int(self.files_done),
            "log_tail": list(self.log_tail),
        }


class InstallJobManager:
    """线程安全的任务注册中心 + huggingface_hub 调度。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: Dict[str, InstallJob] = {}

    # ── public API ────────────────────────────────────────────────

    def start(
        self,
        release: str,
        mirror: Optional[str] = None,
        *,
        repo_root: Optional[Path] = None,
    ) -> InstallJob:
        """启动新任务。返回 :class:`InstallJob` 引用,调用方拿 ``id`` 后续轮询。

        Args:
            repo_root: 历史兼容字段,新实现已不需要 cwd(直接 in-process 下载),
                仍保留以兼容老调用方,但被忽略。

        Raises:
            ValueError: release / mirror 不在白名单。
            RuntimeError: 同 release 已有 queued / running 任务。
        """
        _ = repo_root  # 兼容老 signature,新实现不再使用
        if release not in _ALLOWED_RELEASES:
            raise ValueError(
                f"unsupported release {release!r}; allowed: {list(_ALLOWED_RELEASES)}"
            )
        if mirror is not None and mirror not in _MIRROR_ENDPOINTS:
            raise ValueError(
                f"unknown mirror {mirror!r}; allowed: {sorted(_MIRROR_ENDPOINTS)}"
            )

        with self._lock:
            for j in self._jobs.values():
                if j.release == release and j.status in (
                    JobStatus.QUEUED, JobStatus.RUNNING,
                ):
                    raise RuntimeError(
                        f"release {release!r} 已有运行中任务 {j.id} (status={j.status.value})"
                    )
            job = InstallJob(
                id=str(uuid.uuid4()),
                release=release,
                mirror=mirror,
                status=JobStatus.QUEUED,
                started_at=time.time(),
            )
            self._jobs[job.id] = job

        t = threading.Thread(
            target=self._run, args=(job,),
            daemon=True, name=f"install-{release}-{job.id[:8]}",
        )
        t.start()
        return job

    def get(self, job_id: str) -> Optional[InstallJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> List[InstallJob]:
        """按 started_at 倒序返回所有已知任务。"""
        with self._lock:
            return sorted(self._jobs.values(),
                          key=lambda j: j.started_at, reverse=True)

    def cancel(self, job_id: str) -> InstallJob:
        """请求中止 ``job_id``,返回当前 job 视图。

        语义:
        * 已是终态(succeeded/failed/cancelled) → no-op,返回原状态
        * queued/running → 置 cancel 标志,后台线程在下次循环点检测后过渡到 CANCELLED

        Raises:
            KeyError: job_id 未知。
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"unknown job_id {job_id!r}")
            if job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED):
                return job
            job._cancel_requested = True
        return job

    # ── internals ─────────────────────────────────────────────────

    def _run(self, job: InstallJob) -> None:
        """后台线程:循环下载 release 的所有模型,逐文件更新 progress。"""
        with self._lock:
            job.status = JobStatus.RUNNING
            job.step = "downloading"

        mirror_key = job.mirror or "hf-mirror"

        try:
            from huggingface_hub import hf_hub_download, snapshot_download
        except ImportError as e:
            with self._lock:
                job.log_tail.append(
                    f"[install_job] huggingface_hub 不可用: {e!r};无法下载"
                )
                job.status = JobStatus.FAILED
                job.exit_code = 1
                job.ended_at = time.time()
            return

        # 强制走 hf-mirror —— 只设 HF_ENDPOINT 环境变量太晚(huggingface_hub
        # import 时已把端点固化进各子模块)。运行时覆盖所有子模块的 ENDPOINT。
        from chayuan.server.model_registry.hf_mirror import force_hf_mirror_endpoint
        _endpoint = force_hf_mirror_endpoint(_MIRROR_ENDPOINTS[mirror_key])
        with self._lock:
            job.log_tail.append(f"[install_job] HF endpoint = {_endpoint}")

        try:
            models = _release_models(job.release)
        except ValueError as e:
            with self._lock:
                job.log_tail.append(f"[install_job] {e}")
                job.status = JobStatus.FAILED
                job.exit_code = 1
                job.ended_at = time.time()
            return

        target_root = _user_bundled_root()
        try:
            target_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            with self._lock:
                job.log_tail.append(
                    f"[install_job] 创建目标目录失败 {target_root}: {e!r}"
                )
                job.status = JobStatus.FAILED
                job.exit_code = 1
                job.ended_at = time.time()
            return

        with self._lock:
            job.files_total = len(models)
            job.files_done = 0
            job.progress_pct = 0.0

        for i, model in enumerate(models, 1):
            with self._lock:
                if job._cancel_requested:
                    job.status = JobStatus.CANCELLED
                    job.exit_code = -1
                    job.ended_at = time.time()
                    job.log_tail.append("[install_job] 收到取消请求,终止")
                    return
                job.progress_message = (
                    f"({i}/{len(models)}) 下载 {model['repo']}"
                    + (f" · {model['file']}" if model.get("file") else " · 整仓")
                )
                job.log_tail.append(f"[install_job] {job.progress_message}")

            cap = model["cap"]
            cap_dir = target_root / cap
            try:
                cap_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                with self._lock:
                    job.log_tail.append(
                        f"[install_job] 创建 {cap_dir} 失败: {e!r}"
                    )
                    job.status = JobStatus.FAILED
                    job.exit_code = 1
                    job.ended_at = time.time()
                return

            try:
                if model.get("file"):
                    hf_hub_download(
                        repo_id=model["repo"],
                        filename=model["file"],
                        local_dir=str(cap_dir),
                    )
                else:
                    snapshot_dest = cap_dir / _repo_safe_name(model["repo"])
                    snapshot_dest.mkdir(parents=True, exist_ok=True)
                    snapshot_download(
                        repo_id=model["repo"],
                        local_dir=str(snapshot_dest),
                        allow_patterns=model.get("allow_patterns"),
                    )
            except Exception as e:  # noqa: BLE001
                logger.exception("install_job 下载 %s 失败", model["repo"])
                with self._lock:
                    job.log_tail.append(
                        f"[install_job] 下载 {model['repo']} 失败: "
                        f"{type(e).__name__}: {e}"
                    )
                    job.status = JobStatus.FAILED
                    job.exit_code = 1
                    job.ended_at = time.time()
                return

            with self._lock:
                job.files_done = i
                job.progress_pct = round(i / max(1, len(models)) * 100.0, 2)

        # 成功后:扫盘 + 自动指派 default,装完即用,不强求用户先走设置页。
        # 任何环节失败仅记日志,不影响 job 成功。
        with self._lock:
            job.step = "finalizing"
            job.progress_message = "扫描磁盘 + 自动指派默认模型"

        try:
            from chayuan.server.model_registry.local_index import scan_once
            scan_once()
        except Exception as e:  # noqa: BLE001
            logger.warning("[install_job] post-install scan_once 失败: %r", e)

        promoted: Dict[str, str] = {}
        try:
            from chayuan.server.model_registry.auto_assign import (
                promote_defaults_from_local,
            )
            promoted = promote_defaults_from_local()
        except Exception as e:  # noqa: BLE001
            logger.warning("[install_job] post-install auto_assign 失败: %r", e)

        with self._lock:
            job.step = "done"
            job.exit_code = 0
            job.status = JobStatus.SUCCEEDED
            job.ended_at = time.time()
            job.progress_pct = 100.0
            job.progress_message = "完成"
            if promoted:
                job.log_tail.append(
                    "[install_job] 自动指派默认: "
                    + ", ".join(f"{k}={v}" for k, v in promoted.items())
                )


# ─────────────────────── 进程内单例 ───────────────────────


_SINGLETON: Optional[InstallJobManager] = None
_SINGLETON_LOCK = threading.Lock()


def get_install_manager() -> InstallJobManager:
    """全局唯一 :class:`InstallJobManager`。"""
    global _SINGLETON
    if _SINGLETON is None:
        with _SINGLETON_LOCK:
            if _SINGLETON is None:
                _SINGLETON = InstallJobManager()
    return _SINGLETON


def _reset_manager_for_test() -> None:
    """测试 hook:清空单例,让 fixtures 可独立。"""
    global _SINGLETON
    with _SINGLETON_LOCK:
        _SINGLETON = None


__all__ = [
    "InstallJob",
    "InstallJobManager",
    "JobStatus",
    "get_install_manager",
]
