"""基于 LangGraph 的 Text2SQL StateGraph。

节点与状态转移（箭头顺序）：

                                    ┌── revise ──┐
                                    ▼            │
  classify → schema_link → generate → validate → execute → synthesize → END
                                              │       │          ▲
                                              └─fail──┘          │
                                                     retry ≤ N ──┘

- classify       判定问题可否用给定源回答；不可则直接给友好失败
- schema_link    LLM 辅助 + 规则（字符串 NER）找出 Top-K 相关表 / 列
- generate       基于 RAG-retrieved 训练样本 + 选中表 DDL 生成 SQL
- validate       sqlglot + 字符串 + 白名单（安全三重）
- execute        SQLAlchemy 执行
- revise         执行失败 → 把 DB error 回喂 LLM → 重新 generate
- synthesize     行集 → 自然语言 + 可下载 markdown

设计哲学：每一步都**有限重试 + fail-soft**；任一步异常都产出"可解释的失败 chunk"
而非抛异常，保证 Orchestrator 能继续渲染其他源的结果。

LangGraph 未安装时自动降级为同等功能的线性实现（保底主进程不依赖 LangGraph）。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from chayuan.server.knowledge_source.base import ConnectorError
from chayuan.server.knowledge_source.sql.safety import ensure_readonly
from chayuan.server.knowledge_source.sql.text2sql import (
    rows_to_markdown,
    strip_sql_fences,
)
from chayuan.server.knowledge_source.types import NLQuery, SchemaSnapshot

logger = logging.getLogger("chayuan.knowledge_source.sql.graph")


def _observe_node(node_name: str):
    """节点装饰器：自动把每次执行耗时打 Prometheus。"""
    def wrap(fn):
        def inner(state):
            import time as _t
            try:
                from chayuan.server.observability.ks_metrics import (
                    T2SQL_NODE_DURATION, T2SQL_RETRY_TOTAL, T2SQL_SQL_LENGTH,
                )
            except Exception:  # noqa: BLE001
                T2SQL_NODE_DURATION = T2SQL_RETRY_TOTAL = T2SQL_SQL_LENGTH = None
            t0 = _t.time()
            dialect = state.get("dialect") or "unknown"
            status = "ok"
            try:
                new_state = fn(state)
                # 状态回执：节点是否产出错误
                if node_name == "validate" and new_state.get("validation_error"):
                    status = "validation_error"
                if node_name == "execute" and new_state.get("exec_error"):
                    status = "exec_error"
                if node_name == "revise":
                    try:
                        if T2SQL_RETRY_TOTAL is not None:
                            reason = "exec" if state.get("exec_error") else "validate"
                            T2SQL_RETRY_TOTAL.labels(dialect=dialect, reason=reason).inc()
                    except Exception:  # noqa: BLE001
                        pass
                if node_name == "generate":
                    try:
                        if T2SQL_SQL_LENGTH is not None:
                            sql_len = len(new_state.get("generated_sql") or "")
                            if sql_len > 0:
                                T2SQL_SQL_LENGTH.labels(dialect=dialect).observe(float(sql_len))
                    except Exception:  # noqa: BLE001
                        pass
                return new_state
            except Exception:
                status = "exception"
                raise
            finally:
                try:
                    if T2SQL_NODE_DURATION is not None:
                        T2SQL_NODE_DURATION.labels(
                            dialect=dialect, node=node_name, status=status,
                        ).observe(_t.time() - t0)
                except Exception:  # noqa: BLE001
                    pass
        inner.__name__ = f"observed_{node_name}"
        return inner
    return wrap


# ---------------------------------------------------------------------------
# State 定义：LangGraph 的节点共享字典；dataclass 形式便于类型检查
# ---------------------------------------------------------------------------

class T2SState(TypedDict, total=False):
    # 输入
    source_id: int
    dialect: str
    sqlglot_dialect: str
    query: str
    top_k: int
    history: List[Dict[str, str]]
    llm_model: Optional[str]
    schema: SchemaSnapshot                   # 完整 schema 快照
    run_sql: Any                             # callable: (sql) -> (columns, rows, error)
    ab_key: Optional[str]                    # P2-6 A/B 分桶键（通常 user_id 字符串化）
    # "范围查询"：allowed_tables 非空时启用 AST 白名单校验；空则不校验（默认全量）
    allowed_tables: Optional[List[str]]

    # 中间产物
    classified_can_answer: bool
    classify_reason: str
    relevant_tables: List[str]
    retrieved_ddl: List[Dict[str, Any]]
    retrieved_docs: List[Dict[str, Any]]
    retrieved_pairs: List[Dict[str, Any]]
    generated_sql: str
    generate_reason: str
    validation_error: str
    exec_error: str
    exec_columns: List[str]
    exec_rows: List[List[Any]]
    retry_count: int
    max_retries: int

    # 输出
    final_content: str
    final_meta: Dict[str, Any]


# ---------------------------------------------------------------------------
# Node: classify
# ---------------------------------------------------------------------------

# P2-6：prompt 从 shared/prompts/registry.yaml 加载；YAML 缺失时走 load_prompt 内置 fallback。
_CLASSIFY_SYS_FALLBACK = """你是数据库问答分诊员。给定用户问题和可用表清单（只含表名与注释），
仅需判断"基于这些表，问题原则上能否用 SELECT 查询回答"。返回 JSON：
{"can_answer": true|false, "reason": "一句话"}。
注意：不判断 SQL 是否简单，只判断"是否可回答"。"""


def _classify_sys(state: T2SState) -> str:
    try:
        from chayuan.server.shared.prompts import load_prompt
        return load_prompt(
            "text2sql.classify",
            ab_key=state.get("ab_key"),
            default=_CLASSIFY_SYS_FALLBACK,
        ) or _CLASSIFY_SYS_FALLBACK
    except Exception:  # noqa: BLE001
        return _CLASSIFY_SYS_FALLBACK


def node_classify(state: T2SState) -> T2SState:
    schema: SchemaSnapshot = state["schema"]
    if not schema.tables:
        state["classified_can_answer"] = False
        state["classify_reason"] = "该数据源尚未同步到任何可用表，请联系管理员刷新 Schema 或配置白名单"
        return state
    table_lines = [
        f"- {t.name}{(': ' + t.comment) if t.comment else ''}"
        for t in schema.tables[:60]
    ]
    user_msg = f"""【问题】
{state['query']}

