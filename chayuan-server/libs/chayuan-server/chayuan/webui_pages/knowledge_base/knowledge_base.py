import os
import time
from typing import Dict, Literal, Tuple

import pandas as pd
import streamlit as st
import streamlit_antd_components as sac
from st_aggrid import AgGrid, JsCode
from st_aggrid.grid_options_builder import GridOptionsBuilder
from streamlit_antd_components.utils import ParseItems

from chayuan.settings import Settings
from chayuan.server.knowledge_base.kb_service.base import (
    SupportedVSType,
    get_kb_details,
    get_kb_file_details,
)
from chayuan.server.knowledge_base.utils import LOADER_DICT, get_file_path
from chayuan.server.utils import get_config_models, get_default_embedding

from chayuan.webui_pages.utils import *

# SENTENCE_SIZE = 100

cell_renderer = JsCode(
    """function(params) {if(params.value==true){return '✓'}else{return '×'}}"""
)


def config_aggrid(
    df: pd.DataFrame,
    columns: Dict[Tuple[str, str], Dict] = {},
    selection_mode: Literal["single", "multiple", "disabled"] = "single",
    use_checkbox: bool = False,
) -> GridOptionsBuilder:
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_column("No", width=40)
    for (col, header), kw in columns.items():
        gb.configure_column(col, header, wrapHeaderText=True, **kw)
    gb.configure_selection(
        selection_mode=selection_mode,
        use_checkbox=use_checkbox,
        pre_selected_rows=st.session_state.get("selected_rows", [0]),
    )
    gb.configure_pagination(
        enabled=True, paginationAutoPageSize=False, paginationPageSize=10
    )
    return gb


def file_exists(kb: str, selected_rows: List) -> Tuple[str, str]:
    """
    check whether a doc file exists in local knowledge base folder.
    return the file's name and path if it exists.
    """
    if selected_rows:
        file_name = selected_rows[0]["file_name"]
        file_path = get_file_path(kb, file_name)
        if os.path.isfile(file_path):
            return file_name, file_path
    return "", ""


