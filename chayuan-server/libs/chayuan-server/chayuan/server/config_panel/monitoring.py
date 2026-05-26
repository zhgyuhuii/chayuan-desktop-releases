"""指标数据工具。

查询层（本模块内置）
--------------------
- 直接复用 ``chayuan.server.db`` 的 ``SessionLocal`` / ORM，不引入新 DB；
- 所有聚合都在 SQL 侧完成（``COUNT`` / ``GROUP BY``），避免把大表拉到内存；
- 统一做 **容错**：DB 不可达 / 表未初始化 / 返回 0 行，都返回空结果而不是抛错——
  面板首页应当是"看不到就空"，而不是直接崩掉配置面板。

渲染层
------
NiceGUI ``ui.echart`` 直接吐 ECharts 配置对象。所有样式集中在 _echart_theme，
方便统一外观。

只读：监控页绝不会写任何业务表，也不会动 yaml；调用侧 / CI / 停机环境都可安全跑。
"""
from __future__ import annotations

import logging
import socket
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("chayuan.config_panel.monitoring")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Overview:
    question_count: int = 0
    answer_count: int = 0
    conversation_count: int = 0
    feedback_count: int = 0
    feedback_avg: float = 0.0  # 0-100
    kb_count: int = 0
    kb_file_count: int = 0
    kb_total_bytes: int = 0

    @property
    def response_rate(self) -> float:
        if self.question_count == 0:
            return 0.0
        return round(100.0 * self.answer_count / self.question_count, 1)

    @property
    def feedback_rate(self) -> float:
        base = self.answer_count or self.question_count
        if base == 0:
            return 0.0
        return round(100.0 * self.feedback_count / base, 1)


@dataclass
class Bucket:
    """一个 (label, value) 对。"""
    label: str
    value: float


@dataclass
class MetricsSnapshot:
    overview: Overview = field(default_factory=Overview)
    # 时间序列：键是 'hour'/'day'/'week'/'month'
    series: Dict[str, List[Bucket]] = field(default_factory=dict)
    chat_types: List[Bucket] = field(default_factory=list)
    models: List[Bucket] = field(default_factory=list)
    vs_types: List[Bucket] = field(default_factory=list)
    top_kbs: List[Bucket] = field(default_factory=list)
    file_exts: List[Bucket] = field(default_factory=list)
    # 反馈分数直方：bucket 为 [0,20)/[20,40)/[40,60)/[60,80)/[80,100]
    feedback_hist: List[Bucket] = field(default_factory=list)
    # 热力图：7 × 24 矩阵，值为问题数
    heatmap: List[List[int]] = field(default_factory=lambda: [[0] * 24 for _ in range(7)])
    heatmap_max: int = 0
    # 元信息
    generated_at: datetime = field(default_factory=datetime.now)
    db_ok: bool = True
    db_error: str = ""
    # 已知字段缺失提示
    notices: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 查询层
# ---------------------------------------------------------------------------

