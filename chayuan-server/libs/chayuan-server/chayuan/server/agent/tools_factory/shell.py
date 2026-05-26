# LangChain 的 Shell 工具
from langchain_community.tools import ShellTool

from chayuan.server.pydantic_v1 import Field

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)

@regist_tool(title="系统命令")
def shell(query: str = Field(description="要执行的 shell 命令(谨慎,默认禁用,启用前请确认 yaml 白名单)")):
    """⚠ 高危 — 在服务器执行 shell 命令并返回 stdout。**默认禁用(use=false)**。
调用时机:仅在用户明确要求「运行命令」「执行 X」「看 ls / df / ps 输出」且管理员开了 yaml 白名单 + 沙箱时。
输入:shell 命令字符串。
输出:stdout(可能混有 stderr)。
不要用于:LLM 自己「试试看」、非用户主动要求的操作、生产环境(除非充分沙箱)、替代标准工具(查文件用 url_reader,计算用 calculate)。"""
    tool = ShellTool()
    return BaseToolOutput(tool.run(tool_input=query))