def knowledge_base_page(api: ApiRequest, is_lite: bool = None):
    try:
        kb_list = {x["kb_name"]: x for x in get_kb_details()}
    except Exception as e:
        st.error(
            "获取知识库信息错误，请检查是否已按照 `README.md` 中 `4 知识库初始化与迁移` 步骤完成初始化或迁移，或是否为数据库连接错误。"
        )
        st.stop()
    kb_names = list(kb_list.keys())

    if (
        "selected_kb_name" in st.session_state
        and st.session_state["selected_kb_name"] in kb_names
    ):
        selected_kb_index = kb_names.index(st.session_state["selected_kb_name"])
    else:
        selected_kb_index = 0

    if "selected_kb_info" not in st.session_state:
        st.session_state["selected_kb_info"] = ""

    def format_selected_kb(kb_name: str) -> str:
        if kb := kb_list.get(kb_name):
            return f"{kb_name} ({kb['vs_type']} @ {kb['embed_model']})"
        else:
            return kb_name

    selected_kb = st.selectbox(
        "请选择或新建知识库：",
        kb_names + ["新建知识库"],
        format_func=format_selected_kb,
        index=selected_kb_index,
    )

    if selected_kb == "新建知识库":
        _render_create_knowledge_base(api, kb_list)

    elif selected_kb:
        kb = selected_kb
        st.session_state["selected_kb_info"] = kb_list[kb]["kb_info"]

        # ------------ 存储后端一览 + 切换 ------------
        _render_kb_storage_panel(api, kb)

        # 上传文件
        files = st.file_uploader(
            "上传知识文件：",
            [i for ls in LOADER_DICT.values() for i in ls],
            accept_multiple_files=True,
        )
        kb_info = st.text_area(
            "请输入知识库介绍:",
            value=st.session_state["selected_kb_info"],
            max_chars=None,
            key=None,
            help=None,
            on_change=None,
            args=None,
            kwargs=None,
        )

        if kb_info != st.session_state["selected_kb_info"]:
            st.session_state["selected_kb_info"] = kb_info
            api.update_kb_info(kb, kb_info)

        # with st.sidebar:
        with st.expander(
            "文件处理配置",
            expanded=True,
        ):
            cols = st.columns(3)
            chunk_size = cols[0].number_input("单段文本最大长度：", 1, 1000, Settings.kb_settings.CHUNK_SIZE)
            chunk_overlap = cols[1].number_input(
                "相邻文本重合长度：", 0, chunk_size, Settings.kb_settings.OVERLAP_SIZE
            )
            cols[2].write("")
            cols[2].write("")
            zh_title_enhance = cols[2].checkbox("开启中文标题加强", Settings.kb_settings.ZH_TITLE_ENHANCE)

        if st.button(
            "添加文件到知识库",
            # use_container_width=True,
            disabled=len(files) == 0,
        ):
            ret = api.upload_kb_docs(
                files,
                knowledge_base_name=kb,
                override=True,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                zh_title_enhance=zh_title_enhance,
            )
            if msg := check_success_msg(ret):
                st.toast(msg, icon="✔")
            elif msg := check_error_msg(ret):
                st.toast(msg, icon="✖")

        st.divider()

        # 知识库详情
        # st.info("请选择文件，点击按钮进行操作。")
        doc_details = pd.DataFrame(get_kb_file_details(kb))
        selected_rows = []
        if not len(doc_details):
            st.info(f"知识库 `{kb}` 中暂无文件")
        else:
            st.write(f"知识库 `{kb}` 中已有文件:")
            st.info("知识库中包含源文件与向量库，请从下表中选择文件后操作")
            doc_details.drop(columns=["kb_name"], inplace=True)
            doc_details = doc_details[
                [
                    "No",
                    "file_name",
                    "document_loader",
                    "text_splitter",
                    "docs_count",
                    "in_folder",
                    "in_db",
                ]
            ]
            doc_details["in_folder"] = (
                doc_details["in_folder"].replace(True, "✓").replace(False, "×")
            )
            doc_details["in_db"] = (
                doc_details["in_db"].replace(True, "✓").replace(False, "×")
            )
            gb = config_aggrid(
                doc_details,
                {
                    ("No", "序号"): {},
                    ("file_name", "文档名称"): {},
                    # ("file_ext", "文档类型"): {},
                    # ("file_version", "文档版本"): {},
                    ("document_loader", "文档加载器"): {},
                    ("docs_count", "文档数量"): {},
                    ("text_splitter", "分词器"): {},
                    # ("create_time", "创建时间"): {},
                    ("in_folder", "源文件"): {},
                    ("in_db", "向量库"): {},
                },
                "multiple",
            )

            doc_grid = AgGrid(
                doc_details,
                gb.build(),
                columns_auto_size_mode="FIT_CONTENTS",
                theme="alpine",
                custom_css={
                    "#gridToolBar": {"display": "none"},
                },
                allow_unsafe_jscode=True,
                enable_enterprise_modules=False,
            )

            selected_rows = doc_grid.get("selected_rows")
            if selected_rows is None:
                selected_rows = []
            else:
                selected_rows = selected_rows.to_dict("records")
            cols = st.columns(4)
            file_name, file_path = file_exists(kb, selected_rows)
            if file_path:
                with open(file_path, "rb") as fp:
                    cols[0].download_button(
                        "下载选中文档",
                        fp,
                        file_name=file_name,
                        use_container_width=True,
                    )
            else:
                cols[0].download_button(
                    "下载选中文档",
                    "",
                    disabled=True,
                    use_container_width=True,
                )

            st.write()
            # 将文件分词并加载到向量库中
            if cols[1].button(
                "重新添加至向量库"
                if selected_rows and (pd.DataFrame(selected_rows)["in_db"]).any()
                else "添加至向量库",
                disabled=not file_exists(kb, selected_rows)[0],
                use_container_width=True,
            ):
                file_names = [row["file_name"] for row in selected_rows]
                api.update_kb_docs(
                    kb,
                    file_names=file_names,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    zh_title_enhance=zh_title_enhance,
                )
                st.rerun()

            # 将文件从向量库中删除，但不删除文件本身。
            if cols[2].button(
                "从向量库删除",
                disabled=not (selected_rows and selected_rows[0]["in_db"]),
                use_container_width=True,
            ):
                file_names = [row["file_name"] for row in selected_rows]
                api.delete_kb_docs(kb, file_names=file_names)
                st.rerun()

            if cols[3].button(
                "从知识库中删除",
                type="primary",
                use_container_width=True,
            ):
                file_names = [row["file_name"] for row in selected_rows]
                api.delete_kb_docs(kb, file_names=file_names, delete_content=True)
                st.rerun()

        st.divider()

        cols = st.columns(3)

        if cols[0].button(
            "依据源文件重建向量库",
            help="无需上传文件，通过其它方式将文档拷贝到对应知识库content目录下，点击本按钮即可重建知识库。",
            use_container_width=True,
            type="primary",
        ):
            with st.spinner("向量库重构中，请耐心等待，勿刷新或关闭页面。"):
                empty = st.empty()
                empty.progress(0.0, "")
                for d in api.recreate_vector_store(
                    kb,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    zh_title_enhance=zh_title_enhance,
                ):
                    if msg := check_error_msg(d):
                        st.toast(msg)
                    else:
                        empty.progress(d["finished"] / d["total"], d["msg"])
                st.rerun()

        if cols[2].button(
            "删除知识库",
            use_container_width=True,
        ):
            ret = api.delete_knowledge_base(kb)
            st.toast(ret.get("msg", " "))
            time.sleep(1)
            st.rerun()

        # B 路线：高级检索能力构建
        st.divider()
        with st.expander("🧠 高级检索能力（RAPTOR 层次摘要 / GraphRAG 知识图谱）", expanded=False):
            st.caption(
                "**RAPTOR**：对现有 chunks 做层次聚类 + LLM 摘要，检索时同池召回细节与全局。"
                " **GraphRAG**：LLM 抽实体+关系，Louvain 社区检测 + 社区摘要，擅长"
                "'行业盘点''竞品对比'等宏观问题。两者均为 **离线构建**，查询时零额外 LLM 成本。"
            )
            cc1, cc2, cc3 = st.columns(3)
            with cc1:
                if st.button("构建 RAPTOR", key=f"btn_raptor_{kb}",
                             use_container_width=True):
                    with st.spinner("RAPTOR 构建中（LLM 密集）..."):
                        ret = api.build_raptor(kb, target_cluster_size=5, max_levels=3)
                    if ret and ret.get("code") == 0:
                        d = ret.get("data") or {}
                        st.success(f"RAPTOR 构建完成：{d.get('levels')} 层，"
                                    f"新增 {d.get('summaries_added')} 条摘要，"
                                    f"耗时 {d.get('elapsed_sec')}s")
                    else:
                        st.error(ret.get("msg") or "构建失败")
            with cc2:
                if st.button("构建 GraphRAG", key=f"btn_grag_{kb}",
                             use_container_width=True):
                    with st.spinner("GraphRAG 构建中（LLM 密集，可能 5-30 分钟）..."):
                        ret = api.build_graphrag(kb, community_min_size=2)
                    if ret and ret.get("code") == 0:
                        d = ret.get("data") or {}
                        st.success(
                            f"GraphRAG 构建完成：实体 {d.get('entities')}，"
                            f"关系 {d.get('relations')}，社区 {d.get('communities')}，"
                            f"耗时 {d.get('elapsed_sec')}s"
                        )
                    else:
                        st.error(ret.get("msg") or "构建失败")
            with cc3:
                if st.button("查看图谱统计", key=f"btn_grag_stats_{kb}",
                             use_container_width=True):
                    stats = api.graphrag_stats(kb) or {}
                    st.json(stats)

        with st.sidebar:
            keyword = st.text_input("查询关键字")
            top_k = st.slider("匹配条数", 1, 100, 3)

        st.write("文件内文档列表。双击进行修改，在删除列填入 Y 可删除对应行。")
        docs = []
        df = pd.DataFrame([], columns=["seq", "id", "content", "source"])
        if selected_rows:
            file_name = selected_rows[0]["file_name"]
            docs = api.search_kb_docs(
                knowledge_base_name=selected_kb, file_name=file_name
            )

            data = [
                {
                    "seq": i + 1,
                    "id": x["id"],
                    "page_content": x["page_content"],
                    "source": x["metadata"].get("source"),
                    "type": x["type"],
                    "metadata": json.dumps(x["metadata"], ensure_ascii=False),
                    "to_del": "",
                }
                for i, x in enumerate(docs)
            ]
            df = pd.DataFrame(data)

            gb = GridOptionsBuilder.from_dataframe(df)
            gb.configure_columns(["id", "source", "type", "metadata"], hide=True)
            gb.configure_column("seq", "No.", width=50)
            gb.configure_column(
                "page_content",
                "内容",
                editable=True,
                autoHeight=True,
                wrapText=True,
                flex=1,
                cellEditor="agLargeTextCellEditor",
                cellEditorPopup=True,
            )
            gb.configure_column(
                "to_del",
                "删除",
                editable=True,
                width=50,
                wrapHeaderText=True,
                cellEditor="agCheckboxCellEditor",
                cellRender="agCheckboxCellRenderer",
            )
            # 启用分页
            gb.configure_pagination(
                enabled=True, paginationAutoPageSize=False, paginationPageSize=10
            )
            gb.configure_selection()
            edit_docs = AgGrid(df, gb.build(), fit_columns_on_grid_load=True)

            if st.button("保存更改"):
                origin_docs = {
                    x["id"]: {
                        "page_content": x["page_content"],
                        "type": x["type"],
                        "metadata": x["metadata"],
                    }
                    for x in docs
                }
                changed_docs = []
                for index, row in edit_docs.data.iterrows():
                    origin_doc = origin_docs[row["id"]]
                    if row["page_content"] != origin_doc["page_content"]:
                        if row["to_del"] not in ["Y", "y", 1]:
                            changed_docs.append(
                                {
                                    "page_content": row["page_content"],
                                    "type": row["type"],
                                    "metadata": json.loads(row["metadata"]),
                                }
                            )

                if changed_docs:
                    if api.update_kb_docs(
                        knowledge_base_name=selected_kb,
                        file_names=[file_name],
                        docs={file_name: changed_docs},
                    ):
                        st.toast("更新文档成功")
                    else:
                        st.toast("更新文档失败")


