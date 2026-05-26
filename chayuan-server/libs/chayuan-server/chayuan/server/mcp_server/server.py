"""MCP Server 实现（basis on ``mcp`` 官方 SDK）。

把察元核心能力包成 MCP tools：
- ``chayuan_kb_search``           — 向量 KB 检索
- ``chayuan_multi_source_search`` — 多源并行
- ``chayuan_text2sql``            — 指定 SQL 数据源 Text2SQL
- ``chayuan_pii_scan``            — PII 扫描 + 脱敏

依赖：``pip install 'mcp>=1.4,<2'``；未装时 ``create_server`` 抛 ImportError。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger("chayuan.mcp_server")


def create_server():
    """构建 MCP Server 实例。"""
    from mcp.server import Server  # type: ignore
    from mcp.types import TextContent, Tool  # type: ignore

    server = Server("chayuan")

    # ------- tools list（schema 供 MCP client 展示）-------
    @server.list_tools()  # type: ignore
    async def _list_tools() -> List[Tool]:
        return [
            Tool(
                name="chayuan_kb_search",
                description="检索察元向量知识库，返回命中的片段列表（markdown 格式）。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "kb_name": {"type": "string", "description": "知识库名"},
                        "query": {"type": "string", "description": "用户问题"},
                        "top_k": {"type": "integer", "default": 5},
                    },
                    "required": ["kb_name", "query"],
                },
            ),
            Tool(
                name="chayuan_multi_source_search",
                description="多源并行检索（SQL/Mongo/ES/Vector），按 source_ids 选源，"
                             "或 select_all=true 检索所有用户可访问源。返回聚合片段 + 元数据。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "source_ids": {"type": "array", "items": {"type": "integer"},
                                        "default": []},
                        "select_all": {"type": "boolean", "default": False},
                        "top_k": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="chayuan_text2sql",
                description="对指定 SQL 数据源（source_id）做自然语言查询；返回生成的 SQL、行集合预览与错误。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source_id": {"type": "integer"},
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "default": 50},
                    },
                    "required": ["source_id", "query"],
                },
            ),
            Tool(
                name="chayuan_pii_scan",
                description="对文本做 PII 检测 + 按指定角色脱敏预览。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "user_role": {"type": "string", "default": "user"},
                    },
                    "required": ["text"],
                },
            ),
            Tool(
                name="chayuan_list_kbs",
                description="列出察元所有知识库（及其向量类型、embed 模型）。",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="chayuan_list_sources",
                description="列出察元所有结构化 / 非结构化数据源（SQL/Mongo/ES/Vector/...）。",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    # ------- tool 调用路由 -------
    @server.call_tool()  # type: ignore
    async def _call_tool(name: str, arguments: Dict[str, Any]):
        try:
            if name == "chayuan_kb_search":
                res = await _kb_search(**arguments)
            elif name == "chayuan_multi_source_search":
                res = await _multi_source_search(**arguments)
            elif name == "chayuan_text2sql":
                res = await _text2sql(**arguments)
            elif name == "chayuan_pii_scan":
                res = _pii_scan(**arguments)
            elif name == "chayuan_list_kbs":
                res = _list_kbs()
            elif name == "chayuan_list_sources":
                res = _list_sources()
            else:
                return [TextContent(type="text", text=f"unknown tool: {name}")]
        except Exception as e:  # noqa: BLE001
            logger.warning("mcp call_tool %s 失败：%r", name, e)
            return [TextContent(type="text",
                                text=f"error: {type(e).__name__}: {e}")]
        return [TextContent(type="text",
                            text=json.dumps(res, ensure_ascii=False, default=str)[:20000])]

    return server


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

async def _kb_search(kb_name: str, query: str, top_k: int = 5, **_kw):
    from chayuan.server.knowledge_base.kb_doc_api import search_docs
    docs = search_docs(
        query=query, knowledge_base_name=kb_name,
        top_k=int(top_k or 5), score_threshold=2.0, file_name="", metadata={},
    )
    return [{"content": d.get("page_content", ""), "metadata": d.get("metadata", {})}
            for d in (docs or [])]


async def _multi_source_search(
    query: str, source_ids=None, select_all: bool = False, top_k: int = 5, **_kw,
):
    from chayuan.server.db.repository.knowledge_source_repository import list_sources
    from chayuan.server.knowledge_source.orchestrator import multi_search_sync

    all_rows = list_sources()
    if select_all:
        sources = all_rows
    elif source_ids:
        ids = {int(x) for x in source_ids}
        sources = [r for r in all_rows if int(r["id"]) in ids]
    else:
        sources = []
    chunks, meta = await multi_search_sync(
        query=query, sources=sources, top_k=int(top_k or 5),
    )
    return {
        "aggregated": [c.to_wire() for c in chunks],
        "sources": meta,
    }


async def _text2sql(source_id: int, query: str, top_k: int = 50, **_kw):
    from chayuan.server.db.repository.knowledge_source_repository import (
        connection_spec_for_source,
    )
    from chayuan.server.knowledge_source.registry import build_connector
    from chayuan.server.knowledge_source.types import NLQuery
    resolved = connection_spec_for_source(int(source_id))
    if resolved is None:
        return {"error": "source not found or not SQL"}
    _, spec = resolved
    conn = build_connector(spec=spec, source_id=int(source_id))
    nl = NLQuery(query=query, top_k=int(top_k or 50))
    chunks = await conn.search(nl)
    if not chunks:
        return {"error": "empty result"}
    first = chunks[0]
    meta = first.citation.meta or {}
    return {
        "generated_sql": first.citation.generated_query,
        "content": first.content,
        "row_count": meta.get("row_count"),
        "error": meta.get("error"),
    }


def _pii_scan(text: str, user_role: str = "user", **_kw):
    from chayuan.server.governance.masking import apply_masking
    from chayuan.server.governance.pii import scan_text
    ents = scan_text(text or "", enable_presidio=False)
    return {
        "entities": ents,
        "masked": apply_masking(text or "", ents, user_role=user_role or "user"),
    }


def _list_kbs(**_kw):
    from chayuan.server.knowledge_base.kb_api import list_kbs
    try:
        res = list_kbs()
        data = res.data if hasattr(res, "data") else res
        return {"kbs": data}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def _list_sources(**_kw):
    from chayuan.server.db.repository.knowledge_source_repository import list_sources
    try:
        rows = list_sources()
        return {"sources": [
            {"id": int(r["id"]), "name": r.get("name", ""),
             "type": r.get("type", ""), "dialect": r.get("dialect", "")}
            for r in rows
        ]}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# 传输
# ---------------------------------------------------------------------------

def run_stdio():
    """stdio 传输（Claude Desktop / Cursor）。"""
    from mcp.server.stdio import stdio_server  # type: ignore
    server = create_server()

    async def _main():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_main())


def run_sse(host: str = "127.0.0.1", port: int = 7862):
    """SSE 传输（HTTP），用于远程访问。"""
    import uvicorn  # type: ignore
    from mcp.server.sse import SseServerTransport  # type: ignore
    from starlette.applications import Starlette  # type: ignore
    from starlette.routing import Mount, Route  # type: ignore

    server = create_server()
    transport = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with transport.connect_sse(
            request.scope, request.receive, request._send,
        ) as (read, write):
            await server.run(read, write, server.create_initialization_options())

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=transport.handle_post_message),
        ],
    )
    uvicorn.run(app, host=host, port=int(port))
