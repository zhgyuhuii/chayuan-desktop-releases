"""只读 SQL 安全校验的覆盖测试。

是 Text2SQL 防写入的最后一道防线；必须覆盖：
- 常见 DML（INSERT/UPDATE/DELETE）× 多方言
- DDL（CREATE/DROP/ALTER/TRUNCATE）× 多方言
- 子查询 / CTE 里的写操作
- 合法只读语句（SELECT / WITH / SHOW / DESCRIBE）必须放行
- sqlglot 解析失败（非法 SQL）要报 sql_parse_error
- 空 SQL 要拒绝

所有测试不依赖外部 DB / LLM；纯静态校验，毫秒级。
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# 合法只读：必须放行
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dialect", ["mysql", "postgres", "sqlite", "tsql", "oracle", "clickhouse"])
@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "SELECT id, name FROM users WHERE id = 1",
    "SELECT COUNT(*) FROM orders o JOIN products p ON o.product_id = p.id",
    "WITH top_sales AS (SELECT product_id, SUM(qty) FROM orders GROUP BY product_id) SELECT * FROM top_sales",
])
def test_readonly_accepts_valid_select(dialect, sql):
    from chayuan.server.knowledge_source.sql.safety import ensure_readonly
    ensure_readonly(sql, dialect=dialect)  # 不抛即通过


@pytest.mark.parametrize("dialect", ["mysql", "postgres", "sqlite", "tsql", "oracle"])
def test_readonly_accepts_union(dialect):
    """UNION 在多数方言是合法只读。ClickHouse 要求 UNION ALL/DISTINCT，排除。"""
    from chayuan.server.knowledge_source.sql.safety import ensure_readonly
    ensure_readonly("SELECT * FROM users UNION SELECT * FROM customers", dialect=dialect)


def test_readonly_accepts_union_all_clickhouse():
    from chayuan.server.knowledge_source.sql.safety import ensure_readonly
    ensure_readonly("SELECT * FROM users UNION ALL SELECT * FROM customers",
                     dialect="clickhouse")


# ---------------------------------------------------------------------------
# 写操作：必须拒绝
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql,expected_code", [
    ("INSERT INTO users (name) VALUES ('x')", "readonly_violation"),
    ("DELETE FROM users WHERE id = 1", "readonly_violation"),
    ("UPDATE users SET name = 'x' WHERE id = 1", "readonly_violation"),
    ("DROP TABLE users", "readonly_violation"),
    ("CREATE TABLE foo (id INT)", "readonly_violation"),
    ("ALTER TABLE users ADD COLUMN x TEXT", "readonly_violation"),
    ("TRUNCATE TABLE users", "readonly_violation"),
    ("", "readonly_violation"),  # 空 SQL 也拒绝
])
def test_readonly_rejects_writes(sql, expected_code):
    from chayuan.server.knowledge_source.base import ConnectorError
    from chayuan.server.knowledge_source.sql.safety import ensure_readonly
    with pytest.raises(ConnectorError) as exc_info:
        ensure_readonly(sql, dialect="mysql")
    assert exc_info.value.code == expected_code


# ---------------------------------------------------------------------------
# CTE + DML / 多条语句的组合攻击
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    # CTE 顶层是 SELECT 但内部有 INSERT（部分方言如 PG 支持 ctes with insert）
    "WITH x AS (INSERT INTO users (name) VALUES ('bad') RETURNING id) SELECT * FROM x",
    # 多语句
    "SELECT 1; DROP TABLE users",
    # 伪装大小写
    "InSeRt INTO users (name) VALUES ('x')",
])
def test_readonly_rejects_tricky(sql):
    from chayuan.server.knowledge_source.base import ConnectorError
    from chayuan.server.knowledge_source.sql.safety import ensure_readonly
    with pytest.raises(ConnectorError):
        ensure_readonly(sql, dialect="postgres")


# ---------------------------------------------------------------------------
# SQL 解析失败
# ---------------------------------------------------------------------------

def test_readonly_rejects_unparseable():
    from chayuan.server.knowledge_source.base import ConnectorError
    from chayuan.server.knowledge_source.sql.safety import ensure_readonly
    with pytest.raises(ConnectorError) as exc_info:
        ensure_readonly("SELECT *** FROM where", dialect="mysql")
    assert exc_info.value.code in ("sql_parse_error", "readonly_violation")


# ---------------------------------------------------------------------------
# 运行时 intercept 钩子：即使静态放过，运行时也必须再拒一次
# ---------------------------------------------------------------------------

def test_runtime_intercept_blocks_dml():
    from sqlalchemy.exc import OperationalError
    from chayuan.server.knowledge_source.sql.safety import intercept_readonly
    with pytest.raises(OperationalError):
        intercept_readonly(
            conn=None, cursor=None,
            statement="DELETE FROM users WHERE 1=1",
            parameters=None, context=None, executemany=False,
        )


def test_runtime_intercept_allows_select():
    from chayuan.server.knowledge_source.sql.safety import intercept_readonly
    # 不抛即通过
    intercept_readonly(
        conn=None, cursor=None,
        statement="SELECT 1",
        parameters=None, context=None, executemany=False,
    )


def test_strip_sql_fences_removes_markdown_block():
    from chayuan.server.knowledge_source.sql.text2sql import strip_sql_fences

    assert strip_sql_fences("```sql\nSELECT platform_name FROM model_platform;\n```") == (
        "SELECT platform_name FROM model_platform"
    )


def test_strip_sql_fences_truncates_trailing_commentary():
    """LLM 在 SQL 后追加 ``` 包裹的自白时,只取真正的 SQL。"""
    from chayuan.server.knowledge_source.sql.text2sql import strip_sql_fences

    raw = (
        "SELECT COUNT(*) AS user_count FROM users;\n```\n"
        "Since the actual execution of the SQL query and fetching results are not "
        "possible in this environment, I will provide a hypothetical result based "
        "on the given data."
    )
    assert strip_sql_fences(raw) == "SELECT COUNT(*) AS user_count FROM users"


def test_strip_sql_fences_picks_first_fenced_block():
    """前置自然语言 + 围栏 SQL + 后置自白:取围栏内 SQL。"""
    from chayuan.server.knowledge_source.sql.text2sql import strip_sql_fences

    raw = (
        "Here is the SQL:\n```sql\nSELECT id FROM users WHERE active = true;\n```\n"
        "Note: this is a hypothetical answer."
    )
    assert strip_sql_fences(raw) == "SELECT id FROM users WHERE active = true"


def test_strip_sql_fences_handles_unclosed_open_fence():
    """LLM 只输出 ```sql 开头但没闭合时,仍能取到 SQL。"""
    from chayuan.server.knowledge_source.sql.text2sql import strip_sql_fences

    assert strip_sql_fences("```sql\nSELECT 1 FROM users;") == "SELECT 1 FROM users"
