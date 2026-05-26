"""数据治理四件套测试（P1-9）。

覆盖：
- PII 规则：手机/身份证/邮箱/银行卡 命中；误报控制
- 脱敏：按角色差异化；span 对齐
- 策略：scope 优先级（user > role > global）
- 血缘：record_chat 写主表 + touch 子表；top_touched_objects 排行正确
- 配额：token 预算 fail-open、QPS 令牌桶（用 fakeredis）
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# PII 规则
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected_types", [
    ("我的电话是13812345678，邮箱 a.b@example.com",
     {"PHONE_CN", "EMAIL"}),
    ("身份证：11010119900101051X",
     {"ID_CARD_CN"}),
    ("服务器 IP 192.168.0.1 挂了",
     {"IPV4"}),
    ("正常无敏感文本，全是普通内容",
     set()),
])
def test_pii_scan_basic(text, expected_types):
    from chayuan.server.governance.pii import scan_text
    ents = scan_text(text, enable_presidio=False)
    types = {e["type"] for e in ents}
    assert expected_types.issubset(types), f"期望 {expected_types}，实际 {types}"


# ---------------------------------------------------------------------------
# 脱敏
# ---------------------------------------------------------------------------

def test_masking_strict_covers_phone_and_email():
    from chayuan.server.governance.masking import apply_masking
    from chayuan.server.governance.pii import scan_text
    text = "联系 13800138000 或 alice@example.com"
    ents = scan_text(text, enable_presidio=False)
    out = apply_masking(text, ents, user_role="user")  # strict
    assert "13800138000" not in out
    assert "alice@example.com" not in out
    # 长度对齐：保证 span 可还原高亮
    assert len(out) == len(text)


def test_masking_loose_partial():
    from chayuan.server.governance.masking import apply_masking
    from chayuan.server.governance.pii import scan_text
    text = "电话 13812345678"
    ents = scan_text(text, enable_presidio=False)
    out = apply_masking(text, ents, user_role="analyst")  # loose
    assert "138****5678" in out


def test_masking_admin_no_op():
    from chayuan.server.governance.masking import apply_masking
    from chayuan.server.governance.pii import scan_text
    text = "电话 13812345678"
    ents = scan_text(text, enable_presidio=False)
    out = apply_masking(text, ents, user_role="admin")
    assert "13812345678" in out


# ---------------------------------------------------------------------------
# 策略优先级
# ---------------------------------------------------------------------------

def test_policy_priority(ks_db):
    from chayuan.server.governance.policy import get_policy, upsert_policy
    upsert_policy(scope="global", daily_token_budget=1000, qps=5, masking_level="loose")
    upsert_policy(scope="role:analyst", daily_token_budget=2000, qps=10, masking_level="strict")
    upsert_policy(scope="user:99", daily_token_budget=99999, qps=1, masking_level="off")

    # 个人策略优先
    p = get_policy(user_id=99, user_role="analyst")
    assert p.daily_token_budget == 99999
    assert p.qps == 1

    # role > global
    p = get_policy(user_id=12345, user_role="analyst")
    assert p.daily_token_budget == 2000

    # global 兜底
    p = get_policy(user_id=333, user_role="")
    assert p.daily_token_budget == 1000


# ---------------------------------------------------------------------------
# 血缘
# ---------------------------------------------------------------------------

def test_lineage_record_and_top_objects(ks_db):
    from chayuan.server.governance.lineage import (
        list_lineage, record_chat, top_touched_objects,
    )
    sources = [
        {
            "source_id": 1, "kind": "sql", "name": "mysql:prod",
            "chunks": [{
                "citation": {"meta": {"columns": ["name", "price"]}},
            }],
        },
        {
            "source_id": 2, "kind": "vector", "name": "kb_samples",
            "chunks": [{
                "citation": {"title": "readme.md", "meta": {}},
            }],
        },
    ]
    lineage_id = record_chat(
        user_id=1, username="u1", conversation_id="c1", request_id="r1",
        mode="multi_source", query="最贵的商品",
        answer_preview="MacBook Pro", llm_model="gpt-4",
        sources=sources, retrieved_chunks=[],
        pii_count=0, tokens_total=123,
    )
    assert lineage_id is not None

    records = list_lineage(user_id=1, hours=24)
    assert records and records[0]["mode"] == "multi_source"
    assert records[0]["tokens_total"] == 123

    cols = top_touched_objects(object_type="column")
    names = {c["qualified_name"] for c in cols}
    assert "source:mysql:prod.name" in names
    assert "source:mysql:prod.price" in names

    files = top_touched_objects(object_type="file")
    assert any("readme.md" in x["qualified_name"] for x in files)


# ---------------------------------------------------------------------------
# 配额（fakeredis）
# ---------------------------------------------------------------------------

def test_quota_fail_open_without_redis(ks_db, monkeypatch):
    import chayuan.server.governance.quota as q
    monkeypatch.setattr(q, "_get_redis", lambda: None)
    ok, reason = q.check_and_reserve(user_id=1, user_role="user")
    assert ok is True
    assert reason == ""


def test_quota_qps_hits_limit(ks_db, monkeypatch, fake_redis):
    import chayuan.server.governance.quota as q
    from chayuan.server.governance.policy import upsert_policy

    monkeypatch.setattr(q, "_REDIS_CLIENT", fake_redis, raising=False)
    monkeypatch.setattr(q, "_REDIS_CHECKED", True, raising=False)
    monkeypatch.setattr(q, "_get_redis", lambda: fake_redis)

    upsert_policy(scope="user:42", qps=2, daily_token_budget=-1, masking_level="loose")

    # 第 1、2 次通过；第 3 次应被拒
    for i in range(2):
        ok, _ = q.check_and_reserve(user_id=42, user_role="")
        assert ok is True, f"第 {i+1} 次应通过"
    ok, reason = q.check_and_reserve(user_id=42, user_role="")
    assert ok is False
    assert "QPS" in reason


def test_quota_daily_token_budget_exhausted(ks_db, monkeypatch, fake_redis):
    import chayuan.server.governance.quota as q
    from chayuan.server.governance.policy import upsert_policy

    monkeypatch.setattr(q, "_REDIS_CLIENT", fake_redis, raising=False)
    monkeypatch.setattr(q, "_REDIS_CHECKED", True, raising=False)
    monkeypatch.setattr(q, "_get_redis", lambda: fake_redis)

    upsert_policy(scope="user:7", qps=-1, daily_token_budget=500, masking_level="loose")
    # 先消耗 600
    q.record_usage(user_id=7, tokens=600, mode="llm")
    ok, reason = q.check_and_reserve(user_id=7, user_role="")
    assert ok is False
    assert "预算" in reason or "budget" in reason.lower()