【可用表】
{chr(10).join(table_lines)}

请输出 JSON。"""
    from chayuan.server.shared.structured_llm import call_structured
    from chayuan.server.shared.structured_schemas import T2SClassify
    res = call_structured(
        system=_classify_sys(state), user=user_msg,
        schema=T2SClassify, llm_model=state.get("llm_model"),
        default=T2SClassify(can_answer=True, reason=""),
    )
    state["classified_can_answer"] = bool(res.can_answer) if res else True
    state["classify_reason"] = (res.reason if res else "") or ""
    return state


# ---------------------------------------------------------------------------
# Node: schema_link  — 选出相关表 + 规则打底
# ---------------------------------------------------------------------------

_SCHEMA_LINK_SYS = """你是 Schema Linker。从「可用表清单」中挑选最可能用于回答用户问题的表，
按相关性降序返回 ≤6 张。严格 JSON：{"tables": ["<name>", ...]}。若无明显相关表，返回空数组。"""


def node_schema_link(state: T2SState) -> T2SState:
    schema: SchemaSnapshot = state["schema"]
    # 规则先跑：把问题里出现的表名/列名词直接标记
    q_lower = (state["query"] or "").lower()
    rule_pick = [t.name for t in schema.tables if t.name.lower() in q_lower]

    table_lines = []
    for t in schema.tables[:80]:
        # 把每张表的列名拼接 20 个作为提示，让模型能靠列名推断
        col_hint = ", ".join(c.name for c in t.columns[:20])
        note = f" ({t.comment})" if t.comment else ""
        table_lines.append(f"- {t.name}{note}  cols: {col_hint}")
    user_msg = f"""【问题】
{state['query']}

【可用表】
{chr(10).join(table_lines)}

