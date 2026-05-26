"""Scope 语义单测：精确匹配 / 资源级通配 / 全通配 / 老格式兼容 /
事件映射 / 订阅过滤。"""
from __future__ import annotations


def test_normalize_legacy_and_new():
    from chayuan.server.shared.scopes import normalize

    # 老格式 (chat) → chat:*
    assert normalize(["chat"]) == {"chat:*"}
    assert normalize(["chat", "kb"]) == {"chat:*", "kb:*"}
    # 新格式原样
    assert normalize(["chat:read", "kb:write"]) == {"chat:read", "kb:write"}
    # 大小写 / 空格归一化
    assert normalize(["  ChAt:Read  "]) == {"chat:read"}
    # * / 空 过滤
    assert normalize(["*", "", "  "]) == {"*"}


def test_covers_exact_and_wildcards():
    from chayuan.server.shared.scopes import covers

    # 精确
    assert covers(["chat:read"], "chat:read")
    assert not covers(["chat:read"], "chat:write")

    # 资源级通配
    assert covers(["chat:*"], "chat:read")
    assert covers(["chat:*"], "chat:stream")
    assert not covers(["chat:*"], "kb:read")

    # 全通配
    assert covers(["*"], "chat:read")
    assert covers(["*:*"], "admin:write")

    # 老格式 chat 应等价 chat:*
    assert covers(["chat"], "chat:read")
    assert covers(["chat"], "chat:write")
    assert not covers(["chat"], "kb:read")

    # 多个 have 只要命中一个就行
    assert covers(["kb:read", "chat:read"], "chat:read")

    # 空 required 恒 True
    assert covers([], "")


def test_missing_list():
    from chayuan.server.shared.scopes import missing

    assert missing(["chat:*"], ["chat:read", "chat:write", "kb:read"]) == ["kb:read"]
    assert missing(["*:*"], ["anything:foo"]) == []


def test_event_scope_map_and_filter():
    from chayuan.server.shared.scopes import (
        event_scope, filter_subscribable,
    )

    assert event_scope("chat.completed") == "chat:read"
    # 未知事件 → admin:read
    assert event_scope("unknown.event") == "admin:read"

    # chat:read 可订阅 chat.completed；kb.doc.* 不行
    acc, rej = filter_subscribable(
        ["chat.completed", "kb.doc.*"], ["chat:read"],
    )
    assert acc == ["chat.completed"]
    assert rej == ["kb.doc.*"]

    # kb:* 能订阅 kb.doc.*；不能订 chat.completed
    acc, rej = filter_subscribable(
        ["chat.completed", "kb.doc.*"], ["kb:*"],
    )
    assert "kb.doc.*" in acc
    assert "chat.completed" in rej

    # *:* 全部通过
    acc, rej = filter_subscribable(["chat.completed", "kb.doc.*", "app.created"], ["*:*"])
    assert rej == []


def test_sanitize_scope_list():
    from chayuan.server.shared.scopes import sanitize_scope_list

    # 合法 + 老格式 + 非法
    out = sanitize_scope_list(["chat:read", "kb", "tools:call", "bogus", ""])
    assert out == ["chat:read", "kb:*", "tools:call"]

    # 去重保序
    out = sanitize_scope_list(["chat:read", "chat:read", "chat:write"])
    assert out == ["chat:read", "chat:write"]
