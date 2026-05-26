"""察元 AI 助手 Streamlit 入口。

v3 布局（2026-04）—— App-Shell 全宽顶栏：
- **顶栏**：深蓝主色条横跨整页（覆盖 sidebar 顶端），左侧 logo + 产品名 + 版本号；
  右上角是单个用户头像下拉菜单（个人信息 / 修改密码 / 退出登录），符合 GitHub /
  Claude / ChatGPT 等主流产品的头部交互惯例。
- **侧边栏**：被顶栏压在下方，保留主导航菜单（多功能对话 / RAG / 知识库 / MCP）。
- **主内容区**：各业务页面原样保留。

视觉细节全部通过全局 CSS 注入，不改第三方组件源码；
交互部分（popover / button）仍是真正的 Streamlit 控件，保证 rerun 行为一致。
"""
import sys

import streamlit as st
import streamlit_antd_components as sac

from chayuan import __version__
from chayuan.server.utils import api_address
from chayuan.webui_pages.auth import (
    render_header_logout_button,
    render_header_user_menu,
    require_login,
)
from chayuan.webui_pages.dialogue.dialogue import dialogue_page
from chayuan.webui_pages.kb_chat import kb_chat
from chayuan.webui_pages.utils import *  # noqa: F401,F403 (ApiRequest & get_img_base64 等)

api = ApiRequest(base_url=api_address())


# ---------------------------------------------------------------------------
# 全局样式（App-Shell 布局：顶栏横跨整页，下方左侧边栏 + 右主区）
# ---------------------------------------------------------------------------
#
# Streamlit 本身没有"顶栏覆盖 sidebar"的布局组件（``st.navigation(position="top")``
# 要 1.52+，我们 1.37；``streamlit-navigation-bar`` 只支持菜单、不能嵌 popover/按钮）。
# 所以用纯 CSS 做 App-Shell：
#
#   ┌─────────────────────────────────────────────────────────┐
#   │  logo  察元AI助手 · v...       [🚪 退出] [🅐 username ▾] │ ← fixed 56px 顶栏
#   ├──────────┬──────────────────────────────────────────────┤
#   │ sidebar  │            chat / kb / mcp ...                │
#   │ (menu)   │                                               │
#   └──────────┴──────────────────────────────────────────────┘
#
# 关键技巧：
# 1) 隐藏 Streamlit 原生 header；注入我们自己的 ``.chayuan-topbar`` 固定条（全宽）。
# 2) sidebar 的 top 偏移 56px，高度减掉 56px —— 天然让出顶栏空间。
# 3) 主区 ``section[data-testid="stMain"]`` 整体 ``padding-top: 56 + 16 px``。
# 4) 右上角放**两个并排真实控件**（要保 rerun 行为）：
#      col0 = 🚪 退出按钮   —— 高频、破坏性操作，一级入口一键退出；
#      col1 = 🅐 用户名 ▾   —— 打开后展示「个人信息 / 修改密码」。
#    它们位于主区首行 ``stHorizontalBlock`` 内；CSS 把这一行 ``position: fixed``
#    吸附到顶栏右端。用 ``:first-of-type`` 精确锚定，后续业务页面的 HorizontalBlock
#    不会被误命中。
# 5) logo / 产品名 / 版本号是**纯装饰 HTML**，直接画在 ``.chayuan-topbar`` 里，
#    不占 Streamlit 列，避免和右侧交互区争 flex 空间。

_PRIMARY = "#1976d2"
_PRIMARY_DARK = "#115293"
_TOPBAR_HEIGHT = 56  # px；修改这里时下方所有偏移自动跟随

