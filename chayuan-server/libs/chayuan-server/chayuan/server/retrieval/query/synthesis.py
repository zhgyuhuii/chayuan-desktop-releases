"""跨 KB 综合总结(deepseek/千问/perplexity 风格)。

把多源命中喂给 LLM,让它写一段自然语言答案 + [N] 引用。两个入口共用:

  - /knowledge_universe/ask/stream  → 用 universe blocks 形态喂入
  - /api/v1/kb-query/search          → 用 orchestrator items[].blocks[] 形态喂入

通过暴露 build_evidence_from_universe_blocks / build_evidence_from_kb_query_items
两个抽取函数 + 单一 synthesize_answer 综合函数,避免两条路径漂移。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chayuan.retrieval.synthesis")


_SYSTEM_PROMPT = """你是一位帮用户从知识库快速找到答案的助手。规则:
1. 只基于"知识来源"中的内容回答,不要编造
2. 直接回答问题,语气自然亲切;不要用"以下是..."这种开场白
3. 引用证据时用 [1] [2] 这种标号,标号对应"知识来源"列表里的编号
4. 结构化数据(SQL 行/数字)优先直接给数值;文档片段引用关键句
5. 若来源完全无关或为空,简短说"暂未在所选知识库中找到相关信息",并建议下一步
6. 不超过 200 字;复杂场景可适当延长但不要堆砌
"""

_USER_PROMPT = """用户问题:{query}

知识来源:
{evidence}

请直接回答问题,引用证据时用 [N] 标号。"""


def build_evidence_from_universe_blocks(
    blocks: List[Dict[str, Any]],
    *,
    per_source_chars: int = 600,
) -> List[Dict[str, Any]]:
    """从 universe ask 形态(每个 block 是一个 ku_id 的完整结果)抽取证据。"""
    out: List[Dict[str, Any]] = []
    idx = 0
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        ku_id = str(block.get("ku_id") or "")
        kind = str(block.get("kind") or "")
        ok = bool(block.get("ok"))
        if not ok and not block.get("error"):
            continue
        idx += 1
        pieces: List[str] = []
        if not ok:
            pieces.append(f"(查询失败:{block.get('error') or '未知原因'})")
        if kind == "structured":
            if block.get("summary"):
                pieces.append(str(block["summary"]).strip())
            elif block.get("text"):
                pieces.append(str(block["text"]).strip())
            sql = (block.get("sql") or "").strip()
            if sql:
                pieces.append(f"SQL: {sql[:200]}")
            rows = block.get("rows") or []
            if isinstance(rows, list) and rows:
                pieces.append("行: " + "; ".join(
                    (", ".join(f"{k}={v}" for k, v in r.items()) if isinstance(r, dict) else str(r))
                    for r in rows[:3]
                ))
                if len(rows) > 3:
                    pieces.append(f"(共 {len(rows)} 行)")
        elif kind == "document":
            for hit in (block.get("results") or [])[:3]:
                if not isinstance(hit, dict):
                    continue
                file_name = hit.get("file_name") or hit.get("source") or ""
                snippet = (hit.get("snippet") or hit.get("content") or "").strip()
                if snippet:
                    head = f"《{file_name}》" if file_name else ""
                    pieces.append(f"{head}{snippet[:per_source_chars]}".strip())
        elif kind == "vector":
            for hit in (block.get("hits") or [])[:3]:
                if not isinstance(hit, dict):
                    continue
                content = (hit.get("content") or "").strip()
                if content:
                    pieces.append(content[:per_source_chars])
        elif kind == "image":
            for hit in (block.get("results") or [])[:3]:
                if not isinstance(hit, dict):
                    continue
                caption = (hit.get("caption") or hit.get("title") or "").strip()
                if caption:
                    pieces.append(f"[图] {caption[:per_source_chars]}")
        text = "\n".join(p for p in pieces if p).strip() or "(该来源命中但无可读文本)"
        out.append({"idx": idx, "name": ku_id, "kind": kind, "text": text})
    return out


def build_evidence_from_kb_query_items(
    items: List[Dict[str, Any]],
    *,
    per_source_chars: int = 600,
) -> List[Dict[str, Any]]:
    """从 /api/v1/kb-query/search 的 items[].blocks[] 形态抽取证据。

    block.results[] 元素已经是 hit 形态(text/snippet/sql/rows/...)。
    """
    out: List[Dict[str, Any]] = []
    idx = 0
    for item in items or []:
        for block in (item.get("blocks") if isinstance(item, dict) else None) or []:
            if not isinstance(block, dict):
                continue
            kb_id = str(block.get("kb_id") or "")
            kind = str(block.get("kind") or "")
            display = block.get("display_name") or block.get("name") or kb_id
            ok = bool(block.get("ok"))
            err = (block.get("error") or {}) if isinstance(block.get("error"), dict) else {}
            err_msg = err.get("message") or block.get("error") if not isinstance(block.get("error"), dict) else err.get("message")
            if not ok and not err_msg:
                continue
            idx += 1
            pieces: List[str] = []
            if not ok:
                pieces.append(f"(查询失败:{err_msg or '未知原因'})")
            for hit in (block.get("results") or [])[:3]:
                if not isinstance(hit, dict):
                    continue
                if kind == "structured":
                    sql = (hit.get("sql") or "").strip()
                    rows = hit.get("rows") or []
                    text = (hit.get("text") or hit.get("snippet") or "").strip()
                    if text:
                        pieces.append(text[:per_source_chars])
                    if sql:
                        pieces.append(f"SQL: {sql[:200]}")
                    if isinstance(rows, list) and rows:
                        pieces.append("行: " + "; ".join(
                            (", ".join(f"{k}={v}" for k, v in r.items()) if isinstance(r, dict) else str(r))
                            for r in rows[:3]
                        ))
                        if len(rows) > 3:
                            pieces.append(f"(共 {len(rows)} 行)")
                else:
                    file_name = (hit.get("citation", {}) or {}).get("file_name") if isinstance(hit.get("citation"), dict) else ""
                    snippet = (hit.get("snippet") or hit.get("text") or hit.get("content") or "").strip()
                    if snippet:
                        head = f"《{file_name}》" if file_name else ""
                        pieces.append(f"{head}{snippet[:per_source_chars]}".strip())
            text = "\n".join(p for p in pieces if p).strip() or "(该来源命中但无可读文本)"
            out.append({"idx": idx, "name": str(display), "kind": kind, "text": text})
    return out


def synthesize_answer(
    query: str,
    evidence_blocks: List[Dict[str, Any]],
    *,
    model_name: Optional[str] = None,
) -> str:
    """对证据列表做一次 LLM 综合,返回答案文本。失败返回 ''。"""
    if not query or not evidence_blocks:
        return ""
    if not model_name:
        try:
            from chayuan.server.utils import get_default_llm
            model_name = (get_default_llm() or "").strip()
        except Exception:  # noqa: BLE001
            model_name = ""
    if not model_name:
        return ""
    try:
        from chayuan.server.utils import get_ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage

        evidence_text = "\n\n".join(
            f"[{e['idx']}] ({e.get('kind') or 'unknown'}) {e.get('name') or ''}:\n{e.get('text') or ''}"
            for e in evidence_blocks
        )
        prompt_user = _USER_PROMPT.format(query=query, evidence=evidence_text)
        llm = get_ChatOpenAI(
            model_name=model_name,
            temperature=0.3,
            streaming=False,
            local_wrap=True,
            verbose=False,
        )
        out = llm.invoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=prompt_user),
        ])
        text = getattr(out, "content", None) or str(out)
        return (text or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.info("synthesize_answer failed: %r", e)
        return ""