请输出 JSON。"""
    from chayuan.server.shared.structured_llm import call_structured
    from chayuan.server.shared.structured_schemas import T2SSchemaLink
    res = call_structured(
        system=_SCHEMA_LINK_SYS, user=user_msg,
        schema=T2SSchemaLink, llm_model=state.get("llm_model"),
        default=T2SSchemaLink(tables=[]),
    )
    picked = list(res.tables) if res else []
    # 合并规则结果
    all_names = {t.name for t in schema.tables}
    final = []
    for name in rule_pick + picked:
        if name in all_names and name not in final:
            final.append(name)
    # 至少留 3 张（允许 LLM 漏选）；不足时按出现频次补
    if len(final) < 3:
        for t in schema.tables[:6]:
            if t.name not in final:
                final.append(t.name)
            if len(final) >= 6:
                break
    state["relevant_tables"] = final[:8]
    return state


# ---------------------------------------------------------------------------
# Node: retrieve_rag  — 从训练语料里召回相似 DDL / doc / Q-SQL pair
# ---------------------------------------------------------------------------

def node_retrieve_rag(state: T2SState) -> T2SState:
    try:
        from chayuan.server.db.repository.sql_training_repository import (
            retrieve_similar,
        )
        sid = int(state.get("source_id") or 0)
        state["retrieved_ddl"] = retrieve_similar(sid, state["query"], top_k=5, kind="ddl")
        state["retrieved_docs"] = retrieve_similar(sid, state["query"], top_k=3, kind="doc")
        state["retrieved_pairs"] = retrieve_similar(sid, state["query"], top_k=5, kind="pair")
    except Exception as e:  # noqa: BLE001
        logger.debug("RAG 召回失败（忽略，走全 schema 提示）：%r", e)
        state["retrieved_ddl"] = []
        state["retrieved_docs"] = []
        state["retrieved_pairs"] = []
    return state


# ---------------------------------------------------------------------------
# Node: generate  — RAG-augmented SQL 生成
# ---------------------------------------------------------------------------

# P2-6：允许 registry.yaml 覆盖。保留 inline fallback 保证 dev 环境无 YAML 也能跑。
# P2-14-b：方言 hint 追加在 body 末尾（Hive / MySQL / PostgreSQL 有各自独特的日期/分页语义）
_GENERATE_SYS_FALLBACK = """你是 {dialect} 数据库资深工程师。请严格遵守以下规则：
1. 只生成只读 SELECT，绝不生成 INSERT/UPDATE/DELETE/DDL。
2. 仅使用给定「可用表」中的表名与列名。
3. 必要时使用方言原生函数（MySQL: NOW()、PostgreSQL: CURRENT_DATE、Oracle: SYSDATE 等）。
4. 自动 LIMIT {top_k}，聚合/排行 类问题默认 DESC。
5. 参考「历史成功范例」（question → sql）的风格；不要照抄范例里不存在的列。
6. 输出严格 JSON：{{"sql": "<SQL>", "reason": "<两句话理由>"}}。
7. 若无法用给定表回答，输出 {{"sql": "", "reason": "无法回答：<简述>"}}。"""


# P2-14-b：按 dialect / sqlglot_dialect 给 LLM 专项 hint
_DIALECT_HINTS: Dict[str, str] = {
    "hive": (
        "Hive 专项提醒：\n"
        "- 日期格式化用 DATE_FORMAT(...) 或 FROM_UNIXTIME(...)，不支持 PostgreSQL 的 TO_CHAR；\n"
        "- 分页只用 LIMIT N，不写 LIMIT offset,count 也不写 OFFSET；\n"
        "- 字符串拼接用 CONCAT(...)；条件用 CASE WHEN；\n"
        "- 避免 UPDATE/DELETE/MERGE（事务性差，只生成 SELECT）。"
    ),
    "mysql": (
        "MySQL 专项提醒：\n"
        "- 日期用 DATE_FORMAT(NOW(), '%Y-%m-%d')；\n"
        "- 分页 LIMIT offset, count 或 LIMIT count OFFSET offset 都可。"
    ),
    "postgres": (
        "PostgreSQL 专项提醒：\n"
        "- 日期用 TO_CHAR(CURRENT_DATE, 'YYYY-MM-DD') / DATE_TRUNC('day', ts)；\n"
        "- 字符串拼接用 || 或 CONCAT；\n"
        "- LIMIT N OFFSET M 句式。"
    ),
    "oracle": (
        "Oracle 专项提醒：\n"
        "- 日期取 SYSDATE；格式化 TO_CHAR(..., 'YYYY-MM-DD')；\n"
        "- 分页使用 ROWNUM <= N 或 12c+ OFFSET N ROWS FETCH NEXT M ROWS ONLY。"
    ),
    # 信创库：与最近亲 dialect 语法 90% 重合，这里只做增量提醒。
    "kingbase": (
        "人大金仓 KingbaseES 专项提醒（协议层 PostgreSQL 兼容）：\n"
        "- 语法按 PostgreSQL 写：`CURRENT_DATE` / `DATE_TRUNC('day', ts)` / `LIMIT N OFFSET M`；\n"
        "- schema 访问使用双引号，如 \"PUBLIC\".\"T_USER\"；表名大小写以 search_path 决定；\n"
        "- 字符串拼接用 `||` 或 `CONCAT(...)`；\n"
        "- 金仓有 Oracle 兼容模式，但此处输入方言已选 KingbaseES，请一律按 PostgreSQL 写。"
    ),
    "dm": (
        "达梦 DM 专项提醒（协议层 Oracle 兼容）：\n"
        "- 当前日期 `SYSDATE`；日期格式化 `TO_CHAR(col, 'YYYY-MM-DD HH24:MI:SS')`；\n"
        "- 分页优先 `ROWNUM <= N`；也支持 `OFFSET ... ROWS FETCH NEXT ... ROWS ONLY`；\n"
        "- 字符串拼接用 `||`；`NVL(col, 0)` 替代 NULL 值；\n"
        "- schema.table 形如 `SYSDBA.T_USER`；不支持 AUTO_INCREMENT/LIMIT 关键字。"
    ),
}


def _generate_sys_template(state: T2SState) -> str:
    try:
        from chayuan.server.shared.prompts import load_prompt
        body = load_prompt(
            "text2sql.generate",
            ab_key=state.get("ab_key"),
            default=_GENERATE_SYS_FALLBACK,
        ) or _GENERATE_SYS_FALLBACK
    except Exception:  # noqa: BLE001
        body = _GENERATE_SYS_FALLBACK

    # 查找顺序优先 `dialect`（更具体，能区分 kingbase vs postgres / dm vs oracle），
    # 再回退到 `sqlglot_dialect`（近亲）；前者能命中就不走后者，避免金仓/达梦的信创专项提醒被 PG/Oracle 覆盖。
    specific = (state.get("dialect") or "").strip().lower()
    generic = (state.get("sqlglot_dialect") or "").strip().lower()
    hint = _DIALECT_HINTS.get(specific) or _DIALECT_HINTS.get(generic) or ""
    if hint:
        body = body + "\n\n" + hint
    return body


def _render_rag_context(state: T2SState) -> str:
    schema: SchemaSnapshot = state["schema"]
    picked = set(state.get("relevant_tables") or [])
    parts: List[str] = []

    # "范围查询"：allowed_tables 非空时向 LLM 明确声明硬约束，提升首次命中率
    allowed = list(state.get("allowed_tables") or [])
    if allowed:
        parts.append("【⚠️ 严格范围（只能使用以下表，引用其它表会被拒绝）】")
        parts.append(", ".join(allowed[:50]))
        parts.append("")  # 空行分隔

    parts.append("【可用表（已做 schema linking，优先使用这几张）】")
    for t in schema.tables:
        if picked and t.name not in picked:
            continue
        parts.append(t.ddl_hint())
        if t.sample_rows:
            for i, row in enumerate(t.sample_rows[:2]):
                parts.append(f"  sample#{i+1}: {json.dumps(row, ensure_ascii=False, default=str)[:200]}")

    ddls = state.get("retrieved_ddl") or []
    if ddls:
        parts.append("\n【相关 DDL 片段（从训练语料检索）】")
        for d in ddls[:3]:
            parts.append((d.get("sql") or "")[:500])

    docs = state.get("retrieved_docs") or []
    if docs:
        parts.append("\n【相关业务说明】")
        for d in docs[:2]:
            parts.append("- " + (d.get("content") or "")[:400])

    pairs = state.get("retrieved_pairs") or []
    if pairs:
        parts.append("\n【历史成功范例（question → sql）】")
        for p in pairs[:5]:
            parts.append(f"Q: {p.get('question') or ''}")
            parts.append(f"A: {(p.get('sql') or '').strip()[:400]}")

    hist = state.get("history") or []
    if hist:
        parts.append("\n【最近对话】")
        for h in hist[-3:]:
            parts.append(f"- {h.get('role', 'user')}: {str(h.get('content', ''))[:200]}")

    return "\n".join(parts)


def node_generate(state: T2SState) -> T2SState:
    # 模板缓存（P0-2 L2）命中 → 跳过 LLM
    try:
        from chayuan.server.knowledge_source.cache import template_cache_get
        hit = template_cache_get(
            source_id=int(state.get("source_id") or 0),
            query=state.get("query") or "",
            schema_like=state.get("schema"),
            kind="sql",
        )
        if hit and hit.get("sql"):
            # 缓存里的 SQL 可能是历史版本写入时未剥围栏的(已修但旧条目残留),读时再清洗一遍
            state["generated_sql"] = strip_sql_fences(hit["sql"])
            state["generate_reason"] = (hit.get("reason") or "") + "（缓存命中）"
            state["validation_error"] = ""
            state["exec_error"] = ""
            return state
    except Exception as e:  # noqa: BLE001
        logger.debug("template cache read 失败（忽略）：%r", e)

    user_msg = f"""【问题】
{state['query']}

