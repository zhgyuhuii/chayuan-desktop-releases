"""NewsAPI 新闻聚合。

newsapi.org —— 聚合全球 70+ 国家、150K+ 新闻源，支持按关键词 / 国家 / 分类过滤。
免费开发者 tier 每天 100 次。
"""
from typing import Optional

import httpx

from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import get_tool_config

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)


@regist_tool(title="NewsAPI 新闻")
def news_api(
    query: str = Field(description="查询关键词，空表示头条"),
    category: Optional[str] = Field(
        None, description="business / entertainment / general / health / science / sports / technology",
    ),
    country: Optional[str] = Field(None, description="ISO 2 字母国家码，如 us / cn / jp"),
):
    """通过 newsapi.org 拉取近 30 天的新闻聚合(需 API Key)。
调用时机:用户问「最近的新闻」「X 领域的头条」「X 国家近期发生了什么」需要新闻聚合的场景。
输入:query(关键词,空字符串则返头条);country(ISO2 国家码,如 us / cn / jp);category(business / technology / science / health / sports / entertainment)。
输出:文章标题、来源、发布时间、链接、摘要。
不要用于:历史事件深度分析(用 search_internet 或 wikipedia)、技术问答、超过 30 天前的内容。"""
    cfg = get_tool_config("news_api") or {}
    api_key = (cfg.get("api_key") or "").strip()
    if not api_key:
        return BaseToolOutput({
            "error": "未配置 NewsAPI Key，请到 https://newsapi.org 申请",
        }, format="json")

    page_size = int(cfg.get("page_size", 5) or 5)
    # 有关键词走 /everything，否则走 /top-headlines
    if (query or "").strip():
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": query, "apiKey": api_key, "pageSize": page_size,
            "language": cfg.get("language", "en"),
            "sortBy": cfg.get("sort_by", "publishedAt"),
        }
    else:
        url = "https://newsapi.org/v2/top-headlines"
        params = {"apiKey": api_key, "pageSize": page_size}
        if country:
            params["country"] = country
        if category:
            params["category"] = category
    try:
        with httpx.Client(timeout=float(cfg.get("timeout", 15) or 15)) as cli:
            resp = cli.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        return BaseToolOutput({"error": f"{type(e).__name__}: {e}"}, format="json")

    articles = [
        {
            "title": a.get("title"),
            "source": (a.get("source") or {}).get("name"),
            "url": a.get("url"),
            "publishedAt": a.get("publishedAt"),
            "summary": a.get("description") or a.get("content"),
        }
        for a in (data.get("articles") or [])
    ]
    return BaseToolOutput({"query": query, "articles": articles}, format="json")
