# LangChain 的 ArxivQueryRun 工具
from chayuan.server.pydantic_v1 import Field

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)

@regist_tool(title="ARXIV论文")
def arxiv(query: str = Field(description="arXiv 论文搜索关键词(英文为主,可含中文)")):
    """搜索 arXiv.org 上的学术论文摘要(覆盖 AI/ML、物理、数学、CS、统计等理工领域的预印本)。
调用时机:用户问「某模型/方法的论文」「某作者最近的论文」「某主题的最新研究」等学术问题。
输入:论文搜索关键词。**强烈建议英文**——arXiv 本身是英文库,英文召回率显著高于中文(可以保留人名/方法名等英文专名)。
输出:论文标题、作者、摘要、arXiv ID、PDF 链接。
不要用于:商业产品资讯、新闻、医学专业文献(用 pubmed_search)、综述查找(用 semantic_scholar)。"""
    from langchain_community.tools.arxiv.tool import ArxivQueryRun

    tool = ArxivQueryRun()
    return BaseToolOutput(tool.run(tool_input=query))
