"""设置面板：知识源管理页。

Tab 结构：
1. 向量知识库（复用原 knowledge_base_page）
2. 数据库连接（SQL）
3. NoSQL / 搜索引擎（Mongo / ES）
4. 授权管理（批量 × 多源 × 多用户）

提供：
- 方言选择 → 动态表单（不同方言字段不同，例如 SQLite 只要 database 路径）
- 实时「测试连接」按钮
- 创建成功后直接 introspect，展示可勾选的表 / collection / index 做白名单
- 列表里每行可：刷新 schema、编辑、授权、删除
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import streamlit as st

from chayuan.webui_pages.utils import ApiRequest

# 方言 → 默认端口 + 字段显隐策略
DIALECT_DEFAULT_PORT = {
    "mysql": 3306, "postgres": 5432, "sqlite": 0,
    "mssql": 1433, "oracle": 1521, "clickhouse": 8123, "doris": 9030,
    "hive": 10000,
    # 信创：人大金仓 54321、达梦 5236
    "kingbase": 54321, "dm": 5236,
    "mongo": 27017, "es": 9200,
}

DIALECTS_SQL = (
    "mysql", "postgres", "sqlite", "mssql", "oracle", "clickhouse", "doris",
    "hive", "kingbase", "dm",
)
DIALECTS_NOSQL = ("mongo", "es")


def _all_dialects(api: ApiRequest) -> Dict[str, str]:
    try:
        return api.ks_list_dialects() or {}
    except Exception:  # noqa: BLE001
        return {
            "mysql": "MySQL", "postgres": "PostgreSQL", "sqlite": "SQLite",
            "mssql": "SQL Server", "oracle": "Oracle",
            "clickhouse": "ClickHouse", "doris": "Doris",
            "hive": "Apache Hive",
            "kingbase": "人大金仓 KingbaseES", "dm": "达梦 DM",
            "mongo": "MongoDB", "es": "Elasticsearch",
        }


def _render_connection_form(
    dialect: str, defaults: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """按方言渲染不同字段；返回 form 当前值（host/port/database/username/password/options）。"""
    defaults = defaults or {}
    c1, c2 = st.columns([2, 1])
    with c1:
        if dialect == "sqlite":
            database = st.text_input(
                "数据库文件路径",
                value=str(defaults.get("database") or ""),
                help="绝对路径，例如 /data/app.db；:memory: 可用作临时内存库",
            )
            host, port, username, password = "", 0, "", ""
        else:
            host = st.text_input("主机", value=str(defaults.get("host") or "127.0.0.1"))
            database = st.text_input(
                "数据库" if dialect not in ("mongo", "es") else "默认库名（可选）",
                value=str(defaults.get("database") or ""),
            )
            username = st.text_input("用户名", value=str(defaults.get("username") or ""))
            password = st.text_input(
                "密码（不改则留空）", value="", type="password",
                help="编辑模式下留空表示保持原密码；新建时必填",
            )
    with c2:
        if dialect == "sqlite":
            port = 0
        else:
            port = int(st.number_input(
                "端口", value=int(defaults.get("port") or DIALECT_DEFAULT_PORT.get(dialect, 0)),
                min_value=0, max_value=65535,
            ))

    # options 表单：按方言给常用键
    options: Dict[str, Any] = dict((defaults.get("options") or {}))
    with st.expander("高级选项（可选）", expanded=False):
        if dialect == "mssql":
            options["odbc_driver"] = st.text_input(
                "ODBC 驱动", value=str(options.get("odbc_driver") or "ODBC Driver 18 for SQL Server"),
            )
            options["odbc_extra"] = st.text_input(
                "附加 ODBC 参数", value=str(options.get("odbc_extra") or "Encrypt=no;TrustServerCertificate=yes"),
            )
        elif dialect == "oracle":
            options["service_name"] = st.text_input(
                "service_name（推荐）", value=str(options.get("service_name") or ""),
                help="多数 Oracle 部署使用 service_name（如 XEPDB1）；留空则回退 database 字段",
            )
        elif dialect == "kingbase":
            st.caption(
                "金仓 KingbaseES 协议与 PostgreSQL 兼容。"
                "优先走官方 `ksycopg2 + sqlalchemy-kingbase`；未装会自动降级到 `psycopg2`（需金仓开启 PG 兼容模式）。"
            )
            options["search_path"] = st.text_input(
                "search_path（可选）", value=str(options.get("search_path") or ""),
                help="多个 schema 用逗号分隔，如 `public,sys_catalog`；空则走连接用户默认。",
            )
        elif dialect == "dm":
            st.caption(
                "达梦 DM 协议与 Oracle 兼容。需要装 `dmPython` 与 `sqlalchemy-dm`，"
                "并确保 DPI 动态库可被加载（达梦官方文档：LD_LIBRARY_PATH / PATH）。"
            )
            options["schema"] = st.text_input(
                "默认 schema（可选）", value=str(options.get("schema") or ""),
                help="达梦 schema 语义 ≈ Oracle user。留空则按连接 user 的默认 schema 读。",
            )
            options["autoCommit"] = st.checkbox(
                "autoCommit", value=bool(options.get("autoCommit", False)),
                help="只读查询场景建议关闭；默认由 SQLAlchemy 按 transaction 管理。",
            )
        elif dialect == "mongo":
            options["uri"] = st.text_input(
                "完整 URI（可选，优先使用）",
                value=str(options.get("uri") or ""),
                help="如 mongodb+srv://user:pwd@cluster.mongodb.net/db，可直接粘贴 Atlas URI",
            )
            options["authSource"] = st.text_input(
                "authSource", value=str(options.get("authSource") or "admin"),
            )
        elif dialect == "es":
            options["scheme"] = st.selectbox(
                "协议", ["http", "https"],
                index=(0 if str(options.get("scheme") or "http") == "http" else 1),
            )
            options["api_key"] = st.text_input(
                "api_key（可选）", value=str(options.get("api_key") or ""), type="password",
            )
            options["verify_certs"] = st.checkbox(
                "校验证书", value=bool(options.get("verify_certs", False)),
            )
    return {
        "host": host, "port": port, "database": database,
        "username": username, "password": password, "options": options,
    }


def _render_allowed_picker(
    api: ApiRequest, schema: Dict[str, Any], kind: str,
    preselected: Optional[List[str]] = None,
) -> List[str]:
    """把 introspect 返回的对象名渲染成多选框；供白名单勾选。"""
    tables = schema.get("tables") or []
    names = [t.get("name") for t in tables if t.get("name")]
    label = {
        "sql": "选择可访问的表（白名单；空则允许全部）",
        "mongo": "选择可访问的 collection（白名单；空则允许全部）",
        "es": "选择可访问的 index（白名单；空则允许全部）",
    }.get(kind, "白名单")
    default = [x for x in (preselected or []) if x in names]
    return st.multiselect(label, names, default=default, key=f"ks_allowed_{kind}_{id(names)}")


def _scope_kind_for(dialect: str, kind: str) -> str:
    """统一"范围"语义的 kind 标签，用于 UI 提示与 allowed 字段键名。

    - SQL / Hive / Kingbase / DM / ClickHouse 等结构化库 → "table"（对应 allowed["tables"]）
    - Mongo → "collection"（对应 allowed["collections"]）
    - ES / 外部向量库（es 走 index）→ "index"
    - 外部向量库（milvus / zilliz / chromadb / pg / relyt）→ "collection"
    """
    k = (kind or "").strip().lower()
    d = (dialect or "").strip().lower()
    if k == "vs":
        return "index" if d in ("es", "elasticsearch") else "collection"
    if k == "mongo":
        return "collection"
    if k == "es":
        return "index"
    # sql / default
    return "table"


def _allowed_key_for(scope_kind: str) -> str:
    return {"table": "tables", "collection": "collections", "index": "indices"}.get(
        scope_kind, "tables",
    )


def _render_scope_picker(
    api: ApiRequest, *, state_key: str, dialect: str, kind: str,
    host: str, port: int, database: str,
    username: str, password: str, options: Dict[str, Any],
    preselected: Optional[List[str]] = None,
) -> Dict[str, List[str]]:
    """创建流程中的"范围"多选面板。

    交互设计（行业最佳实践）：
    1. 初始折叠，显示"留空 = 默认全部（不限定范围）"，与用户需求一致；
    2. 用户点"拉取可选项"按钮 → 调 /knowledge_source/catalog 轻量探测；
    3. 结果缓存在 ``st.session_state[state_key]`` 里，刷新前不重拉；
    4. 用多选框让用户挑选；支持全选 / 全清 快捷按钮；
    5. 返回 ``allowed`` dict，可直接丢给 ks_create。

    调用方（SQL / NoSQL / VS）都复用本函数；只是 ``scope_kind`` 不同。
    """
    scope_kind = _scope_kind_for(dialect, kind)
    allowed_key = _allowed_key_for(scope_kind)
    pretty = {"table": "表", "collection": "集合", "index": "索引"}.get(scope_kind, "对象")

    with st.expander(
        f"🎯 限定范围（可选，默认全部{pretty}）", expanded=False,
    ):
        st.caption(
            f"留空 = 不限定范围，查询会在后端当前{pretty}全量里做检索（默认行为）；"
            f"选了则本知识库只在选中的{pretty}范围内检索。"
        )

        col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
        fetch = col_btn1.button(f"🔎 拉取可选{pretty}", key=f"{state_key}_fetch")
        select_all = col_btn2.button("全选", key=f"{state_key}_all")
        clear_all = col_btn3.button("清空", key=f"{state_key}_clear")

        if fetch:
            with st.spinner(f"探测可用{pretty}..."):
                data = api.ks_catalog_probe(
                    dialect=dialect, host=host, port=port, database=database,
                    username=username, password=password,
                    options=options or {}, kind=kind, refresh=True,
                )
            names = list((data or {}).get("names") or [])
            st.session_state[state_key] = names
            if data and data.get("count", 0) > 0:
                st.toast(f"拉到 {data['count']} 个{pretty}", icon="✅")
            else:
                st.info(f"未探测到{pretty}；请先保证 “测试连接” 成功，或手工填写白名单。")

        names: List[str] = list(st.session_state.get(state_key) or [])
        default = [x for x in (preselected or []) if x in names]

        if select_all and names:
            st.session_state[f"{state_key}_sel"] = list(names)
        if clear_all:
            st.session_state[f"{state_key}_sel"] = []

        selected = st.multiselect(
            f"已选 {pretty}",
            options=names,
            default=st.session_state.get(f"{state_key}_sel", default),
            key=f"{state_key}_multi",
            help=f"多选；按字典序或频次检索命中率更高的{pretty}置顶",
        )
        # 兼容离线模式：支持用户手工输入（逗号分隔）
        manual = st.text_input(
            f"或手工输入（逗号分隔）",
            value="", key=f"{state_key}_manual",
            help=f"离线 / 不想拉取时，直接填 {pretty} 名；与上方多选合并去重",
        )
        manual_list = [x.strip() for x in manual.split(",") if x.strip()]
        # 合并保序去重
        merged: List[str] = []
        seen = set()
        for x in (selected or []) + manual_list:
            if x and x not in seen:
                seen.add(x); merged.append(x)

        if merged:
            st.caption(f"📌 已锁定 {len(merged)} 个{pretty}：{', '.join(merged[:10])}"
                       + (" ..." if len(merged) > 10 else ""))
        else:
            st.caption(f"🌐 未选 = 查询时覆盖后端当前{pretty}的全部")

        return {allowed_key: merged} if merged else {}


def _source_row_kind(src: Dict[str, Any]) -> str:
    return src.get("kind") or "vector"


def _render_training_panel(api: ApiRequest, source_id: int) -> None:
    """Vanna-style Text2SQL 训练样本管理。

    用户可以：
    - 查看现有样本（ddl / doc / pair 三类）
    - 手工添加 Question-SQL pair（最有价值，提升命中率立竿见影）
    - 添加业务文档（doc）
    - 删除不合适的样本
    """
    st.markdown("##### 🧠 Text2SQL 训练样本（RAG 语料）")
    st.caption(
        "三类语料自动参与检索：**DDL**（系统自动补种）、**doc**（业务说明，如『GMV = 订单金额 - 退款』）、"
        "**pair**（问题→SQL 示例，最大幅度提升准确率）"
    )
    tabs = st.tabs(["📝 问题-SQL 对", "📖 业务说明", "📋 DDL 片段"])
    with tabs[0]:
        _render_training_list(api, source_id, "pair")
        with st.form(f"ks_add_pair_{source_id}", clear_on_submit=True):
            q = st.text_area("问题（自然语言）", placeholder="例如：查询上月销售额前十的商品")
            sql = st.text_area("对应 SQL", placeholder="SELECT ... FROM ...", height=120)
            if st.form_submit_button("添加到训练集", use_container_width=True, type="primary"):
                if not q.strip() or not sql.strip():
                    st.error("问题和 SQL 都不能为空")
                else:
                    ret = api.ks_training_add(source_id, kind="pair", question=q, sql=sql)
                    if ret and ret.get("code") == 0:
                        st.toast("已添加", icon="✔")
                        st.rerun()
                    else:
                        st.error(ret.get("msg") or "添加失败")
    with tabs[1]:
        _render_training_list(api, source_id, "doc")
        with st.form(f"ks_add_doc_{source_id}", clear_on_submit=True):
            content = st.text_area(
                "业务说明", placeholder="例如：GMV 定义 / 某字段枚举含义 / 业务口径...", height=150,
            )
            if st.form_submit_button("添加", use_container_width=True, type="primary"):
                if content.strip():
                    api.ks_training_add(source_id, kind="doc", content=content)
                    st.rerun()
    with tabs[2]:
        _render_training_list(api, source_id, "ddl")
        st.caption("DDL 片段由系统在每次检索后自动幂等补种，无需手工添加。")


def _render_training_list(api: ApiRequest, source_id: int, kind: str) -> None:
    samples = api.ks_training_list(source_id, kind=kind) or []
    if not samples:
        st.info(f"暂无 {kind} 类样本")
        return
    for s in samples[:20]:
        c1, c2, c3 = st.columns([4, 1, 1])
        with c1:
            if kind == "pair":
                st.markdown(f"**Q：** {s.get('question') or ''}")
                st.code(s.get("sql") or "", language="sql")
            elif kind == "doc":
                st.write(s.get("content") or "")
            else:
                st.code((s.get("sql") or "")[:400], language="sql")
        c2.caption(f"hits={s.get('hit_count', 0)}  fb={s.get('feedback_score', 0)}")
        if c3.button("🗑", key=f"del_train_{source_id}_{s['id']}"):
            api.ks_training_delete(source_id, int(s["id"]))
            st.rerun()


def _render_sql_tab(api: ApiRequest) -> None:
    st.caption(
        "管理结构化数据源：MySQL / PostgreSQL / SQLite / SQL Server / Oracle / "
        "ClickHouse / Doris / Hive / **人大金仓 / 达梦**（信创）"
    )

    # --- 列表 ---
    all_sources = api.ks_list() or []
    sql_sources = [s for s in all_sources if _source_row_kind(s) == "sql"]
    st.markdown("#### 已接入的 SQL 数据源")
    if not sql_sources:
        st.info("尚未接入任何 SQL 数据源，使用下方表单新建。")
    else:
        for s in sql_sources:
            with st.expander(
                f"🗄️ **{s.get('display_name') or s.get('name')}**  ·  "
                f"id={s.get('id')}  ·  {s.get('visibility', 'private')}",
                expanded=False,
            ):
                detail = api.ks_get(int(s["id"])) or {}
                conn = detail.get("connection") or {}
                st.write(
                    f"- 方言：`{conn.get('dialect', '-')}`  "
                    f"- 连接：`{conn.get('host', '')}:{conn.get('port', 0)}/{conn.get('database', '')}`"
                )
                if conn.get("last_check_ok"):
                    st.success(f"最近一次体检：成功 @ {conn.get('last_check_time')}")
                elif conn.get("last_check_time"):
                    st.error(f"最近一次体检失败：{conn.get('last_check_error')}")

                cols = st.columns(5)
                if cols[0].button("刷新 Schema", key=f"ks_introspect_{s['id']}"):
                    with st.spinner("正在 introspect..."):
                        ret = api.ks_introspect(int(s["id"]))
                    st.toast(f"共 {ret.get('tables', 0)} 张表", icon="✔")
                    st.rerun()
                if cols[1].button("查看 Schema", key=f"ks_schema_{s['id']}"):
                    schema = api.ks_schema(int(s["id"])) or {}
                    st.json(schema, expanded=False)
                if cols[2].button("测试连接", key=f"ks_retest_{s['id']}"):
                    ret = api.ks_test_connection(
                        dialect=conn.get("dialect", ""),
                        connection_id=int(detail.get("connection_id") or 0),
                    )
                    if ret.get("ok"):
                        st.toast("连接成功 ✅")
                    else:
                        st.error(f"连接失败：{ret.get('msg')}")
                if cols[3].button("训练样本", key=f"ks_train_{s['id']}"):
                    st.session_state[f"show_train_{s['id']}"] = not st.session_state.get(f"show_train_{s['id']}", False)
                if cols[4].button("删除", key=f"ks_del_{s['id']}"):
                    api.ks_delete(int(s["id"]))
                    st.toast("已删除")
                    st.rerun()

                # 训练样本管理
                if st.session_state.get(f"show_train_{s['id']}"):
                    _render_training_panel(api, int(s["id"]))

    # --- 新建 ---
    # 注意：Streamlit 的 st.form 内部不支持多个"提交按钮"触发不同逻辑，
    # 这里拆成"表单字段 + 范围多选 + 三类操作按钮"的组合式布局：
    # "拉取可选表"需要当场调接口（form 外），所以把范围面板与表单并列。
    st.markdown("#### 新建 SQL 数据源")
    c1, c2, c3 = st.columns([1, 1, 2])
    dialect = c1.selectbox("方言", list(DIALECTS_SQL), index=0, key="ks_new_sql_dialect")
    name = c2.text_input("唯一标识（英文/数字/下划线）", value="", key="ks_new_sql_name")
    display_name = c3.text_input("显示名（支持中文）", value="", key="ks_new_sql_display")
    description = st.text_input(
        "业务简介（可选，LLM 路由会读这里）", value="", key="ks_new_sql_desc",
    )
    form_vals = _render_connection_form(dialect)
    visibility = st.selectbox("可见性", ["private", "public"], index=0, key="ks_new_sql_vis")

    # 范围多选（选了则固定、没选则"默认全部"）
    allowed_payload = _render_scope_picker(
        api, state_key="ks_new_sql_scope",
        dialect=dialect, kind="sql",
        host=form_vals["host"], port=form_vals["port"],
        database=form_vals["database"],
        username=form_vals["username"], password=form_vals["password"],
        options=form_vals["options"],
    )

    c_test, c_save = st.columns([1, 1])
    do_test = c_test.button("仅测试连接", use_container_width=True, key="ks_new_sql_test")
    do_save = c_save.button(
        "创建", use_container_width=True, type="primary", key="ks_new_sql_save",
    )

    if do_test:
        ret = api.ks_test_connection(dialect=dialect, **{k: form_vals[k] for k in
                                                          ("host", "port", "database", "username", "password", "options")})
        if ret.get("ok"):
            st.success(f"✅ {ret.get('msg', '连接成功')}")
        else:
            st.error(f"❌ {ret.get('msg', '连接失败')}")

    if do_save:
        if not name.strip():
            st.error("name 不能为空")
        else:
            ret = api.ks_create(
                name=name.strip(), kind="sql",
                display_name=display_name.strip() or name.strip(),
                description=description.strip(),
                dialect=dialect,
                host=form_vals["host"], port=form_vals["port"],
                database=form_vals["database"],
                username=form_vals["username"], password=form_vals["password"],
                options=form_vals["options"],
                allowed=allowed_payload,
                visibility=visibility,
            )
            if ret and ret.get("code") == 0:
                sid = (ret.get("data") or {}).get("id")
                if allowed_payload:
                    st.success(
                        f"✅ 已创建数据源 id={sid}，范围锁定"
                        f"{sum(len(v) for v in allowed_payload.values())} 个对象。"
                    )
                else:
                    st.success(f"✅ 已创建数据源 id={sid}（未限定范围，默认全部）")
                st.rerun()
            else:
                st.error(ret.get("msg") or ret.get("detail") or "创建失败")


def _render_nosql_tab(api: ApiRequest) -> None:
    st.caption("管理 MongoDB / Elasticsearch 等半结构化数据源")

    all_sources = api.ks_list() or []
    nosql_sources = [s for s in all_sources if _source_row_kind(s) in ("mongo", "es")]
    st.markdown("#### 已接入的 NoSQL / 搜索引擎数据源")
    if not nosql_sources:
        st.info("尚未接入任何 NoSQL / ES 数据源。")
    else:
        for s in nosql_sources:
            icon = "🍃" if s["kind"] == "mongo" else "🔍"
            with st.expander(
                f"{icon} **{s.get('display_name') or s.get('name')}**  ·  "
                f"id={s.get('id')}  ·  {s.get('kind')}",
                expanded=False,
            ):
                detail = api.ks_get(int(s["id"])) or {}
                conn = detail.get("connection") or {}
                st.write(
                    f"- 连接：`{conn.get('host', '')}:{conn.get('port', 0)}/{conn.get('database', '') or '-'}`"
                )
                cols = st.columns(4)
                if cols[0].button("刷新 Schema", key=f"ks_n_introspect_{s['id']}"):
                    with st.spinner("正在探测..."):
                        ret = api.ks_introspect(int(s["id"]))
                    st.toast(f"共 {ret.get('tables', 0)} 个对象", icon="✔")
                    st.rerun()
                if cols[1].button("查看 Schema", key=f"ks_n_schema_{s['id']}"):
                    st.json(api.ks_schema(int(s["id"])) or {}, expanded=False)
                if cols[2].button("测试连接", key=f"ks_n_retest_{s['id']}"):
                    ret = api.ks_test_connection(
                        dialect=s["kind"],
                        connection_id=int(detail.get("connection_id") or 0),
                    )
                    (st.toast("连接成功 ✅") if ret.get("ok") else st.error(ret.get("msg")))
                if cols[3].button("删除", key=f"ks_n_del_{s['id']}"):
                    api.ks_delete(int(s["id"]))
                    st.toast("已删除")
                    st.rerun()

    st.markdown("#### 新建 NoSQL / ES 数据源")
    c1, c2 = st.columns([1, 2])
    kind = c1.selectbox("类型", ["mongo", "es"], key="ks_new_nosql_kind")
    name = c2.text_input("唯一标识", value="", key="ks_new_nosql_name")
    display_name = st.text_input("显示名", value="", key="ks_new_nosql_display")
    description = st.text_input("业务简介（可选）", value="", key="ks_new_nosql_desc")
    form_vals = _render_connection_form(kind)
    visibility = st.selectbox("可见性", ["private", "public"], index=0, key="ks_new_nosql_vis")

    # 范围多选：Mongo 选 collection，ES 选 index；都走同一份 _render_scope_picker
    allowed_payload = _render_scope_picker(
        api, state_key="ks_new_nosql_scope",
        dialect=kind, kind=kind,
        host=form_vals["host"], port=form_vals["port"],
        database=form_vals["database"],
        username=form_vals["username"], password=form_vals["password"],
        options=form_vals["options"],
    )

    c_test, c_save = st.columns([1, 1])
    do_test = c_test.button("仅测试连接", use_container_width=True, key="ks_new_nosql_test")
    do_save = c_save.button(
        "创建", use_container_width=True, type="primary", key="ks_new_nosql_save",
    )

    if do_test:
        ret = api.ks_test_connection(dialect=kind, **{k: form_vals[k] for k in
                                                       ("host", "port", "database", "username", "password", "options")})
        (st.success(f"✅ {ret.get('msg', 'ok')}") if ret.get("ok")
         else st.error(f"❌ {ret.get('msg', '连接失败')}"))

    if do_save:
        if not name.strip():
            st.error("name 不能为空")
        else:
            ret = api.ks_create(
                name=name.strip(), kind=kind,
                display_name=display_name.strip() or name.strip(),
                description=description.strip(),
                dialect=kind,
                host=form_vals["host"], port=form_vals["port"],
                database=form_vals["database"],
                username=form_vals["username"], password=form_vals["password"],
                options=form_vals["options"],
                allowed=allowed_payload,
                visibility=visibility,
            )
            if ret and ret.get("code") == 0:
                sid = (ret.get("data") or {}).get("id")
                if allowed_payload:
                    total = sum(len(v) for v in allowed_payload.values())
                    st.success(f"✅ 已创建数据源 id={sid}，范围锁定 {total} 个对象。")
                else:
                    st.success(f"✅ 已创建数据源 id={sid}（未限定范围，默认全部）")
                st.rerun()
            else:
                st.error(ret.get("msg") or ret.get("detail") or "创建失败")


def _render_ext_vs_tab(api: ApiRequest) -> None:
    """外部向量库管理：Milvus / Zilliz / PG / Relyt / Chroma / ES（kind=vs）。"""
    st.caption(
        "连接**外部已有的向量库**作为知识源：检索时支持在多个 collection "
        "范围内并行 fan-out，结果合并后统一 rerank。"
    )

    # 方言 → 默认字段 & 友好名
    try:
        vs_dialects = api.get("/knowledge_source/dialects")
        # 用和 VS 专属 registry 一致的列表；若后端未暴露，兜底到硬编码
        vs_options = ["milvus", "zilliz", "pg", "relyt", "chromadb", "es"]
    except Exception:  # noqa: BLE001
        vs_options = ["milvus", "zilliz", "pg", "relyt", "chromadb", "es"]

    vs_port_default = {
        "milvus": 19530, "zilliz": 19530, "pg": 5432, "relyt": 5432,
        "chromadb": 0, "es": 9200,
    }

    # --- 列表 ---
    all_sources = api.ks_list() or []
    vs_sources = [s for s in all_sources if _source_row_kind(s) == "vs"]
    st.markdown("#### 已接入的外部向量库")
    if not vs_sources:
        st.info("尚未接入任何外部向量库。使用下方表单新建。")
    else:
        for s in vs_sources:
            with st.expander(
                f"🧠 **{s.get('display_name') or s.get('name')}**  ·  "
                f"id={s.get('id')}  ·  {s.get('visibility', 'private')}",
                expanded=False,
            ):
                detail = api.ks_get(int(s["id"])) or {}
                conn = detail.get("connection") or {}
                allowed = conn.get("allowed") or {}
                st.write(
                    f"- 方言：`{conn.get('dialect', '-')}`  "
                    f"- 连接：`{conn.get('host', '')}:{conn.get('port', 0)}`  "
                    f"- 已选集合：**{len(allowed.get('collections', []) + allowed.get('indices', []))}**"
                )
                cols = st.columns(4)
                if cols[0].button("刷新 catalog", key=f"vs_cat_{s['id']}"):
                    data = api.ks_catalog_for_source(int(s["id"]), refresh=True)
                    st.toast(f"发现 {data.get('count', 0)} 个集合", icon="✔")
                if cols[1].button("调整范围", key=f"vs_scope_{s['id']}"):
                    st.session_state[f"vs_edit_scope_{s['id']}"] = True
                if cols[2].button("测试连接", key=f"vs_retest_{s['id']}"):
                    ret = api.ks_test_connection(
                        dialect=conn.get("dialect", ""),
                        connection_id=int(detail.get("connection_id") or 0),
                        kind="vs",
                    )
                    if ret.get("ok"):
                        st.toast("连接成功 ✅")
                    else:
                        st.error(f"连接失败：{ret.get('msg')}")
                if cols[3].button("删除", key=f"vs_del_{s['id']}"):
                    api.ks_delete(int(s["id"]))
                    st.toast("已删除")
                    st.rerun()

                # 范围编辑（集合多选）
                if st.session_state.get(f"vs_edit_scope_{s['id']}"):
                    cat = api.ks_catalog_for_source(int(s["id"]))
                    names = list(cat.get("names") or [])
                    key_name = "indices" if conn.get("dialect") in ("es", "elasticsearch") else "collections"
                    current = list((conn.get("allowed") or {}).get(key_name) or [])
                    selected = st.multiselect(
                        f"选择集合（留空 = 不限范围）",
                        options=names,
                        default=[x for x in current if x in names],
                        key=f"vs_scope_sel_{s['id']}",
                    )
                    if st.button("保存范围", key=f"vs_save_scope_{s['id']}",
                                  type="primary"):
                        api.ks_patch_allowed(int(s["id"]), {key_name: selected})
                        st.toast("已更新")
                        st.session_state.pop(f"vs_edit_scope_{s['id']}", None)
                        st.rerun()

    # --- 新建 ---
    st.markdown("#### 新建外部向量库")
    c1, c2 = st.columns([1, 2])
    dialect = c1.selectbox("方言", vs_options, index=0, key="vs_new_dialect")
    name = c2.text_input("唯一标识", value="", key="vs_new_name")
    display_name = st.text_input("显示名", value="", key="vs_new_display")
    description = st.text_input("业务简介（可选）", value="", key="vs_new_desc")

    c3, c4 = st.columns([2, 1])
    if dialect in ("chromadb",):
        host = ""; port = 0
        c3.caption("Chroma 走本地嵌入路径，无需 host/port。")
    else:
        host = c3.text_input("主机", value="127.0.0.1", key="vs_new_host")
        port = int(c4.number_input(
            "端口", value=int(vs_port_default.get(dialect, 0)),
            min_value=0, max_value=65535, key="vs_new_port",
        ))

    c5, c6 = st.columns([1, 1])
    username = c5.text_input("用户名（可选）", value="", key="vs_new_user")
    password = c6.text_input("密码（可选）", value="", type="password", key="vs_new_pwd")

    options: Dict[str, Any] = {}
    with st.expander("高级选项", expanded=False):
        if dialect == "zilliz":
            options["token"] = st.text_input("Zilliz token（推荐）", type="password",
                                              key="vs_new_token")
            options["secure"] = st.checkbox("TLS", value=True, key="vs_new_secure")
        elif dialect == "milvus":
            options["secure"] = st.checkbox("TLS", value=False, key="vs_new_secure_m")
            options["db_name"] = st.text_input("db_name（可选）", value="", key="vs_new_db")
        elif dialect in ("pg", "relyt"):
            options["sslmode"] = st.text_input("sslmode（可选）", value="prefer",
                                                 key="vs_new_ssl")
        elif dialect == "es":
            options["scheme"] = st.selectbox("协议", ["http", "https"], key="vs_new_es_scheme")
            options["verify_certs"] = st.checkbox("校验证书", value=False,
                                                    key="vs_new_es_verify")
        options["embed_model"] = st.text_input(
            "embed_model（可选，默认走系统 embed）", value="", key="vs_new_embed",
        )
        options["collection_timeout_sec"] = float(st.number_input(
            "单集合检索超时（秒）", value=10.0, min_value=1.0, max_value=120.0,
            key="vs_new_col_timeout",
        ))

    # **核心**：范围多选（collection / index）；留空 = 默认全部
    allowed_payload = _render_scope_picker(
        api, state_key="vs_new_scope",
        dialect=dialect, kind="vs",
        host=host, port=port, database="",
        username=username, password=password,
        options=dict(options),
    )

    visibility = st.selectbox("可见性", ["private", "public"], index=0, key="vs_new_vis")

    c_test, c_save = st.columns([1, 1])
    do_test = c_test.button("仅测试连接", use_container_width=True, key="vs_new_test")
    do_save = c_save.button(
        "创建", use_container_width=True, type="primary", key="vs_new_save",
    )

    if do_test:
        ret = api.ks_test_connection(
            dialect=dialect, host=host, port=port, database="",
            username=username, password=password, options=options, kind="vs",
        )
        if ret.get("ok"):
            st.success(f"✅ {ret.get('msg', '连接成功')}")
        else:
            st.error(f"❌ {ret.get('msg', '连接失败')}")

    if do_save:
        if not name.strip():
            st.error("name 不能为空")
        else:
            ret = api.ks_create(
                name=name.strip(), kind="vs",
                display_name=display_name.strip() or name.strip(),
                description=description.strip(),
                dialect=dialect,
                host=host, port=port, database="",
                username=username, password=password,
                options=options,
                allowed=allowed_payload,
                visibility=visibility,
            )
            if ret and ret.get("code") == 0:
                sid = (ret.get("data") or {}).get("id")
                if allowed_payload:
                    total = sum(len(v) for v in allowed_payload.values())
                    st.success(f"✅ 已创建外部向量库 id={sid}，范围锁定 {total} 个集合。")
                else:
                    st.success(
                        f"✅ 已创建外部向量库 id={sid}（未限定范围，"
                        f"查询时会现采后端当前全部集合做 fan-out 并发）"
                    )
                st.rerun()
            else:
                st.error(ret.get("msg") or ret.get("detail") or "创建失败")


def _render_grants_tab(api: ApiRequest) -> None:
    st.caption("批量授权：一次给多个用户授权多个知识源。")
    all_sources = api.ks_list() or []
    if not all_sources:
        st.info("当前没有可管理的数据源。")
        return
    options = {f"{s.get('display_name') or s.get('name')} (id={s['id']}, {s['kind']})": int(s["id"])
               for s in all_sources}
    selected_labels = st.multiselect("选择数据源（支持多选）", list(options))
    selected_ids = [options[l] for l in selected_labels]

    user_ids_raw = st.text_input(
        "被授权用户 ID 列表（逗号/空格分隔）",
        value="",
        help="输入用户 id，例如 `3, 5, 12`；当前版本不在此页面提供用户选择器，需管理员提供 id。",
    )
    role = st.selectbox("角色", ["reader", "editor"], index=0)
    if st.button("批量授权", type="primary", use_container_width=True):
        try:
            user_ids = [int(x.strip()) for x in user_ids_raw.replace(",", " ").split() if x.strip()]
        except Exception:
            st.error("用户 ID 必须是整数")
            return
        if not selected_ids or not user_ids:
            st.error("请至少选择一个数据源和一个用户")
            return
        ret = api.ks_grant_batch(selected_ids, user_ids, role=role)
        if ret and ret.get("code") == 0:
            st.success(f"已新增 {ret.get('data', {}).get('added', 0)} 条授权")
        else:
            st.error(ret.get("msg") or "授权失败")

    st.divider()
    st.markdown("#### 单源授权详情")
    target = st.selectbox("查看单个数据源", ["(选择)"] + list(options.keys()))
    if target and target != "(选择)":
        sid = options[target]
        detail = api.ks_get(int(sid)) or {}
        # 当前仅 owner 可看 grants；非 owner 会 403，UI 给提示
        try:
            resp = api.get(f"/knowledge_source/{int(sid)}/grants")
            data = api._get_response_value(resp, as_json=True, value_func=lambda r: r.get("data", []))
        except Exception as e:  # noqa: BLE001
            st.error(f"加载失败：{e}")
            return
        if not data:
            st.info("暂无授权记录")
        else:
            for g in data:
                c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                c1.write(f"user_id = {g.get('user_id')}")
                c2.write(f"role = {g.get('role')}")
                c3.write(f"grantedBy = {g.get('granted_by')}")
                if c4.button("撤销", key=f"rev_{sid}_{g.get('user_id')}"):
                    api.ks_revoke(int(sid), int(g["user_id"]))
                    st.rerun()


def _render_image_tab(api: ApiRequest) -> None:
    """图像知识源管理 Tab。"""
    st.caption(
        "图像知识源支持**文本查图**（跨模态）与**以图搜图**。需要图像向量化模型"
        "（CLIP / SigLIP / Chinese-CLIP 等）；模型下载与管理见「🧠 图像模型」Tab。"
    )
    all_sources = api.ks_list() or []
    image_sources = [s for s in all_sources if s.get("kind") == "image"]

    st.markdown("#### 已接入的图像知识源")
    if not image_sources:
        st.info("暂未接入图像知识源。")
    else:
        for s in image_sources:
            with st.expander(
                f"🖼️ **{s.get('display_name') or s.get('name')}** (id={s.get('id')})",
                expanded=False,
            ):
                detail = api.ks_get(int(s["id"])) or {}
                conn = detail.get("connection") or {}
                opt = conn.get("options") or {}
                st.write(f"- 模型：`{opt.get('embedder_model') or '(默认)'}`")
                # 列出若干条
                listing = api.image_list(int(s["id"]), limit=20) or {}
                st.write(f"- 索引数：**{listing.get('count', 0)}**，向量维度：{listing.get('dim', 0)}")
                items = listing.get("items") or []
                if items:
                    import pandas as pd
                    df = pd.DataFrame([
                        {
                            "id": i.get("id"),
                            "file": __import__("os").path.basename(i.get("path") or ""),
                            "size_kb": int((i.get("size_bytes") or 0) / 1024),
                            "tags": i.get("tags") or "",
                            "model": i.get("embedder_model") or "",
                        } for i in items
                    ])
                    st.dataframe(df, use_container_width=True, hide_index=True)
                # 上传
                files = st.file_uploader(
                    "上传图片（可多选）",
                    ["png", "jpg", "jpeg", "bmp", "webp"],
                    accept_multiple_files=True,
                    key=f"img_upload_{s['id']}",
                )
                tags_in = st.text_input("标签（逗号分隔，可选）", value="",
                                         key=f"img_tags_{s['id']}")
                if files and st.button("上传并索引", key=f"img_upload_btn_{s['id']}",
                                         type="primary"):
                    with st.spinner(f"向量化 {len(files)} 张图..."):
                        pairs = [(f.name, f.getvalue()) for f in files]
                        ret = api.image_upload(int(s["id"]), pairs, tags=tags_in)
                    added = (ret.get("data") or {}).get("added") or []
                    errors = (ret.get("data") or {}).get("errors") or []
                    if added:
                        st.success(f"已索引 {len(added)} 张图")
                    if errors:
                        st.error(f"{len(errors)} 张失败；详情：{errors}")
                    st.rerun()

                # 以图搜图
                st.markdown("###### 🔎 以图搜图（调试）")
                qfile = st.file_uploader(
                    "上传查询图",
                    ["png", "jpg", "jpeg", "bmp", "webp"],
                    accept_multiple_files=False,
                    key=f"img_qfile_{s['id']}",
                )
                if qfile and st.button("搜索", key=f"img_search_btn_{s['id']}"):
                    with st.spinner("匹配中..."):
                        hits = api.image_search_by_image(
                            int(s["id"]), qfile.getvalue(), top_k=5,
                        )
                    st.write(f"命中 {len(hits)} 条：")
                    for h in hits or []:
                        cite = h.get("citation") or {}
                        st.write(f"- **{cite.get('title')}**  score={h.get('score'):.4f}")
                        st.write(h.get("content") or "")

    st.divider()
    st.markdown("#### 新建图像知识源")
    with st.form("ks_new_image", clear_on_submit=False):
        c1, c2 = st.columns([1, 2])
        name = c1.text_input("唯一标识", key="img_new_name")
        display_name = c2.text_input("显示名（中文）", key="img_new_display")
        # 模型选择
        try:
            models = api.image_models_list() or []
        except Exception:  # noqa: BLE001
            models = []
        model_options = [m["name"] for m in models] or [
            "google/siglip2-base-patch16-224",
            "OFA-Sys/chinese-clip-vit-base-patch16",
        ]
        embedder = st.selectbox(
            "向量化模型",
            model_options, index=0, key="img_new_model",
            help="中文优先 → Chinese-CLIP；英文/通用 → SigLIP 2 / CLIP；长文 → JinaCLIP v2。",
        )
        description = st.text_input("描述（可选）", key="img_new_desc")
        visibility = st.selectbox("可见性", ["private", "public"], index=0, key="img_new_vis")
        if st.form_submit_button("创建", type="primary", use_container_width=True):
            if not name.strip():
                st.error("name 不能为空")
            else:
                ret = api.ks_create(
                    name=name.strip(), kind="image",
                    display_name=display_name or name,
                    description=description,
                    dialect="image",
                    host="", port=0, database=name,
                    username="", password="",
                    options={"embedder_model": embedder, "source_name": name},
                    visibility=visibility,
                )
                if ret and ret.get("code") == 0:
                    st.success(f"已创建图像知识源 id={ret.get('data', {}).get('id')}")
                    st.rerun()
                else:
                    st.error(ret.get("msg") or ret.get("detail") or "创建失败")


def _render_image_models_tab(api: ApiRequest) -> None:
    """图像向量化模型管理 Tab（全局）。"""
    st.caption(
        "图像模型大小在 **400MB - 1.7GB** 之间；首次下载可能较慢。"
        "默认使用镜像 `https://hf-mirror.com`；如需直连官方可设置 `HF_ENDPOINT=https://huggingface.co` 后重启服务。"
    )
    st.markdown("##### 📚 支持的模型")
    try:
        models = api.image_models_list() or []
    except Exception as e:  # noqa: BLE001
        st.error(f"加载模型列表失败：{e}")
        return
    if not models:
        st.warning("后端未返回模型列表；检查依赖是否装好（`pip install 'chayuan-server[image]'`）。")
        return
    import pandas as pd
    df = pd.DataFrame([
        {
            "name": m["name"],
            "family": m["family"],
            "dim": m["dim"],
            "size_mb": m["approx_size_mb"],
            "chinese": m["chinese_level"],
            "langs": m["languages"],
            "cached": "✅" if m["cached"] else "❌",
            "cached_size_mb": m["cached_size_mb"],
            "deps": "✅" if m["deps_available"] else f"❌ {m.get('deps_reason','')}",
            "description": m["description"],
        } for m in models
    ])
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("##### 📥 下载模型")
    target = st.selectbox("选择模型", [m["name"] for m in models])
    sync = st.checkbox("同步下载（阻塞 HTTP 直至完成；大模型建议 False）", value=False)
    if st.button("开始下载", type="primary"):
        with st.spinner("下载中..."):
            ret = api.image_model_download(target, sync=bool(sync))
        st.json(ret)

    st.divider()
    st.markdown("##### 📤 上传模型 Bundle（离线场景）")
    st.caption(
        "流程：① 在有网环境下 `huggingface-cli download <model>` 把模型拉到本地 → "
        "② `tar czf siglip.tar.gz models--*/` 打包 → ③ 这里选 zip/tar.gz 上传。"
    )
    model_for_upload = st.text_input("模型名（要与 zip 内目录对应）",
                                       value="google/siglip2-base-patch16-224")
    bundle = st.file_uploader("上传 bundle", ["zip", "tar.gz", "tgz", "tar"])
    if bundle is not None and st.button("导入"):
        from io import BytesIO
        resp = api.post(
            "/image_models/upload_bundle",
            data={"model_name": model_for_upload},
            files=[("bundle", (bundle.name, BytesIO(bundle.getvalue()), "application/octet-stream"))],
        )
        ret = api._get_response_value(resp, as_json=True)
        st.json(ret)

    st.divider()
    st.markdown("##### 💾 磁盘占用")
    du = api.image_model_disk_usage() or {}
    st.metric("总占用 (MB)", du.get("total_mb", 0))
    st.caption(f"缓存根目录：`{du.get('root', '')}`")
    items = du.get("items") or []
    if items:
        st.dataframe(pd.DataFrame(items), use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("##### 📖 模型下载指引")
    st.markdown(
        """
