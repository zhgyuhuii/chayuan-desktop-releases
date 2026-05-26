"""StackExchange（StackOverflow 为主）编程问答检索。

让代码助手能直接引用 StackOverflow / ServerFault / AskUbuntu 的高票答案，
免费，无需 API Key（有每日配额；批量调用建议注册 key 配额更大）。
"""
from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import get_tool_config

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)


@regist_tool(title="StackExchange 编程问答")
def stackexchange(
    query: str = Field(description="英文技术问题或关键词"),
):
    """搜索 StackExchange(以 StackOverflow 为主)上的技术问答。
调用时机:用户问编程报错、代码片段、库/框架/工具用法、配置问题、性能优化、线上故障排查等技术问题。
输入:英文技术问题或关键词(SO 99% 答案是英文,英文召回显著优于中文;中文报错信息建议保留原文)。
输出:相关问题标题、最高票答案摘要、链接。
不要用于:非技术问题、纯概念解释(用 wikipedia_search)、最新框架版本(用 search_internet)。"""
    try:
        from langchain_community.tools.stackexchange.tool import (
            StackExchangeTool,
        )
        from langchain_community.utilities.stackexchange import (
            StackExchangeAPIWrapper,
        )
    except ImportError:
        return BaseToolOutput({
            "error": "stackapi 未安装，请 `pip install stackapi`",
        }, format="json")

    cfg = get_tool_config("stackexchange") or {}
    api = StackExchangeAPIWrapper(
        max_results=int(cfg.get("max_results", 3) or 3),
        query_type=str(cfg.get("query_type", "all") or "all"),
    )
    try:
        tool = StackExchangeTool(api_wrapper=api)
        return BaseToolOutput(tool.run(tool_input=query))
    except Exception as e:  # noqa: BLE001
        return BaseToolOutput({"error": f"{type(e).__name__}: {e}"}, format="json")
