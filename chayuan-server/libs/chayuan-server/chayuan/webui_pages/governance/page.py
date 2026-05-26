"""治理管理面板（Streamlit）。

包含 5 个 Tab：
1. 策略（CRUD）
2. 血缘记录
3. 对象排行（哪些表/列/文件被 AI 读得最多）
4. 用量与配额
5. PII 调试扫描
"""
from __future__ import annotations

import json
from typing import Dict, List

import pandas as pd
import streamlit as st

from chayuan.webui_pages.utils import ApiRequest


def _api_get(api: ApiRequest, path: str, params: Dict = None) -> Dict:
    try:
        resp = api.get(path, params=params)
        return api._get_response_value(resp, as_json=True) or {}
    except Exception as e:  # noqa: BLE001
        return {"code": -1, "msg": str(e)}


def _api_post(api: ApiRequest, path: str, json_body: Dict) -> Dict:
    try:
        resp = api.post(path, json=json_body)
        return api._get_response_value(resp, as_json=True) or {}
    except Exception as e:  # noqa: BLE001
        return {"code": -1, "msg": str(e)}


def _render_policy_tab(api: ApiRequest) -> None:
    st.caption("按 **scope** 定义策略：`user:<id>` / `role:<name>` / `global`。生效优先级：user > role > global。")
    data = _api_get(api, "/governance/policy")
    if data.get("code") == 0:
        rows = data.get("data") or []
        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("暂无策略（走默认：不限额 + 松散脱敏）")
    else:
        st.error(data.get("msg") or "加载失败")

    st.divider()
    st.markdown("#### 新建 / 更新策略")
    with st.form("gov_policy", clear_on_submit=False):
        c1, c2, c3 = st.columns([2, 1, 1])
        scope = c1.text_input("scope", value="role:analyst",
                               help="如 user:123 / role:analyst / global")
        qps = c2.number_input("QPS 上限（-1 不限）", -1, 1000, value=-1)
        budget = c3.number_input("日 token 预算（-1 不限）", -1, 100_000_000, value=-1)
        masking = st.selectbox("脱敏等级", ["off", "loose", "strict"], index=1)
        modes = st.text_input("允许的 mode（逗号分隔；空=不限）",
                                value="llm,kb,multi_source,agent")
        if st.form_submit_button("保存", type="primary", use_container_width=True):
            allowed_modes = [m.strip() for m in modes.split(",") if m.strip()]
            ret = _api_post(api, "/governance/policy", {
                "scope": scope, "daily_token_budget": int(budget), "qps": int(qps),
                "masking_level": masking, "allowed_modes": allowed_modes,
                "extra": {}, "enabled": 1,
            })
            if ret.get("code") == 0:
                st.success("保存成功")
                st.rerun()
            else:
                st.error(ret.get("msg") or "保存失败")


def _render_lineage_tab(api: ApiRequest) -> None:
    c1, c2, c3 = st.columns([1, 1, 1])
    uid = c1.number_input("用户 id（0 = 全部 / 非 admin 会自动回落到自己）", 0, 100000, value=0)
    mode = c2.selectbox("mode", ["", "llm", "kb", "file", "search_engine",
                                   "multi_source", "agent", "vision"])
    hours = c3.number_input("最近多少小时", 1, 24 * 30, value=24)
    params = {"hours": int(hours)}
    if uid:
        params["target_user_id"] = int(uid)
    if mode:
        params["mode"] = mode
    data = _api_get(api, "/governance/lineage", params=params)
    if data.get("code") == 0:
        rows = data.get("data") or []
        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
            st.caption(f"共 {len(rows)} 条记录")
        else:
            st.info("当前范围内无记录")


def _render_top_objects_tab(api: ApiRequest) -> None:
    c1, c2 = st.columns([1, 1])
    object_type = c1.selectbox("对象类型", ["", "table", "column", "file",
                                             "collection", "index"])
    hours = c2.number_input("最近多少小时", 1, 24 * 90, value=168)
    params = {"hours": int(hours), "limit": 50}
    if object_type:
        params["object_type"] = object_type
    data = _api_get(api, "/governance/lineage/top_objects", params=params)
    if data.get("code") == 0:
        rows = data.get("data") or []
        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("暂无数据")


def _render_usage_tab(api: ApiRequest) -> None:
    data = _api_get(api, "/governance/usage/today")
    if data.get("code") == 0:
        d = data.get("data") or {}
        c1, c2, c3 = st.columns(3)
        c1.metric("今日已用 token", d.get("used", 0))
        budget = d.get("budget", -1)
        c2.metric("今日预算", "不限" if budget < 0 else str(budget))
        remaining = d.get("remaining", -1)
        c3.metric("剩余", "-" if remaining < 0 else str(remaining))

    st.divider()
    st.markdown("#### 当前生效策略（本人）")
    ep = _api_get(api, "/governance/policy/effective")
    if ep.get("code") == 0:
        st.json(ep.get("data") or {})


def _render_pii_tab(api: ApiRequest) -> None:
    st.caption("输入任意文本 → 调用后端 PII 扫描 + 脱敏预览。")
    text = st.text_area("待扫描文本", value="联系：13812345678，邮箱 alice@example.com，身份证 11010119900101051X",
                         height=120)
    c1, c2 = st.columns([1, 1])
    role = c1.selectbox("期望脱敏等级（用户角色）",
                         ["admin", "analyst", "user", "guest"], index=2)
    use_presidio = c2.checkbox("启用 Presidio（需 pip install presidio-analyzer）", value=False)
    if st.button("扫描", type="primary"):
        ret = _api_post(api, "/governance/pii/scan", {
            "text": text, "enable_presidio": bool(use_presidio),
            "user_role": role,
        })
        if ret.get("code") == 0:
            d = ret.get("data") or {}
            st.markdown("**识别到的实体：**")
            st.json(d.get("entities") or [])
            st.markdown("**脱敏后预览：**")
            st.code(d.get("masked") or "", language="text")
        else:
            st.error(ret.get("msg") or "失败")


def governance_page(api: ApiRequest, is_lite: bool = False) -> None:
    st.markdown("## 数据治理")
    st.caption("策略 / 血缘 / 用量 / PII — 按 P1-9 设计，所有能力由 ChatGraph 自动落点。")
    tabs = st.tabs(["📜 策略", "🧭 血缘记录", "🏆 对象排行", "📊 用量 / 配额", "🔎 PII 扫描"])
    with tabs[0]:
        _render_policy_tab(api)
    with tabs[1]:
        _render_lineage_tab(api)
    with tabs[2]:
        _render_top_objects_tab(api)
    with tabs[3]:
        _render_usage_tab(api)
    with tabs[4]:
        _render_pii_tab(api)
