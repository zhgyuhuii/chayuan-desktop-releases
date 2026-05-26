import base64
from datetime import datetime
import os
import uuid
from typing import List, Literal

import openai
from PIL import Image

from chayuan.settings import Settings
from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import MsgType, get_tool_config, get_model_info

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)

@regist_tool(title="""
#文本生成图片工具
##描述
则根据用户的描述生成图片。
##请求参数
参数名	类型	必填	描述 
prompt	String	是	所需图像的文本描述
size	String	否	图片尺寸，可选值：1024x1024,768x1344,864x1152,1344x768,1152x864,1440x720,720x1440，默认是1024x1024。 
""", return_direct=True)
def text2images(
    prompt: str = Field(description="用户的描述"),
    n: int = Field(1, description="需生成图片的数量"),
    size: Literal["1024x1024", "768x1344", "864x1152", "1344x768", "1152x864", "1440x720", "720x1440"] = Field(description="图片尺寸"),
):
    """文生图 — 调用图像生成模型(DALL-E / Stable Diffusion / Flux 等)生成图片。
调用时机:用户**明确要求**「画一张 X」「生成 Y 的图片」「做一张 Z 的封面/插画」时。
输入:prompt(中英文描述,英文表达对多数模型更稳;描述越具体效果越好);n(生成数量,默认 1);size(``1024x1024`` 正方 / ``768x1344`` 竖 / ``1344x768`` 横 / 其它支持的比例)。
输出:图片 URL/本地路径列表(用户可点开查看)。
不要用于:用户只是描述场景但没说「画/生成」的问题、图像识别/理解(走 vision 模型)、图片编辑/抠图。"""

    tool_config = get_tool_config("text2images")
    model_config = get_model_info(tool_config["model"])
    assert model_config, "请正确配置文生图模型"

    client = openai.Client(
        base_url=model_config["api_base_url"],
        api_key=model_config["api_key"],
        timeout=600,
    )
    resp = client.images.generate(
        prompt=prompt,
        n=n,
        size=size,
        response_format="b64_json",
        model=model_config["model_name"],
    )
    images = []
    for x in resp.data:
        if x.b64_json is not None:
            uid = uuid.uuid4().hex
            today = datetime.now().strftime("%Y-%m-%d")
            path = os.path.join(Settings.basic_settings.MEDIA_PATH, "image", today)
            os.makedirs(path, exist_ok=True)
            filename = f"image/{today}/{uid}.png"
            with open(os.path.join(Settings.basic_settings.MEDIA_PATH, filename), "wb") as fp:
                fp.write(base64.b64decode(x.b64_json))
            images.append(filename)
        else:
            images.append(x.url)
    return BaseToolOutput(
        {"message_type": MsgType.IMAGE, "images": images}, format="json"
    )


if __name__ == "__main__":
    import sys
    from io import BytesIO
    from pathlib import Path

    from matplotlib import pyplot as plt

    sys.path.append(str(Path(__file__).parent.parent.parent.parent))

    prompt = "draw a house with trees and river"
    prompt = "画一个带树、草、河流的山中小屋"
    params = text2images.args_schema.parse_obj({"prompt": prompt}).dict()
    print(params)
    image = text2images.invoke(params)[0]
    buffer = BytesIO(base64.b64decode(image))
    image = Image.open(buffer)
    plt.imshow(image)
    plt.show()
