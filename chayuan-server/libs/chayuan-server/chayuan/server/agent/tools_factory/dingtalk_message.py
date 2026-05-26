"""钉钉群机器人消息推送。

使用自定义机器人 Webhook + 可选「加签」方式。钉钉 security 模式有 3 种：
- 仅关键词（无 secret）
- 加签（HMAC-SHA256）
- IP 白名单（服务端无需处理）

这里支持 ①/②；③ 由钉钉侧控制，代码无变化。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse
from typing import Optional

import httpx

from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import get_tool_config

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)


def _sign(secret: str) -> tuple[str, str]:
    ts = str(round(time.time() * 1000))
    string_to_sign = f"{ts}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    return ts, sign


@regist_tool(title="钉钉消息推送")
def dingtalk_message(
    text: str = Field(description="消息内容；默认 text 类型，markdown 需在 yaml 切 msgtype"),
    mention_mobile: Optional[str] = Field(
        None, description="逗号分隔手机号；传 @all 艾特全体",
    ),
):
    """钉钉自定义机器人 webhook 推送消息(可选 HMAC 签名)。
调用时机:用户明确要求「发到钉钉」「通知钉钉群」时。
输入:text(消息内容);mention_mobile(逗号分隔手机号或 ``@all`` 艾特全员)。
输出:发送结果。
不要用于:用户没说「钉钉」的问题、私聊、其它平台。"""
    cfg = get_tool_config("dingtalk_message") or {}
    webhook = (cfg.get("webhook") or "").strip()
    secret = (cfg.get("secret") or "").strip()
    if not webhook:
        return BaseToolOutput({"error": "未配置钉钉机器人 webhook"}, format="json")

    url = webhook
    if secret:
        ts, sign = _sign(secret)
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}timestamp={ts}&sign={sign}"

    msgtype = (cfg.get("msgtype") or "text").strip().lower()
    at_all = mention_mobile == "@all"
    at_mobiles = []
    if mention_mobile and mention_mobile != "@all":
        at_mobiles = [m.strip() for m in mention_mobile.split(",") if m.strip()]

    if msgtype == "markdown":
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": cfg.get("markdown_title") or "chayuan", "text": text},
            "at": {"atMobiles": at_mobiles, "isAtAll": at_all},
        }
    else:
        payload = {
            "msgtype": "text",
            "text": {"content": text},
            "at": {"atMobiles": at_mobiles, "isAtAll": at_all},
        }

    try:
        with httpx.Client(timeout=15) as cli:
            resp = cli.post(url, json=payload)
            data = resp.json() if resp.content else {}
    except Exception as e:  # noqa: BLE001
        return BaseToolOutput({"error": f"{type(e).__name__}: {e}"}, format="json")
    return BaseToolOutput({
        "status": resp.status_code,
        "dd_code": data.get("errcode"), "msg": data.get("errmsg"),
    }, format="json")
