from chayuan.server.pydantic_v1 import Field

from .tools_registry import regist_tool


from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)

@regist_tool(title="数学计算器")
def calculate(text: str = Field(description="数学表达式,如 ``1+2*3`` / ``sin(0.5)+sqrt(2)`` / ``2**10``,由 numexpr 求值")) -> float:
    """精确数学计算(走 numexpr 数值求值)。
调用时机:用户问算术、三角函数、幂运算、单位/进制换算、简单代数化简等可量化数学问题——比 LLM 心算可靠数倍。
输入:numexpr 可识别的数学表达式,如 ``1+2*3`` / ``sin(0.5)+sqrt(2)`` / ``2**10`` / ``log(100, 10)`` / ``(1+0.05)**30``。LLM 应把用户的自然语言数学问题先翻译成表达式再调用。
输出:数值结果。
不要用于:符号微积分/方程求解(用 wolfram)、定理证明、统计分析(写 python_repl)。"""
    import numexpr

    try:
        ret = str(numexpr.evaluate(text))
    except Exception as e:
        ret = f"计算失败: {e}"

    return BaseToolOutput(ret)