- **有网**：上面点"开始下载"即可，默认使用 `https://hf-mirror.com`（推荐异步，不会阻塞）。
- **直连官方**：启动服务前 `export HF_ENDPOINT=https://huggingface.co`（Windows `set HF_ENDPOINT=...`）。
- **完全离线**：
  1. 在能访问 HuggingFace 的机器执行：
     ```bash
     huggingface-cli download google/siglip2-base-patch16-224 \\
         --local-dir ./siglip2-base
     tar czf siglip2-base.tar.gz siglip2-base/
     ```
  2. 把 `siglip2-base.tar.gz` 传到本机
  3. 上方"📤 上传模型 Bundle" 选它导入；后端会解压到模型缓存目录
- **替代**：直接把 HF cache 目录（如 `~/.cache/huggingface/hub/models--google--siglip2-base-patch16-224`）
  整体复制到 `$CHAYUAN_ROOT/models/huggingface/hub/` 下
        """
    )


def _render_rag_tab() -> None:
    """RAG 检索增强开关（P0-1）。这些值直接从 Settings.kb_settings 读写。"""
    from chayuan.settings import Settings
    kb = Settings.kb_settings
    st.caption(
        "开启后主动走 **Hybrid（BM25 + 向量融合）+ CrossEncoder rerank + 邻居 chunk 扩展**，"
        "中文命中率典型提升 20~30%。CrossEncoder 模型首次加载需 1-3 秒；"
        "生产建议 `RERANKER_DEVICE=cuda` 并 `pip install 'chayuan-server[rag-pro]'`。"
    )
    use_hybrid = st.checkbox("启用 Hybrid（BM25 + 向量融合）",
                              value=bool(getattr(kb, "USE_HYBRID_RETRIEVER", False)))
    bm25_w = st.slider("BM25 权重", 0.0, 1.0, value=float(getattr(kb, "HYBRID_BM25_WEIGHT", 0.4)),
                        step=0.05, help="向量权重自动 = 1 - BM25 权重")
    candidate_k = st.number_input("候选池 Top-K（rerank 前）", 5, 200,
                                   value=int(getattr(kb, "HYBRID_CANDIDATE_TOP_K", 20)))
    st.divider()
    use_rerank = st.checkbox("启用 CrossEncoder rerank",
                              value=bool(getattr(kb, "USE_RERANKER", False)))
    rerank_model = st.text_input("Rerank 模型",
                                  value=str(getattr(kb, "RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")))
    rerank_device = st.selectbox("Rerank device", ["cpu", "cuda", "mps"],
                                  index={"cpu": 0, "cuda": 1, "mps": 2}.get(
                                      str(getattr(kb, "RERANKER_DEVICE", "cpu")), 0))
    st.divider()
    use_expand = st.checkbox("启用邻居 chunk 扩展（轻量 Parent-Child）",
                              value=bool(getattr(kb, "USE_CONTEXT_EXPANSION", False)))
    neighbors = st.number_input("邻居 chunk 数（前后各取 N）", 0, 5,
                                 value=int(getattr(kb, "CONTEXT_EXPANSION_NEIGHBORS", 1)))
    if st.button("保存到 kb_settings.yaml", type="primary", use_container_width=True):
        try:
            kb.USE_HYBRID_RETRIEVER = bool(use_hybrid)
            kb.HYBRID_BM25_WEIGHT = float(bm25_w)
            kb.HYBRID_CANDIDATE_TOP_K = int(candidate_k)
            kb.USE_RERANKER = bool(use_rerank)
            kb.RERANKER_MODEL = rerank_model.strip() or "BAAI/bge-reranker-v2-m3"
            kb.RERANKER_DEVICE = rerank_device
            kb.USE_CONTEXT_EXPANSION = bool(use_expand)
            kb.CONTEXT_EXPANSION_NEIGHBORS = int(neighbors)
            kb.create_template_file(write_file=True)
            st.success("已保存并热重载")
        except Exception as e:  # noqa: BLE001
            st.error(f"保存失败：{e}")

    st.divider()
    st.markdown("##### 🧪 架构开关（高级）")
    bs = Settings.basic_settings
    use_graph = st.checkbox(
        "启用 ChatGraph（/chat/kb_chat 等老入口内部切到 LangGraph 管线）",
        value=bool(getattr(bs, "USE_CHAT_GRAPH", False)),
        help="默认关：走老实现。开后所有 chat 路径都自动纳入治理 / Guardrail。失败会自动回退老实现。",
    )
    disable_lf = st.checkbox(
        "禁用 Langfuse（应急开关；等价 CHAYUAN_LANGFUSE_DISABLE=1）",
        value=bool(getattr(bs, "CHAYUAN_LANGFUSE_DISABLE", False)),
    )
    guardrail_enabled = st.checkbox(
        "启用 Guardrail（输入 / 输出安全过滤）",
        value=bool(getattr(bs, "GUARDRAIL_ENABLED", False)),
    )
    guardrail_backend = st.selectbox(
        "Guardrail 后端",
        ["rules", "llama_guard", "nemo", "disabled"],
        index={"rules": 0, "llama_guard": 1, "nemo": 2, "disabled": 3}.get(
            str(getattr(bs, "GUARDRAIL_BACKEND", "rules")), 0),
    )
    if st.button("保存架构开关到 basic_settings.yaml", use_container_width=True):
        try:
            bs.USE_CHAT_GRAPH = bool(use_graph)
            bs.CHAYUAN_LANGFUSE_DISABLE = bool(disable_lf)
            bs.GUARDRAIL_ENABLED = bool(guardrail_enabled)
            bs.GUARDRAIL_BACKEND = str(guardrail_backend)
            bs.create_template_file(write_file=True)
            # 热重置 langfuse / guardrail 单例
            try:
                from chayuan.server.observability.langfuse_integration import reset_for_tests
                reset_for_tests()
            except Exception:  # noqa: BLE001
                pass
            try:
                from chayuan.server.guardrails.factory import reset_guardrail_cache
                reset_guardrail_cache()
            except Exception:  # noqa: BLE001
                pass
            st.success("已保存；重启进程前，LLM 调用/Guardrail 都按新设置生效")
        except Exception as e:  # noqa: BLE001
            st.error(f"保存失败：{e}")


def knowledge_source_settings_page(api: ApiRequest, is_lite: bool = False):
    """设置面板入口。由 webui.py 菜单调用。"""
    st.markdown("## 知识库与数据源管理")
    st.caption(
        "察元支持三类知识源：**向量知识库**（文件 + Embedding）、**结构化数据库**（SQL 方言）、"
        "**半结构化数据库**（MongoDB / Elasticsearch）。对话页可一次勾选多个源并行检索。"
    )
    tab1, tab2, tab3, tab_vs, tab4, tab5, tab6, tab7, tab8 = st.tabs([
        "📚 向量知识库", "🗄️ SQL 数据库", "🍃 NoSQL / ES",
        "🧠 外部向量库",
        "🖼️ 图像知识源", "🔐 授权管理", "⚡ RAG 增强", "🧠 图像模型",
        "📦 文件存储",
    ])
    with tab1:
        from chayuan.webui_pages.knowledge_base.knowledge_base import knowledge_base_page
        knowledge_base_page(api=api, is_lite=is_lite)
    with tab2:
        _render_sql_tab(api)
    with tab3:
        _render_nosql_tab(api)
    with tab_vs:
        _render_ext_vs_tab(api)
    with tab4:
        _render_image_tab(api)
    with tab5:
        _render_grants_tab(api)
    with tab6:
        _render_rag_tab()
    with tab7:
        _render_image_models_tab(api)
    with tab8:
        _render_storage_tab(api)


def _render_storage_tab(api: ApiRequest) -> None:
    """文件存储管理面板（本地 / MinIO 后端切换）。"""
    st.caption(
        "察元把所有**用户上传文件**（知识库内容、对话临时文件、图像）统一走 FileStorage "
        "抽象存储。默认落本地磁盘；生产建议切 MinIO / S3 做持久化与水平扩展。"
    )

    status = api.storage_status() or {}
    backend = status.get("type") or "(未知)"
    healthy = status.get("healthy", False)

    # 当前存储状态卡
    c1, c2, c3 = st.columns(3)
    c1.metric("当前后端", backend, delta="✅" if healthy else "❌")
    if backend == "local":
        c2.metric("本地根目录", "", status.get("root", ""))
    else:
        c2.metric("Endpoint", status.get("endpoint", ""))
    ns_data = status.get("namespaces") or {}
    total_objects = sum((n or {}).get("objects", 0) for n in ns_data.values())
    total_mb = sum((n or {}).get("size_mb", 0) for n in ns_data.values())
    c3.metric("总对象数 / MB", f"{total_objects} / {total_mb:.1f}")

    # 命名空间细分
    if ns_data:
        import pandas as pd
        df = pd.DataFrame([
            {
                "namespace": k,
                "bucket": (v or {}).get("bucket", "-"),
                "objects": (v or {}).get("objects", 0),
                "size_mb": (v or {}).get("size_mb", 0),
            }
            for k, v in ns_data.items()
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("##### 🔧 切换后端")
    new_backend = st.selectbox("后端", ["local", "minio"],
                                index=0 if backend == "local" else 1)
    if new_backend == "minio":
        with st.form("storage_minio_form"):
            c1, c2 = st.columns([3, 1])
            endpoint = c1.text_input("Endpoint", value="127.0.0.1:9000")
            secure = c2.checkbox("HTTPS", value=False)
            ak = st.text_input("Access Key", value="minioadmin")
            sk = st.text_input("Secret Key", value="", type="password")
            region = st.text_input("Region", value="us-east-1")
            prefix = st.text_input("Bucket Prefix", value="chayuan")

            col_t, col_s = st.columns(2)
            do_test = col_t.form_submit_button("测试连接", use_container_width=True)
            do_save = col_s.form_submit_button("切换并保存", use_container_width=True,
                                                 type="primary")
        if do_test:
            ret = api.storage_test_connection(endpoint, ak, sk, secure=secure, region=region)
            if ret.get("ok"):
                st.success("✅ MinIO 连接成功")
                st.json(ret.get("info") or {})
            else:
                st.error(f"❌ {ret.get('error')}")
        if do_save:
            ret = api.storage_switch_backend("minio", {
                "endpoint": endpoint, "access_key": ak, "secret_key": sk,
                "secure": bool(secure), "region": region,
                "bucket_prefix": prefix,
            })
            if ret and ret.get("code") == 0:
                st.success("✅ 已切换到 MinIO")
                st.json(ret.get("data") or {})
                st.rerun()
            else:
                st.error(ret.get("msg") or "切换失败")
    else:
        if backend != "local" and st.button("切换回本地", type="primary"):
            ret = api.storage_switch_backend("local")
            st.json(ret)
            st.rerun()

    st.divider()
    st.markdown("##### 🔄 数据迁移（local ↔ minio）")
    c1, c2, c3 = st.columns([1, 1, 1])
    ns_sel = c1.selectbox("命名空间",
                           ["kb_content", "chat_temp", "image_files", "misc"])
    direction = c2.selectbox("方向",
                               ["local_to_minio", "minio_to_local"])
    dry = c3.checkbox("Dry run（只列不动）", value=True)
    if st.button("开始迁移"):
        with st.spinner("进行中..."):
            ret = api.storage_migrate(ns_sel, direction=direction, dry_run=bool(dry))
        st.json(ret)

    st.divider()
    st.markdown("##### 📂 浏览对象")
    c1, c2 = st.columns([1, 2])
    ns_list = c1.selectbox("ns", ["kb_content", "chat_temp", "image_files", "misc"],
                            key="store_list_ns")
    prefix_list = c2.text_input("前缀（可选）", key="store_list_prefix")
    if st.button("列出"):
        items = api.storage_list(ns_list, prefix=prefix_list, limit=200) or []
        if items:
            import pandas as pd
            df = pd.DataFrame(items)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("空")
