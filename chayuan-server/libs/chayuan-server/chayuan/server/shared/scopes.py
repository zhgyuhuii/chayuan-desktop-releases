"""开放平台 App 权限 (scope) 系统。

模型
----
Scope 采用 ``<resource>:<action>`` 二元组：

- 资源 (resource)：``chat / kb / tools / events / admin``
- 动作 (action)：``read / write / call / stream / subscribe / admin``

通配符
------
- ``<res>:*``  覆盖该资源的**所有**动作，如 ``kb:*`` = ``kb:read + kb:write + kb:admin``；
- ``*`` 或 ``*:*``  超管，覆盖一切；
- 只写资源名的老格式（如 ``"chat"``，没有冒号）按 ``<res>:*`` 处理，保证存量 yaml 不破。

事件 → scope 映射
-----------------
WS 订阅与广播都会按下表查每条事件需要哪个 scope；未登记的事件默认要 ``admin:read``
（保守策略：避免新加事件忘记更新映射就泄漏给所有订阅者）。
"""
from __future__ import annotations

import fnmatch
from typing import Iterable


# 所有声明过的细粒度 scope；用于面板 UI / 字段合法性校验
ALL_SCOPES: tuple[str, ...] = (
    # chat
    "chat:read", "chat:write", "chat:stream",
    # 知识库
    "kb:read", "kb:write", "kb:admin",
    "kb:public-read",  # plan v1.3 §4.3：明确允许 App 看 visibility=public 的 KB（默认安全：不给）
    # 工具
    "tools:read", "tools:call",
    # 开放平台 WS
    "events:subscribe",
    # 运维 / 平台管理
    "admin:read", "admin:write",
)


# 资源级通配符列表（用于面板「一键全选该组」）
RESOURCE_WILDCARDS: tuple[str, ...] = (
    "chat:*", "kb:*", "tools:*", "events:*", "admin:*",
)


# 分组（用于面板按分组展示 checkbox）
SCOPE_GROUPS: dict[str, tuple[str, ...]] = {
    "chat":   ("chat:read", "chat:write", "chat:stream"),
    "kb":     ("kb:read", "kb:write", "kb:admin", "kb:public-read"),
    "tools":  ("tools:read", "tools:call"),
    "events": ("events:subscribe",),
    "admin":  ("admin:read", "admin:write"),
}


# 事件 → 必需 scope；未声明的事件 → 默认 "admin:read"（保守 deny-by-default）
EVENT_SCOPE_MAP: dict[str, str] = {
    "chat.started":    "chat:read",
    "chat.completed":  "chat:read",
    "chat.streamed":   "chat:stream",
    "kb.doc.updated":  "kb:read",
    "kb.doc.deleted":  "kb:read",
    "kb.doc.created":  "kb:read",
    "tool.called":     "tools:read",
    "app.created":     "admin:read",
    "app.updated":     "admin:read",
    "app.deleted":     "admin:read",
}


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def normalize(scopes: Iterable[str]) -> set[str]:
    """规范化：去空格、小写、老格式补全为 ``res:*``。"""
    out: set[str] = set()
    for s in scopes or []:
        if not s:
            continue
        s = str(s).strip().lower()
        if not s:
            continue
        if ":" not in s and s not in ("*",):
            s = f"{s}:*"
        out.add(s)
    return out


def covers(have: Iterable[str], required: str) -> bool:
    """判断 ``have`` 集合是否覆盖 ``required`` scope。

    规则：精确匹配 / 资源级通配 / 全通配 / 老格式兼容。
    """
    if not required:
        return True
    have_set = normalize(have)
    req = str(required).strip().lower()
    if not req:
        return True

    if "*" in have_set or "*:*" in have_set:
        return True
    if req in have_set:
        return True
    res, _, action = req.partition(":")
    if f"{res}:*" in have_set:
        return True
    # have 里如果是纯资源名（老格式），normalize 已经补成 res:* 了，这里已被前面命中
    # 但为了保险再来一遍 fnmatch
    for s in have_set:
        if fnmatch.fnmatchcase(req, s):
            return True
    return False


def missing(have: Iterable[str], required: Iterable[str]) -> list[str]:
    """返回 ``required`` 里未被 ``have`` 覆盖的项。"""
    return [r for r in required if not covers(have, r)]


def event_scope(event: str) -> str:
    """返回某事件需要的 scope；未知事件默认 ``admin:read``。"""
    return EVENT_SCOPE_MAP.get(event, "admin:read")


def filter_subscribable(
    patterns: Iterable[str], have: Iterable[str],
) -> tuple[list[str], list[str]]:
    """WS subscribe 的过滤：把客户端要求订阅的 pattern 列表，按 scope 拆成
    ``(accepted, rejected)``。

    判定：pattern 在 ``EVENT_SCOPE_MAP`` 里匹配到至少一条 event，且该 event 所需
    scope 被 ``have`` 覆盖 → 接受；否则拒绝。
    """
    accepted: list[str] = []
    rejected: list[str] = []
    have_set = normalize(have)
    for p in patterns or []:
        p = str(p).strip()
        if not p:
            continue
        matched_events = [
            ev for ev in EVENT_SCOPE_MAP if fnmatch.fnmatchcase(ev, p)
        ]
        if not matched_events:
            # 未知事件模式：按 admin:read 判定
            if covers(have_set, "admin:read"):
                accepted.append(p)
            else:
                rejected.append(p)
            continue
        if any(covers(have_set, EVENT_SCOPE_MAP[ev]) for ev in matched_events):
            accepted.append(p)
        else:
            rejected.append(p)
    return accepted, rejected


def sanitize_scope_list(values: Iterable[str]) -> list[str]:
    """面板保存时用：只保留合法的 ``ALL_SCOPES`` 成员 + 通配；其它丢弃。

    也接受老格式（``chat / kb / tools / admin``）并自动归一化为 ``res:*``。
    """
    allowed = set(ALL_SCOPES) | set(RESOURCE_WILDCARDS) | {"*", "*:*"}
    out: list[str] = []
    for s in values or []:
        if not s:
            continue
        s = str(s).strip().lower()
        if not s:
            continue
        # 老格式兼容：单资源名 → res:*
        if ":" not in s and s != "*":
            s = f"{s}:*"
        if s in allowed:
            out.append(s)
    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for s in out:
        if s not in seen:
            uniq.append(s)
            seen.add(s)
    return uniq
