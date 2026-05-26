# Langchain 自带的 Wolfram Alpha API 封装

from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import get_tool_config

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)

@regist_tool(title="Wolfram Alpha 计算")
def wolfram(query: str = Field(description="WolframAlpha 计算公式或自然语言查询(英文表达更稳),如 ``solve x^2-4=0`` / ``integrate sin x dx``")):
    """调用 WolframAlpha 解符号数学和复杂科学计算。
调用时机:calculate 不够用——需要符号微积分、方程求解、单位换算、物理化学常数、几何、自然语言数学时。
输入:WolframAlpha 自然语言查询(英文表达更稳),例如 ``solve x^2-4=0`` / ``integrate sin(x) from 0 to pi`` / ``derivative of x^3 at x=2`` / ``speed of light in m/s`` / ``boiling point of water in Kelvin``。
输出:WolframAlpha 计算结果(可能含步骤、解析解、图)。
不要用于:简单四则运算(用 calculate 更快)、网页搜索、非数学/科学问题。"""

    from langchain_community.utilities.wolfram_alpha import WolframAlphaAPIWrapper

    wolfram = WolframAlphaAPIWrapper(
        wolfram_alpha_appid=get_tool_config("wolfram").get("appid")
    )
    ans = wolfram.run(query)
    return BaseToolOutput(ans)
