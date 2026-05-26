"""飞书（Lark）群机器人消息推送。

仅使用「自定义机器人 Webhook」方式，无需飞书 SDK、无需 OAuth。
适合场景：Agent 把「生成的总结 / 告警 / 日报」推送到运维/研发群。

yaml 示例：

    lark_message:
      use: true
      webhook: https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx
      default_mention: ""  # 可选 all 或 @user_id
"""
from typing import Optional

import httpx

from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import get_tool_config

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)


@regist_tool(title="飞书消息推送")
def lark_message(
    text: str = Field(description="要发送到飞书群的消息文本（支持 Markdown）"),
    mention: Optional[str] = Field(
        None, description="可选：all 艾特全体 / @user_id / 不传则不艾特",
    ),
):
    """飞书(Lark)群机器人 webhook 推送消息。
调用时机:用户**明确要求**「发到飞书」「通知飞书群」且配置了 webhook 时,适合通知/告警/工作流回执。
输入:text(消息文本,支持 markdown)。
输出:发送结果(成功/失败 + API 返回)。
不要用于:用户没说「飞书」的问题、私聊(本工具只发群机器人 webhook)、其它消息平台(用 wechat_work_message / dingtalk_message)。"""
    cfg = get_tool_config("lark_message") or {}
    webhook = (cfg.get("webhook") or "").strip()
    if not webhook:
        return BaseToolOutput({
            "error": "未配置飞书机器人 webhook，请先在群聊 -> 群设置 -> 群机器人 添加自定义机器人",
        }, format="json")

    mention = mention or cfg.get("default_mention") or ""
    body_text = text
    if mention == "all":
        body_text = f"<at user_id=\"all\"></at> {text}"
    elif mention:
        body_text = f"<at user_id=\"{mention}\"></at> {text}"

    payload = {"msg_type": "text", "content": {"text": body_text}}
    try:
        with httpx.Client(timeout=15) as cli:
            resp = cli.post(webhook, json=payload)
            data = resp.json() if resp.content else {}
    except Exception as e:  # noqa: BLE001
        return BaseToolOutput({"error": f"{type(e).__name__}: {e}"}, format="json")
    return BaseToolOutput({
        "status": resp.status_code, "feishu_code": data.get("code"),
        "msg": data.get("msg"),
    }, format="json")
