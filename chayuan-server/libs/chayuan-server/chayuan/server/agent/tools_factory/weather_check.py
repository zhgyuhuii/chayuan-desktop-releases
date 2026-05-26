"""
简单的单参数输入工具实现，用于查询现在天气的情况
"""
import requests

from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import get_tool_config

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)

@regist_tool(title="天气查询")
def weather_check(
    city: str = Field(description="城市名(精确到市/县,如 \"厦门\" / \"北京\" / \"杭州\")"),
):
    """心知天气 — 中国大陆城市的实时天气(轻量,只返当前天气)。
调用时机:用户只问「X 现在天气」、不需要预报、对延迟敏感时,作为 amap_weather 的降级备选。
输入:中国大陆城市名(如 ``厦门`` / ``北京``)。
输出:当前温度 + 天气描述。
不要用于:多日预报(用 amap_weather)、海外城市(用 openweather)。amap_weather 信息更全,优先它。"""

    tool_config = get_tool_config("weather_check")
    api_key = tool_config.get("api_key")
    url = f"https://api.seniverse.com/v3/weather/now.json?key={api_key}&location={city}&language=zh-Hans&unit=c"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        weather = {
            "temperature": data["results"][0]["now"]["temperature"],
            "description": data["results"][0]["now"]["text"],
        }
        return BaseToolOutput(weather)
    else:
        raise Exception(f"获取天气失败: HTTP {response.status_code}")
