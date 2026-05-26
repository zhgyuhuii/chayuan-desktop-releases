from chayuan.server.pydantic_v1 import Field

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)

@regist_tool(title="油管视频")
def search_youtube(query: str = Field(description="YouTube 视频搜索关键词(中英文均可)")):
    """搜索 YouTube 视频(返回若干视频链接)。
调用时机:用户明确说「找几个关于 X 的视频」「有没有 X 的教程视频」「X 的演讲」且不限 YouTube 之外的平台时。
输入:中英文视频搜索关键词。
输出:YouTube 视频链接列表(不返回视频内容/字幕)。
不要用于:Bilibili/抖音/优酷 等其它平台、视频内容总结(本工具只返链接)、需要外网才能访问的环境若网络不通会失败。"""
    from langchain_community.tools import YouTubeSearchTool

    tool = YouTubeSearchTool()
    return BaseToolOutput(tool.run(tool_input=query))
