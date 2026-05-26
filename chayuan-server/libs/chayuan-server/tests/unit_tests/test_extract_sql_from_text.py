"""结构化检索:从 langchain / LLM 自由文本里挖 SQL 的多 pattern 兜底测试。

NL→SQL 失败常见原因不是 LLM 没生成 SQL,而是输出格式跟正则不严格匹配。
覆盖 4 种已观测到的输出形态,确保 _extract_sql_from_text 都能挑出 SQL,
并且去掉围栏 / 后置自白等噪音。
"""
from __future__ import annotations


def test_extract_from_self_format():
    """query_database 自己拼的 "执行的sql:'…'" 形态,正常路径。"""
    from chayuan.server.api_server.knowledge_universe_routes import _extract_sql_from_text

    text = "查询结果:8\n\n执行的sql:'SELECT COUNT(*) FROM users'\n\n"
    assert _extract_sql_from_text(text) == "SELECT COUNT(*) FROM users"


def test_extract_from_langchain_trace():
    """LLM 没按 query_database 包装但 langchain 原始 trace 在 text 里。"""
    from chayuan.server.api_server.knowledge_universe_routes import _extract_sql_from_text

    text = (
        "Question: 有多少用户\n"
        "SQLQuery: SELECT COUNT(*) FROM users\n"
        "SQLResult: [(8,)]\n"
        "Answer: 共有 8 位用户。"
    )
    assert _extract_sql_from_text(text) == "SELECT COUNT(*) FROM users"


def test_extract_from_markdown_fence():
    """LLM 把 SQL 包在 ```sql 围栏里。"""
    from chayuan.server.api_server.knowledge_universe_routes import _extract_sql_from_text

    text = "下面是查询:\n```sql\nSELECT COUNT(*) AS user_count FROM users;\n```\n执行成功"
    assert _extract_sql_from_text(text) == "SELECT COUNT(*) AS user_count FROM users"


def test_extract_with_trailing_commentary():
    """围栏 SQL + 后置 LLM 自白(常见诱因) — 必须只取 SQL,不带后置文字。"""
    from chayuan.server.api_server.knowledge_universe_routes import _extract_sql_from_text

    text = (
        "SELECT COUNT(*) AS user_count FROM users;\n```\n"
        "Since the actual execution of the SQL query is not possible..."
    )
    sql = _extract_sql_from_text(text)
    assert sql is not None
    assert sql.startswith("SELECT")
    assert "Since" not in sql
    assert "```" not in sql


def test_extract_returns_none_for_no_sql():
    from chayuan.server.api_server.knowledge_universe_routes import _extract_sql_from_text

    assert _extract_sql_from_text("") is None
    assert _extract_sql_from_text(None) is None
    assert _extract_sql_from_text("用户问题与数据库无关") is None


def test_extract_skips_non_select_candidates():
    """正则即便命中片段,如果 strip 后不是 SELECT/WITH,要返回 None,避免把 LLM 注释当 SQL 执行。"""
    from chayuan.server.api_server.knowledge_universe_routes import _extract_sql_from_text

    text = "执行的sql:'-- 用户表有 8 行'"
    assert _extract_sql_from_text(text) is None