def _safe(fn: Callable[..., Any], default: Any, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("metric query failed: %s: %s", type(e).__name__, e)
        return default


def load_metrics() -> MetricsSnapshot:
    """采集所有图表所需数据。整个过程不抛错。"""
    snap = MetricsSnapshot()

    try:
        from chayuan.server.db.session import session_scope
        from chayuan.server.db.models.message_model import MessageModel
        from chayuan.server.db.models.conversation_model import ConversationModel
        from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
        from chayuan.server.db.models.knowledge_file_model import KnowledgeFileModel
    except Exception as e:  # noqa: BLE001
        snap.db_ok = False
        snap.db_error = f"加载 ORM 失败：{type(e).__name__}: {e}"
        return snap

    # ---------- 基础 KPI ----------
    def _kpi(session):
        from sqlalchemy import func as _f
        o = snap.overview
        o.question_count = session.query(_f.count(MessageModel.id)).filter(
            MessageModel.query != None, MessageModel.query != ""
        ).scalar() or 0
        o.answer_count = session.query(_f.count(MessageModel.id)).filter(
            MessageModel.response != None, MessageModel.response != ""
        ).scalar() or 0
        o.conversation_count = session.query(_f.count(ConversationModel.id)).scalar() or 0

        # 反馈
        row = session.query(
            _f.count(MessageModel.id), _f.avg(MessageModel.feedback_score),
        ).filter(MessageModel.feedback_score >= 0).first()
        if row is not None:
            o.feedback_count = int(row[0] or 0)
            o.feedback_avg = float(row[1] or 0.0)

        # 知识库
        o.kb_count = session.query(_f.count(KnowledgeBaseModel.id)).scalar() or 0
        row2 = session.query(
            _f.count(KnowledgeFileModel.id), _f.coalesce(_f.sum(KnowledgeFileModel.file_size), 0),
        ).first()
        if row2 is not None:
            o.kb_file_count = int(row2[0] or 0)
            o.kb_total_bytes = int(row2[1] or 0)

    # ---------- 时间序列 ----------
    def _series(session) -> Dict[str, List[Bucket]]:
        now = datetime.now()
        rows = session.query(MessageModel.create_time).filter(
            MessageModel.query != None, MessageModel.query != "",
            MessageModel.create_time >= now - timedelta(days=400),
        ).all()
        times = [r[0] for r in rows if r[0] is not None]

        def bucket_by(labels: List[str], keyer: Callable[[datetime], Optional[str]]) -> List[Bucket]:
            c = Counter()
            for t in times:
                k = keyer(t)
                if k is not None:
                    c[k] += 1
            return [Bucket(label=l, value=c.get(l, 0)) for l in labels]

        # 最近 24 小时
        labels_h: List[str] = []
        for i in range(23, -1, -1):
            t = now - timedelta(hours=i)
            labels_h.append(t.strftime("%H:00"))
        def h_key(t: datetime) -> Optional[str]:
            delta = now - t
            if delta < timedelta(hours=0) or delta > timedelta(hours=24):
                return None
            return t.strftime("%H:00")
        hour = bucket_by(labels_h, h_key)

        # 最近 30 天
        labels_d = [(now - timedelta(days=i)).strftime("%m-%d") for i in range(29, -1, -1)]
        def d_key(t: datetime) -> Optional[str]:
            d = (now.date() - t.date()).days
            if d < 0 or d > 29:
                return None
            return t.strftime("%m-%d")
        day = bucket_by(labels_d, d_key)

        # 最近 12 周
        def week_start(d: datetime) -> datetime:
            return d - timedelta(days=d.weekday())
        this_week = week_start(now)
        labels_w: List[str] = []
        for i in range(11, -1, -1):
            wk = this_week - timedelta(weeks=i)
            labels_w.append(wk.strftime("%m-%d"))
        def w_key(t: datetime) -> Optional[str]:
            wk = week_start(t)
            delta_w = (this_week - wk).days // 7
            if delta_w < 0 or delta_w > 11:
                return None
            return wk.strftime("%m-%d")
        week = bucket_by(labels_w, w_key)

        # 最近 12 个月
        def month_key(d: datetime) -> str:
            return d.strftime("%Y-%m")
        labels_m: List[str] = []
        y, m = now.year, now.month
        for i in range(11, -1, -1):
            yy = y
            mm = m - i
            while mm <= 0:
                mm += 12
                yy -= 1
            labels_m.append(f"{yy:04d}-{mm:02d}")
        def m_key(t: datetime) -> Optional[str]:
            k = t.strftime("%Y-%m")
            return k if k in labels_m else None
        month = bucket_by(labels_m, m_key)

        # 顺带构造 heatmap（周 × 小时，最近 90 天）
        mat = [[0] * 24 for _ in range(7)]
        cutoff = now - timedelta(days=90)
        for t in times:
            if t < cutoff:
                continue
            mat[t.weekday()][t.hour] += 1
        snap.heatmap = mat
        snap.heatmap_max = max((v for row in mat for v in row), default=0)

        return {"hour": hour, "day": day, "week": week, "month": month}

    # ---------- 分类 ----------
    def _categories(session):
        from sqlalchemy import func as _f
        rows = session.query(
            MessageModel.chat_type, _f.count(MessageModel.id),
        ).filter(
            MessageModel.query != None, MessageModel.query != "",
        ).group_by(MessageModel.chat_type).all()
        snap.chat_types = [
            Bucket(label=(r[0] or "unknown"), value=int(r[1] or 0)) for r in rows
        ]

        # 向量库类型
        rows2 = session.query(
            KnowledgeBaseModel.vs_type, _f.count(KnowledgeBaseModel.id),
        ).group_by(KnowledgeBaseModel.vs_type).all()
        snap.vs_types = [
            Bucket(label=(r[0] or "unknown"), value=int(r[1] or 0)) for r in rows2
        ]

        # Top KBs（按文件数）
        rows3 = session.query(
            KnowledgeFileModel.kb_name, _f.count(KnowledgeFileModel.id),
        ).group_by(KnowledgeFileModel.kb_name).order_by(_f.count(KnowledgeFileModel.id).desc()).limit(10).all()
        snap.top_kbs = [
            Bucket(label=(r[0] or "unknown"), value=int(r[1] or 0)) for r in rows3
        ]

        # 文件扩展名 Top 10
        rows4 = session.query(
            KnowledgeFileModel.file_ext, _f.count(KnowledgeFileModel.id),
        ).group_by(KnowledgeFileModel.file_ext).order_by(_f.count(KnowledgeFileModel.id).desc()).limit(10).all()
        snap.file_exts = [
            Bucket(label=(r[0] or "(无)"), value=int(r[1] or 0)) for r in rows4
        ]

        # 模型使用分布：从最近 5000 条消息的 meta_data 里抽 model 字段
        rows_m = session.query(MessageModel.meta_data).filter(
            MessageModel.meta_data != None,
            MessageModel.query != None,
            MessageModel.query != "",
        ).order_by(MessageModel.create_time.desc()).limit(5000).all()
        mc: Counter = Counter()
        for (md,) in rows_m:
            try:
                if isinstance(md, dict):
                    name = str(md.get("model") or "").strip()
                    if name:
                        mc[name] += 1
            except Exception:  # noqa: BLE001
                continue
        snap.models = [
            Bucket(label=k, value=v)
            for k, v in sorted(mc.items(), key=lambda x: x[1], reverse=True)[:10]
        ]

        # 反馈分数直方
        edges = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]
        hist: List[Bucket] = []
        for lo, hi in edges:
            label = f"{lo}-{hi}"
            cnt = session.query(_f.count(MessageModel.id)).filter(
                MessageModel.feedback_score >= lo,
                MessageModel.feedback_score < (hi if hi < 100 else 101),
            ).scalar() or 0
            hist.append(Bucket(label=label, value=int(cnt)))
        snap.feedback_hist = hist

    try:
        with session_scope() as session:
            _safe(_kpi, None, session)
            snap.series = _safe(_series, {}, session) or {}
            _safe(_categories, None, session)
    except Exception as e:  # noqa: BLE001
        snap.db_ok = False
        snap.db_error = f"DB 连接失败：{type(e).__name__}: {e}"
        logger.warning(snap.db_error)
        return snap

    if not snap.models and snap.overview.question_count > 0:
        snap.notices.append(
            "模型使用分布为空：说明历史消息中未记录 `meta_data.model`。"
            "新会话起会自动写入，稍后重新打开本页即可看到。"
        )
    return snap


