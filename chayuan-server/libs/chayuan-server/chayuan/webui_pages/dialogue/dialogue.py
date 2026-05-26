import base64
import hashlib
import io
import os
import uuid
from copy import deepcopy
from datetime import datetime
from PIL import Image as PILImage
from typing import Dict, List
from urllib.parse import urlencode

# from audio_recorder_streamlit import audio_recorder
import openai
import streamlit as st
import streamlit_antd_components as sac
from streamlit_chatbox import *
from streamlit_extras.bottom_container import bottom
from streamlit_paste_button import paste_image_button

from chayuan.settings import Settings
from langchain_chayuan.callbacks.agent_callback_handler import AgentStatus
from chayuan.server.knowledge_base.model.kb_document_model import DocumentWithVSId
from chayuan.server.knowledge_base.utils import format_reference
from chayuan.server.utils import MsgType, get_config_models, get_config_platforms, get_default_llm
from chayuan.webui_pages.utils import *


chat_box = ChatBox(assistant_avatar=get_img_base64("logo.png"))

_DIALOGUE_ICON_CSS = """
<style>
.dlg-icon-marker {
    display: none !important;
}
.dlg-section-title {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 6px 0 10px 0;
    color: #0f172a;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.2px;
}
.dlg-section-title::before {
    content: "";
    width: 16px;
    height: 16px;
    display: inline-block;
    flex-shrink: 0;
    background-repeat: no-repeat;
    background-position: center;
    background-size: 16px 16px;
}
.dlg-section-title.tools::before {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='none' stroke='%23475569' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M8.8 4.5 5.1 8.2a2 2 0 0 0 0 2.8l3.9 3.9a2 2 0 0 0 2.8 0l3.7-3.7'/%3E%3Cpath d='m11.2 4.5 4.3 4.3'/%3E%3Ccircle cx='6.2' cy='13.8' r='1.2'/%3E%3C/svg%3E");
}
.dlg-section-title.sessions::before {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='none' stroke='%23475569' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M4.5 5.8h11'/%3E%3Cpath d='M4.5 10h11'/%3E%3Cpath d='M4.5 14.2h7.5'/%3E%3Ccircle cx='13.9' cy='14.2' r='1.3'/%3E%3C/svg%3E");
}
.dlg-section-title.images::before {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='none' stroke='%23475569' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='3.8' y='4.2' width='12.4' height='11.6' rx='2'/%3E%3Ccircle cx='8.1' cy='8.3' r='1.2'/%3E%3Cpath d='m6.2 13.5 2.7-2.7 2.1 2.1 2.8-3'/%3E%3C/svg%3E");
}

/* 对话页动作图标系统：统一为轻量线性图标，避免 emoji / 默认下载图标 / 纯文字混杂。 */
section[data-testid="stMain"] .stButton > button,
section[data-testid="stMain"] .stDownloadButton > button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
}

/* 底部工具条：两侧做成紧凑图标按钮。 */
[data-testid="stBottomBlockContainer"] div[data-testid="column"]:has(.dlg-icon-settings) .stButton > button,
[data-testid="stBottomBlockContainer"] div[data-testid="column"]:has(.dlg-icon-clear-bottom) .stButton > button {
    width: 40px !important;
    min-width: 40px !important;
    height: 40px !important;
    min-height: 40px !important;
    padding: 0 !important;
    border-radius: 10px !important;
}
[data-testid="stBottomBlockContainer"] div[data-testid="column"]:has(.dlg-icon-settings) .stButton > button p,
[data-testid="stBottomBlockContainer"] div[data-testid="column"]:has(.dlg-icon-clear-bottom) .stButton > button p {
    font-size: 0 !important;
    margin: 0 !important;
}
[data-testid="stBottomBlockContainer"] div[data-testid="column"]:has(.dlg-icon-settings) .stButton > button::before,
[data-testid="stBottomBlockContainer"] div[data-testid="column"]:has(.dlg-icon-clear-bottom) .stButton > button::before {
    content: "";
    display: block;
    width: 18px;
    height: 18px;
    background-repeat: no-repeat;
    background-position: center;
    background-size: 18px 18px;
}
[data-testid="stBottomBlockContainer"] div[data-testid="column"]:has(.dlg-icon-settings) .stButton > button::before {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='none' stroke='%23475569' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cline x1='4' y1='5' x2='16' y2='5'/%3E%3Ccircle cx='8' cy='5' r='2' fill='white'/%3E%3Cline x1='4' y1='10' x2='16' y2='10'/%3E%3Ccircle cx='13' cy='10' r='2' fill='white'/%3E%3Cline x1='4' y1='15' x2='16' y2='15'/%3E%3Ccircle cx='10' cy='15' r='2' fill='white'/%3E%3C/svg%3E");
}
[data-testid="stBottomBlockContainer"] div[data-testid="column"]:has(.dlg-icon-clear-bottom) .stButton > button::before {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='none' stroke='%23475569' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M4 6h12'/%3E%3Cpath d='M7 6V4.8c0-.44.36-.8.8-.8h4.4c.44 0 .8.36.8.8V6'/%3E%3Cpath d='M6.5 6l.7 9.1c.04.51.46.9.97.9h3.76c.51 0 .93-.39.97-.9L13.5 6'/%3E%3Cpath d='M8.6 9.2v4.2M11.4 9.2v4.2'/%3E%3C/svg%3E");
}

/* 侧边栏动作：统一图标 + 文字按钮。 */
section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-new) .stButton > button::before,
section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-rename) .stButton > button::before,
section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-delete) .stButton > button::before,
section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-clear-side) .stButton > button::before,
section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-export) .stDownloadButton > button::before {
    content: "";
    display: inline-block;
    width: 16px;
    height: 16px;
    flex-shrink: 0;
    background-repeat: no-repeat;
    background-position: center;
    background-size: 16px 16px;
}
section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-new) .stButton > button::before {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='none' stroke='%23475569' stroke-width='1.8' stroke-linecap='round'%3E%3Cpath d='M10 4v12M4 10h12'/%3E%3C/svg%3E");
}
section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-rename) .stButton > button::before {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='none' stroke='%23475569' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M4 14.8V16h1.2l8.3-8.3-1.2-1.2L4 14.8Z'/%3E%3Cpath d='M11.6 5.4l1.2 1.2M13.1 3.9a1.1 1.1 0 0 1 1.6 0l1.4 1.4a1.1 1.1 0 0 1 0 1.6l-.7.7-3-3 .7-.7Z'/%3E%3C/svg%3E");
}
section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-delete) .stButton > button::before,
section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-clear-side) .stButton > button::before {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='none' stroke='%23b91c1c' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M4 6h12'/%3E%3Cpath d='M7 6V4.8c0-.44.36-.8.8-.8h4.4c.44 0 .8.36.8.8V6'/%3E%3Cpath d='M6.5 6l.7 9.1c.04.51.46.9.97.9h3.76c.51 0 .93-.39.97-.9L13.5 6'/%3E%3Cpath d='M8.6 9.2v4.2M11.4 9.2v4.2'/%3E%3C/svg%3E");
}
section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-export) .stDownloadButton > button::before {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='none' stroke='%23475569' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M10 3.8v8.4'/%3E%3Cpath d='m6.8 9.8 3.2 3.2 3.2-3.2'/%3E%3Cpath d='M4.5 15.6h11'/%3E%3C/svg%3E");
}

/* 侧边栏 tabs：补齐功能图标，保持与动作按钮同一图标语言。 */
section[data-testid="stSidebar"] div[data-baseweb="tab-list"] {
    margin-bottom: 14px;
}
section[data-testid="stSidebar"] div[data-baseweb="tab"] {
    display: inline-flex !important;
    align-items: center;
    gap: 6px;
}
section[data-testid="stSidebar"] div[data-baseweb="tab"]::before {
    content: "";
    width: 14px;
    height: 14px;
    display: inline-block;
    background-repeat: no-repeat;
    background-position: center;
    background-size: 14px 14px;
    opacity: 0.88;
}
section[data-testid="stSidebar"] div[data-baseweb="tab"]:nth-child(1)::before {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='none' stroke='%236b7280' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M8.8 4.5 5.1 8.2a2 2 0 0 0 0 2.8l3.9 3.9a2 2 0 0 0 2.8 0l3.7-3.7'/%3E%3Cpath d='m11.2 4.5 4.3 4.3'/%3E%3Ccircle cx='6.2' cy='13.8' r='1.2'/%3E%3C/svg%3E");
}
section[data-testid="stSidebar"] div[data-baseweb="tab"]:nth-child(2)::before {
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='none' stroke='%236b7280' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M4.5 5.8h11'/%3E%3Cpath d='M4.5 10h11'/%3E%3Cpath d='M4.5 14.2h7.5'/%3E%3Ccircle cx='13.9' cy='14.2' r='1.3'/%3E%3C/svg%3E");
}
section[data-testid="stSidebar"] div[data-baseweb="tab"][aria-selected="true"]::before {
    filter: brightness(0) saturate(100%) invert(36%) sepia(90%) saturate(1085%) hue-rotate(186deg) brightness(95%) contrast(91%);
}

/* 上传图片入口：让 file_uploader 更像一个带图标的扁平按钮。 */
section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-upload-image) [data-testid="stFileUploaderDropzone"] {
    border: 1px dashed #cbd5e1 !important;
    border-radius: 10px !important;
    background: #f8fafc !important;
    padding: 14px 12px !important;
}
section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-upload-image) [data-testid="stFileUploaderDropzone"] button {
    display: inline-flex !important;
    align-items: center;
    justify-content: center;
    gap: 8px;
    border-radius: 8px !important;
}
section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-upload-image) [data-testid="stFileUploaderDropzone"] button::before {
    content: "";
    width: 16px;
    height: 16px;
    display: inline-block;
    background-repeat: no-repeat;
    background-position: center;
    background-size: 16px 16px;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='none' stroke='%23475569' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M10 13.8V5.2'/%3E%3Cpath d='m6.8 8.4 3.2-3.2 3.2 3.2'/%3E%3Cpath d='M4.5 15.8h11'/%3E%3C/svg%3E");
}

section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-delete) .stButton > button,
section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-clear-side) .stButton > button {
    color: #b91c1c !important;
    border-color: #fecaca !important;
    background: #fff5f5 !important;
}
section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-delete) .stButton > button:hover,
section[data-testid="stSidebar"] div[data-testid="column"]:has(.dlg-icon-clear-side) .stButton > button:hover {
    background: #fee2e2 !important;
    border-color: #fca5a5 !important;
    color: #991b1b !important;
}
</style>
"""