# ===========================================================================
# 新建知识库：4 种类型的统一入口
# ===========================================================================
#
# 为什么把"类型选择"放在 ``st.form`` 外：Streamlit form 内不会触发 rerun，
# 无法做到"选了 SQL 就展开 dialect/host/port…"这种动态字段。把选择器放外面
# 其值会立即 rerun，进入对应分支的 form，保证字段随类型精确展示。
#
# 每种类型一个 _render_new_{kind} 函数，按需调不同 API：
#   - 非结构化 → /knowledge_base/create_knowledge_base
#   - SQL / NoSQL → /knowledge_source/test_connection → /knowledge_source/
#   - 图像   → /knowledge_source/（kind=image，options 里塞 embedder_model）

_KB_TYPE_OPTIONS = [
    ("vector",  "📄 非结构化（文档 + 向量库）",
     "文本 / PDF / markdown 等文件，切片向量化后可语义检索。"),
    ("sql",     "🗄️ 结构化数据库（SQL）",
     "MySQL / Postgres / SQLite / SQL Server / Oracle / ClickHouse / Doris；"
     "自动 Text2SQL 回答自然语言问题。"),
    ("nosql",   "🍃 半结构化（MongoDB / Elasticsearch）",
     "JSON 文档库 / 搜索引擎；支持 Text2Mongo / Text2ES 自动查询。"),
    ("image",   "🖼️ 图像数据",
     "图像集合，用 SigLIP / CLIP / Chinese-CLIP 等模型向量化，支持以图搜图。"),
]


