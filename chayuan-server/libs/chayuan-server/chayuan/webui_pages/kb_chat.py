from datetime import datetime
import uuid
from typing import List, Dict

import openai
import streamlit as st
import streamlit_antd_components as sac
from streamlit_chatbox import *
from streamlit_extras.bottom_container import bottom

from chayuan.settings import Settings
from chayuan.server.knowledge_base.utils import LOADER_DICT
from chayuan.server.utils import get_config_models, get_config_platforms, get_default_llm, api_address
from chayuan.webui_pages.dialogue.dialogue import (save_session, restore_session, rerun,
                                                    get_messages_history, upload_temp_docs,
                                                    add_conv, del_conv, clear_conv,
                                                    chat_box)
from chayuan.webui_pages.utils import *


# 复用 dialogue.dialogue 里的全局 chat_box（get_messages_history 内部引用的是该模块的
# chatBox 单例）。以前这里自建一个 ChatBox() 会导致：
#   - 本页显示的消息记录属于 kb_chat 自己的 chat_box
#   - get_messages_history() 读的是 dialogue 模块的 chat_box
# 两个实例各自独立 → 发给后端的 history 永远和界面看到的对不上，RAG 像「说话接不上」。


def init_widgets():
    st.session_state.setdefault("history_len", Settings.model_settings.HISTORY_LEN)
    st.session_state.setdefault("selected_kb", Settings.kb_settings.DEFAULT_KNOWLEDGE_BASE)
    st.session_state.setdefault("kb_top_k", Settings.kb_settings.VECTOR_SEARCH_TOP_K)
    st.session_state.setdefault("se_top_k", Settings.kb_settings.SEARCH_ENGINE_TOP_K)
    st.session_state.setdefault("score_threshold", Settings.kb_settings.SCORE_THRESHOLD)
    st.session_state.setdefault("search_engine", Settings.kb_settings.DEFAULT_SEARCH_ENGINE)
    st.session_state.setdefault("return_direct", False)
    st.session_state.setdefault("cur_conv_name", chat_box.cur_chat_name)
    st.session_state.setdefault("last_conv_name", chat_box.cur_chat_name)
    st.session_state.setdefault("file_chat_id", None)
    # 把 history_len 同步写入 chat_box.context，否则 get_messages_history(ctx.get("history_len",0))
    # 永远读到 0，RAG 历史长期为空（number_input 只写 session_state，不会自动同步到 context）。
    try:
        chat_box.context.setdefault("history_len", st.session_state["history_len"])
    except Exception:
        pass