def _icon_marker(name: str) -> None:
    st.markdown(
        f'<span class="dlg-icon-marker dlg-icon-{name}"></span>',
        unsafe_allow_html=True,
    )


def _section_title(title: str, kind: str) -> None:
    st.markdown(
        f'<div class="dlg-section-title {kind}">{title}</div>',
        unsafe_allow_html=True,
    )


def _render_chat_error(e: Exception) -> None:
    """把对话调用的异常友好地渲染到页面。

    旧版本直接 ``st.error(e.body)``，只对 OpenAI 的 ``APIError`` 成立；
    当后端 SSE 被中断（``httpx.RemoteProtocolError``）、本地连不上 API
    （``httpx.ConnectError``）等一般异常发生时 ``e.body`` 会抛 AttributeError
    再掩盖原因。这里做一层归一：
      1. 优先读 ``e.body``（OpenAI 完整错误体）；
      2. 其次读 ``e.message`` / ``str(e)``；
      3. 最后用 ``repr(e)`` 兜底。
    同时打一条带类型的提示，便于排查是后端 500、断流还是网络不通。
    """
    body = getattr(e, "body", None)
    if body:
        st.error(body)
        return
    msg = getattr(e, "message", None) or str(e) or repr(e)
    st.error(f"对话失败（{type(e).__name__}）：{msg}")