def _render_create_knowledge_base(api, kb_list) -> None:
    """新建知识库入口——**类型优先**，字段动态。"""
    st.markdown("### ➕ 新建知识库")
    st.caption("第一步选择知识库类型，下方表单会随类型调整")

    # 4 种类型使用 columns 做 pill 风格选择；Streamlit selectbox 在这里也够
    type_labels = [label for _, label, _ in _KB_TYPE_OPTIONS]
    type_map = {label: (kind, desc) for kind, label, desc in _KB_TYPE_OPTIONS}
    selected_label = st.selectbox(
        "知识库类型",
        type_labels,
        index=0,
        key="new_kb_type_select",
    )
    kind, type_desc = type_map[selected_label]
    st.caption(type_desc)
    st.divider()

    existing_names = set(kb_list.keys()) if kb_list else set()

    if kind == "vector":
        _render_new_vector_kb(api, existing_names)
    elif kind == "sql":
        _render_new_sql_source(api, existing_names)
    elif kind == "nosql":
        _render_new_nosql_source(api, existing_names)
    elif kind == "image":
        _render_new_image_source(api, existing_names)


def _render_new_vector_kb(api, existing_names) -> None:
    """非结构化（向量库）—— 原 create_knowledge_base 路径。"""
    with st.form("新建非结构化知识库"):
        kb_name = st.text_input(
            "知识库名称",
            placeholder="新知识库名称，不支持中文",
            key="kb_name",
        )
        kb_info = st.text_input(
            "知识库简介",
            placeholder="Agent 选择用；例如「公司产品手册」",
            key="kb_info",
        )

        col0, _ = st.columns([3, 1])
        _vs_order = (
            SupportedVSType.MILVUS, SupportedVSType.FAISS, SupportedVSType.ZILLIZ,
            SupportedVSType.PG, SupportedVSType.RELYT, SupportedVSType.ES,
            SupportedVSType.CHROMADB,
        )
        vs_types = [k for k in _vs_order if k in Settings.kb_settings.kbs_config]
        _default_vs = Settings.kb_settings.DEFAULT_VS_TYPE
        _vs_index = vs_types.index(_default_vs) if _default_vs in vs_types else 0
        vs_type = col0.selectbox(
            "向量库类型", vs_types, index=_vs_index, key="kb_vector_store_type",
        )

        col1, _ = st.columns([3, 1])
        with col1:
            embed_models = list(get_config_models(model_type="embed"))
            idx = 0
            if get_default_embedding() in embed_models:
                idx = embed_models.index(get_default_embedding())
            embed_model = st.selectbox("Embedding 模型", embed_models, idx)

        # 文件存储后端（按 KB 覆盖）
        try:
            _glb = api.storage_status() or {}
        except Exception:
            _glb = {}
        _glb_backend = str(_glb.get("type") or "local")
        storage_choice = st.selectbox(
            "文件存储后端",
            ["global", "local", "minio"],
            index=0,
            format_func=lambda x: {
                "global": f"跟随全局默认（当前：{_glb_backend}）",
                "local": "本地磁盘",
                "minio": "MinIO / S3 对象存储",
            }[x],
            key="kb_storage_backend",
            help="知识库的原始文件写到哪里。'跟随全局' 可在【数据源管理 → 文件存储】切换。",
        )
        if storage_choice == "minio" and _glb_backend != "minio":
            st.caption("⚠️ 全局未启用 MinIO；可在 **数据源管理 → 文件存储** 先配置")

        submit = st.form_submit_button("新建", use_container_width=True,
                                           type="primary")

    if not submit:
        return
    if not (kb_name or "").strip():
        st.error("知识库名称不能为空")
        return
    if kb_name in existing_names:
        st.error(f"名为 {kb_name} 的知识库已存在")
        return
    if embed_model is None:
        st.error("请选择 Embedding 模型")
        return
    sb = "" if storage_choice == "global" else storage_choice
    ret = api.create_knowledge_base(
        knowledge_base_name=kb_name, vector_store_type=vs_type,
        embed_model=embed_model, kb_info=kb_info, storage_backend=sb,
    )
    if ret and ret.get("code") in (0, 200):
        st.success(ret.get("msg") or "创建成功")
        st.session_state["selected_kb_name"] = kb_name
        st.session_state["selected_kb_info"] = kb_info
        st.rerun()
    else:
        st.error(ret.get("msg") or "创建失败")