{_render_rag_context(state)}

请输出 JSON。"""
    sys_msg = _generate_sys_template(state).format(
        dialect=state.get("dialect") or "SQL",
        top_k=max(1, int(state.get("top_k") or 50)),
    )
    from chayuan.server.shared.structured_llm import call_structured
    from chayuan.server.shared.structured_schemas import SqlGen
    res = call_structured(
        system=sys_msg, user=user_msg, schema=SqlGen,
        llm_model=state.get("llm_model"), default=SqlGen(sql="", reason=""),
    )
    sql = strip_sql_fences((res.sql if res else ""))
    reason = (res.reason if res else "").strip()
    state["generated_sql"] = sql
    state["generate_reason"] = reason
    state["validation_error"] = ""
    state["exec_error"] = ""
    # 写模板缓存（只写成功生成的；retry 路径靠 revise 节点去覆盖）
    if sql:
        try:
            from chayuan.server.knowledge_source.cache import template_cache_set
            template_cache_set(
                source_id=int(state.get("source_id") or 0),
                query=state.get("query") or "",
                schema_like=state.get("schema"),
                payload={"sql": sql, "reason": reason},
                kind="sql",
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("template cache write 失败（忽略）：%r", e)
    return state


# ---------------------------------------------------------------------------
# Node: validate  — sqlglot 三重校验
# ---------------------------------------------------------------------------

def _extract_referenced_tables(sql: str, dialect: str) -> List[str]:
    """用 sqlglot 提取 SQL 里真正引用到的表名（含 FROM/JOIN/CTE）。

    - 返回保留原名大小写，调用方自己归一比较；
    - CTE / 子查询里的别名与 WITH 表不算"引用"，通过遍历 ``exp.Table`` 节点自然排除；
    - sqlglot 不可用或解析失败 → 返回 []，调用方把白名单校验降级为"通过"以避免假阳性。
    """
    try:
        import sqlglot
        from sqlglot import exp
    except Exception:  # noqa: BLE001
        return []
    try:
        stmts = sqlglot.parse(sql, read=dialect or None)
    except Exception:  # noqa: BLE001
        return []
    refs: List[str] = []
    seen = set()
    for stmt in stmts or []:
        if stmt is None:
            continue
        try:
            for t in stmt.find_all(exp.Table):
                name = (t.name or "").strip()
                if not name:
                    continue
                # schema-qualified 形式 db.tbl；把 db 单独也考虑进去供包含逻辑判断
                db = (t.args.get("db").name if t.args.get("db") else "") or ""
                qualified = f"{db}.{name}" if db else name
                for candidate in (qualified, name):
                    if candidate and candidate not in seen:
                        seen.add(candidate)
                        refs.append(candidate)
        except Exception:  # noqa: BLE001
            continue
    return refs


def _check_tables_allowed(
    sql: str, dialect: str, allowed_tables: List[str],
) -> Tuple[bool, str, List[str]]:
    """判断 SQL 中引用的表是否全部 ∈ allowed_tables。

    语义：``allowed_tables`` 为空 → 不做校验（返回 True）；非空 → 严格校验。
    表名归一：大小写不敏感；``db.tbl`` 与纯 ``tbl`` 都可匹配白名单条目。
    """
    if not allowed_tables:
        return True, "", []
    allowed_norm = {str(x).strip().lower() for x in allowed_tables if str(x).strip()}
    refs = _extract_referenced_tables(sql, dialect)
    # sqlglot 不可用/解析失败 → 保留原先的"通过"行为，交给下一层（execute）兜底
    if not refs:
        return True, "", []
    offenders: List[str] = []
    for r in refs:
        rn = r.strip().lower()
        if rn in allowed_norm:
            continue
        # 兼容：把 "db.tbl" 比较时忽略 db 前缀
        bare = rn.split(".")[-1]
        if bare in allowed_norm:
            continue
        offenders.append(r)
    # offenders 去重保序
    seen = set(); uniq = []
    for o in offenders:
        if o not in seen:
            seen.add(o); uniq.append(o)
    if uniq:
        return False, f"引用了白名单外的表：{', '.join(uniq)}", uniq
    return True, "", []


def node_validate(state: T2SState) -> T2SState:
    sql = (state.get("generated_sql") or "").strip()
    if not sql:
        state["validation_error"] = state.get("generate_reason") or "empty sql"
        return state
    try:
        ensure_readonly(sql, dialect=state.get("sqlglot_dialect") or "")
    except ConnectorError as e:
        state["validation_error"] = f"{e.code}: {e}"
        return state

    # "范围查询"：allowed_tables 非空时硬校验 SQL 引用的表必须全部 ∈ 白名单。
    # 违规走 revise 链路重新生成；revise prompt 会看到 validation_error 里的原因。
    allowed = state.get("allowed_tables") or []
    if allowed:
        ok, reason, offenders = _check_tables_allowed(
            sql, state.get("sqlglot_dialect") or "", list(allowed),
        )
        if not ok:
            try:
                from chayuan.server.observability.ks_metrics import T2SQL_TABLE_VIOLATION
                if T2SQL_TABLE_VIOLATION is not None:
                    T2SQL_TABLE_VIOLATION.labels(
                        dialect=state.get("dialect") or "-",
                        reason="out_of_whitelist",
                    ).inc()
            except Exception:  # noqa: BLE001
                pass
            state["validation_error"] = f"table_not_allowed: {reason}"
            return state

    state["validation_error"] = ""
    return state


# ---------------------------------------------------------------------------
# Node: execute
# ---------------------------------------------------------------------------

def node_execute(state: T2SState) -> T2SState:
    run_sql = state.get("run_sql")
    if not callable(run_sql):
        state["exec_error"] = "run_sql callable 未注入"
        return state
    sql = state.get("generated_sql") or ""
    try:
        columns, rows, err = run_sql(sql)
    except Exception as e:  # noqa: BLE001
        columns, rows, err = [], [], f"{type(e).__name__}: {e}"
    state["exec_columns"] = columns
    state["exec_rows"] = rows
    state["exec_error"] = err or ""
    return state


# ---------------------------------------------------------------------------
# Node: revise  — 执行失败时把 error 回喂 LLM
# ---------------------------------------------------------------------------

_REVISE_SYS = """你之前生成的 {dialect} SQL 在执行时报错。请根据错误信息修正 SQL，**只输出修正后的 SQL 与原因**。
严格 JSON：{{"sql": "<SQL>", "reason": "<你做了什么修改>"}}。"""


def node_revise(state: T2SState) -> T2SState:
    state["retry_count"] = int(state.get("retry_count") or 0) + 1
    sys_msg = _REVISE_SYS.format(dialect=state.get("dialect") or "SQL")
    user_msg = f"""【问题】
{state['query']}

