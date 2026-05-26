"""install_model:下载源仓库不存在(404)→ 人话错误 回归测试。

行为合同
--------
1. modelscope / huggingface SDK 在 repo 不存在时抛裸 HTTP 404
   (``requests.exceptions.HTTPError`` / ``huggingface_hub`` 的 404),
   ``_download_*`` 必须把它转成 :class:`ModelRepoNotFoundError`,消息是人话。
2. ``_run`` 拿到 ``ModelRepoNotFoundError`` 时,job 落 FAILED,log_tail /
   progress_message 写人话,**不**刷整段堆栈。
3. ModelScope 服务端过载伪装的 404(body 含 ``max_prepared_stmt_count``)
   要单独给"服务端临时故障,稍后重试",不能让用户去改 model_id。
4. 非 404 的异常(网络超时等)原样上抛,不被误判成"仓库不存在"。
"""
from __future__ import annotations

import pytest

from chayuan.server.model_registry.install_model import (
    ModelJobStatus,
    ModelRepoNotFoundError,
    InstallModelJobManager,
)
from chayuan.server.model_registry import install_model as _im


# ─────────────────────── 工具:伪造带 response 的 HTTPError ───────────────────────


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeHTTPError(Exception):
    """模拟 requests.exceptions.HTTPError —— 挂 .response。"""

    def __init__(self, status_code: int, text: str = "") -> None:
        super().__init__(f"<Response [{status_code}]>")
        self.response = _FakeResponse(status_code, text)


# ─────────────────────── _raise_if_repo_404 单元 ───────────────────────


def test_404_genuine_missing_repo_modelscope():
    exc = _FakeHTTPError(404, '{"Message": "record not found"}')
    with pytest.raises(ModelRepoNotFoundError) as ei:
        _im._raise_if_repo_404(exc, source="modelscope", model_id="foo/bar")
    msg = str(ei.value)
    assert "foo/bar" in msg
    assert "找不到该模型仓库" in msg
    assert "Hugging Face" in msg  # 建议换源


def test_404_genuine_missing_repo_huggingface():
    exc = _FakeHTTPError(404)
    with pytest.raises(ModelRepoNotFoundError) as ei:
        _im._raise_if_repo_404(exc, source="huggingface", model_id="x/y")
    msg = str(ei.value)
    assert "x/y" in msg and "Hugging Face" in msg
    assert "ModelScope" in msg  # 建议换源


def test_404_modelscope_transient_overload_not_treated_as_missing():
    """ModelScope 服务端过载伪装 404:消息要提"临时故障/稍后重试",不是"拼写错"。"""
    body = (
        '{"Code":10010205001,"Message":"获取模型信息失败，信息：'
        'Error 1461: Can\'t create more than max_prepared_stmt_count statements"}'
    )
    exc = _FakeHTTPError(404, body)
    with pytest.raises(ModelRepoNotFoundError) as ei:
        _im._raise_if_repo_404(exc, source="modelscope", model_id="Qwen/Qwen3-4B-GGUF")
    msg = str(ei.value)
    assert "临时" in msg or "稍后重试" in msg
    assert "服务端" in msg


def test_non_404_error_passes_through_unchanged():
    """非 404(如 500 / 超时)必须原样上抛,不被转成 ModelRepoNotFoundError。"""
    exc = _FakeHTTPError(503)
    with pytest.raises(_FakeHTTPError):
        _im._raise_if_repo_404(exc, source="modelscope", model_id="a/b")

    plain = RuntimeError("connection timed out")
    with pytest.raises(RuntimeError, match="connection timed out"):
        _im._raise_if_repo_404(plain, source="huggingface", model_id="a/b")


# ─────────────────────── _download_modelscope 集成 ───────────────────────


@pytest.fixture(autouse=True)
def _isolate_root(tmp_path, monkeypatch):
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", tmp_path)
    yield


def test_download_modelscope_404_converted(monkeypatch, tmp_path):
    """ModelScope snapshot 抛 404 → _download_modelscope 转 ModelRepoNotFoundError。"""

    def _boom_snapshot(*args, **kwargs):
        raise _FakeHTTPError(404, '{"Message": "record not found"}')

    # SDK 延迟 import:伪造 modelscope.hub.* 模块
    import sys
    import types

    fake_snap = types.ModuleType("modelscope.hub.snapshot_download")
    fake_snap.snapshot_download = _boom_snapshot
    fake_file = types.ModuleType("modelscope.hub.file_download")
    fake_file.model_file_download = _boom_snapshot
    fake_hub = types.ModuleType("modelscope.hub")
    fake_ms = types.ModuleType("modelscope")
    monkeypatch.setitem(sys.modules, "modelscope", fake_ms)
    monkeypatch.setitem(sys.modules, "modelscope.hub", fake_hub)
    monkeypatch.setitem(sys.modules, "modelscope.hub.snapshot_download", fake_snap)
    monkeypatch.setitem(sys.modules, "modelscope.hub.file_download", fake_file)

    mgr = InstallModelJobManager()
    job = _im.InstallModelJob(
        id="t1",
        source="modelscope",
        model_id="ghost/repo-404",
        capability="rerank",
        requested_file=None,
        status=ModelJobStatus.RUNNING,
        started_at=0.0,
    )
    with pytest.raises(ModelRepoNotFoundError):
        mgr._download_modelscope(job, tmp_path)


def test_run_repo_not_found_marks_failed_with_humane_message(monkeypatch):
    """_run 整链:仓库 404 → job FAILED + log_tail/progress_message 是人话,不刷堆栈。"""
    mgr = InstallModelJobManager()

    def _boom(self, job, target_dir):
        raise ModelRepoNotFoundError(
            "ModelScope 上找不到该模型仓库 'ghost/repo' —— 请检查 model_id 拼写"
        )

    monkeypatch.setattr(InstallModelJobManager, "_download_modelscope", _boom)

    job = mgr.start(source="modelscope", model_id="ghost/repo", capability="rerank")
    # start 同步跑 _run(测试里 _download 立即抛),等它落终态
    deadline = __import__("time").time() + 5
    while job.status not in (
        ModelJobStatus.FAILED,
        ModelJobStatus.SUCCEEDED,
        ModelJobStatus.CANCELLED,
    ):
        if __import__("time").time() > deadline:
            pytest.fail(f"job 未落终态,仍 {job.status}")
        __import__("time").sleep(0.02)

    assert job.status == ModelJobStatus.FAILED
    joined = "\n".join(job.log_tail)
    assert "找不到该模型仓库" in joined
    assert "找不到该模型仓库" in job.progress_message
    # 不该有 Python 堆栈
    assert "Traceback" not in joined and "traceback:" not in joined
