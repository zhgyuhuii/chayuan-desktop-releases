# LangChain 的 WikipediaQueryRun 工具
from langchain_community.tools import WikipediaQueryRun
from langchain_community.utilities import WikipediaAPIWrapper
from chayuan.server.pydantic_v1 import Field



from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)

@regist_tool(title="维基百科搜索")
def wikipedia_search(query: str = Field(description="搜索关键词(中英文均可,英文条目更多)")):
    """搜索维基百科条目摘要(默认中文版,英文条目更全可用英文 query 兜底)。
调用时机:用户问「某概念/人物/地名/历史事件/机构是什么」等通识/百科类问题。
输入:中英文条目关键词。
输出:条目摘要(若干段)+ 百科链接。
不要用于:实时动态(维基条目可能滞后几天到几周)、专业研究文献、私域内部知识。"""
    api_wrapper = WikipediaAPIWrapper(lang="zh")
    tool = WikipediaQueryRun(api_wrapper=api_wrapper)
    return BaseToolOutput(tool.run(tool_input=query))
                          
                          
                          
