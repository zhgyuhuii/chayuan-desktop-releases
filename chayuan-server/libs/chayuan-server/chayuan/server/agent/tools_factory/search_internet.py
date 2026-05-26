from typing import Dict, List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.utilities.bing_search import BingSearchAPIWrapper
from langchain_community.utilities.duckduckgo_search import DuckDuckGoSearchAPIWrapper
from langchain_community.utilities.searx_search import SearxSearchWrapper
from markdownify import markdownify
from strsimpy.normalized_levenshtein import NormalizedLevenshtein

from chayuan.settings import Settings
from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import get_tool_config

from .tools_registry import regist_tool, format_context

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)

def searx_search(text ,config, top_k: int):
    search = SearxSearchWrapper(
        searx_host=config["host"],
        engines=config["engines"],
        categories=config["categories"],
    )
    search.params["language"] = config.get("language", "zh-CN")
    return search.results(text, top_k)


def bing_search(text, config, top_k:int):
    search = BingSearchAPIWrapper(
        bing_subscription_key=config["bing_key"],
        bing_search_url=config["bing_search_url"],
    )
    return search.results(text, top_k)


def duckduckgo_search(text, config, top_k:int):
    search = DuckDuckGoSearchAPIWrapper()
    return search.results(text, top_k)


def metaphor_search(
    text: str,
    config: dict,
    top_k:int
) -> List[Dict]:
    from metaphor_python import Metaphor

    client = Metaphor(config["metaphor_api_key"])
    search = client.search(text, num_results=top_k, use_autoprompt=True)
    contents = search.get_contents().contents
    for x in contents:
        x.extract = markdownify(x.extract)
    if config["split_result"]:
        docs = [
            Document(page_content=x.extract, metadata={"link": x.url, "title": x.title})
            for x in contents
        ]
        text_splitter = RecursiveCharacterTextSplitter(
            ["\n\n", "\n", ".", " "],
            chunk_size=config["chunk_size"],
            chunk_overlap=config["chunk_overlap"],
        )
        splitted_docs = text_splitter.split_documents(docs)
        if len(splitted_docs) > top_k:
            normal = NormalizedLevenshtein()
            for x in splitted_docs:
                x.metadata["score"] = normal.similarity(text, x.page_content)
            splitted_docs.sort(key=lambda x: x.metadata["score"], reverse=True)
            splitted_docs = splitted_docs[: top_k]

        docs = [
            {
                "snippet": x.page_content,
                "link": x.metadata["link"],
                "title": x.metadata["title"],
            }
            for x in splitted_docs
        ]
    else:
        docs = [
            {"snippet": x.extract, "link": x.url, "title": x.title} for x in contents
        ]

    return docs


def tavily_search(text: str, config: dict, top_k: int) -> List[Dict]:
    """Tavily Search —— LangChain 官方主推的 AI 原生搜索。

    免费 tier 每月约 1000 次请求，对 LLM 友好（自带摘要），国内可直连。
    需要 ``api_key``（或环境变量 ``TAVILY_API_KEY``），其余参数都有合理默认。
    """
    from langchain_community.utilities.tavily_search import TavilySearchAPIWrapper

    api_key = (config or {}).get("api_key", "").strip()
    wrapper = TavilySearchAPIWrapper(tavily_api_key=api_key) if api_key else TavilySearchAPIWrapper()
    # wrapper.results(...) 返回 [{"title","url","content","score",...}]
    raw = wrapper.results(
        query=text,
        max_results=top_k,
        include_answer=bool((config or {}).get("include_answer", False)),
        include_raw_content=bool((config or {}).get("include_raw_content", False)),
        search_depth=str((config or {}).get("search_depth", "basic") or "basic"),
    )
    return [
        {
            "snippet": r.get("content") or r.get("raw_content") or "",
            "link": r.get("url", ""),
            "title": r.get("title", ""),
        }
        for r in (raw or [])
    ]


def serper_search(text: str, config: dict, top_k: int) -> List[Dict]:
    """Serper —— 代理 Google Search API。

    英文语料质量与覆盖度强于 DuckDuckGo；国内网络通畅时是最佳搜索引擎选择。
    """
    from langchain_community.utilities.google_serper import GoogleSerperAPIWrapper

    api_key = (config or {}).get("api_key", "").strip()
    if not api_key:
        raise ValueError("serper 引擎需要配置 api_key（https://serper.dev 获取）")
    wrapper = GoogleSerperAPIWrapper(
        serper_api_key=api_key,
        k=top_k,
        gl=str((config or {}).get("gl", "us") or "us"),
        hl=str((config or {}).get("hl", "zh-cn") or "zh-cn"),
        type=str((config or {}).get("type", "search") or "search"),
    )
    raw = wrapper.results(query=text)
    # Serper 返回 {"organic": [...], "answerBox": {...}, ...}
    items = (raw or {}).get("organic") or []
    docs: List[Dict] = []
    for r in items[:top_k]:
        docs.append({
            "snippet": r.get("snippet", ""),
            "link": r.get("link", ""),
            "title": r.get("title", ""),
        })
    # 如果 Serper 回了「answer box」，把它作为第一条优先喂给 LLM
    ab = (raw or {}).get("answerBox") or {}
    if ab:
        docs.insert(0, {
            "snippet": ab.get("snippet") or ab.get("answer") or "",
            "link": ab.get("link", ""),
            "title": ab.get("title", "answerBox"),
        })
    return docs


SEARCH_ENGINES = {
    "bing": bing_search,
    "duckduckgo": duckduckgo_search,
    "metaphor": metaphor_search,
    "searx": searx_search,
    "tavily": tavily_search,
    "serper": serper_search,
}


def search_result2docs(search_results) -> List[Document]:
    docs = []
    for result in search_results:
        doc = Document(
            page_content=result["snippet"] if "snippet" in result.keys() else "",
            metadata={
                "source": result["link"] if "link" in result.keys() else "",
                "filename": result["title"] if "title" in result.keys() else "",
            },
        )
        docs.append(doc)
    return docs


def search_engine(query: str, top_k:int=0, engine_name: str="", config: dict={}):
    config = config or get_tool_config("search_internet")
    if top_k <= 0:
        top_k = config.get("top_k", Settings.kb_settings.SEARCH_ENGINE_TOP_K)
    engine_name = engine_name or config.get("search_engine_name")
    search_engine_use = SEARCH_ENGINES[engine_name]
    results = search_engine_use(
        text=query, config=config["search_engine_config"][engine_name], top_k=top_k
    )
    docs = [x for x in search_result2docs(results) if x.page_content and x.page_content.strip()]
    return {"docs": docs, "search_engine": engine_name}


@regist_tool(title="互联网搜索")
def search_internet(query: str = Field(description="互联网搜索关键词(中英文均可),用于查询大模型未掌握的最新信息")):
    """通过搜索引擎(Bing / DuckDuckGo / Tavily / Serper / SearxNG / Metaphor)查询互联网公开信息。
调用时机:用户问题涉及 LLM 训练数据可能过时或未覆盖的最新信息——时事新闻、新发布的产品/版本/事件、当前价格、公众人物近况、特定网站现存内容。
输入:中英文搜索关键词。
输出:相关网页摘要列表(标题/摘要/链接,不含完整正文)。
不要用于:本地知识库、用户上传文件、私域信息、数学计算、数据库查询。"""
    return BaseToolOutput(search_engine(query=query), format=format_context)
