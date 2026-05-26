import requests
from chayuan.server.pydantic_v1 import Field
from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)
from chayuan.server.utils import get_tool_config

BASE_DISTRICT_URL = "https://restapi.amap.com/v3/config/district"
BASE_WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"

def get_adcode(city: str, config: dict) -> str:
    """获取城市行政编码 adcode(高德 API 用)。"""
    API_KEY = config["api_key"]
    params = {
        "keywords": city,
        "subdistrict": 0, 
        "extensions": "base",
        "key": API_KEY
    }
    response = requests.get(BASE_DISTRICT_URL, params=params)
    if response.status_code == 200:
        data = response.json()
        return data["districts"][0]["adcode"]
    else:
        return None

def get_weather(adcode: str, config: dict) -> dict:
    """根据 adcode 拉取高德天气详情。"""
    API_KEY = config["api_key"]
    params = {
        "city": adcode,
        "extensions": "all",
        "key": API_KEY
    }
    response = requests.get(BASE_WEATHER_URL, params=params)
    if response.status_code == 200:
        return response.json()
    else:
        return {"error": "API request failed"}

@regist_tool(title="高德地图天气查询")
def amap_weather(city: str = Field(description="城市名")):
    """高德地图天气查询 — 中国大陆城市实时 + 几日预报。本土数据源,精度优于 OpenWeather。
调用时机:用户问中国大陆城市的「今天天气」「未来几天预报」时**优先调用此工具**。
输入:中国大陆城市名(精确到市/区/县,如 ``北京`` / ``厦门`` / ``深圳市南山区``)。
输出:实时天气 + 未来 3-4 天预报(温度/天气状况/风力/湿度)。
不要用于:海外城市(用 openweather)、地名搜索(用 amap_poi_search)、非天气问题。"""
    tool_config = get_tool_config("amap")
    adcode = get_adcode(city, tool_config)
    if adcode:
        weather_data = get_weather(adcode, tool_config)
        return BaseToolOutput(weather_data)
    else:
        return BaseToolOutput({"error": "无法获取城市编码"})