# ---------------------------------------------------------------------------
# 渲染层
# ---------------------------------------------------------------------------

# 使用 ECharts Top-level 配置时的公共主题
_PALETTE = [
    "#5470C6", "#91CC75", "#FAC858", "#EE6666", "#73C0DE",
    "#3BA272", "#FC8452", "#9A60B4", "#EA7CCC",
]


def _responsive_grid(ui, *, min_col: int = 240, gap: int = 12):
    """返回一个 auto-fit minmax CSS Grid 容器。

    比 ui.grid(columns=N) 可靠，因为：
    - 容器变窄会自动折行，不会把 N 个大卡片硬塞进去撑爆父容器；
    - 不依赖 q-gutter 这类 Quasar 工具类带的负边距，避免水平溢出。
    """
    el = ui.element("div").classes("w-full")
    el.style(
        f"display:grid;"
        f"grid-template-columns:repeat(auto-fit,minmax({min_col}px,1fr));"
        f"gap:{gap}px;"
        "min-width:0;"
    )
    return el


def _probe(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        p = int(port)
    except (TypeError, ValueError):
        return False
    h = (host or "127.0.0.1").strip()
    if h == "0.0.0.0":
        h = "127.0.0.1"
    try:
        with socket.create_connection((h, p), timeout=timeout):
            return True
    except OSError:
        return False


def _fmt_bytes(n: int) -> str:
    if n <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    i = 0
    while f >= 1024 and i < len(units) - 1:
        f /= 1024
        i += 1
    return f"{f:.2f} {units[i]}"


def render_monitoring(ui) -> None:
    """入口：在 NiceGUI 容器内渲染整个监控页面。"""
    snap = load_metrics()

    # 标题行：`no-wrap` 保证窗口窄时不会把标题和按钮撞到两行
    with ui.row().classes("items-center w-full no-wrap q-mb-xs").style("min-width:0"):
        ui.label("指标概览").classes("text-2xl font-semibold")
        ui.space()
        last = ui.label(f"更新：{snap.generated_at.strftime('%H:%M:%S')}").classes("text-xs text-grey-7")
        ui.button(icon="refresh", on_click=lambda: _refresh(ui, container, last)).props(
            "flat dense round"
        ).tooltip("立即刷新")

    ui.label(
        "数据来自已配置的知识库数据库（sqlite / postgresql / …）。图表每 60 秒自动刷新。"
    ).classes("text-sm text-grey-8 q-mb-md")

    if not snap.db_ok:
        with ui.card().classes("w-full q-mb-md bg-red-1"):
            ui.label("数据库不可用").classes("text-base font-semibold text-negative")
            ui.label(snap.db_error).classes("text-sm font-mono text-grey-8")
            ui.label(
                "配置面板可以继续使用，但首页图表暂时为空。"
                "检查 basic_settings.yaml 的 SQLALCHEMY_DATABASE_URI 是否正确。"
            ).classes("text-sm")

    # 主内容：用普通 column（gap 通过 style 控制，不用 q-gutter-md 避免负边距溢出）
    container = ui.column().classes("w-full").style("gap:12px;min-width:0")
    _render_into(ui, container, snap)

    # 60s 周期 refresh — 用 safe_timer_cb 防 client deleted 警告
    from chayuan.server.config_panel._safe_ui import safe_timer_cb
    ui.timer(60.0, safe_timer_cb(lambda: _refresh(ui, container, last)))


def _render_into(ui, container, snap: MetricsSnapshot) -> None:
    with container:
        _render_overview_cards(ui, snap)
        _render_services_strip(ui)
        _render_timeseries(ui, snap)
        _render_distribution_row(ui, snap)
        _render_bottom_row(ui, snap)
        if snap.notices:
            with ui.card().classes("w-full bg-blue-1"):
                ui.label("提示").classes("text-base font-semibold text-primary")
                for n in snap.notices:
                    ui.label("• " + n).classes("text-sm text-grey-8")


def _refresh(ui, container, last_label) -> None:
    snap = load_metrics()
    container.clear()
    _render_into(ui, container, snap)
    if last_label is not None:
        last_label.set_text(f"更新：{snap.generated_at.strftime('%H:%M:%S')}")


# ---------------- KPI 卡 ----------------

def _render_overview_cards(ui, snap: MetricsSnapshot) -> None:
    o = snap.overview

    cards: List[Tuple[str, str, str, str]] = [
        ("用户提问", f"{o.question_count:,}", f"回答率 {o.response_rate}%", "chat"),
        ("模型回答", f"{o.answer_count:,}", f"{o.conversation_count:,} 次会话", "smart_toy"),
        ("知识库", f"{o.kb_count:,}",
         f"{o.kb_file_count:,} 个文件 · {_fmt_bytes(o.kb_total_bytes)}", "menu_book"),
        ("反馈", f"{o.feedback_count:,}",
         f"均分 {o.feedback_avg:.1f} · 反馈率 {o.feedback_rate}%", "thumb_up"),
    ]
    with _responsive_grid(ui, min_col=220):
        for title, value, sub, icon in cards:
            with ui.card().classes("q-pa-md").style("min-width:0"):
                with ui.row().classes("items-center no-wrap").style("min-width:0"):
                    ui.icon(icon).classes("text-2xl text-primary q-mr-sm")
                    ui.label(title).classes("text-sm text-grey-8")
                ui.label(value).classes("text-3xl font-bold text-primary q-mt-xs")
                ui.label(sub).classes("text-xs text-grey-7 q-mt-xs")


def _render_services_strip(ui) -> None:
    from chayuan.settings import Settings

    bs = Settings.basic_settings
    api = dict(getattr(bs, "API_SERVER", {}) or {})
    cfg = dict(getattr(bs, "CONFIG_SERVER", {}) or {})
    rows = [
        ("API", api.get("host", "127.0.0.1"), api.get("port", 62581)),
        ("配置面板", cfg.get("host", "127.0.0.1"), cfg.get("port", 8502)),
    ]
    with ui.card().classes("w-full q-pa-sm"):
        with ui.row().classes("items-center w-full").style("gap:12px;flex-wrap:wrap"):
            ui.label("运行时").classes("text-sm font-semibold")
            for name, h, p in rows:
                ok = _probe(h, p)
                dot = "🟢" if ok else "🔴"
                ui.label(f"{dot} {name} {h}:{p}").classes("text-sm")


# ---------------- 时间序列 ----------------

def _render_timeseries(ui, snap: MetricsSnapshot) -> None:
    series = snap.series or {}

    def chart_option(key: str, label: str) -> dict:
        data = series.get(key) or []
        return {
            "color": _PALETTE,
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 40, "right": 20, "top": 30, "bottom": 40},
            "xAxis": {
                "type": "category",
                "data": [b.label for b in data],
                "axisLabel": {"rotate": 30 if key in ("day", "week") else 0, "fontSize": 10},
            },
            "yAxis": {"type": "value", "name": "提问数"},
            "series": [{
                "name": label,
                "type": "bar",
                "data": [b.value for b in data],
                "itemStyle": {"borderRadius": [4, 4, 0, 0]},
            }],
        }

    with ui.card().classes("w-full").style("min-width:0"):
        with ui.row().classes("items-center w-full no-wrap q-mb-sm").style("min-width:0"):
            ui.label("提问量时间分布").classes("text-base font-semibold")
            ui.space()
            tabs = ui.toggle(
                {"hour": "24h", "day": "30d", "week": "12w", "month": "12m"},
                value="day",
            ).props("dense")

        chart_holder: Dict[str, Any] = {"el": None}
        title_map = {"hour": "过去 24 小时", "day": "过去 30 天", "week": "过去 12 周", "month": "过去 12 个月"}

        def _repaint() -> None:
            key = tabs.value
            opt = chart_option(key, title_map.get(key, ""))
            if chart_holder["el"] is None:
                chart_holder["el"] = ui.echart(opt).classes("w-full").style("height:300px")
            else:
                chart_holder["el"].options = opt
                chart_holder["el"].update()

        _repaint()
        tabs.on("update:model-value", lambda _=None: _repaint())