_GLOBAL_CSS = f"""
<style>
/* ========== 1. 处理 Streamlit 原生顶部 ==========
 * 不再 `display:none` 整个 header——那会连带藏掉：
 *   - sidebar 折叠按钮 (stSidebarCollapseButton，header 左上角)
 *   - sidebar 展开按钮 (stSidebarCollapsedControl，sidebar 折叠后浮左侧)
 *   - 运行状态 + 暂停/停止 (stStatusWidget，header 右上角)
 *
 * 做法：
 *   1) header 强制 fixed + 透明，z-index 抬到 chayuan-topbar (1000000) 之上，
 *      保证按钮实际绘制在品牌蓝 topbar 的前面（单独设 z-index 不够，因为默认
 *      header 是 position:relative，和 fixed 的 chayuan-topbar 不在同一 stacking 层）；
 *   2) header 自身 pointer-events:none 让品牌顶栏可继续 hover/点击；
 *      header 内**按钮**再用 pointer-events:auto 恢复可交互；
 *   3) 只隐藏 stToolbar（Deploy / Settings 三点菜单）与 stDecoration 顶部细条。
 */
header[data-testid="stHeader"] {{
    position: fixed !important;
    top: 0 !important;
    left: 0 !important;
    right: 0 !important;
    height: {_TOPBAR_HEIGHT}px !important;
    background: transparent !important;
    box-shadow: none !important;
    border: none !important;
    z-index: 1000005 !important;
    pointer-events: none;
}}
header[data-testid="stHeader"] > div {{
    background: transparent !important;
    pointer-events: none;
}}
header[data-testid="stHeader"] button,
header[data-testid="stHeader"] [data-testid="stStatusWidget"],
header[data-testid="stHeader"] [role="button"] {{
    pointer-events: auto !important;
    color: #ffffff !important;
}}

/* 顶部彩色小装饰条（Streamlit 默认 2px 橙色）——品牌色顶栏里是噪点，去掉 */
div[data-testid="stDecoration"] {{
    display: none !important;
}}
/* Deploy / Settings / Rerun 三点菜单：生产 UI 里不需要，隐藏 */
div[data-testid="stToolbar"] {{
    display: none !important;
}}

/* —— sidebar 相关的两个控件 —— */
/* A) sidebar 展开状态下，header 里的"折叠" 小箭头按钮 */
[data-testid="stSidebarCollapseButton"],
button[data-testid="stSidebarCollapseButton"],
button[kind="headerNoPadding"] {{
    z-index: 1000010 !important;
    color: #ffffff !important;
    pointer-events: auto !important;
    position: relative !important;
}}
/* 某些 Streamlit 版本里 headerNoPadding 按钮的内置 SVG 会丢失/不可见；
 * 用伪元素补一枚稳定的"菜单"图标，保证折叠态可识别。 */
button[kind="headerNoPadding"]::before,
button[data-testid="stSidebarCollapseButton"]::before {{
    content: "\\2630";
    position: absolute;
    left: 50%;
    top: 50%;
    transform: translate(-50%, -50%);
    font-size: 16px;
    line-height: 1;
    color: #ffffff;
    pointer-events: none;
    opacity: 0.95;
}}
button[kind="headerNoPadding"] svg,
button[data-testid="stSidebarCollapseButton"] svg {{
    opacity: 0 !important;
}}
/* B) sidebar 折叠状态下，浮在主区左上角的"展开"按钮。
 * Streamlit 1.34 实际 testid 是 `collapsedControl`（小写 c），没有 `stSidebarCollapsedControl`；
 * 新版本统一改成 `stSidebarCollapsedControl`。两个都覆盖。
 * 关键是 z-index 必须高于 chayuan-topbar(1000000) 与 header(1000005)，否则
 * 即便按钮被渲染出来也会被蓝色品牌顶栏视觉挡住。
 */
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"] {{
    position: fixed !important;
    top: 10px !important;
    left: 10px !important;
    z-index: 1000020 !important;
    pointer-events: auto !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    width: 36px !important;
    height: 36px !important;
    padding: 0 !important;
    background: rgba(255, 255, 255, 0.22) !important;
    border: 1px solid rgba(255, 255, 255, 0.55) !important;
    border-radius: 6px !important;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15) !important;
    backdrop-filter: blur(6px);
    visibility: visible !important;
    opacity: 1 !important;
}}
[data-testid="collapsedControl"]:hover,
[data-testid="stSidebarCollapsedControl"]:hover {{
    background: rgba(255, 255, 255, 0.35) !important;
}}
[data-testid="collapsedControl"] svg,
[data-testid="stSidebarCollapsedControl"] svg,
[data-testid="collapsedControl"] path,
[data-testid="stSidebarCollapsedControl"] path {{
    fill: #ffffff !important;
    color: #ffffff !important;
    stroke: #ffffff !important;
}}

/* 运行状态 + 暂停/停止：右上角浮起，离用户 popover 远一点 */
div[data-testid="stStatusWidget"] {{
    position: fixed !important;
    top: 10px !important;
    right: 260px !important;
    z-index: 1000006 !important;
    pointer-events: auto !important;
}}

/* ========== 2. 全局 App-Shell 偏移：给顶栏让出 56px ========== */
/* 2a. sidebar 从 56px 开始 */
section[data-testid="stSidebar"] {{
    top: {_TOPBAR_HEIGHT}px !important;
    height: calc(100vh - {_TOPBAR_HEIGHT}px) !important;
}}
/* 2b. 主区整体下推；保持 wide 布局下的舒适阅读宽度。
 * 注意：因为第 1 节里我们把 Streamlit header 从 sticky 改成了 position:fixed，
 *       header 不再占 56px 的文档流空间，这里必须留出大于 {_TOPBAR_HEIGHT}
 *       的 padding-top，否则第一行 block（chat_box 顶部、标题等）会被顶栏吃掉。
 */
section[data-testid="stMain"] .block-container {{
    padding-top: {_TOPBAR_HEIGHT + 32}px !important;
    padding-left: 1.5rem;
    padding-right: 1.5rem;
    padding-bottom: 5rem;
    max-width: 100% !important;
}}

/* ========== 3. 固定顶栏本体（扁平化设计：纯色背景 + 细分隔线） ==========
 * 扁平化原则：
 *   - 取消径向/线性渐变，用纯色 {_PRIMARY} 更"断裂"地承载品牌色；
 *   - 去掉 box-shadow，改用 1px rgba 细线做"顶栏 vs 主区"的分隔，减少视觉噪声；
 *   - logo 的图片阴影也去掉，只留 1px 描边。
 */
.chayuan-topbar {{
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    height: {_TOPBAR_HEIGHT}px;
    z-index: 1000000;
    background: {_PRIMARY};
    color: #ffffff;
    display: flex;
    align-items: center;
    justify-content: space-between;
    /* 左侧 padding 加大，给 sidebar 折叠按钮（fixed left:10px 32px 宽）让位 */
    padding: 0 20px 0 52px;
    border-bottom: 1px solid rgba(0, 0, 0, 0.08);
}}
.chayuan-topbar-brand {{
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
}}
.chayuan-topbar-brand img {{
    width: 32px;
    height: 32px;
    border-radius: 4px;
    background: #ffffff;
    padding: 2px;
    border: 1px solid rgba(255, 255, 255, 0.35);
    flex-shrink: 0;
}}
.chayuan-topbar-brand .app-title {{
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.3px;
    line-height: 1.1;
    color: #ffffff;
    white-space: nowrap;
}}
.chayuan-topbar-brand .app-version {{
    font-size: 12px;
    opacity: 0.78;
    font-weight: 500;
    margin-top: 2px;
    color: #ffffff;
    white-space: nowrap;
}}
/* ========== 4. 顶栏右侧交互区 anchor（基于 :has() 锚点，DOM 层级无关） ==========
 * _render_top_header() 里用 ``st.columns([1, 1])`` 渲染两个并排真实控件：
 *    col0 = 🚪 退出按钮     (``kind="secondary"``)
 *    col1 = 🅐 用户名 popover (``data-testid="stPopoverButton"``)
 * 第一列里额外埋了一个隐藏的 ``<span class="chayuan-topbar-anchor">``。
 *
 * 我们不再用脆弱的 ``.block-container > stVerticalBlock > stHorizontalBlock:first-of-type``
 * 链（Streamlit 不同版本里可能夹了 ``stVerticalBlockBorderWrapper``/ 其他 wrapper，
 * ``>`` 直接子选择器会失配），而是用现代 CSS 的 :has()：
 *    ``div[data-testid="stHorizontalBlock"]:has(.chayuan-topbar-anchor)``
 * —— 精确命中"内部包含这个锚点的那一行 HorizontalBlock"，永远不会误伤业务页面。
 *
 * :has() 在 Chromium 105+/Safari 15.4+/Firefox 121+ 已支持；桌面端我们是把
 * Streamlit 装进 Electron 的，引擎肯定够新。 */
div[data-testid="stHorizontalBlock"]:has(.chayuan-topbar-anchor) {{
    position: fixed !important;
    top: 0 !important;
    right: 16px !important;
    height: {_TOPBAR_HEIGHT}px !important;
    z-index: 1000001 !important;
    width: auto !important;
    margin: 0 !important;
    padding: 0 !important;
    gap: 8px !important;
    flex-wrap: nowrap !important;
    align-items: center !important;
    background: transparent !important;
}}
/* 两列各自收缩到内容宽度；清掉默认的 24px 水平 padding。 */
div[data-testid="stHorizontalBlock"]:has(.chayuan-topbar-anchor)
  > div[data-testid="column"] {{
    flex: 0 0 auto !important;
    width: auto !important;
    min-width: 0 !important;
    padding: 0 !important;
    display: flex !important;
    align-items: center !important;
}}
/* 列内部 stElementContainer 的默认 margin-bottom 会把按钮向下挤出顶栏；归零。 */
div[data-testid="stHorizontalBlock"]:has(.chayuan-topbar-anchor)
  div[data-testid="stElementContainer"],
div[data-testid="stHorizontalBlock"]:has(.chayuan-topbar-anchor)
  div.element-container {{
    margin: 0 !important;
    padding: 0 !important;
}}
/* 锚点自身不可见，不占任何空间。 */
.chayuan-topbar-anchor {{
    display: none !important;
}}

/* ========== 5. 顶栏右侧导航项 skin：a 标签风格（GitHub / Notion header 同款） ==========
 * 两个真实控件：
 *   - popover 按钮  →  ``button[data-testid="stPopoverButton"]``  （用户名 ▾）
 *   - 退出按钮       →  ``button[kind="secondary"]``              （🚪 退出）
 *
 * 设计：**伪装成链接**——
 *   - 透明底 + 无边框 + 无圆角胶囊；
 *   - 半透明白字 rgba(255,255,255,0.82)；
 *   - hover 仅提亮到纯白 + 轻微底色（rgba 0.08），不弹出药丸；
 *   - 退出 hover 用浅红文字（#fecaca）做危险色暗示，但也不上实底色块，保持克制。
 * 这样顶栏就不会有"两颗突兀的白色药丸按钮"，视觉重心回到左侧品牌，
 * 右上角只是常规的"登录态导航链接"。
 */
div[data-testid="stHorizontalBlock"]:has(.chayuan-topbar-anchor)
  button[data-testid="stPopoverButton"],
div[data-testid="stHorizontalBlock"]:has(.chayuan-topbar-anchor)
  button[kind="secondary"] {{
    background: transparent !important;
    color: rgba(255, 255, 255, 0.82) !important;
    border: 1px solid transparent !important;
    border-radius: 4px !important;
    padding: 4px 10px !important;
    font-weight: 500 !important;
    box-shadow: none !important;
    min-height: 30px !important;
    transition: color 0.12s ease, background 0.12s ease !important;
    white-space: nowrap;
}}
div[data-testid="stHorizontalBlock"]:has(.chayuan-topbar-anchor)
  button[data-testid="stPopoverButton"]:hover,
div[data-testid="stHorizontalBlock"]:has(.chayuan-topbar-anchor)
  button[kind="secondary"]:hover {{
    background: rgba(255, 255, 255, 0.08) !important;
    color: #ffffff !important;
    border-color: transparent !important;
}}
/* focus 态：键盘可达性用 2px 半透明白 outline，不变成实心按钮 */
div[data-testid="stHorizontalBlock"]:has(.chayuan-topbar-anchor)
  button[data-testid="stPopoverButton"]:focus-visible,
div[data-testid="stHorizontalBlock"]:has(.chayuan-topbar-anchor)
  button[kind="secondary"]:focus-visible {{
    outline: 2px solid rgba(255, 255, 255, 0.6) !important;
    outline-offset: 1px;
}}
/* 退出按钮 hover：改为"浅红文字"提示危险色，而不是实心红底块。
 * ``:not([data-testid="stPopoverButton"])`` 排除 popover 按钮——部分 Streamlit 版本里
 * popover 按钮也带 ``kind="secondary"``。 */
div[data-testid="stHorizontalBlock"]:has(.chayuan-topbar-anchor)
  button[kind="secondary"]:not([data-testid="stPopoverButton"]):hover {{
    background: rgba(239, 68, 68, 0.14) !important;
    border-color: transparent !important;
    color: #fecaca !important;
}}
div[data-testid="stHorizontalBlock"]:has(.chayuan-topbar-anchor)
  button[kind="secondary"]:not([data-testid="stPopoverButton"]):hover p {{
    color: #fecaca !important;
}}
/* 按钮内部文字：统一字号并去掉 <p> 默认 margin；粗细降到 500（更像链接字重） */
div[data-testid="stHorizontalBlock"]:has(.chayuan-topbar-anchor)
  button[data-testid="stPopoverButton"] p,
div[data-testid="stHorizontalBlock"]:has(.chayuan-topbar-anchor)
  button[kind="secondary"] p {{
    color: inherit !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    margin: 0 !important;
    letter-spacing: 0.2px;
}}

/* ========== 6. 侧边栏视觉微调 ========== */
[data-testid="stSidebarUserContent"] {{
    padding-top: 12px;
}}
[data-testid="stSidebar"] .stImage {{
    margin-top: 4px;
    margin-bottom: 6px;
}}

/* ========== 7. 聊天输入条底部留白 ========== */
[data-testid="stBottomBlockContainer"] {{
    padding-bottom: 16px;
}}

/* ========== 8. 全局扁平化皮肤（对话 / 侧边栏 / 按钮 / 输入） ==========
 * 扁平化设计 checklist：
 *   - 去掉所有 ``box-shadow`` / ``filter: drop-shadow`` / 内凹阴影；
 *   - 渐变 → 纯色，或保留到仅"强调态"（primary 按钮 hover）；
 *   - 4~6px 圆角为主（过度圆润会偏 neumorphism，不够 flat）；
 *   - 以 1px 细线 + 留白构建层次，不靠阴影。
 *
 * 所有规则都用较低特异度（单类 / 单 data-testid）便于第三方组件被正确覆盖。 */

/* 8.1 侧边栏：纯白 + 右侧 1px 分隔线，不再有默认阴影 */
section[data-testid="stSidebar"] {{
    background: #ffffff !important;
    border-right: 1px solid #e5e7eb;
    box-shadow: none !important;
}}
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {{
    padding-top: 16px;
}}

/* 8.2 按钮（secondary / default）：白底 + 1px 边框，hover 仅换背景色，不位移 */
.stButton > button,
.stDownloadButton > button,
.stFormSubmitButton > button {{
    border: 1px solid #d1d5db !important;
    box-shadow: none !important;
    border-radius: 6px !important;
    background: #ffffff;
    color: #1f2937;
    font-weight: 500;
    transition: background 0.12s ease, border-color 0.12s ease, color 0.12s ease;
}}
.stButton > button:hover,
.stDownloadButton > button:hover,
.stFormSubmitButton > button:hover {{
    background: #f3f4f6;
    border-color: #9ca3af !important;
    color: #111827;
    transform: none !important;
}}
/* primary 按钮：实心主色；hover 变深但不位移 */
.stButton > button[kind="primary"],
.stFormSubmitButton > button[kind="primary"] {{
    background: {_PRIMARY} !important;
    color: #ffffff !important;
    border-color: {_PRIMARY} !important;
}}
.stButton > button[kind="primary"]:hover,
.stFormSubmitButton > button[kind="primary"]:hover {{
    background: {_PRIMARY_DARK} !important;
    border-color: {_PRIMARY_DARK} !important;
    color: #ffffff !important;
}}

/* 8.3 输入 / 选择 / 文本域：1px 边框 + 轻微圆角，focus 用 ring（不模糊阴影） */
[data-baseweb="input"] > div,
[data-baseweb="select"] > div,
[data-baseweb="textarea"],
.stTextInput input,
.stTextArea textarea,
.stNumberInput input,
.stDateInput input,
.stTimeInput input {{
    border-radius: 6px !important;
    box-shadow: none !important;
}}
.stTextInput input:focus,
.stTextArea textarea:focus,
.stNumberInput input:focus {{
    outline: none !important;
    border-color: {_PRIMARY} !important;
    box-shadow: 0 0 0 2px rgba(25, 118, 210, 0.2) !important;
}}

/* 8.4 聊天消息：去卡片阴影，改用 6px 圆角 + 细边或纯留白 */
[data-testid="stChatMessage"] {{
    box-shadow: none !important;
    border-radius: 8px !important;
    background: transparent !important;
    padding: 8px 0 !important;
}}
[data-testid="stChatMessage"] [data-testid="stChatMessageContent"] {{
    background: #f9fafb !important;
    border: 1px solid #f1f5f9 !important;
    box-shadow: none !important;
    border-radius: 8px !important;
}}
/* 用户消息（header=user）换淡蓝底区分 */
[data-testid="stChatMessage"].st-emotion-cache-janbn0,
[data-testid="stChatMessage"][data-user-id="user"],
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) [data-testid="stChatMessageContent"] {{
    background: #eff6ff !important;
    border-color: #dbeafe !important;
}}

/* 8.5 聊天输入框底部条：扁平、1px 边框 */
[data-testid="stChatInput"] {{
    border: 1px solid #e5e7eb !important;
    border-radius: 10px !important;
    box-shadow: none !important;
}}
[data-testid="stChatInput"]:focus-within {{
    border-color: {_PRIMARY} !important;
    box-shadow: 0 0 0 2px rgba(25, 118, 210, 0.15) !important;
}}
[data-testid="stBottomBlockContainer"] {{
    background: #ffffff;
    border-top: 1px solid #f1f5f9;
}}

/* 8.6 Tabs：flat underline 风格（而不是带阴影的胶囊 tab） */
div[data-baseweb="tab-list"] {{
    border-bottom: 1px solid #e5e7eb;
    gap: 4px;
}}
div[data-baseweb="tab"] {{
    background: transparent !important;
    padding: 8px 14px !important;
    font-weight: 500;
    color: #6b7280 !important;
    border-radius: 0 !important;
    border-bottom: 2px solid transparent !important;
    transition: color 0.12s ease, border-color 0.12s ease;
}}
div[data-baseweb="tab"][aria-selected="true"] {{
    color: {_PRIMARY} !important;
    border-bottom-color: {_PRIMARY} !important;
    background: transparent !important;
}}
/* 移除 tabs highlight 下划线的 Streamlit 默认渐变条 */
div[data-baseweb="tab-highlight"] {{
    display: none !important;
}}

/* 8.7 展开折叠（expander）/ 弹窗 / 通用卡片：去阴影，用细边代替 */
[data-testid="stExpander"],
[data-testid="stExpander"] > details {{
    border: 1px solid #e5e7eb !important;
    border-radius: 8px !important;
    box-shadow: none !important;
    background: #ffffff;
}}
[data-testid="stExpander"] summary {{
    padding: 10px 14px !important;
}}
[data-testid="stAlert"],
[data-testid="stNotification"] {{
    box-shadow: none !important;
    border-radius: 6px !important;
}}

/* 8.8 popover 面板本身也去阴影，与顶栏扁平化一致 */
div[data-baseweb="popover"] > div,
div[data-testid="stPopoverBody"] {{
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04) !important;
    border: 1px solid #e5e7eb !important;
    border-radius: 8px !important;
}}

/* 8.9 链接色与主色对齐 */
a, .stMarkdown a {{
    color: {_PRIMARY} !important;
    text-decoration: none;
}}
a:hover, .stMarkdown a:hover {{
    text-decoration: underline;
}}
</style>
"""


