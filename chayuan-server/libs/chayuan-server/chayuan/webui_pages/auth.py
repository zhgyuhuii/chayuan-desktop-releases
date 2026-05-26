"""Streamlit 登录 / 账户组件。

职责拆分：
- ``require_login()``     —— 登录"门禁"。未登录时：
    1. 先尝试用 URL 里残留的 ``?_auth_rt=<refresh_token>`` 向 ``/auth/refresh``
       换一对新 token，实现"登录后刷新页面不掉线"；
    2. 换不到 / 没有残留 → 渲染**全屏登录页**并 ``st.stop()``。
  已登录直接 return。登录页本身也自带「全屏接管」CSS：隐藏 Streamlit 侧边栏 / 顶栏
  / 主区内边距，整屏切换成"品牌区 + 登录卡片"双列布局。
- ``render_header_user_menu()`` —— 顶栏右上角用户头像下拉菜单（登录态使用）。

会话持久化（核心设计 · 2026-04 重写）：
----------------------------------------------------------------
Streamlit 的 ``st.session_state`` 跟随 WebSocket 会话生命周期，**刷新页面即丢**。
要跨刷新保持登录态，必须把登录凭证落到浏览器可以带回来的地方。

**本模块选用 URL 查询参数**：登录成功后把 ``refresh_token`` 写进 ``?_auth_rt=…``，
Streamlit 会用 ``history.replaceState`` 把这个 qp 同步到地址栏 —— F5 重载时 URL 会
带着这个参数回到 Python 端，require_login() 自然就能拿它去换新 token。

为什么不走"localStorage + iframe JS bounce"那条复杂路径：
  - Streamlit 的 ``components.v1.html`` iframe 带的 sandbox 不含 ``allow-top-navigation``，
    iframe 里的 JS 没办法直接 ``location.replace`` 去导航父窗口；上一版试过"同源 iframe
    在父 document 上注入 <script>"的经典逃逸手法，结果还是遇到 Streamlit 拆 DOM 的
    时序坑，用户体验呈现为"点击登录后一直卡在正在恢复会话"。
  - URL qp 路线 **纯 Python + Streamlit 原语**，没有任何浏览器端 JS / iframe / sandbox
    依赖：``st.query_params[key] = value`` 就会被 Streamlit 自己 sync 到浏览器地址栏，
    F5 重载是浏览器**自己保留 URL** —— 逻辑无懈可击，也特别好排障。

安全取舍：
  - refresh_token 会**明文出现在地址栏**，稍有隐私泄露面（截屏、书签、浏览器历史）；
  - 但内网 / 单机部署场景里，URL 能被外人拿到的风险 ≈ 浏览器 cookie 被拿到的风险；
  - 后端 ``/auth/refresh`` 做了 rotating refresh token：每换一次就生成新 rt，老的
    在 ``JWT_REFRESH_TTL_SECONDS`` 内还活着但已"过气"；
  - access_token 仅存 session_state，不落 URL；
  - 真要更严，可以把下面 ``_RT_QP`` 改成写 ``#fragment``（服务器/代理完全看不见），
    代价是得再起一段 JS 把 fragment 搬到 qp，迎来新一轮复杂度；目前不值得。

- 旧版本曾经在表单内放过纯前端 SVG 验证码（session_state 存答案 + 图形干扰）；
  实际部署中验证码并非后端 ``/auth/*`` 合约的一部分，且对真正的暴力破解防护意义有限
  （session 级、可绕过），所以已下线。如需恢复，请走"后端 Redis + 一次性 token"方案。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx
import streamlit as st

from chayuan.server.utils import api_address
from chayuan.settings import Settings


# 走 chayuan 的命名空间，配合 startup.py 里配的 root logger，输出自动落到 chayuan.log。
# 前端出问题时后端**几乎没有日志**是这个模块最难 debug 的原因 —— 带个 logger 后续遇到
# "点了登录没反应"类问题，直接查 chayuan.log 里的 [chayuan.webui.auth] 行就能定位。
logger = logging.getLogger("chayuan.webui.auth")


# ---------------------------------------------------------------------------
# 跨刷新持久化：URL query 参数
# ---------------------------------------------------------------------------

# URL 上存 refresh_token 的 key；带下划线前缀 + 特定命名，和业务 qp 撞车概率接近 0。
# 改名会让已登录用户当前的 URL 立刻失效，需要重新登录；升级时慎重。
_RT_QP = "_auth_rt"


def _clear_qp(key: str) -> None:
    """从 Streamlit 的 query_params 里移除 key（并自动 sync 到浏览器 URL）。"""
    try:
        qp = st.query_params
        if key in qp:
            del qp[key]
    except Exception:  # noqa: BLE001
        logger.exception("[_clear_qp] failed for %s", key)


def _read_qp(key: str) -> str:
    """读取单值 query param，兼容 Streamlit 返回 list / str 的差异。

    拿不到 / 读失败一律返回空串，方便调用方用 ``if not x:`` 统一判空。
    """
    try:
        raw = st.query_params.get(key)
    except Exception:  # noqa: BLE001
        logger.exception("[_read_qp] failed for %s", key)
        return ""
    if raw is None:
        return ""
    if isinstance(raw, (list, tuple)):
        return (raw[0] if raw else "") or ""
    return raw or ""


def _write_qp(key: str, value: str) -> None:
    """把 value 写进 URL query（单值，Streamlit 会自动 sync 到地址栏）。

    Streamlit 1.34 的 ``st.query_params`` 是 MutableMapping：``qp[k] = v`` 会被
    下发给前端用 ``history.replaceState`` 更新地址栏 —— **不会**触发 rerun，正合我意。
    """
    try:
        st.query_params[key] = value
    except Exception:  # noqa: BLE001
        logger.exception("[_write_qp] failed for %s", key)


def _do_refresh(refresh_token: str) -> Optional[Dict]:
    """拿 refresh_token 调 ``/auth/refresh`` 换一对新 token。

    成功返回 pack dict；失败（网络 / 401 / 400）返回 None，让调用方按"凭证失效"
    统一处理：清 URL、落登录页。
    """
    try:
        r = _api_call(
            "POST", "/auth/refresh", json={"refresh_token": refresh_token},
        )
    except Exception:  # noqa: BLE001
        logger.exception("[_do_refresh] network error")
        return None
    if r.status_code != 200:
        logger.warning(
            "[_do_refresh] server rejected rt: status=%s body=%s",
            r.status_code, r.text[:200],
        )
        return None
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        logger.exception("[_do_refresh] bad json")
        return None


def _try_restore_from_url() -> bool:
    """页面加载时检查 URL 上有没有 refresh_token，有就用它激活 session。

    返回 True 表示恢复成功（``session_state`` 已填，调用方直接走已登录分支）；
    False 表示 URL 上没凭证 / 凭证失效（调用方继续走登录页流程）。

    失败 / 已消费过的 qp 一律从 URL 清掉；成功时会**重写**为后端轮转出来的新 rt，
    地址栏始终只挂着最新一枚 rt，老的交给后端自然老化。
    """
    rt = _read_qp(_RT_QP).strip()
    if not rt:
        return False

    pack = _do_refresh(rt)
    if not pack:
        # 过期 / 撤销 / 网络异常 —— 无论哪种都清掉 URL 上的僵尸凭证，
        # 否则用户每次 F5 都会再撞一次 401，体感更糟。
        _clear_qp(_RT_QP)
        return False

    _apply_auth(pack)

    # rotating refresh token：后端换对时会吐出新的 rt。写回 URL 让下次 F5 能用上。
    # 后端目前**没有**做老 rt 黑名单，老 rt 在 TTL 内仍然有效，所以即便下面这一步
    # 因为奇怪的时序没生效，用户也只是停留在第一枚 rt 上，不至于被踢出。
    new_rt = pack.get("refresh_token") or rt
    if new_rt != rt:
        _write_qp(_RT_QP, new_rt)

    logger.info(
        "[_try_restore_from_url] session restored for user=%s",
        (pack.get("user") or {}).get("username"),
    )
    return True


# ---------------------------------------------------------------------------
# 配置 / 基础
# ---------------------------------------------------------------------------

def _auth_required() -> bool:
    try:
        return bool(getattr(Settings.basic_settings, "AUTH_REQUIRED", False))
    except Exception:
        return False


def _allow_registration() -> bool:
    """是否允许前端展示“注册”入口。

    与后端 `/auth/register` 的守卫保持一致（``AUTH_ALLOW_REGISTRATION``）：
    - True  → 登录页展示登录 / 注册两个 tab，用户可自助开户；
    - False → 只展示登录 tab，彻底不出现“注册”字样。

    读不到配置时**保守返回 False**（企业化默认值），避免把“自助注册”意外暴露。
    """
    try:
        return bool(
            getattr(Settings.basic_settings, "AUTH_ALLOW_REGISTRATION", False)
        )
    except Exception:
        return False


def _api_call(method: str, path: str, **kwargs) -> httpx.Response:
    base = api_address().rstrip("/")
    url = f"{base}{path}"
    return httpx.request(method, url, timeout=20.0, **kwargs)


# ---------------------------------------------------------------------------
# 认证业务
# ---------------------------------------------------------------------------

def _do_login(username: str, password: str) -> Optional[Dict]:
    try:
        r = _api_call("POST", "/auth/login", json={
            "username": username, "password": password,
        })
    except Exception as e:  # noqa: BLE001
        st.error(f"登录失败：网络异常 {e!r}")
        return None
    if r.status_code != 200:
        try:
            detail = r.json().get("detail") or r.text
        except Exception:
            detail = r.text
        st.error(f"登录失败：{detail}")
        return None
    return r.json()


def _do_register(username: str, password: str, email: Optional[str]) -> bool:
    """调用后端 ``POST /auth/register`` 创建账号。

    成功时返回 True 并由调用方提示“请使用新账号登录”；失败时在页面上 ``st.error``
    友好提示并返回 False。本函数**不自动登录**：
    - 注册 → 登录两步分离可以复用同一套 token 下发逻辑，不用维护两条路径；
    - 也能和"注册需邮箱 / 邀请码审批"等未来扩展保持前向兼容。
    """
    payload: Dict[str, Any] = {"username": username, "password": password}
    if email:
        payload["email"] = email
    try:
        r = _api_call("POST", "/auth/register", json=payload)
    except Exception as e:  # noqa: BLE001
        st.error(f"注册失败：网络异常 {e!r}")
        return False
    if r.status_code in (200, 201):
        return True
    try:
        detail = r.json().get("detail") or r.text
    except Exception:
        detail = r.text
    if r.status_code == 403:
        st.error("当前系统已关闭自助注册，请联系管理员开通账号。")
    else:
        st.error(f"注册失败：{detail}")
    return False


def _apply_auth(pack: Dict) -> None:
    """pack = {access_token, refresh_token?, user: {...}}.

    把 token / 用户资料写到 ``st.session_state`` 供当前 WebSocket 会话使用。
    **不在这里**动 URL qp —— URL 只由 login / restore / logout 那三个"边缘"路径写，
    中间任何业务代码都不该偷偷改 URL，否则排障会非常难。
    """
    st.session_state["access_token"] = pack.get("access_token")
    rt = pack.get("refresh_token")
    if rt:
        st.session_state["refresh_token"] = rt
    user = pack.get("user") or {}
    st.session_state["user"] = user
    st.session_state["username"] = user.get("username")
    st.session_state["role"] = user.get("role")


def logout() -> None:
    """登出：清 session_state + 从 URL 拔掉 refresh_token。

    调用方在本函数返回后需要自己决定是 ``st.rerun()`` 还是 ``st.stop()``，通常配
    ``st.rerun()`` 让 Streamlit 重绘到登录页。
    """
    for k in (
        "access_token", "refresh_token", "user", "username", "role",
        "_me_summary_cached",
    ):
        st.session_state.pop(k, None)
    _clear_qp(_RT_QP)


def _fetch_me_summary() -> Optional[Dict]:
    """调用 /auth/me/summary 拿汇总；失败静默。"""
    token = st.session_state.get("access_token")
    if not token:
        return None
    try:
        r = _api_call(
            "GET", "/auth/me/summary",
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code == 200:
            return r.json()
    except Exception:  # noqa: BLE001
        return None
    return None


def _do_change_password(old: str, new: str) -> bool:
    token = st.session_state.get("access_token")
    user = st.session_state.get("user") or {}
    uid = user.get("id")
    if not token or uid is None:
        st.error("未登录。")
        return False
    try:
        r = _api_call(
            "PATCH", f"/auth/users/{int(uid)}/password",
            json={"old_password": old, "new_password": new},
            headers={"Authorization": f"Bearer {token}"},
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"改密失败：{e!r}")
        return False
    if r.status_code not in (200, 204):
        try:
            detail = r.json().get("detail") or r.text
        except Exception:
            detail = r.text
        st.error(f"改密失败：{detail}")
        return False
    return True


# ---------------------------------------------------------------------------
# UI 组件
# ---------------------------------------------------------------------------

def _initials(name: str) -> str:
    """从用户名里提炼 1~2 个首字符作为头像占位（中文直接取第一个字）。"""
    name = (name or "U").strip()
    if not name:
        return "U"
    # ASCII 名：取首字母大写；中文等 unicode：取第一个字符
    return name[0].upper()


# ---------------------------------------------------------------------------
# 顶栏用户菜单（单一 popover 触发 → 个人信息 / 修改密码 / 退出登录）
# ---------------------------------------------------------------------------
#
# 行业标准的"用户头像下拉菜单"：顶栏右上角只有一个可点的头像按钮，点开展示所有
# 账号操作。相比上一版"三个按钮并排"更紧凑、符合 GitHub/Claude/ChatGPT 的惯例。
#
# 实现要点：
# - popover 的按钮标签就是"头像字母 + 用户名"，由外层 CSS 把它 skin 成圆角胶囊；
# - popover 面板内用 ``st.expander`` 承载"个人信息" / "修改密码"两个二级项，
#   "退出登录"作为底部的 primary 按钮常驻；
# - ``st.columns`` 嵌套限制：popover 里不再嵌 ``st.columns``，所有双列视觉（统计
#   指标、资料表格）都用 HTML 渲染。


def _fmt_time(value: Any) -> str:
    """把后端可能返回的时间字段尽量渲染成易读字符串。"""
    if value in (None, ""):
        return "—"
    try:
        return str(value).split("T")[0] if "T" in str(value) else str(value)
    except Exception:  # noqa: BLE001
        return str(value)


def render_header_user_menu() -> None:
    """顶栏右上角"用户名 ▾"下拉（仅含 **个人信息 / 修改密码**）。

    布局分工（见 webui.py 的 ``_render_top_header``）：
    - 本函数只渲染"用户名 ▾"胶囊 + 点开后的个人信息 / 修改密码面板；
    - 退出登录是高频操作，拆成独立按钮放到本菜单**左侧**，见
      :func:`render_header_logout_button`。

    未登录时的降级：
    - ``AUTH_REQUIRED=false`` —— 显示"G 游客模式"占位 popover（点开只有一条提示）；
    - ``AUTH_REQUIRED=true``  —— 已被 ``require_login()`` 拦在前面，此函数 no-op。

    必须放在 webui.py 的"顶栏交互行"里（那一行会被 CSS 用 ``position: fixed``
    吸附到顶栏右端，视觉上就是 ``.chayuan-topbar`` 内的最右元素）。
    """
    if not is_logged_in():
        if not _auth_required():
            with st.popover("G  游客模式", use_container_width=True):
                st.caption("当前未登录。若需要多用户隔离，请开启 `AUTH_REQUIRED=true` 后登录使用。")
        return

    uname = st.session_state.get("username") or "用户"
    role = st.session_state.get("role") or "-"
    initial = _initials(uname)

    # popover 触发按钮的文字：头像首字 + 用户名 + ▾ 小箭头
    trigger_label = f"{initial}  {uname}  ▾"

    with st.popover(trigger_label, use_container_width=True):
        # ---- 用户卡片头 ----
        st.markdown(
            f"""
            <div style="display:flex;align-items:center;gap:12px;padding:2px 2px 10px 2px;
                        border-bottom:1px solid #eef0f4;margin-bottom:6px;">
                <div style="width:40px;height:40px;border-radius:50%;
                            background:#1976d2;color:#fff;
                            font-weight:700;font-size:16px;
                            display:flex;align-items:center;justify-content:center;">
                    {initial}
                </div>
                <div style="line-height:1.3;">
                    <div style="font-weight:700;font-size:14px;color:#111827;">{uname}</div>
                    <div style="font-size:12px;color:#6b7280;">角色：{role}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # ---- 二级项 1：个人信息 ----
        with st.expander("📋 个人信息", expanded=False):
            # 缓存一次 /auth/me/summary；刷新时（或切账号）由 logout()/重新登录清理
            if st.session_state.get("_me_summary_cached") is None:
                st.session_state["_me_summary_cached"] = _fetch_me_summary()
            summary = st.session_state.get("_me_summary_cached") or {}
            user = summary.get("user") or st.session_state.get("user") or {}
            stats = summary.get("stats") or {}

            email = user.get("email") or "—"
            created_at = _fmt_time(user.get("created_at"))
            last_login = _fmt_time(user.get("last_login_at"))

            st.markdown(
                f"""
                <div style="font-size:13px;line-height:1.9;">
                    <div><span style="color:#6b7280;">用户名：</span><strong>{uname}</strong></div>
                    <div><span style="color:#6b7280;">邮箱：</span>{email}</div>
                    <div><span style="color:#6b7280;">角色：</span>{role}</div>
                    <div><span style="color:#6b7280;">注册时间：</span>{created_at}</div>
                    <div><span style="color:#6b7280;">最近登录：</span>{last_login}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if stats:
                kb_owned = int(stats.get("kb_owned", 0) or 0)
                conversations = int(stats.get("conversations", 0) or 0)
                st.markdown(
                    f"""
                    <div style="display:flex;gap:10px;margin-top:10px;">
                        <div style="flex:1;padding:8px 10px;border-radius:8px;background:#f3f4f6;">
                            <div style="font-size:11px;color:#6b7280;">我的知识库</div>
                            <div style="font-size:20px;font-weight:700;color:#111827;">{kb_owned}</div>
                        </div>
                        <div style="flex:1;padding:8px 10px;border-radius:8px;background:#f3f4f6;">
                            <div style="font-size:11px;color:#6b7280;">历史会话</div>
                            <div style="font-size:20px;font-weight:700;color:#111827;">{conversations}</div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        # ---- 二级项 2：修改密码 ----
        with st.expander("🔐 修改密码", expanded=False):
            old_pwd = st.text_input("原密码", type="password", key="_chpw_old")
            new_pwd = st.text_input("新密码（≥6 位）", type="password", key="_chpw_new")
            if st.button(
                "提交修改", use_container_width=True, type="primary", key="_chpw_btn",
            ):
                if not old_pwd or len(new_pwd) < 6:
                    st.warning("请正确填写原密码与新密码。")
                elif _do_change_password(old_pwd, new_pwd):
                    st.success("已修改，请下次登录使用新密码。")


def render_header_logout_button() -> None:
    """顶栏右上角、在用户名 popover **左侧**的"退出登录"独立按钮。

    退出是高频、破坏性操作；放在一级入口可以让用户一键退出，无需先点开下拉，
    与 GitHub / Notion / 飞书等大多数 SaaS 的顶栏模式一致。

    未登录时不渲染（避免顶栏右侧出现无意义的按钮）。
    """
    if not is_logged_in():
        return
    if st.button(
        "🚪 退出",
        use_container_width=True,
        type="secondary",
        key="_header_logout_btn",
        help="退出当前账号",
    ):
        # logout() 只负责清 session_state + 从 URL 拔掉 refresh_token；这里调 rerun
        # 让 Streamlit 重绘到登录页。无浏览器端 JS 依赖，行为完全可预期。
        logout()
        st.rerun()


# ---------------------------------------------------------------------------
# 登录页：全屏接管式登录
# ---------------------------------------------------------------------------
#
# 视觉目标（对标 Linear / Vercel / 飞书 登录页）：
# - 全屏左右分屏（桌面端），移动端自动堆叠；
# - 左栏深色品牌区，突出价值主张与产品能力；
# - 右栏白色登录面板，仅保留登录动作；
# - 全局扁平化，弱装饰、强层级，避免“套皮感”。


# ----------------------------- 登录页 CSS（全屏接管） -------------------------
#
# 关键约束：``st.markdown('<div class="foo">')`` 在 Streamlit 渲染中是一个**兄弟**
# 节点（而不是后续组件的父节点）—— HTML 解析器会在该 markdown 容器末尾自动闭合这个
# 标签。因此，凡是要命中 Streamlit 控件（stTextInput/stForm/stRadio/stButton…）的
# CSS 都必须走 "第 2 列" 的后代选择器；``.chayuan-auth-form`` 这个类只对我们在
# ``st.markdown`` 里手写的纯 HTML（.form-title / .form-sub / 验证码盒子等）有意义。
#
# 为避免每条规则都重复写一坨长选择器，这里用 Python 字符串拼接定义两个前缀别名：
#   _SEL_HBLOCK  —— 登录卡容器（stHorizontalBlock）
#   _SEL_COL2    —— 表单列（第 2 列）；所有 Streamlit 控件规则都加这个前缀

_SEL_HBLOCK = (
    'section[data-testid="stMain"] .block-container '
    '> div[data-testid="stVerticalBlock"] '
    '> div[data-testid="stHorizontalBlock"]:has(.chayuan-auth-brand)'
)
_SEL_COL2 = f'{_SEL_HBLOCK} > div[data-testid="column"]:nth-of-type(2)'

_LOGIN_CSS_TMPL = """
<style>
/* ===== 1. 隐藏 Streamlit 自带顶栏 / 侧边栏 / 页脚（登录页需要全屏） ===== */
header[data-testid="stHeader"],
div[data-testid="stToolbar"],
section[data-testid="stSidebar"],
div[data-testid="collapsedControl"],
footer {
    display: none !important;
}

/* ===== 2. 主区铺满整屏：全屏分屏容器 ===== */
section[data-testid="stMain"] {
    background: #0f172a;
    min-height: 100vh;
}
section[data-testid="stMain"] .block-container {
    padding: 0 !important;
    max-width: 100% !important;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    justify-content: center;
}

/* ===== 3. 登录分屏壳：满屏，无圆角卡片感 ===== */
__HBLOCK__ {
    width: 100vw !important;
    min-height: 100vh !important;
    margin: 0 !important;
    background: #0f172a;
    border: none !important;
    border-radius: 0;
    box-shadow: none;
    overflow: hidden !important;
    gap: 0 !important;
    flex-wrap: nowrap !important;
    align-items: stretch;
}
__HBLOCK__ > div[data-testid="column"] {
    padding: 0 !important;
    min-height: 100vh !important;
}
@media (max-width: 900px) {
    __HBLOCK__ {
        flex-direction: column !important;
    }
    __HBLOCK__ > div[data-testid="column"] {
        min-height: auto !important;
    }
    .chayuan-auth-brand { min-height: 280px !important; }
}

/* ===== 4. 左侧品牌区：深色企业风 ===== */
.chayuan-auth-brand {
    padding: 64px 56px 48px 56px;
    color: #ffffff;
    min-height: 100vh;
    background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
    display: flex;
    flex-direction: column;
    justify-content: space-between;
}

.chayuan-auth-brand .brand-row {
    display: flex;
    align-items: center;
    gap: 14px;
}
.chayuan-auth-brand .brand-logo {
    width: 44px; height: 44px; border-radius: 4px;
    background: #fff; padding: 4px;
    border: 1px solid rgba(148, 163, 184, 0.45);
}
.chayuan-auth-brand .brand-name {
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 0.2px;
}
.chayuan-auth-brand .brand-tag {
    font-size: 12px;
    color: #94a3b8;
    margin-top: 2px;
}
.chayuan-auth-brand .hero-title {
    font-size: 42px;
    font-weight: 800;
    line-height: 1.22;
    margin: 36px 0 14px 0;
    letter-spacing: 0.2px;
}
.chayuan-auth-brand .hero-sub {
    font-size: 16px;
    line-height: 1.75;
    color: #cbd5e1;
    max-width: 560px;
}
.chayuan-auth-brand .feature-list {
    list-style: none; padding: 0; margin: 24px 0 0 0;
    display: grid; gap: 12px;
}
.chayuan-auth-brand .feature-list li {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    font-size: 14px;
    line-height: 1.6;
    color: #e2e8f0;
}
.chayuan-auth-brand .feature-list .dot {
    width: 8px;
    height: 8px;
    border-radius: 999px;
    background: #38bdf8;
    border: none;
    display: inline-flex; align-items: center; justify-content: center;
    flex-shrink: 0;
    margin-top: 8px;
}
.chayuan-auth-brand .brand-foot {
    font-size: 12px;
    color: #94a3b8;
    margin-top: 36px;
}

/* ===== 5. 右侧登录区：白色面板 + 聚焦表单 ===== */
__COL2__ {
    padding: 0 72px !important;
    background: #f8fafc;
    display: flex !important;
    align-items: center !important;
}
__COL2__ > div[data-testid="stVerticalBlock"] {
    width: min(420px, 100%) !important;
    margin: 0 auto !important;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 14px;
    box-shadow:
        0 16px 36px rgba(15, 23, 42, 0.10),
        0 4px 10px rgba(15, 23, 42, 0.05);
    padding: 30px 28px 26px 28px;
}
__COL2__ .form-title {
    font-size: 30px;
    line-height: 1.25;
    font-weight: 700;
    color: #0f172a;
    margin: 0 0 8px 0;
}
__COL2__ .form-sub {
    font-size: 14px;
    color: #64748b;
    margin: 0 0 24px 0;
}

/* ===== 5.a 登录/注册 tab（segmented control 风格） =====
 *
 * st.tabs 默认会渲染一条横向滚动的 tab 条（下划线指示器），在登录卡片里显得
 * 过于"工具化"。这里把它 skin 成 **分段控件**（segmented control）：
 *   - 整条在卡片里居中，宽度跟内容（输入框）保持一致；
 *   - 未选中：浅灰底 + 中性文字；
 *   - 选中：  品牌蓝 + 白字 + 轻微阴影，突出当前状态；
 *   - 去掉 Streamlit 原生的底部下划线 / highlight 条，防止视觉冲突。
 *
 * 只有在 `_allow_registration()` 为 True 时才会渲染 st.tabs，所以这里的 CSS
 * 对"单登录表单"场景是零副作用（选择器里有 tab-list，没有就不匹配）。
 */
__COL2__ div[data-baseweb="tab-list"] {
    display: flex !important;
    justify-content: center !important;
    align-items: stretch !important;
    gap: 0 !important;
    padding: 4px !important;
    margin: 4px 0 18px 0 !important;
    background: #eef2f7 !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 10px !important;
    box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.04) !important;
    overflow: hidden !important;
}
__COL2__ div[data-baseweb="tab-list"] button[data-baseweb="tab"] {
    flex: 1 1 50% !important;
    height: 36px !important;
    min-width: 0 !important;
    padding: 0 16px !important;
    margin: 0 !important;
    border: none !important;
    background: transparent !important;
    color: #64748b !important;
    font-size: 14px !important;
    font-weight: 600 !important;
    letter-spacing: 0.3px;
    border-radius: 8px !important;
    box-shadow: none !important;
    transition: background 0.15s ease, color 0.15s ease,
                box-shadow 0.15s ease !important;
}
__COL2__ div[data-baseweb="tab-list"] button[data-baseweb="tab"]:hover {
    color: #0f172a !important;
    background: rgba(255, 255, 255, 0.55) !important;
}
__COL2__ div[data-baseweb="tab-list"] button[aria-selected="true"] {
    background: #1976d2 !important;
    color: #ffffff !important;
    box-shadow: 0 2px 6px rgba(25, 118, 210, 0.28) !important;
}
/* 干掉 Streamlit 默认那条底部 highlight 指示条，避免和分段控件风格冲突 */
__COL2__ div[data-baseweb="tab-highlight"],
__COL2__ div[data-baseweb="tab-border"] {
    display: none !important;
}
/* tab 面板：去掉默认 padding，内容直接承接表单 */
__COL2__ div[data-baseweb="tab-panel"] {
    padding: 0 !important;
}

/* Streamlit 控件皮肤（统一走第 2 列后代选择器） */
__COL2__ div[data-testid="stForm"] {
    border: none !important;
    padding: 0 !important;
    background: transparent !important;
    margin-top: 8px;
}
__COL2__ label {
    font-size: 14px !important;
    font-weight: 600 !important;
    color: #334155 !important;
}
__COL2__ input[type="text"],
__COL2__ input[type="password"] {
    border-radius: 6px !important;
    border: 1px solid #d1d5db !important;
    background: #ffffff !important;
    height: 42px !important;
    box-shadow: none !important;
    transition: border-color 0.12s ease, box-shadow 0.12s ease;
}
__COL2__ input[type="text"]:focus,
__COL2__ input[type="password"]:focus {
    border-color: #1976d2 !important;
    box-shadow: 0 0 0 2px rgba(25, 118, 210, 0.2) !important;
    outline: none !important;
}

/* 登录主按钮：240px × 36px，卡片底部居中 */
__COL2__ .stFormSubmitButton,
__COL2__ div[data-testid="stFormSubmitButton"] {
    display: flex !important;
    justify-content: center !important;
    align-items: center !important;
    align-content: center !important;
    margin-top: 10px;
    padding: 0 !important;
}
/* 兼容某些 Streamlit 版本里 form submit 容器的 emotion 哈希类。 */
__COL2__ .st-emotion-cache-0.e1f1d6gn0 {
    align-content: center !important;
}
__COL2__ .stFormSubmitButton > button,
__COL2__ div[data-testid="stFormSubmitButton"] > button,
__COL2__ div[data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"] {
    flex: 0 0 240px !important;
    width: 240px !important;
    max-width: 240px !important;
    min-width: 240px !important;
    height: 36px !important;
    min-height: 36px !important;
    margin: 0 !important;
    padding: 0 16px !important;
    border-radius: 6px !important;
    font-size: 14px !important;
    font-weight: 600 !important;
    letter-spacing: 0.3px;
    background: #1976d2 !important;
    color: #ffffff !important;
    border: 1px solid #1976d2 !important;
    box-shadow: none !important;
    transition: background 0.12s ease, border-color 0.12s ease !important;
}
__COL2__ .stButton > button:hover,
__COL2__ .stFormSubmitButton > button:hover,
__COL2__ div[data-testid="stFormSubmitButton"] > button:hover {
    background: #115293 !important;
    border-color: #115293 !important;
    transform: none !important;
    box-shadow: none !important;
}
__COL2__ .stButton > button:active,
__COL2__ .stFormSubmitButton > button:active,
__COL2__ div[data-testid="stFormSubmitButton"] > button:active {
    background: #0e4279 !important;
    transform: none !important;
}

.chayuan-auth-foot {
    margin-top: 22px;
    display: flex; justify-content: space-between; align-items: center;
    font-size: 13px; color: #64748b;
}
.chayuan-auth-foot a {
    color: #1976d2; text-decoration: none; font-weight: 600;
}
.chayuan-auth-foot a:hover { text-decoration: underline; }

/* 底部版权条 */
.chayuan-auth-copyright {
    text-align: center;
    color: #94a3b8;
    font-size: 12px;
    margin: 0 auto 20px auto;
}

</style>
"""

# 模板里用 ``__COL2__`` 作为第 2 列（表单列）选择器的占位符，这里一次性替换；
# 拼接后的 _LOGIN_CSS 即最终可直接 st.markdown 渲染的 `<style>` 块。
_LOGIN_CSS = _LOGIN_CSS_TMPL.replace("__COL2__", _SEL_COL2)
_LOGIN_CSS = _LOGIN_CSS.replace("__HBLOCK__", _SEL_HBLOCK)


def _render_brand_panel() -> None:
    """左侧品牌区（HTML-only，不含 Streamlit 控件）。"""
    try:
        from chayuan.webui_pages.utils import get_img_base64
        logo_uri = get_img_base64("logo.png")
    except Exception:  # noqa: BLE001
        logo_uri = ""

    from chayuan import __version__ as _ver

    st.markdown(
        f"""
        <div class="chayuan-auth-brand">
            <div>
                <div class="brand-row">
                    {'<img class="brand-logo" src="' + logo_uri + '" alt="logo"/>' if logo_uri else ''}
                    <div>
                        <div class="brand-name">察元 AI 助手</div>
                        <div class="brand-tag">Chayuan AI · v{_ver}</div>
                    </div>
                </div>
                <div class="hero-title">企业级 AI 中枢，<br/>让知识与模型协同工作</div>
                <div class="hero-sub">
                    面向团队协作场景的一体化平台，统一接入模型能力、知识资产与工具系统，
                    在安全可控前提下提升业务响应效率。
                </div>
                <ul class="feature-list">
                    <li><span class="dot"></span><span><b>模型统一编排</b>　多模型路由与策略切换</span></li>
                    <li><span class="dot"></span><span><b>知识资产连接</b>　企业文档检索与问答增强</span></li>
                    <li><span class="dot"></span><span><b>系统能力集成</b>　MCP 与业务工具快速打通</span></li>
                    <li><span class="dot"></span><span><b>权限与审计</b>　角色隔离、行为可追踪</span></li>
                </ul>
            </div>
            <div class="brand-foot">© 北京智灵鸟科技中心</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# 注：旧版本的"忘记密码"折叠区已下线。
# 内网部署没有邮件 / 短信通道，密码重置走管理员 CLI（``chayuan auth-user reset-password``），
# 不在登录页给出该入口可以避免视觉噪声 + 弱化"前端可改密码"的错觉。需要时见 README。


def _render_login_form() -> None:
    """登录区入口。

    根据后端配置 ``AUTH_ALLOW_REGISTRATION`` 决定布局：
    - 开启自助注册：显示**登录 / 注册**两个 tab，同一卡片内切换（见
      ``_render_register_form_inner``）；tab 样式在 ``_LOGIN_CSS_TMPL`` 里
      被 skin 成分段控件；
    - 关闭自助注册：直接渲染登录表单，整个卡片不出现任何"注册"字样，避免
      误导用户以为可以自助开户。
    """
    if _allow_registration():
        tab_login, tab_register = st.tabs(["登录", "注册"])
        with tab_login:
            _render_login_inner()
        with tab_register:
            _render_register_form_inner()
    else:
        _render_login_inner()


def _render_login_inner() -> None:
    """登录表单本体（仅表单部分，不含 tab 切换 / 标题）。"""
    with st.form("login_form_v2", clear_on_submit=False):
        uname = st.text_input("用户名", key="_login_u", placeholder="请输入用户名")
        pwd = st.text_input(
            "密码", type="password", key="_login_p",
            placeholder="请输入密码",
        )

        submit = st.form_submit_button(
            "立即登录", type="primary", use_container_width=True,
        )

    if submit:
        if not uname or not pwd:
            st.warning("请填写用户名与密码。")
            return
        pack = _do_login(uname.strip(), pwd)
        if pack:
            # 登录成功三步：
            #   1. 填 session_state —— 当前 WebSocket 会话直接可用
            #   2. 把 refresh_token 写进 URL query —— F5 刷新时 URL 会带回 Python 端
            #   3. st.rerun() —— require_login 的快路径发现 session_state 有 token 就放行
            # 之前用 iframe + JS reload 绕一大圈，每个环节都可能被 sandbox / DOM 撕扯打断；
            # 改走 "Python 原生写 qp + rerun" 这条路后，整条链完全不依赖浏览器端 JS，
            # 100% Streamlit 可控，登录后进不了主界面的 bug 再也复现不出来。
            _apply_auth(pack)
            rt = pack.get("refresh_token") or ""
            if rt:
                _write_qp(_RT_QP, rt)
            logger.info(
                "[login] user=%s role=%s logged in",
                (pack.get("user") or {}).get("username"),
                (pack.get("user") or {}).get("role"),
            )
            st.rerun()


def _is_valid_username(name: str) -> bool:
    """用户名格式校验：3-32 位字母 / 数字 / 下划线。

    不引入 ``re`` 只是想让本文件的依赖足够轻 —— 手写 ASCII 判断更快、也没有正则转义坑。
    """
    if not (3 <= len(name) <= 32):
        return False
    for ch in name:
        if not (ch.isascii() and (ch.isalnum() or ch == "_")):
            return False
    return True


def _is_plausible_email(email: str) -> bool:
    """非严格邮箱校验：只看"必须有且只有一个 @、两端非空、右侧含点"。

    行业内内网邮箱格式五花八门（含中文、含 +tag 等），过严的正则反而会把合法邮箱误杀。
    这里只做"人类一眼看得出是个邮箱"的程度，后端 ``/auth/register`` 会做最终裁决。
    """
    if email.count("@") != 1:
        return False
    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain:
        return False
    return True


def _render_register_form_inner() -> None:
    """注册表单本体（仅表单部分，不含 tab 切换 / 标题）。

    后端 ``POST /auth/register`` 的合约：
      - ``AUTH_ALLOW_REGISTRATION=false`` 时直接 403 —— 前端已经用
        ``_allow_registration()`` 先行收拢入口，这里属于双保险；
      - 用户名需 3-32 位字母数字下划线，后端会再校验一遍，前端先过一遍是省一次 RTT；
      - 密码 ≥ 6 位，也由后端再校验；邮箱可选。

    注册成功**不**自动登录（见 :func:`_do_register` 注释），只提示用户切到登录 tab，
    设计动机：减少"注册 → 登录"两步的状态耦合，注册失败也不会留下半拉 session 残渣。
    """
    with st.form("register_form_v2", clear_on_submit=False):
        uname = st.text_input(
            "用户名", key="_reg_u", placeholder="3-32 位字母 / 数字 / 下划线",
        )
        email = st.text_input(
            "邮箱（可选）", key="_reg_e", placeholder="留空也可以",
        )
        pwd = st.text_input(
            "密码", type="password", key="_reg_p", placeholder="至少 6 位",
        )
        pwd2 = st.text_input(
            "确认密码", type="password", key="_reg_p2", placeholder="再输一次",
        )
        submit = st.form_submit_button(
            "注册账号", type="primary", use_container_width=True,
        )

    if not submit:
        return

    uname_s = (uname or "").strip()
    email_s = (email or "").strip()

    if not uname_s or not pwd:
        st.warning("请填写用户名与密码。")
        return
    if not _is_valid_username(uname_s):
        st.warning("用户名格式不合法：需 3-32 位字母 / 数字 / 下划线。")
        return
    if len(pwd) < 6:
        st.warning("密码长度需至少 6 位。")
        return
    if pwd != pwd2:
        st.warning("两次输入的密码不一致。")
        return
    if email_s and not _is_plausible_email(email_s):
        st.warning("邮箱格式不正确，留空或填写标准邮箱。")
        return

    if _do_register(uname_s, pwd, email_s or None):
        st.success(f"账号「{uname_s}」已注册成功，请切换到「登录」页签用新账号登录。")


def _render_login_ui() -> None:
    """未登录时的全屏分屏登录页。"""
    from chayuan import __version__ as _ver

    st.markdown(_LOGIN_CSS, unsafe_allow_html=True)

    # 左右双栏：左 = 纯 HTML 品牌区；右 = Streamlit 表单区
    # 由 _LOGIN_CSS 把这个 HorizontalBlock 本身 skin 成卡片登录卡（centered card）
    col_brand, col_form = st.columns([1.05, 1], gap="small")
    with col_brand:
        _render_brand_panel()
    with col_form:
        # 表单区的视觉样式通过 CSS `__COL2__` 选择器直接命中第 2 列；
        # 不再用 st.markdown 的 <div class="chayuan-auth-form"> 包裹，因为
        # Streamlit 把每个 st.markdown 渲染成独立兄弟节点，包裹不生效。
        _render_login_form()

    st.markdown(
        f'<div class="chayuan-auth-copyright">'
        f'察元 AI 助手 · v{_ver} · © 北京智灵鸟科技中心'
        f'</div>',
        unsafe_allow_html=True,
    )


def require_login() -> None:
    """登录门禁（跨刷新保活版）。

    流程：
      1. 若 ``session_state`` 已有 access_token —— 已登录，直接 return。
      2. 否则**同步**调用 ``/auth/refresh``，拿 URL query 里 ``?_auth_rt=<rt>`` 换新
         token 对；换成功就填 session_state 直接放行，走主界面。
      3. 换不到 & ``AUTH_REQUIRED=true`` —— 渲染登录页并 ``st.stop()``。
      4. 换不到 & ``AUTH_REQUIRED=false`` —— 保持游客态，return。

    关键不变量：
      - AUTH_REQUIRED=true 下任何情况都不能把"未登录的主界面"泄露给用户 —— 所以
        登录页那条路径一定 ``st.stop()`` 兜底。
      - 所有凭证解析 / 网络调用都是 Python 端同步进行的，没有"浏览器 JS 往回吐信号"
        的异步依赖，排障只看后端日志就够了。
    """
    # 已登录 —— 最快路径
    if st.session_state.get("access_token"):
        return

    auth_req = _auth_required()

    # URL 上带 refresh_token（登录后 / F5 后）时，不管 auth_req 是否 true 都要尝试恢复
    # —— 游客模式下用户可能本来就已登录，主动放弃登录态反而奇怪。
    if _read_qp(_RT_QP):
        if _try_restore_from_url():
            return

    if not auth_req:
        return

    _render_login_ui()
    st.stop()


def is_logged_in() -> bool:
    return bool(st.session_state.get("access_token"))


def auth_enabled() -> bool:
    return _auth_required()
