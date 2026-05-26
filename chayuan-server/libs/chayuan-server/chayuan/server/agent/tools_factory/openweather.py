"""OpenWeatherMap 天气查询（海外版）。

对应国内「心知天气」的海外替代：免费 tier 每天 ~1000 次调用，全球覆盖。
langchain_community.utilities.openweathermap 已经封装好一键查询。
"""
from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import get_tool_config

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)


@regist_tool(title="OpenWeatherMap 天气")
def openweather(
    location: str = Field(description="城市名称（建议英文 / 拼音，如 Beijing, Shanghai, New York）"),
):
    """OpenWeatherMap 全球城市天气 — 海外城市优先。
调用时机:用户问海外城市天气(伦敦/纽约/东京等)。中国大陆城市建议优先 amap_weather。
输入:城市名(英文/拼音更稳,如 ``London`` / ``New York`` / ``Tokyo`` / ``Beijing``;支持 ``City,CountryCode`` 形式如 ``Paris,FR``)。
输出:实时天气 + 几日预报(温度/天气/风力/湿度,默认摄氏)。
不要用于:中国大陆城市(amap_weather 更准)、地名搜索、非天气问题。"""
    cfg = get_tool_config("openweather") or {}
    api_key = (cfg.get("api_key") or "").strip()
    if not api_key:
        return BaseToolOutput({
            "error": "未配置 OpenWeatherMap API Key，请到面板工具页或 openweathermap.org 申请",
        }, format="json")
    try:
        from langchain_community.utilities.openweathermap import (
            OpenWeatherMapAPIWrapper,
        )
    except ImportError:
        return BaseToolOutput({
            "error": "pyowm 未安装，请 `pip install pyowm`",
        }, format="json")

    wrapper = OpenWeatherMapAPIWrapper(openweathermap_api_key=api_key)
    try:
        result = wrapper.run(location)
    except Exception as e:  # noqa: BLE001
        return BaseToolOutput({"error": f"{type(e).__name__}: {e}"}, format="json")
    return BaseToolOutput({"location": location, "report": result}, format="json")