# ------- SQL -----------------------------------------------------------------

# 常用 dialect 默认端口，UI 自动填充给用户参考（节省输入成本）
_SQL_DIALECTS = [
    ("mysql",      3306, "MySQL"),
    ("postgresql", 5432, "PostgreSQL"),
    ("sqlite",     0,    "SQLite（file）"),
    ("mssql",      1433, "SQL Server"),
    ("oracle",     1521, "Oracle"),
    ("clickhouse", 8123, "ClickHouse"),
    ("doris",      9030, "Doris"),
    ("hive",      10000, "Apache Hive (HiveServer2)"),
    # 国产信创：金仓 54321（PG 兼容）、达梦 5236（Oracle 兼容）
    ("kingbase",  54321, "人大金仓 KingbaseES"),
    ("dm",         5236, "达梦 DM"),
]


def _fetch_supported_dialects(api) -> list:
    """后端 /knowledge_source/dialects 是权威来源；失败回落 _SQL_DIALECTS。"""
    try:
        resp = api.get("/knowledge_source/dialects")
        data = (resp.json() if resp is not None else {}) or {}
        return list(data.get("data") or [])
    except Exception:
        return []


def _render_new_sql_source(api, existing_names) -> None:
    """结构化 SQL 源 —— 走 /knowledge_source/。"""
    backend_dialects = _fetch_supported_dialects(api)
    # 合并：以 _SQL_DIALECTS 作为 UI 顺序，仅保留后端支持的
    backend_set = set([str(d).lower() for d in backend_dialects]) if backend_dialects else None
    dialect_options = [
        (d, port, label) for d, port, label in _SQL_DIALECTS
        if (backend_set is None) or d in backend_set
    ]
    if not dialect_options:
        dialect_options = _SQL_DIALECTS

    # 测试连接按钮要在 form 外，不然 form_submit_button 是唯一提交；这里用两段 form。
    dialect = st.selectbox(
        "数据库类型",
        [d for d, _, _ in dialect_options],
        format_func=lambda d: next(
            (lbl for dk, _, lbl in dialect_options if dk == d), d
        ),
        key="new_sql_dialect",
    )
    default_port = next(
        (p for d, p, _ in dialect_options if d == dialect), 0
    )
    is_sqlite = (dialect == "sqlite")

    with st.form("新建结构化数据源"):
        src_name = st.text_input(
            "数据源名称", placeholder="唯一标识；用于授权和检索", key="new_sql_name"
        )
        display_name = st.text_input(
            "显示名（可选）", placeholder="给人看的名字", key="new_sql_display"
        )
        description = st.text_input(
            "描述（可选）", placeholder="主要内容 / 用途", key="new_sql_desc"
        )

        if is_sqlite:
            database = st.text_input(
                "SQLite 文件路径", placeholder="如 /data/sales.db",
                key="new_sql_database",
            )
            host = ""
            port = 0
            username = ""
            password = ""
        else:
            c1, c2 = st.columns([3, 1])
            host = c1.text_input("主机", placeholder="例如 127.0.0.1",
                                    key="new_sql_host")
            port = c2.number_input(
                "端口", value=int(default_port) or 0, min_value=0, max_value=65535,
                key="new_sql_port",
            )
            database = st.text_input("数据库名", key="new_sql_db")
            c3, c4 = st.columns([1, 1])
            username = c3.text_input("用户名", key="new_sql_user")
            password = c4.text_input("密码", type="password", key="new_sql_pwd")

        allowed_tables = st.text_input(
            "白名单表（可选，逗号分隔）", placeholder="只允许 Text2SQL 触达的表",
            key="new_sql_allowed",
        )
        visibility = st.selectbox("可见性", ["private", "public"], 0,
                                     key="new_sql_vis")

        c_test, c_save = st.columns([1, 1])
        do_test = c_test.form_submit_button("🔌 测试连接",
                                                use_container_width=True)
        do_save = c_save.form_submit_button("💾 保存并创建", type="primary",
                                                use_container_width=True)

    if do_test:
        with st.spinner("连接中..."):
            ret = api.post(
                "/knowledge_source/test_connection",
                json={
                    "dialect": dialect, "host": host, "port": int(port),
                    "database": database, "username": username, "password": password,
                    "options": {},
                },
            )
            try:
                data = (ret.json() or {}).get("data") or {}
            except Exception:
                data = {}
        if data.get("ok"):
            st.success(f"✅ 连通：{data.get('msg') or ''}")
        else:
            st.error(f"❌ 失败：{data.get('msg') or '未知错误'}")

    if do_save:
        if not (src_name or "").strip():
            st.error("数据源名称不能为空")
            return
        if src_name in existing_names:
            st.error(f"同名数据源已存在：{src_name}")
            return
        if is_sqlite and not (database or "").strip():
            st.error("SQLite 需要填写文件路径")
            return
        allowed = {}
        if (allowed_tables or "").strip():
            allowed["tables"] = [t.strip() for t in allowed_tables.split(",")
                                   if t.strip()]
        ret = api.ks_create(
            name=src_name.strip(), kind="sql",
            display_name=display_name, description=description,
            dialect=dialect, host=host, port=int(port), database=database,
            username=username, password=password,
            allowed=allowed, visibility=visibility,
        )
        if ret and ret.get("code") == 0:
            st.success(f"✅ 已创建 SQL 数据源 id={(ret.get('data') or {}).get('id')}")
            st.rerun()
        else:
            st.error(ret.get("msg") or ret.get("detail") or "创建失败")