def kb_chat(api: ApiRequest):
    init_widgets()

    # sac on_change callbacks not working since st>=1.34
    if st.session_state.cur_conv_name != st.session_state.last_conv_name:
        save_session(st.session_state.last_conv_name)
        restore_session(st.session_state.cur_conv_name)
        st.session_state.last_conv_name = st.session_state.cur_conv_name

    # st.write(chat_box.cur_chat_name)
    # st.write(st.session_state)

    @st.experimental_dialog("模型配置", width="large")
    def llm_model_setting():
        # 对话框必须用独立 widget key（experimental_dialog 按 fragment 运行，与主页共用 key 时
        # session 与控件返回值易不同步）；确定时用 session 写回 context 并 rerun(save_session)。
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
            _lm = st.session_state.get("dlg_llm_model") or get_default_llm()
            c["llm_model"] = str(_lm).strip() or get_default_llm()
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

    # 配置参数
    with st.sidebar:
        tabs = st.tabs(["RAG 配置", "会话设置"])
        with tabs[0]:
            dialogue_modes = ["知识库问答",
                              "多源问答",
                              "文件对话",
                              "搜索引擎问答",
                              ]
            dialogue_mode = st.selectbox("请选择对话模式：",
                                         dialogue_modes,
                                         key="dialogue_mode",
                                         help="多源问答支持同时选择多个向量知识库 + 结构化/半结构化数据源并行检索",
                                         )
            placeholder = st.empty()
            st.divider()
            # prompt_templates_kb_list = list(Settings.prompt_settings.rag)
            # prompt_name = st.selectbox(
            #     "请选择Prompt模板：",
            #     prompt_templates_kb_list,
            #     key="prompt_name",
            # )
            prompt_name="default"
            history_len = st.number_input("历史对话轮数：", 0, 20, key="history_len")
            kb_top_k = st.number_input("匹配知识条数：", 1, 20, key="kb_top_k")
            ## Bge 模型会超过1
            score_threshold = st.slider("知识匹配分数阈值：", 0.0, 2.0, step=0.01, key="score_threshold")
            return_direct = st.checkbox("仅返回检索结果", key="return_direct")



            def on_kb_change():
                st.toast(f"已加载知识库： {st.session_state.selected_kb}")

            with placeholder.container():
                if dialogue_mode == "知识库问答":
                    kb_list = [x["kb_name"] for x in api.list_knowledge_bases()]
                    # Widget key "selected_kb" persists; if that KB was removed, selectbox
                    # raises ValueError when serializing state (e.g. default "samples" missing).
                    cur_kb = st.session_state.get("selected_kb")
                    if kb_list and cur_kb not in kb_list:
                        default = Settings.kb_settings.DEFAULT_KNOWLEDGE_BASE
                        st.session_state["selected_kb"] = (
                            default if default in kb_list else kb_list[0]
                        )
                    if kb_list:
                        selected_kb = st.selectbox(
                            "请选择知识库：",
                            kb_list,
                            on_change=on_kb_change,
                            key="selected_kb",
                        )
                    else:
                        st.warning("暂无可用知识库，请先在知识库管理中创建或初始化。")
                        selected_kb = None
                elif dialogue_mode == "多源问答":
                    # 列出用户可访问的所有知识源（向量 / SQL / Mongo / ES / 图像）
                    try:
                        all_sources = api.ks_list() or []
                    except Exception as _e:  # noqa: BLE001
                        st.error(f"加载知识源列表失败：{_e}")
                        all_sources = []
                    if not all_sources:
                        st.info("暂无可用知识源，请先在『数据源管理』中接入。")
                    _kind_icon = {
                        "vector": "📚", "sql": "🗄️", "mongo": "🍃",
                        "es": "🔍", "image": "🖼️",
                    }
                    # 以图搜图入口（需要至少一个 image 源）
                    img_sources = [s for s in all_sources if s.get("kind") == "image"]
                    if img_sources:
                        with st.expander("🖼️ 以图搜图（会把结果挂到当前选中的图像源）", expanded=False):
                            iq = st.file_uploader("查询图", ["png", "jpg", "jpeg", "webp"],
                                                     key="dlg_iq_file")
                            tgt = st.selectbox(
                                "在哪个图像源里搜",
                                [f"{s['display_name']} (id={s['id']})" for s in img_sources],
                                key="dlg_iq_target",
                            )
                            if iq is not None and st.button("搜索", key="dlg_iq_btn"):
                                sid = int(img_sources[
                                    [f"{s['display_name']} (id={s['id']})" for s in img_sources].index(tgt)
                                ]["id"])
                                with st.spinner("查找中..."):
                                    hits = api.image_search_by_image(
                                        sid, iq.getvalue(), top_k=5,
                                    )
                                st.write(f"命中 {len(hits)} 张：")
                                for h in hits or []:
                                    cite = h.get("citation") or {}
                                    st.markdown(f"**{cite.get('title')}**  score={h.get('score'):.4f}")
                                    dl = cite.get("preview_url") or cite.get("download_url") or ""
                                    if dl:
                                        st.markdown(f"[预览]({api.base_url}{dl})")

                    def _opt_label(s):
                        k = s.get("kind", "vector")
                        ic = _kind_icon.get(k, "•")
                        return f"{ic} {s.get('display_name') or s.get('name')} ({k}, id={s.get('id')})"

                    opts = {_opt_label(s): int(s["id"]) for s in all_sources}
                    select_all = st.checkbox(
                        "全选（并行检索我所有可访问的知识源）",
                        key="ks_select_all",
                    )
                    default_sel = list(opts.keys()) if select_all else []
                    chosen = st.multiselect(
                        "选择要参与并行检索的知识源（多选）",
                        list(opts),
                        default=default_sel,
                        disabled=select_all,
                        key="ks_selected_labels",
                    )
                    # 持久到 session 供 prompt 提交时使用
                    if select_all:
                        st.session_state["ks_source_ids"] = list(opts.values())
                    else:
                        st.session_state["ks_source_ids"] = [opts[l] for l in chosen]
                    st.session_state["ks_select_all_flag"] = bool(select_all)
                elif dialogue_mode == "文件对话":
                    files = st.file_uploader("上传知识文件：",
                                            [i for ls in LOADER_DICT.values() for i in ls],
                                            accept_multiple_files=True,
                                            )
                    if st.button("开始上传", disabled=len(files) == 0):
                        st.session_state["file_chat_id"] = upload_temp_docs(files, api)
                elif dialogue_mode == "搜索引擎问答":
                    search_engine_list = list(Settings.tool_settings.search_internet["search_engine_config"])
                    search_engine = st.selectbox(
                        label="请选择搜索引擎",
                        options=search_engine_list,
                        key="search_engine",
                    )

        with tabs[1]:
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
                on_change=on_conv_change,
            )
            chat_box.use_chat_name(conversation_name)
            _c = chat_box.context
            _c.setdefault("uid", uuid.uuid4().hex)
            _c.setdefault("file_chat_id", None)
            _c.setdefault("llm_model", get_default_llm())
            _c.setdefault("temperature", Settings.model_settings.TEMPERATURE)
            conversation_id = _c["uid"]
            if cols[0].button("新建", on_click=add_conv):
                ...
            if cols[1].button("重命名"):
                rename_conversation()
            if cols[2].button("删除", on_click=del_conv):
                ...
        ctx = chat_box.context

    # Display chat messages from history on app rerun
    chat_box.output_messages()
    chat_input_placeholder = "请输入对话内容，换行请使用Shift+Enter。"

    # chat input
    with bottom():
        cols = st.columns([1, 0.2, 15,  1])
        if cols[0].button(":gear:", help="模型配置"):
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
        if cols[-1].button(":wastebasket:", help="清空对话"):
            chat_box.reset_history()
            rerun()
        # with cols[1]:
        #     mic_audio = audio_recorder("", icon_size="2x", key="mic_audio")
        prompt = cols[2].chat_input(chat_input_placeholder, key="prompt")
    if prompt:
        # 真值在 chat_history[会话][context]；顶层 st.session_state["llm_model"] 可能被 save_session 写脏，优先读嵌套并回写顶层
        _live = chat_box.context
        llm_model = (
            str(_live.get("llm_model") or "").strip()
            or str(st.session_state.get("llm_model") or "").strip()
            or get_default_llm()
        )
        _live["llm_model"] = llm_model
        st.session_state["llm_model"] = llm_model
        # history_len 可能只写进 session_state 没同步到 context，这里双通道兜底
        _hist_len = ctx.get("history_len")
        if _hist_len is None:
            _hist_len = st.session_state.get("history_len", 0)
        history = get_messages_history(int(_hist_len or 0))
        messages = history + [{"role": "user", "content": prompt}]
        chat_box.user_say(prompt)

        extra_body = dict(
            top_k=kb_top_k,
            score_threshold=score_threshold,
            temperature=ctx.get("temperature"),
            prompt_name=prompt_name,
            return_direct=return_direct,
            # 与 dialogue.dialogue 的 extra_body 字段对齐：后端 kb_routes 解析这些扩展字段
            # 做会话缓存、权限隔离、selected_llm 路由兜底
            conversation_id=conversation_id,
            _selected_llm_for_chain=llm_model,
        )
    
        api_url = api_address(is_public=True)
        # ---------------- 多源并行检索 + RAG 对话 ----------------
        if dialogue_mode == "多源问答":
            source_ids = list(st.session_state.get("ks_source_ids") or [])
            select_all_flag = bool(st.session_state.get("ks_select_all_flag", False))
            if not select_all_flag and not source_ids:
                st.error("请至少选择一个知识源，或勾选『全选』")
                st.stop()

            # chat_box 占位：
            #   element_index=0 → 出处汇总区（stream 到 SSE 最终 final 后一次性写入）
            #   element_index=1 → LLM 答复正文
            chat_box.ai_say([
                Markdown("...", in_expander=True, title="检索来源与生成的查询",
                         state="running", expanded=return_direct),
                "正在对选中的多个知识源并行检索 ...",
            ])

            # 本分支独立维护答复文本，避免沿用下方旧分支里尚未初始化的 text
            text = ""
            # 用 st.status 分步展示"思考过程"（SQL/DSL 生成 + 每个源用时）
            sources_map: Dict[int, str] = {}
            per_source_status: Dict[int, Dict] = {}
            try:
                with st.status("思考中（并行检索多源）", expanded=True) as status_area:
                    for evt in api.ks_multi_chat_stream(
                        query=prompt,
                        source_ids=source_ids,
                        select_all=select_all_flag,
                        top_k=kb_top_k,
                        history=[{"role": m.get("role", "user"), "content": m.get("content", "")}
                                 for m in history],
                        model=llm_model,
                        temperature=float(ctx.get("temperature") or Settings.model_settings.TEMPERATURE),
                        prompt_name=prompt_name,
                    ):
                        ev = evt.get("event") or "message"
                        data = evt.get("data")
                        if not isinstance(data, dict):
                            continue
                        if ev == "source_started":
                            sid = int(data.get("source_id") or 0)
                            sources_map[sid] = data.get("name") or f"source#{sid}"
                            per_source_status[sid] = {"kind": data.get("kind"), "state": "searching"}
                            status_area.update(label=f"正在查询 {sources_map[sid]} ...")
                            st.write(f"▶ 开始检索 **{sources_map[sid]}** ({data.get('kind')})")
                        elif ev == "source_query":
                            sid = int(data.get("source_id") or 0)
                            gq = data.get("generated_query") or ""
                            with st.expander(f"{sources_map.get(sid, sid)}：生成的查询", expanded=False):
                                st.code(gq, language="sql" if per_source_status.get(sid, {}).get("kind") == "sql" else "json")
                        elif ev == "source_chunks":
                            sid = int(data.get("source_id") or 0)
                            elapsed = int(data.get("elapsed_ms") or 0)
                            per_source_status.setdefault(sid, {})["state"] = "done"
                            per_source_status[sid]["elapsed_ms"] = elapsed
                            st.write(f"✅ **{sources_map.get(sid, sid)}** 完成（{elapsed} ms）")
                        elif ev == "source_failed":
                            sid = int(data.get("source_id") or 0)
                            per_source_status.setdefault(sid, {})["state"] = "failed"
                            per_source_status[sid]["error"] = data.get("error")
                            st.write(f"❌ **{sources_map.get(sid, sid)}** 失败：{data.get('error')}")
                        elif ev == "aggregating":
                            status_area.update(label="正在汇总与重排 ...")
                        elif ev == "sources":
                            # 出处数据到齐 → 写入 expander + 在 status 区落真·下载按钮
                            aggregated = data.get("aggregated") or []
                            lines = []
                            # 留一个 token 安全的 session 容器供下文的 download_button 使用
                            st.session_state.setdefault("_ks_cited", {})
                            for i, ch in enumerate(aggregated):
                                cite = ch.get("citation") or {}
                                title = cite.get("title") or f"source#{ch.get('source_id')}"
                                kind = ch.get("source_kind") or ""
                                score = ch.get("score")
                                head = f"**[{i+1}] {title}** ({kind}, score={score})"
                                dl = cite.get("download_url") or ""
                                if dl:
                                    # 向量源：附带 access_token（浏览器点 <a> 无 Auth 头）
                                    token = st.session_state.get("access_token") or ""
                                    sep = "&" if "?" in dl else "?"
                                    dl_full = f"{api_url}{dl}{sep}token={token}" if token else f"{api_url}{dl}"
                                    lines.append(f"{head}  [📥 下载原文]({dl_full})")
                                else:
                                    lines.append(head)
                                content_preview = (ch.get("content") or "")
                                if content_preview:
                                    lines.append(f"> {content_preview[:600]}{'...' if len(content_preview) > 600 else ''}")
                                lines.append("")
                                # 结构化源：在 status_area 落 download_button（CSV/JSON）
                                if kind in ("sql", "mongo", "es"):
                                    sid = int(ch.get("source_id") or 0)
                                    # 避免每次 rerun 都重新请求后端：拉一次放进 session
                                    cache_key = f"dl_{sid}_{hash(prompt)}"
                                    if cache_key not in st.session_state["_ks_cited"]:
                                        try:
                                            csv_bytes = api.ks_download_result(
                                                source_id=sid, query=prompt,
                                                format="csv", top_k=200,
                                            )
                                            st.session_state["_ks_cited"][cache_key] = csv_bytes
                                        except Exception:  # noqa: BLE001
                                            st.session_state["_ks_cited"][cache_key] = b""
                                    csv_bytes = st.session_state["_ks_cited"].get(cache_key) or b""
                                    if csv_bytes:
                                        st.download_button(
                                            label=f"📊 下载 [{i+1}] {title} CSV",
                                            data=csv_bytes,
                                            file_name=f"source_{sid}_{i+1}.csv",
                                            mime="text/csv",
                                            key=f"dl_btn_{sid}_{i}_{hash(prompt)}",
                                        )
                            chat_box.update_msg(
                                "\n".join(lines) if lines else "未命中任何知识源。",
                                element_index=0, streaming=False, state="complete",
                            )
                        elif ev == "token":
                            delta = data.get("delta") or ""
                            text += delta
                            chat_box.update_msg(text.replace("\n", "\n\n"),
                                                 element_index=1, streaming=True)
                        elif ev == "answer":
                            text = data.get("content") or ""
                            chat_box.update_msg(text, element_index=1, streaming=False)
                        elif ev == "done":
                            chat_box.update_msg(text, element_index=1, streaming=False)
                            status_area.update(label="完成", state="complete")
                        elif ev == "stage":
                            status_area.update(label=f"阶段：{data.get('stage', '')}")
            except Exception as e:  # noqa: BLE001
                body = getattr(e, "body", None)
                if body:
                    st.error(body)
                else:
                    st.error(f"多源问答失败（{type(e).__name__}）：{e}")
            # 多源分支结束：不落入下方旧分支
        elif dialogue_mode == "知识库问答":
            if not selected_kb:
                st.error("暂无可用知识库，无法对话。")
                st.stop()
            client = openai.Client(
                base_url=f"{api_url}/knowledge_base/local_kb/{selected_kb}",
                api_key=st.session_state.get("access_token") or "NONE",
            )
            chat_box.ai_say([
                Markdown("...", in_expander=True, title="知识库匹配结果", state="running", expanded=return_direct),
                f"正在查询知识库 `{selected_kb}` ...",
            ])
        elif dialogue_mode == "文件对话":
            if st.session_state.get("file_chat_id") is None:
                st.error("请先上传文件再进行对话")
                st.stop()
            knowledge_id=st.session_state.get("file_chat_id")
            client = openai.Client(
                base_url=f"{api_url}/knowledge_base/temp_kb/{knowledge_id}",
                api_key=st.session_state.get("access_token") or "NONE",
            )
            chat_box.ai_say([
                Markdown("...", in_expander=True, title="知识库匹配结果", state="running", expanded=return_direct),
                f"正在查询文件 `{st.session_state.get('file_chat_id')}` ...",
            ])
        else:
            client = openai.Client(
                base_url=f"{api_url}/knowledge_base/search_engine/{search_engine}",
                api_key=st.session_state.get("access_token") or "NONE",
            )
            chat_box.ai_say([
                Markdown("...", in_expander=True, title="知识库匹配结果", state="running", expanded=return_direct),
                f"正在执行 `{search_engine}` 搜索...",
            ])

        if dialogue_mode != "多源问答":
            # 多源问答分支已经自己完成了全部 SSE 渲染；这里只服务知识库/文件/搜索引擎三种旧模式。
            text = ""
            first = True

            try:
                for d in client.chat.completions.create(messages=messages, model=llm_model, stream=True, extra_body=extra_body):
                    if first:
                        chat_box.update_msg("\n\n".join(d.docs), element_index=0, streaming=False, state="complete")
                        chat_box.update_msg("", streaming=False)
                        first = False
                        continue
                    text += d.choices[0].delta.content or ""
                    chat_box.update_msg(text.replace("\n", "\n\n"), streaming=True)
                chat_box.update_msg(text, streaming=False)
                # TODO: 搜索未配置API KEY时产生报错
            except Exception as e:
                # 归一化错误显示：OpenAI APIError 有 .body，其它异常（断流、网络）没有，
                # 直接读会二次 AttributeError 掩盖原因。
                body = getattr(e, "body", None)
                if body:
                    st.error(body)
                else:
                    msg = getattr(e, "message", None) or str(e) or repr(e)
                    st.error(f"对话失败（{type(e).__name__}）：{msg}")

    now = datetime.now()
    with tabs[1]:
        cols = st.columns(2)
        export_btn = cols[0]
        if cols[1].button(
            "清空对话",
            use_container_width=True,
        ):
            chat_box.reset_history()
            rerun()

    export_btn.download_button(
        "导出记录",
        "".join(chat_box.export2md()),
        file_name=f"{now:%Y-%m-%d %H.%M}_对话记录.md",
        mime="text/markdown",
        use_container_width=True,
    )

    # st.write(chat_box.history)