# context_from_session 会把「顶层」session 整表写入 chat_history[name][context]。
# llm_model 等既在嵌套 context 里（会话真值），又在顶层做控件镜像；顶层若因 dialog fragment 等滞后，
# 会在每次 rerun/save_session 时把旧值盖回 context，表现为「弹窗回显对、对话仍用旧模型」。
_CONTEXT_MERGE_EXCLUDE = [
    "selected_page",
    "prompt",
    "cur_conv_name",
    "upload_image",
    "llm_model",
    "platform",
    "temperature",
    "system_message",
    "dlg_platform",
    "dlg_llm_model",
    "dlg_temperature",
    "dlg_system_message",
]


def save_session(conv_name: str = None):
    """save session state to chat context"""
    chat_box.context_from_session(conv_name, exclude=_CONTEXT_MERGE_EXCLUDE)


def restore_session(conv_name: str = None):
    """restore sesstion state from chat context"""
    chat_box.context_to_session(
        conv_name, exclude=["selected_page", "prompt", "cur_conv_name", "upload_image"]
    )


def rerun():
    """
    save chat context before rerun
    """
    save_session()
    st.rerun()


def get_messages_history(
    history_len: int, content_in_expander: bool = False
) -> List[Dict]:
    """
    返回消息历史。
    content_in_expander控制是否返回expander元素中的内容，一般导出的时候可以选上，传入LLM的history不需要
    """

    def filter(msg):
        content = [
            x for x in msg["elements"] if x._output_method in ["markdown", "text"]
        ]
        if not content_in_expander:
            content = [x for x in content if not x._in_expander]
        content = [x.content for x in content]

        return {
            "role": msg["role"],
            "content": "\n\n".join(content),
        }

    messages = chat_box.filter_history(history_len=history_len, filter=filter)
    if sys_msg := chat_box.context.get("system_message"):
        messages = [{"role": "system", "content": sys_msg}] + messages

    return messages