# ---------------- 分类图一行两图 ----------------

def _render_distribution_row(ui, snap: MetricsSnapshot) -> None:
    with _responsive_grid(ui, min_col=320):
        _render_pie(ui, "对话类型分布", snap.chat_types)
        _render_pie(ui, "模型使用分布", snap.models)
        _render_pie(ui, "向量库类型分布", snap.vs_types)


def _render_pie(ui, title: str, data: List[Bucket]) -> None:
    with ui.card().classes("w-full").style("min-width:0"):
        ui.label(title).classes("text-base font-semibold q-mb-sm")
        if not data:
            ui.label("暂无数据").classes("text-sm text-grey-7")
            ui.echart({"color": _PALETTE,
                       "series": [{"type": "pie", "data": []}]}).classes("w-full").style("height:260px")
            return
        opt = {
            "color": _PALETTE,
            "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
            "legend": {"bottom": 0, "left": "center", "type": "scroll"},
            "series": [{
                "type": "pie",
                "radius": ["40%", "68%"],
                "avoidLabelOverlap": True,
                "itemStyle": {"borderRadius": 6, "borderColor": "#fff", "borderWidth": 2},
                "label": {"show": True, "formatter": "{b}\n{d}%"},
                "labelLine": {"show": True},
                "data": [{"name": b.label, "value": b.value} for b in data],
            }],
        }
        ui.echart(opt).classes("w-full").style("height:260px")