【上次生成的 SQL】
```sql
{state.get('generated_sql') or ''}
```

【数据库错误】
{state.get('exec_error') or state.get('validation_error') or '(未知错误)'}

{_render_rag_context(state)}

请输出 JSON。"""
    from chayuan.server.shared.structured_llm import call_structured
    from chayuan.server.shared.structured_schemas import SqlGen
    res = call_structured(
        system=sys_msg, user=user_msg, schema=SqlGen,
        llm_model=state.get("llm_model"), default=SqlGen(sql="", reason=""),
    )
    sql = strip_sql_fences((res.sql if res else ""))
    reason = (res.reason if res else "").strip()
    if sql:
        state["generated_sql"] = sql
        state["generate_reason"] = reason
        state["validation_error"] = ""
        state["exec_error"] = ""
    else:
        # 生成为空：保持错误、不再递归
        state["retry_count"] = int(state.get("max_retries") or 2)
    return state


# ---------------------------------------------------------------------------
# Node: synthesize
# ---------------------------------------------------------------------------

def node_synthesize(state: T2SState) -> T2SState:
    # 组装 final_content & final_meta
    sql = state.get("generated_sql") or ""
    reason = state.get("generate_reason") or ""
    columns = state.get("exec_columns") or []
    rows = state.get("exec_rows") or []

    # 处理失败分支
    if not state.get("classified_can_answer", True):
        state["final_content"] = (
            f"无法基于该数据源回答：{state.get('classify_reason') or '-'}"
        )
        state["final_meta"] = {"error": "classify_no", "reason": state.get("classify_reason")}
        return state
    if state.get("validation_error"):
        state["final_content"] = (
            f"生成的 SQL 未通过只读安全校验：{state['validation_error']}\n\n"
            f"```sql\n{sql}\n```"
        )
        state["final_meta"] = {
            "error": "validation_failed",
            "reason": state["validation_error"],
            "generated_sql": sql,
        }
        return state
    if state.get("exec_error"):
        state["final_content"] = (
            f"SQL 执行仍然失败（已尝试 {state.get('retry_count') or 0} 次修正）：\n"
            f"{state['exec_error']}\n\n```sql\n{sql}\n```"
        )
        state["final_meta"] = {
            "error": "execution_failed",
            "reason": state["exec_error"],
            "generated_sql": sql,
            "retry_count": state.get("retry_count") or 0,
        }
        return state
    if not sql:
        state["final_content"] = f"未能生成 SQL：{reason or '未知原因'}"
        state["final_meta"] = {"error": "empty_sql", "reason": reason}
        return state

    md = rows_to_markdown(columns, rows, limit=50)
    state["final_content"] = (
        f"**生成的 SQL**：\n\n```sql\n{sql}\n```\n\n"
        f"**原因**：{reason}\n\n"
        f"**结果（{len(rows)} 行）**：\n\n{md}"
    )
    state["final_meta"] = {
        "columns": columns,
        "rows": rows[:50],
        "row_count": len(rows),
        "generated_sql": sql,
        "reason": reason,
        "retry_count": state.get("retry_count") or 0,
    }
    # 记一次命中计数（让高频有效的 pair 加权）
    try:
        from chayuan.server.db.repository.sql_training_repository import bump_hit
        ids = [
            int(p["id"]) for p in (state.get("retrieved_pairs") or [])
            if p.get("id")
        ]
        if ids:
            bump_hit(ids)
    except Exception:  # noqa: BLE001
        pass
    # Few-shot 自学习：把本次成功 Q→SQL 写入训练库（approved=0 待人工审阅）。
    # retrieve_similar 默认只拿 approved=1，所以不会自我污染。
    if sql and not state.get("exec_error") and not state.get("validation_error"):
        try:
            from chayuan.server.db.repository.sql_training_repository import add_sample
            add_sample(
                source_id=int(state.get("source_id") or 0),
                kind="pair",
                question=(state.get("query") or "")[:500],
                sql=sql[:4000],
                dialect=state.get("dialect") or "",
                approved=0,
            )
        except Exception:  # noqa: BLE001
            pass
    return state


# ---------------------------------------------------------------------------
# 组图与条件边
# ---------------------------------------------------------------------------

def _needs_retry(state: T2SState) -> str:
    """execute 后的条件分支：失败 → revise；成功 → synthesize。"""
    if state.get("exec_error"):
        if int(state.get("retry_count") or 0) < int(state.get("max_retries") or 2):
            return "revise"
    return "synthesize"


def _after_validate(state: T2SState) -> str:
    if state.get("validation_error"):
        if int(state.get("retry_count") or 0) < int(state.get("max_retries") or 2):
            return "revise"
        return "synthesize"
    return "execute"


def _after_classify(state: T2SState) -> str:
    return "schema_link" if state.get("classified_can_answer", True) else "synthesize"


def build_graph():
    """懒加载 langgraph；未安装时返回 None 让调用方走线性 fallback。"""
    try:
        from langgraph.graph import END, START, StateGraph
    except Exception:  # noqa: BLE001
        return None

    g = StateGraph(T2SState)
    g.add_node("classify", _observe_node("classify")(node_classify))
    g.add_node("schema_link", _observe_node("schema_link")(node_schema_link))
    g.add_node("retrieve_rag", _observe_node("retrieve_rag")(node_retrieve_rag))
    g.add_node("generate", _observe_node("generate")(node_generate))
    g.add_node("validate", _observe_node("validate")(node_validate))
    g.add_node("execute", _observe_node("execute")(node_execute))
    g.add_node("revise", _observe_node("revise")(node_revise))
    g.add_node("synthesize", _observe_node("synthesize")(node_synthesize))

    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", _after_classify,
                            {"schema_link": "schema_link", "synthesize": "synthesize"})
    g.add_edge("schema_link", "retrieve_rag")
    g.add_edge("retrieve_rag", "generate")
    g.add_edge("generate", "validate")
    g.add_conditional_edges("validate", _after_validate,
                            {"execute": "execute",
                             "revise": "revise",
                             "synthesize": "synthesize"})
    g.add_conditional_edges("execute", _needs_retry,
                            {"revise": "revise", "synthesize": "synthesize"})
    g.add_edge("revise", "validate")
    g.add_edge("synthesize", END)

    return g.compile()


# 单例 compile（首包延迟换常驻零耗）
_GRAPH_SINGLETON = None
_GRAPH_BUILD_LOCK_TRIED = False


def _get_graph():
    global _GRAPH_SINGLETON, _GRAPH_BUILD_LOCK_TRIED
    if _GRAPH_SINGLETON is not None:
        return _GRAPH_SINGLETON
    if _GRAPH_BUILD_LOCK_TRIED:
        return None
    _GRAPH_BUILD_LOCK_TRIED = True
    try:
        _GRAPH_SINGLETON = build_graph()
    except Exception as e:  # noqa: BLE001
        logger.warning("build_graph 失败：%r", e)
        _GRAPH_SINGLETON = None
    return _GRAPH_SINGLETON


# ---------------------------------------------------------------------------
# 对外入口：优先 LangGraph，否则线性执行一遍
# ---------------------------------------------------------------------------

def run_text2sql_pipeline(
    *,
    source_id: int,
    dialect: str,
    sqlglot_dialect: str,
    query: NLQuery,
    schema: SchemaSnapshot,
    run_sql,
    max_retries: int = 3,
    allowed_tables: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """返回 {content, meta} 供 Connector 封装成 RetrievalChunk。"""
    init_state: T2SState = {
        "source_id": int(source_id),
        "dialect": dialect,
        "sqlglot_dialect": sqlglot_dialect,
        "query": query.query,
        "top_k": int(query.top_k or 50),
        "history": query.history or [],
        "llm_model": query.llm_model,
        "schema": schema,
        "run_sql": run_sql,
        "max_retries": int(max_retries),
        "retry_count": 0,
        # P2-6：A/B 分桶键取 user_id；未登录则为 None（走 default 版本）
        "ab_key": (str(query.user_id) if getattr(query, "user_id", None) else None),
        # "范围查询"：Connector 传入的表白名单；空则不约束
        "allowed_tables": list(allowed_tables or []),
    }

    graph = _get_graph()
    if graph is not None:
        try:
            final_state = graph.invoke(init_state, config={"recursion_limit": 20})
            return {
                "content": final_state.get("final_content") or "",
                "meta": final_state.get("final_meta") or {},
                "generated_sql": final_state.get("generated_sql") or "",
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("LangGraph 运行失败，回退线性：%r", e)

    # --- 线性 fallback ---
    s = node_classify(init_state)
    if s.get("classified_can_answer", True):
        s = node_schema_link(s)
        s = node_retrieve_rag(s)
        s = node_generate(s)
        for _ in range(int(max_retries) + 1):
            s = node_validate(s)
            if s.get("validation_error"):
                if int(s.get("retry_count") or 0) >= int(max_retries):
                    break
                s = node_revise(s)
                continue
            s = node_execute(s)
            if not s.get("exec_error"):
                break
            if int(s.get("retry_count") or 0) >= int(max_retries):
                break
            s = node_revise(s)
    s = node_synthesize(s)
    return {
        "content": s.get("final_content") or "",
        "meta": s.get("final_meta") or {},
        "generated_sql": s.get("generated_sql") or "",
    }