@st.cache_data
def upload_temp_docs(files, _api: ApiRequest) -> str:
    """
    将文件上传到临时目录，用于文件对话
    返回临时向量库ID
    """
    return _api.upload_temp_docs(files).get("data", {}).get("id")


@st.cache_data
def upload_image_file(file_name: str, content: bytes) -> dict:
    '''upload image for vision model using openai sdk'''
    client = openai.Client(
        base_url=f"{api_address()}/v1",
        api_key=st.session_state.get("access_token") or "NONE",
    )
    return client.files.create(file=(file_name, content), purpose="assistants").to_dict()


def get_image_file_url(upload_file: dict) -> str:
    file_id = upload_file.get("id")
    return f"{api_address(True)}/v1/files/{file_id}/content"


def add_conv(name: str = ""):
    conv_names = chat_box.get_chat_names()
    if not name:
        i = len(conv_names) + 1
        while True:
            name = f"会话{i}"
            if name not in conv_names:
                break
            i += 1
    if name in conv_names:
        sac.alert(
            "创建新会话出错",
            f"该会话名称 “{name}” 已存在",
            color="error",
            closable=True,
        )
    else:
        chat_box.use_chat_name(name)
        st.session_state["cur_conv_name"] = name


def del_conv(name: str = None):
    conv_names = chat_box.get_chat_names()
    name = name or chat_box.cur_chat_name

    if len(conv_names) == 1:
        sac.alert(
            "删除会话出错", f"这是最后一个会话，无法删除", color="error", closable=True
        )
    elif not name or name not in conv_names:
        sac.alert(
            "删除会话出错", f"无效的会话名称：“{name}”", color="error", closable=True
        )
    else:
        chat_box.del_chat_name(name)
        # restore_session()
    st.session_state["cur_conv_name"] = chat_box.cur_chat_name


def clear_conv(name: str = None):
    chat_box.reset_history(name=name or None)


# @st.cache_data
def list_tools(_api: ApiRequest):
    return _api.list_tools() or {}


