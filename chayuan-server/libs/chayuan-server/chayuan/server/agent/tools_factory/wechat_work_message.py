"""企业微信群机器人消息推送。

基于企业微信「群机器人 Webhook」，无 SDK 依赖。
"""
from typing import Optional

import httpx

from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import get_tool_config

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)


@regist_tool(title="企业微信消息推送")
def wechat_work_message(
    text: str = Field(description="消息内容（支持 markdown，需在 yaml 里把 msgtype 切到 markdown）"),
    mentioned_mobile: Optional[str] = Field(
        None, description="逗号分隔手机号，艾特指定人；传 @all 艾特全体",
    ),
):
    """企业微信群机器人 webhook 推送消息。
调用时机:用户明确要求「发到企微/企业微信」「通知企微群」时。
输入:text(默认 text 类型,markdown 需在 yaml 切 msgtype)。
输出:发送结果。
不要用于:用户没说「企微」的问题、个人微信(企微和个微完全是两套系统)、私聊、其它平台。"""
    cfg = get_tool_config("wechat_work_message") or {}
    webhook = (cfg.get("webhook") or "").strip()
    if not webhook:
        return BaseToolOutput({
            "error": "未配置企业微信 webhook",
        }, format="json")

    msgtype = (cfg.get("msgtype") or "text").strip().lower()
    if msgtype == "markdown":
        payload = {"msgtype": "markdown", "markdown": {"content": text}}
    else:
        content = {"content": text}
        if mentioned_mobile:
            content["mentioned_mobile_list"] = [
                m.strip() for m in mentioned_mobile.split(",") if m.strip()
            ]
        payload = {"msgtype": "text", "text": content}
    try:
        with httpx.Client(timeout=15) as cli:
            resp = cli.post(webhook, json=payload)
            data = resp.json() if resp.content else {}
    except Exception as e:  # noqa: BLE001
        return BaseToolOutput({"error": f"{type(e).__name__}: {e}"}, format="json")
    return BaseToolOutput({
        "status": resp.status_code,
        "wx_code": data.get("errcode"), "msg": data.get("errmsg"),
    }, format="json")
