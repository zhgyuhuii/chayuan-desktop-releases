"""N-7 Supervisor：三 Agent 协作（调研 / 写作 / 审校）。

典型用法（ChatGraph agent 模式的一个变种）：
  req.mode = "supervisor"（用户侧显式或后端路由判定）
  req.tools = ["search_internet", "search_local_knowledgebase"]

流程：
  Supervisor → researcher（收集资料）
            → writer（起草）
            → reviewer（审校 / 提升质量）
            → 判断是否继续 / FINISH

未装 langgraph_supervisor → 自动回退 sequence 串联。
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

logger = logging.getLogger("chayuan.chat.graph.supervisor")


def _load(name: str, fallback: str) -> str:
    """从 shared.prompts 读；缺失时回退 inline 文本。"""
    try:
        from chayuan.server.shared.prompts import load_prompt
        txt = load_prompt(name, default=fallback)
        return txt or fallback
    except Exception:  # noqa: BLE001
        return fallback


_RESEARCHER_PROMPT = _load("supervisor.researcher", """你是调研员。给你用户问题后：
1. 使用所给工具收集资料（知识库 / 搜索 / SQL 等）
2. 产出一份"事实清单 + 证据出处"交给写作员
不要自己下结论。""")

_WRITER_PROMPT = _load("supervisor.writer", """你是撰写员。依据调研员给出的事实清单，写一份完整、结构化、可读的答复。
- 引用证据时标[出处编号]
- 篇幅适中（300-800 字）；结构：开门见山 → 论据 → 结论""")

_REVIEWER_PROMPT = _load("supervisor.reviewer", """你是审校员。检查写作员的草稿：
- 事实准确性（有没有引用错）
- 结构合理性（开头/论据/结论齐全）
- 风险（有没有泄露敏感信息 / 过度推断）
若有问题直接回改；若 OK 输出 FINAL:<原文>。""")


def build_supervisor_team(
    tools: List[Any], model: Optional[str] = None,
):
    """构造 3-Agent Supervisor。未装 langgraph_supervisor → 回退顺序链。

    P2-10：构建耗时本身写进 ``supervisor_runs_total{role=team, status=built}``，
    方便观察长链路加载抖动；team 的每次业务执行由调用方另行埋点即可。
    """
    import time as _t
    from chayuan.server.shared.agent_factory import (
        build_react_agent, build_supervisor_agent,
    )
    from chayuan.server.observability.metrics import record_supervisor_run

    t0 = _t.perf_counter()
    try:
        researcher = build_react_agent(
            tools=tools, model=model, temperature=0.1, system_prompt=_RESEARCHER_PROMPT,
        )
        writer = build_react_agent(
            tools=[], model=model, temperature=0.3, system_prompt=_WRITER_PROMPT,
        )
        reviewer = build_react_agent(
            tools=[], model=model, temperature=0.1, system_prompt=_REVIEWER_PROMPT,
        )
        team = build_supervisor_agent(
            sub_agents={"researcher": researcher, "writer": writer, "reviewer": reviewer},
            model=model, temperature=0.0,
        )
        record_supervisor_run("team", "built", _t.perf_counter() - t0)
        return team
    except Exception:
        record_supervisor_run("team", "error", _t.perf_counter() - t0)
        raise
