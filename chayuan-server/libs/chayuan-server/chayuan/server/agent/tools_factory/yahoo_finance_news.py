"""Yahoo Finance 财经新闻。

按股票代码（如 AAPL / TSLA / 00700.HK）拉取近期新闻摘要，免费无需 Key。
"""
from chayuan.server.pydantic_v1 import Field

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)


@regist_tool(title="Yahoo 财经新闻")
def yahoo_finance_news(
    query: str = Field(description="股票代码或公司简称，如 AAPL / TSLA / 00700.HK"),
):
    """Yahoo Finance 上指定股票/公司的财经新闻。
调用时机:用户问「X 公司最近的新闻」「AAPL/TSLA/00700.HK 的财报/动态」等股票相关新闻。
输入:股票代码或公司简称。美股直接代码(AAPL/TSLA);港股带 .HK 后缀如 0700.HK;A 股 .SS(上交所)/.SZ(深交所)如 600519.SS;
输出:财经新闻列表(标题、链接、摘要、发布时间)。
不要用于:实时股价(本工具只返新闻不返价格)、技术指标分析、非上市公司、加密货币(用 search_internet)。"""
    try:
        from langchain_community.tools.yahoo_finance_news import (
            YahooFinanceNewsTool,
        )
    except ImportError:
        return BaseToolOutput({
            "error": "yfinance 未安装，请 `pip install yfinance`",
        }, format="json")

    try:
        tool = YahooFinanceNewsTool()
        return BaseToolOutput(tool.run(tool_input=query))
    except Exception as e:  # noqa: BLE001
        return BaseToolOutput({"error": f"{type(e).__name__}: {e}"}, format="json")
