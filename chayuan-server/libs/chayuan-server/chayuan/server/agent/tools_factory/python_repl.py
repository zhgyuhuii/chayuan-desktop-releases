"""Python REPL（危险）。

允许 LLM 在部署机器上执行任意 Python 代码。可以干很多事：
- 复杂数据分析 / pandas DataFrame 操作
- matplotlib / seaborn 画图（返回保存路径）
- 调用第三方 SDK 做一次性脚本

但**极度危险**，等同 shell：模型幻觉就能读你的文件、连你的数据库、发公网请求。
生产环境**请配合容器 / rootless / 断网沙箱**，或干脆关闭。
"""
from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import get_tool_config

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)


@regist_tool(title="Python REPL（危险）")
def python_repl(
    code: str = Field(description="要执行的 Python 代码；标准输出会返回给 LLM"),
):
    """⚠ 极高危 — 执行任意 Python 代码并返回 stdout。**默认禁用**。
调用时机:仅在沙箱环境 + 用户**明确**要求「运行这段 python」「用 python 算 X」「画个图(matplotlib)」时。
输入:Python 代码字符串(标准输出会回给 LLM)。
输出:stdout。
不要用于:任何无沙箱的生产环境、LLM 自己探索性写代码、网络/文件 IO 危险操作、简单计算(优先 calculate 比这快且安全)。"""
    cfg = get_tool_config("python_repl") or {}
    if not bool(cfg.get("confirm_dangerous", False)):
        return BaseToolOutput({
            "error": "python_repl 默认拒绝执行；请在 yaml 里显式把 confirm_dangerous 设为 true "
                     "并确认部署环境已做沙箱 / 权限隔离后再开启。",
        }, format="json")
    try:
        from langchain_experimental.tools.python.tool import PythonREPLTool
    except ImportError:
        return BaseToolOutput({
            "error": "langchain-experimental 未安装，请 `pip install langchain-experimental`",
        }, format="json")

    try:
        tool = PythonREPLTool()
        return BaseToolOutput(tool.run(tool_input=code))
    except Exception as e:  # noqa: BLE001
        return BaseToolOutput({"error": f"{type(e).__name__}: {e}"}, format="json")