# ------- NoSQL（Mongo / ES）--------------------------------------------------

def _render_new_nosql_source(api, existing_names) -> None:
    """半结构化数据源：MongoDB / Elasticsearch。"""
    kind_label = st.selectbox(
        "半结构化类型",
        ["MongoDB", "Elasticsearch"],
        index=0, key="new_nosql_kind",
    )
    is_mongo = (kind_label == "MongoDB")
    dialect = "mongodb" if is_mongo else "elasticsearch"
    default_port = 27017 if is_mongo else 9200

    with st.form("新建半结构化数据源"):
        src_name = st.text_input("数据源名称", key="new_nosql_name")
        display_name = st.text_input("显示名（可选）", key="new_nosql_display")
        description = st.text_input("描述（可选）", key="new_nosql_desc")

        c1, c2 = st.columns([3, 1])
        host = c1.text_input("主机", placeholder="例如 127.0.0.1",
                                key="new_nosql_host")
        port = c2.number_input("端口", value=default_port, min_value=0,
                                  max_value=65535, key="new_nosql_port")
        database = st.text_input(
            "MongoDB 库名" if is_mongo else "ES 默认 index（可空）",
            key="new_nosql_db",
        )
        c3, c4 = st.columns([1, 1])
        username = c3.text_input("用户名（可选）", key="new_nosql_user")
        password = c4.text_input("密码（可选）", type="password",
                                    key="new_nosql_pwd")
        allowed_raw = st.text_input(
            "白名单集合/索引（逗号分隔，可选）",
            placeholder="mongo：collections；es：indices",
            key="new_nosql_allowed",
        )
        visibility = st.selectbox("可见性", ["private", "public"], 0,
                                     key="new_nosql_vis")

        c_test, c_save = st.columns([1, 1])
        do_test = c_test.form_submit_button("🔌 测试连接",
                                                use_container_width=True)
        do_save = c_save.form_submit_button("💾 保存并创建", type="primary",
                                                use_container_width=True)

    if do_test:
        with st.spinner("连接中..."):
            ret = api.post(
                "/knowledge_source/test_connection",
                json={
                    "dialect": dialect, "host": host, "port": int(port),
                    "database": database, "username": username, "password": password,
                },
            )
            try:
                data = (ret.json() or {}).get("data") or {}
            except Exception:
                data = {}
        if data.get("ok"):
            st.success(f"✅ 连通：{data.get('msg') or ''}")
        else:
            st.error(f"❌ 失败：{data.get('msg') or '未知错误'}")

    if do_save:
        if not (src_name or "").strip():
            st.error("数据源名称不能为空")
            return
        if src_name in existing_names:
            st.error(f"同名数据源已存在：{src_name}")
            return
        allowed = {}
        if (allowed_raw or "").strip():
            items = [s.strip() for s in allowed_raw.split(",") if s.strip()]
            allowed["collections" if is_mongo else "indices"] = items
        ret = api.ks_create(
            name=src_name.strip(),
            kind="mongo" if is_mongo else "es",
            display_name=display_name, description=description,
            dialect=dialect, host=host, port=int(port), database=database,
            username=username, password=password,
            allowed=allowed, visibility=visibility,
        )
        if ret and ret.get("code") == 0:
            st.success(
                f"✅ 已创建 {kind_label} 数据源 id={(ret.get('data') or {}).get('id')}"
            )
            st.rerun()
        else:
            st.error(ret.get("msg") or ret.get("detail") or "创建失败")


