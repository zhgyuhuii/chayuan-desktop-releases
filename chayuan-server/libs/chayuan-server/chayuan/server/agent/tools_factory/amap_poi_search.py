import requests
from chayuan.server.pydantic_v1 import Field
from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)
from chayuan.server.utils import get_tool_config

BASE_URL = "https://restapi.amap.com/v5/place/text"

def amap_poi_search_engine(keywords: str,types: str,config: dict):
    API_KEY = config["api_key"]
    params = {
        "keywords": keywords,
        "types": types,
        "key": API_KEY
    }
    response = requests.get(BASE_URL, params=params)
    if response.status_code == 200:
        return response.json()
    else:
        return {"error": "API request failed"}



@regist_tool(title="高德地图POI搜索")
def amap_poi_search(location: str = Field(description="'实际地名'或者'具体的地址',不能使用简称或者别称"),
                types: str = Field(description="POI类型，比如商场、学校、医院等等")):
    """高德地图兴趣点(POI)搜索 — 中国大陆地名 + 类型搜附近场所。
调用时机:用户问「X 附近的医院/学校/银行/超市/餐厅/加油站」「在 Y 找 Z 类型店」等本地生活问题,**仅限中国大陆**。
输入:location(实际地名/具体地址,**不能用简称**,如 ``北京大学`` 不能用 ``北大``);types(POI 类型,如 商场/学校/医院/银行/酒店/餐厅/加油站/景点)。
输出:附近 POI 列表(名称、地址、距离、电话)。
不要用于:海外地点(高德不覆盖)、路径导航、城市天气(用 amap_weather)。"""
    tool_config = get_tool_config("amap")
    return BaseToolOutput(amap_poi_search_engine(keywords=location,types=types,config=tool_config))