def _render_top_header() -> None:
    """渲染 App-Shell 顶栏。

    两部分：
    1. 纯 HTML 的 ``.chayuan-topbar``（固定 56px 全宽蓝条）——仅含 logo + 产品名 + 版本。
    2. **两个** 并排真实 Streamlit 控件，视觉上位于蓝条最右侧（通过 CSS ``position: fixed``
       吸附）：

           ...  [🚪 退出]  [🅐 用户名 ▾]
                  ↑              ↑
       col_logout (secondary)    col_user (popover)

       - **退出按钮**：高频、破坏性操作，放到一级入口一键可达；
       - **用户名 ▾**：打开后展示"个人信息 / 修改密码"等低频项。

    这样做的理由（对标 GitHub / Notion / 飞书）：日常用户登出是高频，且需要快速确认
    当前登录态；把它埋到下拉菜单里会让用户多点一次。个人资料、改密则是低频操作，
    收在用户名胶囊里更干净。

    这里必须用 ``st.columns([1, 1])`` 而不是直接调两个控件，是为了：
    - **保证"首行 HorizontalBlock"这个 CSS 锚点始终存在**——否则后续业务页面渲染的
      第一行 columns 会被 CSS 的 ``:first-of-type`` 错误命中，退出按钮和用户名会飞走。
    - 游客态下 ``render_header_logout_button`` 自动 no-op，右列的 popover 会显示"游客
      模式"占位，锚点仍然稳定。
    """
    logo_uri = get_img_base64("logo.png")

    # ---- (1) 全宽固定顶栏：logo + 标题 + 版本 -------------------------------
    st.markdown(
        f"""
        <div class="chayuan-topbar">
            <div class="chayuan-topbar-brand">
                <img src="{logo_uri}" alt="logo"/>
                <div>
                    <div class="app-title">察元AI助手</div>
                    <div class="app-version">Chayuan AI · v{__version__}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ---- (2) 顶栏右侧交互区：退出 + 用户名下拉 -----------------------------
    # 在第一列里塞一个隐藏的 ``<span class="chayuan-topbar-anchor">`` 作为锚点，
    # CSS 用 ``div[data-testid="stHorizontalBlock"]:has(.chayuan-topbar-anchor)``
    # 精确命中这一行（而不是用脆弱的 :first-of-type + 直接子选择器）。
    # 这样无论 Streamlit 升级引入多少层 VerticalBlockBorderWrapper / 内部 wrapper，
    # 这行都能稳稳地吸附到顶栏右端。
    col_logout, col_user = st.columns([1, 1])
    with col_logout:
        # st.html 在 Streamlit ≥ 1.33 可用，直接原样注入、不走 markdown 解析，
        # 能保证 class 属性完整保留，让 :has(.chayuan-topbar-anchor) 可靠命中。
        st.html('<span class="chayuan-topbar-anchor" aria-hidden="true"></span>')
        render_header_logout_button()
    with col_user:
        render_header_user_menu()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    is_lite = "lite" in sys.argv  # TODO: remove lite mode

    st.set_page_config(
        page_title="察元AI助手",
        page_icon=get_img_base64("logo.png"),
        initial_sidebar_state="expanded",
        menu_items={ 
            "About": f"""欢迎使用察元AI助手 {__version__}（© 北京智灵鸟科技中心）。""",
        },
        layout="wide",
    )

    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)

    # 登录门禁：AUTH_REQUIRED=true 且未登录时会 st.stop() 阻塞在登录页
    require_login()

    # 顶栏（logo 左、用户区右）—— 必须是 block-container 的第一行 stHorizontalBlock，
    # 否则 CSS 的 `:first-of-type` 命中错位，顶栏会退化成普通白色行。
    _render_top_header()

    # =====================================================================
    # 对话前端职责聚焦：
    #   - 多功能对话 / RAG 对话
    # 其它管理操作（知识库创建、MCP、治理、数据源、文件存储、模型下载等）
    # 统一到 Config Panel（默认 8502）；本页侧栏给一个跳转入口避免迷路。
    # =====================================================================
    with st.sidebar:
        selected_page = sac.menu(
            [
                sac.MenuItem("多功能对话", icon="chat"),
                sac.MenuItem("RAG 对话", icon="database"),
            ],
            key="selected_page",
            open_index=0,
        )

        sac.divider()

        # —— 跳到配置面板 —————————————————————————————————————————
        # 设置面板的访问 URL = http(s)://<CONFIG_SERVER.host>:<port>/<login_path>
        # 这里不要求用户手记密码路径；dashboard.py 会处理"已登录/未登录"跳转。
        try:
            from chayuan.settings import Settings
            _cs = getattr(Settings.basic_settings, "CONFIG_SERVER", {}) or {}
            _host = str(_cs.get("host") or "127.0.0.1")
            if _host in ("0.0.0.0", ""):
                _host = "127.0.0.1"
            _port = int(_cs.get("port") or 8502)
            _panel_url = f"http://{_host}:{_port}/"
        except Exception:
            _panel_url = "http://127.0.0.1:8502/"
        st.markdown(
            f"""
            <a href="{_panel_url}" target="_blank" style="
                display:block;padding:8px 12px;margin-top:8px;
                border:1px solid #d1d5db;border-radius:6px;
                color:#1f2937;text-decoration:none;font-size:13px;
                background:#f9fafb;">
                ⚙️ 打开配置面板
                <span style="color:#6b7280;font-size:12px;float:right">↗</span>
            </a>
            <div style="font-size:12px;color:#6b7280;padding:4px 4px 0 4px;">
                知识库 / 模型 / 数据源 / MCP / 治理 等管理操作请在配置面板进行。
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 路由：只保留对话类页面
    if selected_page == "RAG 对话":
        kb_chat(api=api)
    else:
        dialogue_page(api=api, is_lite=is_lite)
