"""install_model 取消→重试 回归测试。

行为合同
--------
1. ``cancel()`` 必须**立即**把 job 状态落到 CANCELLED — 不等后台阻塞下载返回。
2. 取消之后,同 ``model_id + capability`` 的 ``start()`` 必须能成功,不报
   "已有运行中任务"。
3. 没被取消的真·运行中任务,重复 ``start()`` 仍要拒绝(去重不能误伤)。

背景:hf / modelscope 的 ``snapshot_download`` 是阻塞调用,``_cancel_requested``
标志要等 SDK 整段返回后才会被 ``_check_cancel`` 看到;若 ``cancel()`` 不立即
落状态,job 会一直挂 RUNNING,用户取消后永远无法重新下载
(对应线上反馈:"点击取消后还是无法下载,提示已有运行中任务 status=running")。
"""
from unittest.mock import patch

import pytest

from chayuan.server.model_registry.install_model import (
    InstallModelJobManager,
    ModelJobStatus,
)


def _quiet_run(self, job):  # noqa: ANN001, ARG001
    """替身 _run:什么都不做,让 start() 只建 job、不真的下载。"""
    return None


def test_cancel_marks_job_cancelled_immediately():
    mgr = InstallModelJobManager()
    with patch.object(InstallModelJobManager, "_run", _quiet_run):
        job = mgr.start(
            source="huggingface",
            model_id="BAAI/bge-m3",
            capability="text-embedding",
        )
    cancelled = mgr.cancel(job.id)
    assert cancelled.status == ModelJobStatus.CANCELLED
    assert cancelled._cancel_requested is True
    assert cancelled.ended_at is not None


def test_start_allowed_after_cancel_same_model():
    mgr = InstallModelJobManager()
    with patch.object(InstallModelJobManager, "_run", _quiet_run):
        job1 = mgr.start(
            source="huggingface",
            model_id="BAAI/bge-m3",
            capability="text-embedding",
        )
        mgr.cancel(job1.id)
        # 关键:取消后同 model+cap 必须能重新 start,不报"已有运行中任务"
        job2 = mgr.start(
            source="huggingface",
            model_id="BAAI/bge-m3",
            capability="text-embedding",
        )
    assert job2.id != job1.id
    assert job2.status in (ModelJobStatus.QUEUED, ModelJobStatus.RUNNING)


def test_start_still_rejects_genuine_running_duplicate():
    """没取消的真·运行中任务,重复 start 仍然要拒绝 — 去重逻辑不能被削弱。"""
    mgr = InstallModelJobManager()
    with patch.object(InstallModelJobManager, "_run", _quiet_run):
        mgr.start(
            source="huggingface",
            model_id="BAAI/bge-m3",
            capability="text-embedding",
        )
        with pytest.raises(RuntimeError, match="已有运行中任务"):
            mgr.start(
                source="huggingface",
                model_id="BAAI/bge-m3",
                capability="text-embedding",
            )
