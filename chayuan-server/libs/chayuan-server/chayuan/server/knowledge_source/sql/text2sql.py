"""Text2SQL 现代化生成器。

与旧版 ``agent/tools_factory/text2sql.py`` 的区别：

- 直接在 Connector 内部使用，不依赖 LangChain 的 SQLDatabaseChain（已被 langchain
  标记为 legacy）
- Prompt 里**只注入白名单表**的轻量 DDL + 3 行采样，省 token 且避免 schema 泄露
- LLM 只输出 JSON `{"sql": ..., "reason": ...}`，再用 sqlglot 严格校验
- 行结果与 SQL 合成 markdown RetrievalChunk，走多源 orchestrator 统一渲染

这比"让 LLM 自由决定+agent 多步调用"更可控：单轮 LLM → 单条 SQL → 执行 → 行集合，
失败时由上层决定是否重试/降级，便于接入 SSE 进度。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from chayuan.server.knowledge_source.types import NLQuery, SchemaSnapshot

logger = logging.getLogger("chayuan.knowledge_source.text2sql")


SYSTEM_PROMPT = """你是一名资深数据库工程师，正在协助用户用自然语言查询 {dialect} 数据库。
规则（严格遵守）：
1. 只允许生成**只读 SELECT** 语句；禁止 INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE 等任何写操作或 DDL。
2. 仅使用用户给出的「可用表清单」中的表和列，不要编造表名、列名。
3. 输出结构化 JSON：sql 字段放 SQL 文本，reason 字段放两句话说明。
4. SQL 必须可以直接在 {dialect} 上执行，语法严格符合该方言。
5. 若问题无法用给定表回答，sql 字段置空字符串，reason 字段简述原因。
6. 自动加 LIMIT {top_k}，避免返回超大结果集。对聚合/TOP/排行查询如无特殊要求默认降序。
7. 日期/时间若用户说模糊时间（如"最近一周"），请用数据库函数动态计算（例如 NOW()/CURRENT_DATE/SYSDATE）。
"""

USER_PROMPT = """【用户问题】
{query}

【可用表清单（已按白名单过滤）】
{schema_block}

【最近对话片段（仅供参考）】
{history_block}

请输出 JSON。"""


def _render_schema(schema: SchemaSnapshot, max_tables: int = 20) -> str:
    tables = schema.tables[:max_tables]
    blocks: List[str] = []
    for t in tables:
        head = t.ddl_hint()
        sample = ""
        if t.sample_rows:
            sample_lines = []
            for i, row in enumerate(t.sample_rows[:3]):
                sample_lines.append(f"  sample#{i+1}: {json.dumps(row, ensure_ascii=False, default=str)[:200]}")
            sample = "\n" + "\n".join(sample_lines)
        blocks.append(head + sample)
    return "\n\n".join(blocks) if blocks else "(无可用表，请让用户联系管理员配置白名单)"


def _render_history(history: List[Dict[str, str]], limit: int = 4) -> str:
    items = history[-limit:]
    if not items:
        return "(无)"
    return "\n".join(
        f"- {h.get('role', 'user')}: {str(h.get('content', ''))[:200]}" for h in items
    )


def generate_sql(
    nl: NLQuery,
    schema: SchemaSnapshot,
    dialect: str,
    llm_model: Optional[str] = None,
) -> Dict[str, Any]:
    """调 LLM 生成 SQL。返回 {"sql": "...", "reason": "..."}；失败返回 sql="" 不抛。

    使用 ``shared.structured_llm.call_structured``：优先走 function-calling /
    JSON mode，解析失败率 ≈ 0；自由文本兜底仅在 provider 完全不支持时触发。
    """
    from chayuan.server.shared.structured_llm import call_structured
    from chayuan.server.shared.structured_schemas import SqlGen
    from chayuan.server.utils import get_default_llm

    model = llm_model or nl.llm_model or get_default_llm()
    sys_msg = SYSTEM_PROMPT.format(dialect=dialect or "SQL", top_k=nl.top_k or 50)
    usr_msg = USER_PROMPT.format(
        query=nl.query,
        schema_block=_render_schema(schema),
        history_block=_render_history(nl.history),
    )
    res = call_structured(
        system=sys_msg, user=usr_msg, schema=SqlGen,
        llm_model=model, temperature=0.0,
        default=SqlGen(sql="", reason="LLM 解析失败"),
    )
    if res is None:
        return {"sql": "", "reason": "LLM 调用失败"}
    sql = strip_sql_fences(res.sql or "")
    reason = (res.reason or "").strip()
    return {"sql": sql, "reason": reason}


def strip_sql_fences(s: str) -> str:
    """剥掉 LLM 偶尔会包的 markdown 代码围栏 (```sql ... ``` / ``` ... ```)。

    场景:provider 不严格遵守 JSON mode 时,会把 SQL 字符串字段塞回 markdown
    格式,导致执行时 psycopg2 报 ``syntax error at or near "```"``。
    更糟的是 LLM 常常在 SQL 之后追加自白("Since the actual execution of the
    SQL query is not possible..."),整段被原样送进 cursor.execute。

    规则(顺序):
      1. 去前后 whitespace
      2. 若文本里出现成对围栏 ```[lang]\\n<body>\\n```,只取第一段 body
      3. 否则若以 ``` 开头(只开未闭):剥掉 fence 头 + lang 行,取剩下全部
      4. 否则若文本中段出现 ```(SQL 后接说明):在该处截断
      5. 防御性再剥任何残留反引号
      6. 末尾分号去掉(SQLAlchemy text() 不需要)
    """
    import re

    if not s:
        return ""
    t = s.strip()
    # 1. 文本中第一个完整成对的围栏 ```[lang]\n<body>\n```
    m = re.search(
        r"```[a-zA-Z0-9_+-]*[ \t]*\r?\n?(.*?)```",
        t, re.DOTALL,
    )
    if m:
        t = m.group(1).strip()
    elif t.startswith("```"):
        # 2. 只有开头围栏没闭合:剥掉 fence 头 + 可能的 lang 行
        body = t[3:]
        nl = body.find("\n")
        if 0 <= nl < 20:
            body = body[nl + 1:]
        t = body.strip()
    else:
        # 3. SQL 后跟随 ``` 包裹的说明文字时,在首个 ``` 处截断
        idx = t.find("```")
        if idx >= 0:
            t = t[:idx].strip()

    # 4. 防御:任何残留的反引号继续剥
    while t.startswith("```"):
        t = t[3:].lstrip()
        nl = t.find("\n")
        if 0 <= nl < 20:
            t = t[nl + 1:]
    while t.endswith("```"):
        t = t[:-3].rstrip()
    return t.strip().rstrip(";").strip()


def rows_to_markdown(columns: List[str], rows: List[List[Any]], limit: int = 50) -> str:
    """SQL 结果行集合 → Markdown 表格（用于放入 RetrievalChunk.content）。"""
    if not columns:
        return "(空结果)"
    rows = rows[:limit]
    head = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    body_lines = []
    for r in rows:
        vals = [("" if v is None else str(v)).replace("|", "\\|").replace("\n", " ") for v in r]
        body_lines.append("| " + " | ".join(vals) + " |")
    more = "" if len(rows) < limit else f"\n\n_(仅显示前 {limit} 行)_"
    return "\n".join([head, sep, *body_lines]) + more
