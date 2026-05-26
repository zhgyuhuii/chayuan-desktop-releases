"""向量库 / 全文检索引擎配置卡片。

设计要点
--------
- 知识库靠 ``kb_settings.yaml`` 里的 ``DEFAULT_VS_TYPE`` 选型，具体连接参数放在
  ``kbs_config.<type>`` 子节点下（见 ``data_test/kb_settings.yaml``）。
- 本模块把项目真实支持的七种类型结构化：
    * ``faiss``    — 本地索引，无连接参数；
    * ``milvus``   — host/port/user/password/secure/token/db_name；
    * ``zilliz``   — Zilliz Cloud，默认 secure=true，主要用 token；
    * ``pg``       — pgvector，填完整 SQLAlchemy URI；
    * ``relyt``    — Relyt(gp-based)，同 pg；
    * ``es``       — Elasticsearch，scheme/host/port/user/password/index_name；
    * ``chromadb`` — 当前版本使用本地持久化，无连接参数。
- 每种类型都有专属的 ``validate`` 路径，全部走真实的客户端而不是假跑 ping。
- 「保存」把当前表单的 type 写到 ``DEFAULT_VS_TYPE``，把表单字段写到
  ``kbs_config.<type>.*``；其它类型的子节点保持原样不动（避免误删用户手配好
  的其它后端参数）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from chayuan.server.config_panel import yaml_store


# ---------------------------------------------------------------------------
# 字段 / 类型定义
# ---------------------------------------------------------------------------

@dataclass
class VsField:
    name: str
    label: str
    widget: str = "text"        # text / password / number / switch / select / textarea
    default: Any = ""
    choices: List[str] = field(default_factory=list)
    placeholder: str = ""
    help: str = ""


@dataclass
class VsType:
    key: str
    label: str
    help: str = ""
    install_hint: str = ""
    fields: List[VsField] = field(default_factory=list)
    validator: Optional[str] = None


VS_TYPES: List[VsType] = [
    VsType(
        key="milvus",
        label="Milvus（自托管）",
        help=(
            "生产/多用户推荐。需先在 19530 端口启动 Milvus，"
            "集合名会在服务端被清理为 ASCII，详见 knowledge_base.utils.safe_vs_collection_name。"
        ),
        install_hint="Python 驱动：pymilvus（已随项目安装）",
        fields=[
            VsField("host", "主机 host", default="127.0.0.1",
                    help="Milvus Server 地址；本机部署填 127.0.0.1。"),
            VsField("port", "端口 port", default="19530",
                    help="默认 19530。注意写字符串形式，例如 '19530'。"),
            VsField("user", "用户名 user", default="",
                    help="开启鉴权后填；未开鉴权留空即可。"),
            VsField("password", "密码 password", widget="password", default="",
                    help="开启鉴权后填。"),
            VsField("secure", "TLS 连接 secure", widget="switch", default=False,
                    help="Milvus 服务端开启 TLS 时打开。"),
            VsField("token", "Token（可选）", widget="password", default="",
                    help="Milvus 2.2+ 支持 API Token，填入后可不填 user/password。"),
            VsField("db_name", "Database（可选）", default="",
                    help="Milvus 2.2+ 的多 database 功能；留空使用 default。"),
        ],
        validator="milvus",
    ),
    VsType(
        key="zilliz",
        label="Zilliz Cloud",
        help="Zilliz 托管的 Milvus。推荐用 Token 认证；secure 默认开启。",
        install_hint="Python 驱动：pymilvus（已随项目安装）",
        fields=[
            VsField("host", "Endpoint", default="",
                    help="Zilliz 控制台提供的 public endpoint，不含协议前缀，例如 in01-xxx.zilliz.com.cn。"),
            VsField("port", "端口 port", default="19530",
                    help="通常是 19530 或 443（看 Zilliz 控制台）。"),
            VsField("user", "用户名 user", default="",
                    help="推荐留空、改用 Token。"),
            VsField("password", "密码 password", widget="password", default="",
                    help="推荐留空、改用 Token。"),
            VsField("secure", "TLS 连接 secure", widget="switch", default=True,
                    help="Zilliz 通常需要 TLS，保持打开。"),
            VsField("token", "Token", widget="password", default="",
                    help="Zilliz 控制台生成的 API Key。最佳实践是优先用 Token 认证。"),
        ],
        validator="milvus",
    ),
    VsType(
        key="pg",
        label="PostgreSQL + pgvector",
        help="pgvector 扩展。connection_uri 即标准 SQLAlchemy URI。",
        install_hint="Python 驱动：psycopg2-binary（已随项目安装）；PG 侧需 CREATE EXTENSION vector。",
        fields=[
            VsField(
                "connection_uri", "connection_uri", default="",
                placeholder="postgresql://user:pass@host:port/database",
                help=(
                    "例：postgresql://root:root@127.0.0.1:5432/chayuan。"
                    "密码里的特殊字符需 URL-encode。"
                ),
            ),
        ],
        validator="sqlalchemy",
    ),
    VsType(
        key="relyt",
        label="Relyt（阿里云瑶池/Greenplum）",
        help="Relyt 底层是 PG 协议，用 SQLAlchemy + psycopg2 连。",
        install_hint="Python 驱动：psycopg2-binary（已随项目安装）",
        fields=[
            VsField(
                "connection_uri", "connection_uri", default="",
                placeholder="postgresql+psycopg2://user:pass@host:port/database",
                help="例：postgresql+psycopg2://root:root@127.0.0.1:7000/chayuan。",
            ),
        ],
        validator="sqlalchemy",
    ),
    VsType(
        key="es",
        label="Elasticsearch",
        help="ES 既做向量存储也做倒排索引；推荐 ES ≥ 8.0。",
        install_hint="Python 驱动：elasticsearch（已随项目安装）",
        fields=[
            VsField("scheme", "协议 scheme", widget="select",
                    choices=["http", "https"], default="http",
                    help="公网/生产建议 https；自签证书时 https 可能需要关闭校验（本项目暂不支持）。"),
            VsField("host", "主机 host", default="127.0.0.1"),
            VsField("port", "端口 port", default="9200"),
            VsField("user", "用户名 user", default="",
                    help="ES 8.x 默认开启安全，需填 elastic 等账号。"),
            VsField("password", "密码 password", widget="password", default=""),
            VsField("index_name", "索引前缀 index_name", default="test_index",
                    help="每个知识库会生成 <index_name>_<kb> 格式的索引名。"),
        ],
        validator="elasticsearch",
    ),
    VsType(
        key="faiss",
        label="FAISS（本地）",
        help=(
            "本地进程内库，默认存在 $CHAYUAN_ROOT/data/knowledge_base/<kb>/vector_store/ 下，"
            "无需连接参数。⚠️ FAISS 无法跨 worker / 副本共享，切 KB 会触发整库 reload，"
            "生产场景请改用 milvus 或 pg。"
        ),
        fields=[],
        validator="local",
    ),
    VsType(
        key="chromadb",
        label="ChromaDB（本地）",
        help="当前版本使用 Chroma 的本地持久化模式，暂无连接参数。",
        fields=[],
        validator="local",
    ),
]


def vs_type_by_key(key: str) -> Optional[VsType]:
    for t in VS_TYPES:
        if t.key == key:
            return t
    return None


# ---------------------------------------------------------------------------
# 值读写
# ---------------------------------------------------------------------------

def _default_values_for(t: VsType) -> Dict[str, Any]:
    return {f.name: f.default for f in t.fields}


def load_values(t: VsType, doc: Dict[str, Any]) -> Dict[str, Any]:
    """从 yaml doc 里读出 ``kbs_config.<type>`` 作为表单初值。缺省时用字段默认。"""
    base = _default_values_for(t)
    node = (doc or {}).get("kbs_config", {})
    if not isinstance(node, dict):
        return base
    sub = node.get(t.key, {})
    if not isinstance(sub, dict):
        return base
    for k in base.keys():
        if k in sub and sub[k] is not None:
            base[k] = sub[k]
    return base


def _normalize(t: VsType, values: Dict[str, Any]) -> Dict[str, Any]:
    """把控件里的值规范化为 yaml 里期望的类型（port 保留字符串、switch 保留 bool）。"""
    out: Dict[str, Any] = {}
    for f in t.fields:
        v = values.get(f.name, f.default)
        if f.widget == "switch":
            out[f.name] = bool(v)
        elif f.widget == "number":
            try:
                out[f.name] = int(v) if v not in (None, "") else f.default
            except (TypeError, ValueError):
                out[f.name] = f.default
        else:
            out[f.name] = "" if v is None else str(v)
    return out


# ---------------------------------------------------------------------------
# 验证
# ---------------------------------------------------------------------------

def _validate_milvus(values: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("pymilvus", "pymilvus>=2.4,<3.0")
        from pymilvus import connections, utility  # type: ignore
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"未安装 pymilvus（自动安装失败）：{e}", "detail": ""}

    host = str(values.get("host", "")).strip()
    port = str(values.get("port", "19530")).strip() or "19530"
    user = str(values.get("user", "") or "")
    password = str(values.get("password", "") or "")
    secure = bool(values.get("secure", False))
    token = str(values.get("token", "") or "")
    db_name = str(values.get("db_name", "") or "")

    if not host:
        return {"ok": False, "message": "host 不能为空", "detail": ""}

    alias = f"_chayuan_probe_{int(time.time() * 1000)}"
    kwargs: Dict[str, Any] = {
        "alias": alias,
        "host": host,
        "port": port,
        "secure": secure,
        "timeout": timeout,
    }
    if user:
        kwargs["user"] = user
    if password:
        kwargs["password"] = password
    if token:
        kwargs["token"] = token
    if db_name:
        kwargs["db_name"] = db_name

    try:
        connections.connect(**kwargs)
        try:
            collections = utility.list_collections(using=alias)
        except Exception:  # noqa: BLE001
            collections = []
        return {
            "ok": True,
            "message": f"连通成功（现有 {len(collections)} 个 collection）",
            "detail": f"{host}:{port}",
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}", "detail": ""}
    finally:
        try:
            connections.disconnect(alias)
        except Exception:
            pass


def _validate_sqlalchemy(values: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
    uri = str(values.get("connection_uri", "") or "").strip()
    if not uri:
        return {"ok": False, "message": "connection_uri 不能为空", "detail": ""}
    try:
        from sqlalchemy import create_engine, text  # type: ignore
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"未安装 SQLAlchemy：{e}", "detail": ""}
    try:
        # connect_timeout 仅 PG/MySQL 接受;SQLite 会抛 TypeError
        _connect_args = (
            {"connect_timeout": int(timeout)}
            if uri.startswith(("postgresql", "postgres", "mysql"))
            else {}
        )
        eng = create_engine(uri, pool_pre_ping=True, connect_args=_connect_args)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"URI 解析失败：{type(e).__name__}: {e}", "detail": ""}
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True, "message": "连通成功", "detail": _strip_password(uri)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}", "detail": _strip_password(uri)}
    finally:
        try:
            eng.dispose()
        except Exception:
            pass


def _strip_password(uri: str) -> str:
    try:
        from urllib.parse import urlparse, urlunparse
        p = urlparse(uri)
        if p.password:
            netloc = p.netloc.replace(f":{p.password}", ":***")
            return urlunparse(p._replace(netloc=netloc))
    except Exception:
        pass
    return uri


def _validate_elasticsearch(values: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("elasticsearch", "elasticsearch>=8.0,<9.0")
        from elasticsearch import Elasticsearch  # type: ignore
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"未安装 elasticsearch（自动安装失败）：{e}", "detail": ""}

    scheme = str(values.get("scheme", "http") or "http")
    host = str(values.get("host", "") or "").strip()
    port = str(values.get("port", "9200") or "9200").strip() or "9200"
    user = str(values.get("user", "") or "")
    password = str(values.get("password", "") or "")

    if not host:
        return {"ok": False, "message": "host 不能为空", "detail": ""}

    url = f"{scheme}://{host}:{port}"
    try:
        kwargs: Dict[str, Any] = {"request_timeout": timeout}
        if user:
            # elasticsearch 8.x: basic_auth; 7.x: http_auth。用 try-except 兼容。
            kwargs["basic_auth"] = (user, password)
        client = Elasticsearch(url, **kwargs)
        if not client.ping():
            return {"ok": False, "message": "ping 返回 false（地址或鉴权错误）", "detail": url}
        info = client.info()
        version = ""
        try:
            version = info.get("version", {}).get("number", "")
        except Exception:
            pass
        return {"ok": True, "message": f"连通成功（ES {version or '版本未知'}）", "detail": url}
    except TypeError:
        # 落回 7.x 的 http_auth
        try:
            kwargs2: Dict[str, Any] = {"request_timeout": timeout}
            if user:
                kwargs2["http_auth"] = (user, password)
            client = Elasticsearch(url, **kwargs2)
            if not client.ping():
                return {"ok": False, "message": "ping 返回 false", "detail": url}
            return {"ok": True, "message": "连通成功", "detail": url}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": f"{type(e).__name__}: {e}", "detail": url}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}", "detail": url}


def _validate_local(values: Dict[str, Any], timeout: float = 5.0, *, key: str = "") -> Dict[str, Any]:
    # chromadb 虽然"本地"，但依然需要第三方包；这里顺手检查 / 自动安装
    if key == "chromadb":
        try:
            from chayuan.server.shared.deps import ensure_pkg
            ok = ensure_pkg("chromadb", "chromadb>=0.4,<0.6")
            if not ok:
                return {
                    "ok": False,
                    "message": "未安装 chromadb（自动安装失败），请手动 `pip install 'chromadb>=0.4,<0.6'`",
                    "detail": "",
                }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": f"chromadb 依赖检查失败：{e}", "detail": ""}

    return {
        "ok": True,
        "message": "本地向量库，无需联网连接",
        "detail": "索引文件会写在 $CHAYUAN_ROOT/data/knowledge_base/<kb>/vector_store/ 下",
    }


def validate_connection(t: VsType, values: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
    v = _normalize(t, values)
    if t.validator == "milvus":
        return _validate_milvus(v, timeout=timeout)
    if t.validator == "sqlalchemy":
        return _validate_sqlalchemy(v, timeout=timeout)
    if t.validator == "elasticsearch":
        return _validate_elasticsearch(v, timeout=timeout)
    return _validate_local(v, timeout=timeout, key=t.key)


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------

def save_values(t: VsType, values: Dict[str, Any], *, set_as_default: bool) -> Dict[str, tuple]:
    v = _normalize(t, values)
    updates: Dict[str, Any] = {}
    if set_as_default:
        updates["DEFAULT_VS_TYPE"] = t.key
    for name, val in v.items():
        updates[f"kbs_config.{t.key}.{name}"] = val
    _path, _bak, changes = yaml_store.save_updates("kb_settings.yaml", updates)
    return changes


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def render_vs_card(ui, *, mark_restart_needed: Optional[Callable[[], None]] = None) -> None:
    load_res = yaml_store.load_yaml("kb_settings.yaml")
    doc = load_res.doc or {}
    current_default_key = str(doc.get("DEFAULT_VS_TYPE") or "milvus")
    if vs_type_by_key(current_default_key) is None:
        current_default_key = "milvus"

    # 每种类型的表单值单独保存一份：切换下拉时不丢失当前正在编辑的内容。
    form_values: Dict[str, Dict[str, Any]] = {
        t.key: load_values(t, doc) for t in VS_TYPES
    }

    inputs: Dict[str, Any] = {}
    state: Dict[str, Any] = {"current_key": current_default_key}

    # ---- 局部函数 -----------------------------------------------------------

    def _collect_current() -> Dict[str, Any]:
        key = state["current_key"]
        t = vs_type_by_key(key)
        if t is None:
            return {}
        out = dict(form_values.get(key, {}))
        for name, el in inputs.items():
            try:
                out[name] = el.value
            except Exception:
                pass
        return out

    def _cache_current() -> None:
        form_values[state["current_key"]] = _collect_current()

    def _render_fields(t: VsType) -> None:
        nonlocal inputs
        inputs = {}
        fields_container.clear()
        with fields_container:
            if t.help:
                ui.label(t.help).classes("text-xs text-grey-7").style("white-space: pre-line")
            if t.install_hint:
                ui.label(t.install_hint).classes("text-xs text-grey-6 q-mb-sm").style(
                    "white-space: pre-line"
                )

            if not t.fields:
                return

            values = form_values.get(t.key) or _default_values_for(t)
            with ui.grid(columns=2).classes("w-full q-gutter-sm"):
                for f in t.fields:
                    cur = values.get(f.name, f.default)
                    el = _build_input(f, cur)
                    inputs[f.name] = el
            for el in inputs.values():
                el.classes("w-full")

    def _build_input(f: VsField, value: Any):
        if f.widget == "switch":
            el = ui.switch(text=f.label, value=bool(value))
        elif f.widget == "password":
            el = (
                ui.input(
                    label=f.label,
                    value="" if value is None else str(value),
                    password=True,
                    password_toggle_button=True,
                )
                .props("outlined dense")
            )
            if f.placeholder:
                el.props(f'placeholder="{f.placeholder}"')
        elif f.widget == "select":
            el = (
                ui.select(
                    f.choices,
                    label=f.label,
                    value=value if value in f.choices else (f.choices[0] if f.choices else None),
                )
                .props("outlined dense")
            )
        elif f.widget == "number":
            el = (
                ui.number(label=f.label, value=value if value not in (None, "") else None)
                .props("outlined dense")
            )
        else:
            el = (
                ui.input(label=f.label, value="" if value is None else str(value))
                .props("outlined dense")
            )
            if f.placeholder:
                el.props(f'placeholder="{f.placeholder}"')
        if f.help:
            el.tooltip(f.help)
        return el

    def _on_type_change(_=None) -> None:
        _cache_current()
        new_key = type_select.value
        state["current_key"] = new_key
        t = vs_type_by_key(new_key)
        if t is None:
            return
        _render_fields(t)
        _clear_result()

    def _clear_result() -> None:
        if result_row is not None:
            result_row.visible = False

    def _show_result(ok: bool, message: str, detail: str = "") -> None:
        result_row.visible = True
        if ok:
            result_icon.props("name=check_circle color=positive")
        else:
            result_icon.props("name=error color=negative")
        result_text.text = message
        result_detail.text = detail or ""
        result_detail.visible = bool(detail)

    def _show_busy(message: str, detail: str = "") -> None:
        result_row.visible = True
        result_icon.props("name=hourglass_top color=primary")
        result_text.text = message
        result_detail.text = detail or ""
        result_detail.visible = bool(detail)

    def _on_validate() -> None:
        t = vs_type_by_key(state["current_key"])
        if t is None:
            return
        values = _collect_current()
        _show_busy(f"正在验证 {t.label} 连接……")
        res = validate_connection(t, values, timeout=5)
        _show_result(bool(res.get("ok")), str(res.get("message", "")), str(res.get("detail", "")))

    def _on_save() -> None:
        t = vs_type_by_key(state["current_key"])
        if t is None:
            return
        values = _collect_current()
        try:
            changes = save_values(t, values, set_as_default=True)
        except Exception as e:  # noqa: BLE001
            ui.notify(f"保存失败：{type(e).__name__}: {e}", color="negative")
            return
        if changes:
            ui.notify(
                f"已保存到 kb_settings.yaml（{len(changes)} 项）；"
                "部分字段需要重启服务后生效。",
                color="positive", timeout=6000,
            )
            if mark_restart_needed is not None:
                try:
                    mark_restart_needed()
                except Exception:
                    pass
        else:
            ui.notify("配置未变化", color="info", timeout=3000)
        # 保存后顺带跑一次验证，给一个闭环反馈。
        res = validate_connection(t, values, timeout=5)
        _show_result(bool(res.get("ok")), str(res.get("message", "")), str(res.get("detail", "")))

    # ---- 布局 -------------------------------------------------------------

    with ui.card().classes("w-full q-mb-md q-pa-md"):
        with ui.row().classes("items-center w-full no-wrap q-mb-sm").style("gap:12px"):
            ui.label("向量库 / 检索引擎").classes("text-base font-semibold")
            type_select = (
                ui.select(
                    {t.key: t.label for t in VS_TYPES},
                    label="类型",
                    value=current_default_key,
                )
                .props("outlined dense")
                .style("min-width: 280px")
            )
            type_select.tooltip(
                "切换后下方会刷新字段；保存时会同步写入 DEFAULT_VS_TYPE。"
            )
            type_select.on("update:model-value", _on_type_change)
            ui.space()
            ui.label(f"配置文件：{load_res.path.name}").classes(
                "text-xs text-grey-7 font-mono"
            )

        fields_container = ui.column().classes("w-full q-gutter-xs")
        _render_fields(vs_type_by_key(current_default_key) or VS_TYPES[0])

        with ui.row().classes("items-center w-full no-wrap q-mt-sm").style("gap:8px"):
            result_row = ui.row().classes("items-center").style(
                "flex: 1; min-width: 0; gap: 6px;"
            )
            result_row.visible = False
            with result_row:
                result_icon = ui.icon("info").style("font-size: 20px")
                with ui.column().classes("q-gutter-none").style("min-width: 0"):
                    result_text = ui.label("").classes("text-sm")
                    result_detail = ui.label("").classes(
                        "text-xs text-grey-7 font-mono"
                    ).style("word-break: break-all")
                    result_detail.visible = False

            ui.space()
            ui.button("验证连接", icon="bolt", on_click=_on_validate).props(
                "color=secondary dense"
            ).tooltip("按当前类型发起一次真实连接（FAISS/Chromadb 会跳过，仅显示本地存储说明）")
            ui.button("保存", icon="save", on_click=_on_save).props(
                "color=primary dense"
            ).tooltip("写入 kb_settings.yaml 的 DEFAULT_VS_TYPE 与 kbs_config.<type>.*")
