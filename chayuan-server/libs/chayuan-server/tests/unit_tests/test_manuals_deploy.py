"""``chayuan.server.manuals.deploy`` 行为测试。"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from chayuan.server.manuals.deploy import (
    MANUAL_FILES,
    deploy_user_manuals,
    get_manual_path,
    list_deployed_manuals,
)


@pytest.fixture
def chayuan_root_tmp(tmp_path, monkeypatch):
    """临时 CHAYUAN_ROOT,避免污染真实数据目录。"""
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", tmp_path)
    return tmp_path


# ─────────────────────────── 主流程 ───────────────────────────


def test_deploy_writes_md_and_docx(chayuan_root_tmp):
    rep = deploy_user_manuals()
    md_dir = chayuan_root_tmp / "manuals"
    assert md_dir.is_dir()
    # 至少一本手册被写出来
    assert rep.md_written, f"md 未写出: errors={rep.errors}"
    for name in rep.md_written:
        md_file = md_dir / f"{name}.md"
        assert md_file.is_file()
        # 所有手册都至少含"察元"主品牌词
        assert "察元" in md_file.read_text(encoding="utf-8")
    # docx 在 python-docx 可用时也应被写出
    if rep.docx_written:
        for name in rep.docx_written:
            docx_file = md_dir / f"{name}.docx"
            assert docx_file.is_file()
            assert docx_file.stat().st_size > 1000  # docx 至少几 KB


def test_deploy_is_idempotent(chayuan_root_tmp):
    rep1 = deploy_user_manuals()
    rep2 = deploy_user_manuals()
    # 第二次应该全部跳过(.md 已是最新)
    for name in rep1.md_written:
        assert name in rep2.skipped or name in rep2.md_written
    # 至少没新写,主要走 skipped
    assert len(rep2.md_written) == 0 or rep1.md_written != rep2.md_written


def test_deploy_force_overwrites(chayuan_root_tmp):
    deploy_user_manuals()
    md_file = (chayuan_root_tmp / "manuals"
               / f"{MANUAL_FILES[0].public_name}.md")
    md_file.write_text("modified", encoding="utf-8")

    # 默认幂等检测:内容变了 → 视为需要重写
    rep = deploy_user_manuals()
    assert MANUAL_FILES[0].public_name in rep.md_written
    assert "察元 AI 助手" in md_file.read_text(encoding="utf-8")

    # force 标志亦能覆盖
    md_file.write_text("modified again", encoding="utf-8")
    rep = deploy_user_manuals(force=True)
    assert MANUAL_FILES[0].public_name in rep.md_written


def test_deploy_handles_unwritable_target(monkeypatch, tmp_path):
    # 把 CHAYUAN_ROOT 指到一个不能创建子目录的路径(用一个文件占位)
    blocker = tmp_path / "blocked_root"
    blocker.write_text("not a dir")
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", blocker)
    rep = deploy_user_manuals()
    # 应该不抛,但 errors 非空
    assert rep.errors


def test_deploy_missing_chayuan_root(monkeypatch):
    """``from chayuan.settings import CHAYUAN_ROOT`` 失败 → 返回 errors 报告。"""
    import sys
    # 把 chayuan.settings 模块从 sys.modules 临时挪走,触发 ImportError
    real = sys.modules.get("chayuan.settings")
    sys.modules["chayuan.settings"] = None  # type: ignore[assignment]
    try:
        rep = deploy_user_manuals()
    finally:
        if real is not None:
            sys.modules["chayuan.settings"] = real
    # _manuals_dir 返回 None 时 target_dir 为 None
    assert rep.target_dir is None
    assert rep.errors


# ─────────────────────────── 查询入口 ───────────────────────────


def test_list_deployed_marks_existence(chayuan_root_tmp):
    pre = list_deployed_manuals()
    # 部署前文件不存在
    for item in pre:
        assert item["md_exists"] is False
        assert item["docx_exists"] is False
    deploy_user_manuals()
    post = list_deployed_manuals()
    for item in post:
        assert item["md_exists"] is True


def test_get_manual_path_returns_existing(chayuan_root_tmp):
    deploy_user_manuals()
    name = MANUAL_FILES[0].public_name
    md_path = get_manual_path(name, fmt="md")
    assert md_path is not None and md_path.is_file()
    assert md_path.name.endswith(".md")


def test_get_manual_path_unknown_returns_none(chayuan_root_tmp):
    deploy_user_manuals()
    assert get_manual_path("不存在的手册", fmt="md") is None


def test_get_manual_path_docx_falls_back_to_none_when_python_docx_missing(
    chayuan_root_tmp, monkeypatch,
):
    """python-docx 模拟不可用时,docx 应该没生成,get_manual_path('...', fmt='docx')
    返回 None。
    """
    import sys
    real = sys.modules.get("docx")
    sys.modules["docx"] = None  # type: ignore[assignment]
    try:
        deploy_user_manuals()
        # docx 不应生成
        name = MANUAL_FILES[0].public_name
        assert get_manual_path(name, fmt="docx") is None
        # md 仍正常
        assert get_manual_path(name, fmt="md") is not None
    finally:
        if real is not None:
            sys.modules["docx"] = real
        else:
            sys.modules.pop("docx", None)


# ─────────────────────────── 序列化 ───────────────────────────


def test_deploy_report_to_dict_is_json_safe(chayuan_root_tmp):
    import json
    rep = deploy_user_manuals()
    s = json.dumps(rep.to_dict())
    d = json.loads(s)
    assert "md_written" in d
    assert "errors" in d