# ------- 图像 ----------------------------------------------------------------

def _render_new_image_source(api, existing_names) -> None:
    """图像数据源：选择 embedder 模型即可。"""
    try:
        models = api.image_models_list() or []
    except Exception:
        models = []
    model_options = [m["name"] for m in models] or [
        "google/siglip2-base-patch16-224",
        "OFA-Sys/chinese-clip-vit-base-patch16",
    ]

    with st.form("新建图像数据源"):
        src_name = st.text_input("图像库名称",
                                    placeholder="用作 namespace / 索引名",
                                    key="new_img_name")
        display_name = st.text_input("显示名（可选）", key="new_img_display")
        description = st.text_input("描述（可选）", key="new_img_desc")
        embedder = st.selectbox(
            "图像向量化模型",
            model_options, index=0, key="new_img_embedder",
            help="中文优先 → Chinese-CLIP；通用 → SigLIP 2 / CLIP；"
                 "长文 → JinaCLIP v2。模型可在【数据源管理 → 图像模型】统一下载。",
        )
        # 展示模型依赖状态（若可知）：未装就提示
        dep_ok = True
        dep_msg = ""
        for m in models:
            if m.get("name") == embedder:
                dep_ok = bool(m.get("deps_available", True))
                dep_msg = m.get("deps_reason", "")
                break
        if not dep_ok:
            st.warning(f"⚠️ 依赖缺失：{dep_msg}\n请先执行 "
                        f"`pip install 'chayuan-server[image]'`")
        visibility = st.selectbox("可见性", ["private", "public"], 0,
                                     key="new_img_vis")

        submit = st.form_submit_button("💾 保存并创建", type="primary",
                                           use_container_width=True)

    if submit:
        if not (src_name or "").strip():
            st.error("图像库名称不能为空")
            return
        if src_name in existing_names:
            st.error(f"同名数据源已存在：{src_name}")
            return
        ret = api.ks_create(
            name=src_name.strip(), kind="image",
            display_name=display_name or src_name,
            description=description, dialect="image",
            host="", port=0, database=src_name,
            options={"embedder_model": embedder, "source_name": src_name},
            visibility=visibility,
        )
        if ret and ret.get("code") == 0:
            st.success(
                f"✅ 已创建图像知识源 id={(ret.get('data') or {}).get('id')}"
            )
            st.rerun()
        else:
            st.error(ret.get("msg") or ret.get("detail") or "创建失败")


