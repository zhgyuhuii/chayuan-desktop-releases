"""SQL 只读安全校验。

三重防线：

1. **静态 AST 检查**：用 sqlglot 解析为 AST，只允许 SELECT / WITH / SHOW / EXPLAIN /
   DESCRIBE 等只读语句；任何写/DDL 关键字节点一律拒。多方言感知。
2. **字符串前缀检查**：sqlglot 不可用时的兜底；覆盖常见关键字。
3. **运行时拦截**：在 SQLAlchemy `before_cursor_execute` 事件里再拦一次。

任何一层拒绝都会抛 ConnectorError(code="readonly_violation")。
"""
from __future__ import annotations

import re
from typing import Tuple

from chayuan.server.knowledge_source.base import ConnectorError

_WRITE_KEYWORDS = (
    "insert", "update", "delete", "create", "drop", "alter", "truncate",
    "rename", "grant", "revoke", "merge", "replace", "attach", "detach",
    "vacuum", "call", "exec", "execute",
)


def _strip_comments(sql: str) -> str:
    s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    s = re.sub(r"--[^\n]*", " ", s)
    return s.strip()


def _string_check(sql: str) -> Tuple[bool, str]:
    s = _strip_comments(sql).lower()
    if not s:
        return False, "空 SQL"
    first = s.split()[0] if s.split() else ""
    if first in _WRITE_KEYWORDS:
        return False, f"检测到写操作关键字：{first.upper()}"
    for kw in _WRITE_KEYWORDS:
        if re.search(rf"\b{kw}\b", s):
            # 例外：SELECT ... FROM create_view_tbl 这种被误命中的场景较少；
            # 但为安全起见，只要有写关键字词素就拒绝
            return False, f"SQL 中包含禁止关键字：{kw.upper()}"
    return True, ""


def ensure_readonly(sql: str, dialect: str = "") -> None:
    """只读校验入口。违规抛 ConnectorError。"""
    try:
        import sqlglot
        from sqlglot import exp
    except Exception:
        ok, msg = _string_check(sql)
        if not ok:
            raise ConnectorError(msg, code="readonly_violation", dialect=dialect)
        return

    try:
        statements = sqlglot.parse(sql, read=dialect or None)
    except Exception as e:  # noqa: BLE001
        raise ConnectorError(
            f"SQL 解析失败：{e}", code="sql_parse_error", dialect=dialect,
        ) from e

    if not statements:
        raise ConnectorError("空 SQL", code="readonly_violation", dialect=dialect)

    allowed_roots = (
        exp.Select, exp.Union, exp.Intersect, exp.Except, exp.With,
        exp.Show, exp.Describe, exp.Pragma,
    )
    # EXPLAIN 在不同方言里类型不同；兜底用 sql() 前缀判断
    for stmt in statements:
        if stmt is None:
            raise ConnectorError("空 SQL 片段", code="readonly_violation", dialect=dialect)
        # 顶层允许的只读节点
        if not isinstance(stmt, allowed_roots):
            head = (stmt.sql(dialect=dialect or None) or "").strip().lower()
            if not (head.startswith("explain") or head.startswith("describe")
                    or head.startswith("desc ") or head.startswith("show ")):
                raise ConnectorError(
                    f"仅允许只读查询；检测到：{type(stmt).__name__}",
                    code="readonly_violation", dialect=dialect,
                )
        # 深度搜索：是否含有写节点。sqlglot 25+ 合并/改名了一些节点，这里按 name 容错查找。
        _BAD_NODE_NAMES = (
            "Insert", "Update", "Delete", "Drop", "Create",
            "Alter",           # sqlglot 25+：所有 ALTER 统一到 exp.Alter
            "AlterTable", "AlterColumn", "AlterIndex", "AlterRename", "AlterSet",
            "TruncateTable",
        )
        for node_name in _BAD_NODE_NAMES:
            node_cls = getattr(exp, node_name, None)
            if node_cls is None:
                continue
            try:
                if stmt.find(node_cls):  # type: ignore[arg-type]
                    raise ConnectorError(
                        f"SQL 中包含写操作：{node_name}",
                        code="readonly_violation", dialect=dialect,
                    )
            except ConnectorError:
                raise
            except Exception:  # noqa: BLE001
                continue


def intercept_readonly(conn, cursor, statement, parameters, context, executemany):
    """SQLAlchemy ``before_cursor_execute`` 钩子：拦第三道。"""
    if not statement:
        return
    s = statement.strip().lower()
    for kw in _WRITE_KEYWORDS:
        if s.startswith(kw + " ") or s == kw:
            from sqlalchemy.exc import OperationalError
            raise OperationalError(
                f"Database is read-only. Blocked write operation: {kw.upper()}",
                params=None,
                orig=None,
            )
