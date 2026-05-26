from .arxiv import arxiv
from .calculate import calculate
from .search_internet import search_internet
from .search_local_knowledgebase import search_local_knowledgebase
from .search_youtube import search_youtube
from .shell import shell
from .text2image import text2images
from .text2sql import text2sql
from .weather_check import weather_check
from .wolfram import wolfram
from .amap_poi_search import amap_poi_search
from .amap_weather import amap_weather
from .wikipedia_search import wikipedia_search
from .text2promql import text2promql
from .url_reader import url_reader
from .pubmed_search import pubmed_search
from .http_request import http_request
# T2 批量新增
from .openweather import openweather
from .yahoo_finance_news import yahoo_finance_news
from .semantic_scholar import semantic_scholar
from .stackexchange import stackexchange
from .news_api import news_api
from .python_repl import python_repl
from .github_tool import github_tool
from .gitlab_tool import gitlab_tool
from .lark_message import lark_message
from .wechat_work_message import wechat_work_message
from .dingtalk_message import dingtalk_message
from .openapi_call import openapi_call
from .notion_search import notion_search
from .confluence_search import confluence_search

# 自定义工具运行时：启动时从 custom_tools.yaml 读规格并动态合成注册；
# 从此自定义工具与上面的手写 @regist_tool 在 _TOOLS_REGISTRY 里平等对待。
from . import custom_tools_runtime  # noqa: F401
# WebSocket 端点同样动态合成为 StructuredTool，来源是 websocket_endpoints.yaml。
from . import websocket_tools_runtime  # noqa: F401