# ---------------- 底部：横道 / 反馈直方 / 热力图 ----------------

def _render_bottom_row(ui, snap: MetricsSnapshot) -> None:
    with _responsive_grid(ui, min_col=360):
        _render_top_kbs(ui, snap)
        _render_feedback_hist(ui, snap)
    _render_heatmap(ui, snap)
    # 文件扩展名单独一行
    with ui.card().classes("w-full"):
        ui.label("文件扩展名分布（Top 10）").classes("text-base font-semibold q-mb-sm")
        if not snap.file_exts:
            ui.label("暂无知识库文件。").classes("text-sm text-grey-7")
            return
        labels = [b.label for b in snap.file_exts]
        values = [b.value for b in snap.file_exts]
        opt = {
            "color": _PALETTE,
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 40, "right": 20, "top": 20, "bottom": 30},
            "xAxis": {"type": "category", "data": labels},
            "yAxis": {"type": "value"},
            "series": [{
                "type": "bar", "data": values,
                "itemStyle": {"borderRadius": [4, 4, 0, 0]},
            }],
        }
        ui.echart(opt).classes("w-full").style("height:260px")


def _render_top_kbs(ui, snap: MetricsSnapshot) -> None:
    with ui.card().classes("w-full").style("min-width:0"):
        ui.label("知识库文件数 Top 10").classes("text-base font-semibold q-mb-sm")
        data = list(reversed(snap.top_kbs))  # 横道从下到上
        if not data:
            ui.label("暂无知识库。").classes("text-sm text-grey-7")
            ui.echart({"color": _PALETTE, "series": []}).classes("w-full").style("height:260px")
            return
        labels = [b.label for b in data]
        values = [b.value for b in data]
        opt = {
            "color": _PALETTE,
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 100, "right": 30, "top": 10, "bottom": 30},
            "xAxis": {"type": "value"},
            "yAxis": {"type": "category", "data": labels},
            "series": [{
                "type": "bar", "data": values, "label": {"show": True, "position": "right"},
                "itemStyle": {"borderRadius": [0, 4, 4, 0]},
            }],
        }
        ui.echart(opt).classes("w-full").style("height:260px")


