"""Notion 搜索工具（读取页面 / 数据库 entries）。

Notion 官方 API 提供统一的 ``/v1/search`` 接口；只需一个 internal integration token。
本工具返回匹配结果的标题 + 摘要 + URL，把读取正文/数据库字段交给后续调用。
"""
from typing import Literal

import httpx

from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import get_tool_config

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)


@regist_tool(title="Notion 搜索")
def notion_search(
    query: str = Field(description="搜索关键词；支持中英文"),
    object_type: Literal["any", "page", "database"] = Field(
        "any", description="限定对象类型",
    ),
):
    """通过 Notion 官方 API 搜索 workspace 中的页面/数据库条目。
调用时机:用户问「我的 Notion 里有没有 X」「找一下 Notion 里关于 Y 的笔记」时,前提是已配置 Notion 集成 token。
输入:中英文搜索关键词。
输出:页面标题、URL、最近编辑时间、所属数据库。
不要用于:Notion 之外的笔记(本地 KB / Confluence)、用户没说「Notion」的问题、写入操作。"""
    cfg = get_tool_config("notion_search") or {}
    token = (cfg.get("token") or "").strip()
    if not token:
        return BaseToolOutput({
            "error": "未配置 Notion integration token (https://www.notion.so/my-integrations)",
        }, format="json")

    body = {"query": query, "page_size": int(cfg.get("page_size", 5) or 5)}
    if object_type != "any":
        body["filter"] = {"property": "object", "value": object_type}

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": cfg.get("api_version", "2022-06-28"),
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=15) as cli:
            resp = cli.post("https://api.notion.com/v1/search",
                            json=body, headers=headers)
            data = resp.json() if resp.content else {}
    except Exception as e:  # noqa: BLE001
        return BaseToolOutput({"error": f"{type(e).__name__}: {e}"}, format="json")

    out = []
    for r in data.get("results", [])[: int(cfg.get("page_size", 5) or 5)]:
        title = ""
        try:
            if r.get("object") == "page":
                props = r.get("properties") or {}
                for v in props.values():
                    if v.get("type") == "title":
                        segs = v.get("title") or []
                        title = "".join(s.get("plain_text", "") for s in segs)
                        break
            elif r.get("object") == "database":
                segs = r.get("title") or []
                title = "".join(s.get("plain_text", "") for s in segs)
        except Exception:  # noqa: BLE001
            pass
        out.append({
            "object": r.get("object"), "title": title or "(untitled)",
            "url": r.get("url"), "id": r.get("id"),
        })
    return BaseToolOutput({
        "status": resp.status_code, "results": out,
    }, format="json")