# ===========================================================================
# 存储后端面板：查看 + 切换（可选迁移）
# ===========================================================================

def _render_kb_storage_panel(api, kb_name: str) -> None:
    """渲染知识库的存储后端状态条 + 切换表单。

    设计要点：
    - 顶部一行"徽章":``📦 local``、``📦 minio``、继承全局时标"（跟随全局：xxx）"；
    - 展开后放切换 UI：下拉 + 迁移勾选 + Dry Run 预览 + 保存；
    - 调用 ``/knowledge_base/storage_backend`` 读当前；改动走 ``update_storage_backend``。
    """
    try:
        info = api.get_kb_storage_backend(kb_name) or {}
    except Exception as e:  # noqa: BLE001
        st.caption(f"📦 存储后端信息获取失败：{e}")
        return

    override = (info.get("override") or "").strip().lower()
    resolved = (info.get("resolved") or "").strip().lower() or "(unknown)"
    global_backend = (info.get("global") or "local").strip().lower()
    available = info.get("available") or ["local"]

    cols = st.columns([4, 1])
    with cols[0]:
        if not override:
            st.markdown(
                f"**📦 存储后端**：`{resolved}` "
                f"<span style='color:#64748b'>（跟随全局默认 · {global_backend}）</span>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"**📦 存储后端**：`{resolved}` "
                f"<span style='color:#059669'>（本 KB 单独指定：{override}）</span>",
                unsafe_allow_html=True,
            )
    with cols[1]:
        if "minio" not in available:
            st.caption(
                "ℹ️ MinIO 未启用；可在\n**数据源管理 → 文件存储** 启用"
            )

    with st.expander("⚙️ 变更本 KB 的存储后端", expanded=False):
        choices = ["global"] + list(dict.fromkeys(available + ["local"]))
        labels = {
            "global": f"跟随全局默认（{global_backend}）",
            "local": "本地磁盘",
            "minio": "MinIO / S3 对象存储",
        }
        current = override or "global"
        try:
            idx = choices.index(current)
        except ValueError:
            idx = 0
        new_choice = st.selectbox(
            "目标后端", choices, index=idx,
            format_func=lambda x: labels.get(x, x),
            key=f"kb_target_storage_{kb_name}",
        )
        migrate = st.checkbox(
            "同时迁移历史文件到新后端",
            value=False, key=f"kb_migrate_{kb_name}",
            help="勾选后会把 KB 已上传的文件从旧后端复制到新后端；不勾选时旧"
                 "文件留在原处（后续访问仍能读到）。",
        )
        c_dry, c_save = st.columns([1, 1])
        do_dry = c_dry.button("🔍 预览迁移（Dry Run）",
                                 key=f"kb_dry_{kb_name}",
                                 disabled=not migrate)
        do_save = c_save.button("💾 保存", type="primary",
                                   key=f"kb_save_storage_{kb_name}")

        target = "" if new_choice == "global" else new_choice
        if do_dry:
            with st.spinner("分析中..."):
                ret = api.update_kb_storage_backend(
                    kb_name, storage_backend=target, migrate=True, dry_run=True,
                )
            data = (ret or {}).get("data") or {}
            if data.get("dry_run"):
                st.info(
                    f"将迁移 **{data.get('count', 0)}** 个对象（"
                    f"合计 {round(float(data.get('bytes', 0)) / 1024 / 1024, 2)} MB）"
                    f"：{data.get('old')} → {data.get('new')}"
                )
                preview = data.get("preview") or []
                if preview:
                    st.code("\n".join(preview[:20]))
            else:
                st.json(ret)

        if do_save:
            with st.spinner("处理中..."):
                ret = api.update_kb_storage_backend(
                    kb_name, storage_backend=target,
                    migrate=bool(migrate), dry_run=False,
                )
            if (ret or {}).get("code") in (0, 200):
                data = ret.get("data") or {}
                st.success(
                    f"✅ 已切换 {data.get('old')} → {data.get('new')}；"
                    f"迁移 {data.get('migrated_count', 0)} 个对象，"
                    f"失败 {data.get('errors_count', 0)} 个"
                )
                if data.get("errors_preview"):
                    st.json(data["errors_preview"])
                st.rerun()
            else:
                st.error(f"❌ 失败：{(ret or {}).get('msg')}")