def _render_feedback_hist(ui, snap: MetricsSnapshot) -> None:
    with ui.card().classes("w-full").style("min-width:0"):
        ui.label("反馈分数分布").classes("text-base font-semibold q-mb-sm")
        data = snap.feedback_hist
        if not data or all(b.value == 0 for b in data):
            ui.label("暂无反馈数据。").classes("text-sm text-grey-7")
            ui.echart({"color": _PALETTE, "series": []}).classes("w-full").style("height:260px")
            return
        labels = [b.label for b in data]
        values = [b.value for b in data]
        opt = {
            "color": ["#91CC75"],
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 40, "right": 20, "top": 10, "bottom": 40},
            "xAxis": {"type": "category", "data": labels, "name": "分数段"},
            "yAxis": {"type": "value"},
            "series": [{
                "type": "bar", "data": values, "barMaxWidth": 48,
                "itemStyle": {"borderRadius": [4, 4, 0, 0]},
            }],
        }
        ui.echart(opt).classes("w-full").style("height:260px")


def _render_heatmap(ui, snap: MetricsSnapshot) -> None:
    with ui.card().classes("w-full").style("min-width:0"):
        ui.label("提问热力图（近 90 天 · 星期 × 小时）").classes("text-base font-semibold q-mb-sm")

        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        hours = [f"{h}" for h in range(24)]
        points = []
        for d in range(7):
            for h in range(24):
                v = snap.heatmap[d][h]
                points.append([h, d, v])
        vmax = max(snap.heatmap_max, 1)

        if snap.heatmap_max == 0:
            ui.label("最近 90 天无提问记录。").classes("text-sm text-grey-7")

        opt = {
            "tooltip": {
                "position": "top",
                "formatter": "周{b1}  {c0}:00  {c2} 条",
            },
            "grid": {"left": 50, "right": 20, "top": 10, "bottom": 30},
            "xAxis": {"type": "category", "data": hours, "splitArea": {"show": True}},
            "yAxis": {"type": "category", "data": weekdays, "splitArea": {"show": True}},
            "visualMap": {
                "min": 0, "max": vmax,
                "calculable": True,
                "orient": "horizontal",
                "left": "center", "bottom": 0,
                "inRange": {"color": ["#eff6ff", "#60a5fa", "#1d4ed8"]},
            },
            "series": [{
                "type": "heatmap",
                "data": points,
                "label": {"show": vmax <= 20},
                "emphasis": {"itemStyle": {"shadowBlur": 10, "shadowColor": "rgba(0,0,0,.3)"}},
            }],
        }
        ui.echart(opt).classes("w-full").style("height:260px")


__all__ = [
    "MetricsSnapshot", "Overview", "Bucket",
    "load_metrics", "render_monitoring",
]
