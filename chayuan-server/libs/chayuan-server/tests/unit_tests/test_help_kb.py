"""``chayuan.server.manuals.help_kb.seed_help_kb`` 行为测试。

覆盖两条 DB-free 的「跳过」守卫 —— 它们是「首启只建一次、删了不复活」的关键:
  * 标记文件已存在 → 直接 skip(用户删掉「使用帮助」后,下次启动不复活);
  * 同名 KB 已存在 → 写标记 + skip(不抢占用户自己建的同名库)。

建库 + 拷文件的 happy path 依赖数据库,留给首启实跑 + ``[help-kb]`` 日志验证。
"""
from __future__ import annotations

import pytest

from chayuan.server.manuals.help_kb import seed_help_kb


@pytest.fixture
def chayuan_root_tmp(tmp_path, monkeypatch):
    """临时 CHAYUAN_ROOT,避免污染真实数据目录。"""
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", tmp_path)
    return tmp_path


def test_skip_when_marker_exists(chayuan_root_tmp):
    """标记文件已存在 → seed_help_kb 直接跳过,不再建库。"""
    manuals = chayuan_root_tmp / "manuals"
    manuals.mkdir(parents=True, exist_ok=True)
    (manuals / ".help_kb_seeded").write_text("seeded\n", encoding="utf-8")

    res = seed_help_kb()

    assert res["action"] == "skipped"
    assert "marker" in res.get("reason", "")


def test_skip_and_mark_when_kb_name_taken(chayuan_root_tmp, monkeypatch):
    """已存在同名 KB → 不抢占用户的库,写标记后跳过。"""
    monkeypatch.setattr(
        "chayuan.server.knowledge_base.kb_service.base."
        "KBServiceFactory.get_service_by_name",
        lambda name: object(),
    )

    res = seed_help_kb()

    assert res["action"] == "skipped"
    assert res.get("reason") == "kb name already taken"
    # 写了标记 → 下次启动也不会再尝试
    assert (chayuan_root_tmp / "manuals" / ".help_kb_seeded").exists()