def dialogue_page(
    api: ApiRequest,
    is_lite: bool = False,
):
    st.markdown(_DIALOGUE_ICON_CSS, unsafe_allow_html=True)
    st.session_state.setdefault("cur_conv_name", chat_box.cur_chat_name)
    st.session_state.setdefault("last_conv_name", chat_box.cur_chat_name)

    # sac on_change callbacks not working since st>=1.34
    if st.session_state.cur_conv_name != st.session_state.last_conv_name:
        save_session(st.session_state.last_conv_name)
        restore_session(st.session_state.cur_conv_name)
        st.session_state.last_conv_name = st.session_state.cur_conv_name

    # st.write(chat_box.cur_chat_name)
    # st.write(st.session_state)
    # st.write(chat_box.context)

    @st.experimental_dialog("模型配置", width="large")
    def llm_model_setting():
        cols = st.columns(3)
        platforms = ["所有"] + list(get_config_platforms())
        platform = cols[0].selectbox("选择模型平台", platforms, key="dlg_platform")
        llm_models = list(
            get_config_models(
                model_type="llm", platform_name=None if platform == "所有" else platform
            )
        )
        llm_models += list(
            get_config_models(
                model_type="image2text", platform_name=None if platform == "所有" else platform
            )
        )
        cols[1].selectbox("选择LLM模型", llm_models, key="dlg_llm_model")
        cols[2].slider("Temperature", 0.0, 1.0, key="dlg_temperature")
        st.text_area("System Message:", key="dlg_system_message")
        if st.button("OK"):
            c = chat_box.context
            lm = st.session_state.get("dlg_llm_model") or get_default_llm()
            c["llm_model"] = str(lm).strip() or get_default_llm()
            c["temperature"] = float(st.session_state.get("dlg_temperature", Settings.model_settings.TEMPERATURE))
            c["system_message"] = st.session_state.get("dlg_system_message", "") or ""
            c["platform"] = st.session_state.get("dlg_platform", "所有")
            st.session_state["llm_model"] = c["llm_model"]
            st.session_state["platform"] = c["platform"]
            st.session_state["temperature"] = c["temperature"]
            st.session_state["system_message"] = c["system_message"]
            rerun()

    @st.experimental_dialog("重命名会话")
    def rename_conversation():
        name = st.text_input("会话名称")
        if st.button("OK"):
            chat_box.change_chat_name(name)
            restore_session()
            st.session_state["cur_conv_name"] = name
            rerun()

    with st.sidebar:
        tab1, tab2 = st.tabs(["工具设置", "会话设置"])

        with tab1:
            _section_title("工具能力", "tools")
            use_agent = st.checkbox(
                "启用Agent", help="请确保选择的模型具备Agent能力", key="use_agent"
            )

            # 选择工具
            tools = list_tools(api)
            tool_names = ["None"] + list(tools)
            use_mcp = False
            if use_agent:
                use_mcp = st.checkbox("使用MCP", key="use_mcp")
                # selected_tools = sac.checkbox(list(tools), format_func=lambda x: tools[x]["title"], label="选择工具",
                # check_all=True, key="selected_tools")
                selected_tools = st.multiselect(
                    "选择工具",
                    list(tools),
                    format_func=lambda x: tools[x]["title"],
                    key="selected_tools",
                )
            else:
                # selected_tool = sac.buttons(list(tools), format_func=lambda x: tools[x]["title"], label="选择工具",
             
                selected_tools = []
            selected_tool_configs = {
                name: tool["config"]
                for name, tool in tools.items()
                if name in selected_tools
            }

            if "None" in selected_tools:
                selected_tools.remove("None")
            # 当不启用Agent时，手动生成工具参数
            # TODO: 需要更精细的控制控件
            tool_input = {}
            if not use_agent and len(selected_tools) == 1:
                with st.expander("工具参数", True):
                    for k, v in tools[selected_tools[0]]["args"].items():
                        if choices := v.get("choices", v.get("enum")):
                            tool_input[k] = st.selectbox(v["title"], choices)
                        else:
                            if v["type"] == "integer":
                                tool_input[k] = st.slider(
                                    v["title"], value=v.get("default")
                                )
                            elif v["type"] == "number":
                                tool_input[k] = st.slider(
                                    v["title"], value=v.get("default"), step=0.1
                                )
                            else:
                                tool_input[k] = st.text_input(
                                    v["title"], v.get("default")
                                )

            # uploaded_file = st.file_uploader("上传附件", accept_multiple_files=False)
            # files_upload = process_files(files=[uploaded_file]) if uploaded_file else None
            files_upload = None

            # 用于图片对话、文生图的图片
            upload_image = None
            def on_upload_file_change():
                if f := st.session_state.get("upload_image"):
                    name = ".".join(f.name.split(".")[:-1]) + ".png"
                    st.session_state["cur_image"] = (name, PILImage.open(f))
                else:
                    st.session_state["cur_image"] = (None, None)
                st.session_state.pop("paste_image", None)

            _section_title("图像输入", "images")
            image_cols = st.columns(2)
            with image_cols[0]:
                _icon_marker("upload-image")
                st.file_uploader(
                    "上传图片",
                    ["bmp", "jpg", "jpeg", "png"],
                    accept_multiple_files=False,
                    key="upload_image",
                    on_change=on_upload_file_change,
                    label_visibility="collapsed",
                )
            with image_cols[1]:
                paste_image = paste_image_button("粘贴图像", key="paste_image")
            cur_image = st.session_state.get("cur_image", (None, None))
            if cur_image[1] is None and paste_image.image_data is not None:
                name = hashlib.md5(paste_image.image_data.tobytes()).hexdigest()+".png"
                cur_image = (name, paste_image.image_data) 
            if cur_image[1] is not None:
                st.image(cur_image[1])
                buffer = io.BytesIO()
                cur_image[1].save(buffer, format="png")
                upload_image = upload_image_file(cur_image[0], buffer.getvalue())

        with tab2:
            _section_title("会话管理", "sessions")
            # 会话
            cols = st.columns(3)
            conv_names = chat_box.get_chat_names()

            def on_conv_change():
                print(conversation_name, st.session_state.cur_conv_name)
                save_session(conversation_name)
                restore_session(st.session_state.cur_conv_name)

            conversation_name = sac.buttons(
                conv_names,
                label="当前会话：",
                key="cur_conv_name",
                # on_change=on_conv_change, # not work
            )
            chat_box.use_chat_name(conversation_name)
            # 必须在 use_chat_name 之后绑定 ctx，否则会话切换后仍读写上一会话的 context
            ctx = chat_box.context
            ctx.setdefault("uid", uuid.uuid4().hex)
            ctx.setdefault("file_chat_id", None)
            ctx.setdefault("llm_model", get_default_llm())
            ctx.setdefault("temperature", Settings.model_settings.TEMPERATURE)
            conversation_id = chat_box.context["uid"]
            with cols[0]:
                _icon_marker("new")
                if st.button("新建", on_click=add_conv, use_container_width=True):
                    ...
            with cols[1]:
                _icon_marker("rename")
                if st.button("重命名", use_container_width=True):
                    rename_conversation()
            with cols[2]:
                _icon_marker("delete")
                if st.button("删除", on_click=del_conv, use_container_width=True):
                    ...

    # Display chat messages from history on app rerun
    chat_box.output_messages()
    chat_input_placeholder = "请输入对话内容，换行请使用Shift+Enter。"

    # def on_feedback(
    #         feedback,
    #         message_id: str = "",
    #         history_index: int = -1,
    # ):

    #     reason = feedback["text"]
    #     score_int = chat_box.set_feedback(feedback=feedback, history_index=history_index)
    #     api.chat_feedback(message_id=message_id,
    #                       score=score_int,
    #                       reason=reason)
    #     st.session_state["need_rerun"] = True

    # feedback_kwargs = {
    #     "feedback_type": "thumbs",
    #     "optional_text_label": "欢迎反馈您打分的理由",
    # }

    # TODO: 这里的内容有点奇怪，从后端导入Settings.model_settings.LLM_MODEL_CONFIG，然后又从前端传到后端。需要优化
    #  传入后端的内容
    llm_model_config = Settings.model_settings.LLM_MODEL_CONFIG
    chat_model_config = {key: {} for key in llm_model_config.keys()}
    for key in llm_model_config:
        if c := llm_model_config[key]:
            model = c.get("model", "").strip() or get_default_llm()
            chat_model_config[key][model] = llm_model_config[key]
    llm_model = (
        str(ctx.get("llm_model") or "").strip()
        or str(st.session_state.get("llm_model") or "").strip()
        or get_default_llm()
    )
    chat_model_config["llm_model"][llm_model] = llm_model_config["llm_model"].get(
        llm_model, {}
    )

    # chat input
    with bottom():
        cols = st.columns([1, 0.2, 15,  1])
        with cols[0]:
            _icon_marker("settings")
            if st.button("模型配置", help="模型配置"):
                widget_keys = ["platform", "llm_model", "temperature", "system_message"]
                chat_box.context_to_session(include=widget_keys)
                c0 = chat_box.context
                st.session_state["dlg_platform"] = st.session_state.get("platform", "所有")
                st.session_state["dlg_llm_model"] = c0.get("llm_model", get_default_llm())
                st.session_state["dlg_temperature"] = float(
                    c0.get("temperature", Settings.model_settings.TEMPERATURE)
                )
                st.session_state["dlg_system_message"] = c0.get("system_message", "") or ""
                llm_model_setting()
        with cols[-1]:
            _icon_marker("clear-bottom")
            if st.button("清空对话", help="清空对话"):
                chat_box.reset_history()
                rerun()
        # with cols[1]:
        #     mic_audio = audio_recorder("", icon_size="2x", key="mic_audio")
        prompt = cols[2].chat_input(chat_input_placeholder, key="prompt")
    if prompt:
        _live = chat_box.context
        llm_model = (
            str(_live.get("llm_model") or "").strip()
            or str(st.session_state.get("llm_model") or "").strip()
            or get_default_llm()
        )
        _live["llm_model"] = llm_model
        st.session_state["llm_model"] = llm_model
        # 为 preprocess / action / postprocess 等链路与当前 UI 模型名对齐（见下方 extra_body 前循环）
        for _mt, _tpl in llm_model_config.items():
            if isinstance(_tpl, dict) and _tpl:
                chat_model_config.setdefault(_mt, {})
                chat_model_config[_mt][llm_model] = deepcopy(_tpl)
        history = get_messages_history(
            chat_model_config["llm_model"]
            .get(llm_model, {})
            .get("history_len", 1)
        )

        is_vision_chat = upload_image and not selected_tools

        # Vision 守卫：带图问答走后端 /chat/chat/completions 的 vision 旁路直连 /v1，
        # 此路径不识别 tool_calls / MCP 协议；前端这里先把相关开关清零，避免后端看到
        # tools 后再绕回 agent 分支产生不一致。
        if is_vision_chat:
            use_mcp = False
            selected_tools = []
            selected_tool_configs = {}
            tool_input = {}

        if is_vision_chat: # multimodal chat
            chat_box.user_say([Image(get_image_file_url(upload_image), width=100), Markdown(prompt)])
        else:
            chat_box.user_say(prompt)
        if files_upload:
            if files_upload["images"]:
                st.markdown(
                    f'<img src="data:image/jpeg;base64,{files_upload["images"][0]}" width="300">',
                    unsafe_allow_html=True,
                )
            elif files_upload["videos"]:
                st.markdown(
                    f'<video width="400" height="300" controls><source src="data:video/mp4;base64,{files_upload["videos"][0]}" type="video/mp4"></video>',
                    unsafe_allow_html=True,
                )
            elif files_upload["audios"]:
                st.markdown(
                    f'<audio controls><source src="data:audio/wav;base64,{files_upload["audios"][0]}" type="audio/wav"></audio>',
                    unsafe_allow_html=True,
                )

        chat_box.ai_say("正在思考...")
        text = ""
        started = False

        client = openai.Client(
            base_url=f"{api_address()}/chat",
            api_key=st.session_state.get("access_token") or "NONE",
            timeout=100000,
        )
        if is_vision_chat: # multimodal chat
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": get_image_file_url(upload_image)}}
            ]
            messages = [{"role": "user", "content": content}]
        else:
            messages = history + [{"role": "user", "content": prompt}]
        tools = list(selected_tool_configs)
        if len(selected_tools) == 1:
            tool_choice = selected_tools[0]
        else:
            tool_choice = None
        # 如果 tool_input 中有空的字段，设为用户输入
        for k in tool_input:
            if tool_input[k] in [None, ""]:
                tool_input[k] = prompt

        extra_body = dict(
            metadata=files_upload,
            chat_model_config=chat_model_config,
            conversation_id=conversation_id,
            tool_input=tool_input,
            upload_image=upload_image,
            use_mcp=use_mcp,
            # 供服务端在 OpenAI 请求体 model 字段异常时仍能解析用户所选模型
            _selected_llm_for_chain=llm_model,
        )
        stream = not is_vision_chat
        params = dict(
            messages=messages,
            model=llm_model,
            stream=stream, # TODO：xinference qwen-vl-chat 流式输出会出错，后续看更新
            extra_body=extra_body,
        )
        if tools:
            params["tools"] = tools
        if tool_choice:
            params["tool_choice"] = tool_choice
        if Settings.model_settings.MAX_TOKENS:
            params["max_tokens"] = Settings.model_settings.MAX_TOKENS

        if stream:
            try:
                for d in client.chat.completions.create(**params):
                    # import rich
                    # rich.print(d)
                    # 走 ChatGraph dispatcher 或对接原生 OpenAI 兼容端点时，chunk 可能
                    # 不带我们扩展的 message_id / status / message_type 字段；
                    # 用 getattr 兜底，避免 AttributeError 打断整条流。
                    message_id = getattr(d, "message_id", None)
                    status = getattr(d, "status", None)
                    metadata = {
                        "message_id": message_id,
                    }

                    # clear initial message
                    if not started:
                        chat_box.update_msg("", streaming=False)
                        started = True

                    if status == AgentStatus.error:
                        st.error(d.choices[0].delta.content)
                    elif status == AgentStatus.llm_start:
                        chat_box.insert_msg("正在解读工具输出结果...")
                        text = d.choices[0].delta.content or ""
                    elif status == AgentStatus.llm_new_token:
                        text += d.choices[0].delta.content or ""
                        chat_box.update_msg(
                            text.replace("\n", "\n\n"), streaming=True, metadata=metadata
                        )
                    elif status == AgentStatus.llm_end:
                        text += d.choices[0].delta.content or ""
                        chat_box.update_msg(
                            text.replace("\n", "\n\n"), streaming=False, metadata=metadata
                        )
                    # tool 的输出与 llm 输出重复了
                    elif status == AgentStatus.tool_start:
                        formatted_data = {
                            "Function": d.choices[0].delta.tool_calls[0].function.name,
                            "function_input": d.choices[0].delta.tool_calls[0].function.arguments,
                        }
                        formatted_json = json.dumps(formatted_data, indent=2, ensure_ascii=False)
                        text = """\n```{}\n```\n""".format(formatted_json)
                        chat_box.insert_msg( # TODO: insert text directly not shown
                            Markdown(text, title="Function call", in_expander=True, expanded=True, state="running"))
                    elif status == AgentStatus.tool_end:
                        tool_output = d.choices[0].delta.tool_calls[0].tool_output
                        if getattr(d, "message_type", MsgType.TEXT) == MsgType.IMAGE:
                            for url in json.loads(tool_output).get("images", []):
                                # 判断是否携带域名
                                if not url.startswith("http"):
                                    url = f"{api.base_url}/media/{url}"
                                # md语法不支持，所以pos 跳过
                                chat_box.insert_msg(Image(url), pos=-2)
                            chat_box.update_msg(text, streaming=False, expanded=True, state="complete")
                        else:
                            text += """\n```\nObservation:\n{}\n```\n""".format(tool_output)
                            chat_box.update_msg(text, streaming=False, expanded=False, state="complete")
                    elif status == AgentStatus.agent_finish:
                        text = d.choices[0].delta.content or ""
                        chat_box.update_msg(text.replace("\n", "\n\n"))
                    elif status is None:  # not agent chat
                        if getattr(d, "is_ref", False):
                            context = str(d.tool_output)
                            if isinstance(d.tool_output, dict):
                                docs = d.tool_output.get("docs", [])
                                source_documents = format_reference(
                                    kb_name=d.tool_output.get("knowledge_base"),
                                    docs=docs,
                                    api_base_url=api_address(is_public=True),
                                    access_token=st.session_state.get("access_token") or "",
                                )
                                context = "\n".join(source_documents)

                            chat_box.insert_msg(
                                Markdown(
                                    context,
                                    in_expander=True,
                                    state="complete",
                                    title="参考资料",
                                )
                            )
                            chat_box.insert_msg("")
                        elif getattr(d, "tool_call", None) == "text2images":  # TODO：特定工具特别处理，需要更通用的处理方式
                            for img in d.tool_output.get("images", []):
                                chat_box.insert_msg(Image(f"{api.base_url}/media/{img}"), pos=-2)
                        else:
                            text += d.choices[0].delta.content or ""
                            chat_box.update_msg(
                                text.replace("\n", "\n\n"), streaming=True, metadata=metadata
                            )
                    chat_box.update_msg(text, streaming=False, metadata=metadata)
            except Exception as e:
                _render_chat_error(e)
        else:
            try:
                d =client.chat.completions.create(**params)
                chat_box.update_msg(d.choices[0].message.content or "", streaming=False)
            except Exception as e:
                _render_chat_error(e)

        # if os.path.exists("tmp/image.jpg"):
        #     with open("tmp/image.jpg", "rb") as image_file:
        #         encoded_string = base64.b64encode(image_file.read()).decode()
        #         img_tag = (
        #             f'<img src="data:image/jpeg;base64,{encoded_string}" width="300">'
        #         )
        #         st.markdown(img_tag, unsafe_allow_html=True)
            # os.remove("tmp/image.jpg")
        # chat_box.show_feedback(**feedback_kwargs,
        #                        key=message_id,
        #                        on_submit=on_feedback,
        #                        kwargs={"message_id": message_id, "history_index": len(chat_box.history) - 1})

        # elif dialogue_mode == "文件对话":
        #     if st.session_state["file_chat_id"] is None:
        #         st.error("请先上传文件再进行对话")
        #         st.stop()
        #     chat_box.ai_say([
        #         f"正在查询文件 `{st.session_state['file_chat_id']}` ...",
        #         Markdown("...", in_expander=True, title="文件匹配结果", state="complete"),
        #     ])
        #     text = ""
        #     for d in api.file_chat(prompt,
        #                            knowledge_id=st.session_state["file_chat_id"],
        #                            top_k=kb_top_k,
        #                            score_threshold=score_threshold,
        #                            history=history,
        #                            model=llm_model,
        #                            prompt_name=prompt_template_name,
        #                            temperature=temperature):
        #         if error_msg := check_error_msg(d):
        #             st.error(error_msg)
        #         elif chunk := d.get("answer"):
        #             text += chunk
        #             chat_box.update_msg(text, element_index=0)
        #     chat_box.update_msg(text, element_index=0, streaming=False)
        #     chat_box.update_msg("\n\n".join(d.get("docs", [])), element_index=1, streaming=False)

    now = datetime.now()
    with tab2:
        cols = st.columns(2)
        export_btn = cols[0]
        with cols[1]:
            _icon_marker("clear-side")
            if st.button(
                "清空对话",
                use_container_width=True,
            ):
                chat_box.reset_history()
                rerun()

    with export_btn:
        _icon_marker("export")
        st.download_button(
            "导出记录",
            "".join(chat_box.export2md()),
            file_name=f"{now:%Y-%m-%d %H.%M}_对话记录.md",
            mime="text/markdown",
            use_container_width=True,
        )

    # st.write(chat_box.history)
