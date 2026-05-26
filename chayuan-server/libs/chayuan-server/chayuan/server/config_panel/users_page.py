"""Config Panel · 用户管理页。

定位
----
面板内的"用户运营中心"。除了最基础的 CRUD（启停 / 改密 / 新建）之外，还要能
**一眼看出每个用户做了什么**：提了多少问、用过哪些模型 / 工具 / 知识库、
估算消耗了多少 tokens、活跃时段是什么样、反馈得分分布如何。

页面结构（从上到下）
--------------------
1. **Hero 区**：
   - 最近 30 天每日消息量 + 活跃用户数 的双轴折线图；
   - 4 张 KPI 卡：总用户 / 近 7 日活跃 / 总消息 / 估算总 tokens。
2. **Top 10 卡片组**（300×200 横向网格）：
   - Top 10 · 按估算 tokens；
   - Top 10 · 按消息数；
   - Top 10 · 模型使用频率；
   - Top 10 · 工具使用频率；
   - Top 10 · 知识库使用频率；
   - 对话类型分布（pie）。
3. **洞察图表**：
   - 用户 × 模型 热力图（Top 10 用户 × Top 10 模型，看每人偏好）；
   - 用户活跃热力图（星期 × 小时，最近 90 天）；
   - 反馈分数分布（雷达）。
4. **用户列表表格**：
   - 支持关键字搜索、排序；每一行包含当期的消息数 / tokens / 反馈均分；
   - 行内操作：画像（AI 分析） / 改密 / 启停。
5. **AI 用户画像对话框**：
   - 单个用户的详细 KPI、模型 / 工具 / 知识库 / 时段 / 最近提问；
   - 一键让配置好的 LLM 生成"这个用户的使用画像 + 产品建议"。

数据采集约束
------------
- **tokens**：表里没有原始 token 数，用 ``len(query + response)``（粗略但可对
  比）。这是公认的"估算"路径；真实精度需后续在写库时加埋点。
- **工具使用**：``meta_data.intermediate_steps`` 是 Langchain ``dumps()`` 的
  JSON 串，一层层剥 ``kwargs.tool`` 字段；解析失败静默跳过。
- **知识库使用**：``kb_chat`` 目前不写 ``message`` 表，所以只能通过
  ``meta_data.kb_name`` / ``chat_type=kb_chat`` / conversation 名前缀等启发式
  推断；缺数据时会显示"暂无"，不影响其它卡。

全部查询走单次 ``session_scope``，一次拉齐所有需要的数据并在 Python 端做分组；
面板不要起新的 DB 连接流量。
"""
from __future__ import annotations

import collections
import html as _html
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("chayuan.config_panel.users_page")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

# 匿名 / legacy 消息在快照里用 user_id=0 代表，便于统一渲染
_ANON_UID = 0
_ANON_LABEL = "（匿名 / legacy）"

# 每个汉字 ≈ 1 token；英文 1 token ≈ 4 char；实际混合文本用 1.5 这个系数
# 得到的"估算 tokens"比字符数小一点，更接近用户认知。纯启发式，别较真。
_TOKENS_PER_CHAR = 1.0 / 1.5


def _estimate_tokens(text_len: int) -> int:
    return int(text_len * _TOKENS_PER_CHAR)


@dataclass
class UserInfo:
    id: int
    username: str
    role: str = "user"
    email: str = ""
    is_active: bool = True
    last_login_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


@dataclass
class UserStats:
    user: UserInfo
    msg_count: int = 0
    est_tokens: int = 0           # 基于字符长度估算
    total_chars: int = 0
    avg_feedback: Optional[float] = None
    feedback_count: int = 0
    first_active: Optional[datetime] = None
    last_active: Optional[datetime] = None
    model_usage: Dict[str, int] = field(default_factory=dict)
    chat_type_usage: Dict[str, int] = field(default_factory=dict)
    tool_usage: Dict[str, int] = field(default_factory=dict)
    kb_usage: Dict[str, int] = field(default_factory=dict)
    # 活跃度热力图 7 × 24（星期 × 小时）
    heatmap: List[List[int]] = field(default_factory=lambda: [[0] * 24 for _ in range(7)])
    recent_queries: List[Tuple[datetime, str, str, str]] = field(default_factory=list)
    # (create_time, chat_type, query(可能截断), model)


@dataclass
class UsersSnapshot:
    users: List[UserStats] = field(default_factory=list)
    daily_msg: List[Tuple[str, int]] = field(default_factory=list)    # (date, msg_count)
    daily_active: List[Tuple[str, int]] = field(default_factory=list) # (date, active_users)
    total_users: int = 0
    active_7d: int = 0
    total_messages: int = 0
    total_tokens: int = 0
    total_feedback: int = 0
    avg_feedback: float = 0.0
    # 聚合 Top10 的便捷缓存
    top_model: List[Tuple[str, int]] = field(default_factory=list)
    top_tool: List[Tuple[str, int]] = field(default_factory=list)
    top_kb: List[Tuple[str, int]] = field(default_factory=list)
    top_chat_type: List[Tuple[str, int]] = field(default_factory=list)
    # 用户 × 模型 热力（Top10 × Top10）
    user_model_matrix: List[Tuple[str, str, int]] = field(default_factory=list)
    # 全局 7×24 活跃热力（所有用户）
    global_heatmap: List[List[int]] = field(default_factory=lambda: [[0] * 24 for _ in range(7)])
    global_heatmap_max: int = 0
    # 反馈分数分布
    feedback_hist: List[int] = field(default_factory=lambda: [0, 0, 0, 0, 0])
    generated_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# 数据采集
# ---------------------------------------------------------------------------

def _safe_parse_intermediate_steps(raw: Any) -> List[str]:
    """从 agent meta_data.intermediate_steps 里抽出调用过的工具名列表。

    Langchain ``dumps()`` 的 JSON 结构大致是::

        [ [{"lc":1,"type":"constructor","id":[...],
            "kwargs":{"tool":"search_local_knowledgebase", "tool_input":...}},
           "observation 文本"],
          ... ]

    我们只关心 ``kwargs.tool`` 字段；任意层级解析失败都返回空列表。
    """
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        data = list(raw)
    elif isinstance(raw, str):
        try:
            data = json.loads(raw)
        except Exception:
            # 兜底正则：任何 `"tool": "xxx"` 都算一次工具调用
            return re.findall(r'"tool"\s*:\s*"([^"\\]+)"', raw)
    else:
        return []

    tools: List[str] = []
    if not isinstance(data, list):
        return tools
    for step in data:
        # step 可能是 [action, observation] 的二元组；也可能是单个 dict
        action = None
        if isinstance(step, (list, tuple)) and step:
            action = step[0]
        elif isinstance(step, dict):
            action = step
        if not isinstance(action, dict):
            continue
        kwargs = action.get("kwargs") or {}
        tool_name = kwargs.get("tool") or action.get("tool")
        if isinstance(tool_name, str) and tool_name:
            tools.append(tool_name)
    return tools


def _extract_kb_name(chat_type: str, meta: Dict[str, Any], conv_name: str = "") -> Optional[str]:
    """从 meta_data 或会话名里启发式提取知识库名。

    - 明面字段：``meta.kb_name`` / ``meta.knowledge_base`` / ``meta.kb``；
    - ``chat_type`` = 'kb_chat' 且 conversation.name 以 '[KB]' 或 'kb/<name>' 开头；
    - 否则返回 None（"未识别"）。
    """
    if isinstance(meta, dict):
        for k in ("kb_name", "knowledge_base", "kb"):
            v = meta.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    ct = (chat_type or "").lower()
    if "kb" in ct or "knowledge" in ct:
        # 尝试从会话名里抠
        m = re.search(r"\[([^\]]+)\]", conv_name or "")
        if m:
            return m.group(1).strip()
        return "（未命名 KB）"
    return None


def build_snapshot() -> UsersSnapshot:
    """一次性抓全量用户 + 近 400 天的消息并聚合；失败时返回空快照。"""
    snap = UsersSnapshot()
    try:
        from chayuan.server.db.session import session_scope
        from chayuan.server.db.models.user_model import UserModel
        from chayuan.server.db.models.message_model import MessageModel
        from chayuan.server.db.models.conversation_model import ConversationModel
    except Exception as e:  # noqa: BLE001
        logger.warning("import ORM failed: %s", e)
        return snap

    try:
        with session_scope() as s:
            # 1) 拉所有用户
            users = s.query(UserModel).order_by(UserModel.id).all()
            user_map: Dict[int, UserStats] = {}
            for u in users:
                info = UserInfo(
                    id=int(u.id),
                    username=u.username,
                    role=u.role or "user",
                    email=u.email or "",
                    is_active=bool(u.is_active),
                    last_login_at=u.last_login_at,
                    created_at=u.created_at,
                )
                user_map[info.id] = UserStats(user=info)
            # 匿名桶（user_id 为空的历史消息走这里）
            user_map[_ANON_UID] = UserStats(
                user=UserInfo(id=_ANON_UID, username=_ANON_LABEL, role="anon", is_active=True)
            )
            snap.total_users = len(users)

            # 2) 拉最近 400 天的消息（太老的对用户画像价值不大）
            cutoff = datetime.now() - timedelta(days=400)
            rows = s.query(
                MessageModel.id,
                MessageModel.user_id,
                MessageModel.chat_type,
                MessageModel.query,
                MessageModel.response,
                MessageModel.meta_data,
                MessageModel.feedback_score,
                MessageModel.create_time,
                MessageModel.conversation_id,
            ).filter(MessageModel.create_time >= cutoff).all()

            # 拉对话表（仅 id → chat_type/name 的映射，用于 KB 启发式）
            conv_map: Dict[str, Tuple[str, str]] = {}
            for c in s.query(
                ConversationModel.id, ConversationModel.name, ConversationModel.chat_type
            ).all():
                conv_map[str(c.id)] = (c.name or "", c.chat_type or "")

            # 3) 分组聚合
            daily_counter: Dict[str, int] = collections.defaultdict(int)
            daily_active_users: Dict[str, set] = collections.defaultdict(set)
            active_since = datetime.now() - timedelta(days=7)
            active_7d_users: set = set()
            total_messages = 0
            total_chars = 0
            feedback_sum = 0.0
            feedback_count = 0
            feedback_edges = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]

            for row in rows:
                mid = row[0]
                uid = int(row[1] or 0) or _ANON_UID
                chat_type = row[2] or ""
                q = row[3] or ""
                r_ = row[4] or ""
                meta = row[5] or {}
                fb = int(row[6] if row[6] is not None else -1)
                ctime = row[7]
                conv_id = str(row[8] or "")

                stats = user_map.get(uid) or user_map[_ANON_UID]
                stats.msg_count += 1
                total_messages += 1
                length = len(q) + len(r_)
                total_chars += length
                stats.total_chars += length
                stats.est_tokens += _estimate_tokens(length)

                if ctime is not None:
                    if stats.first_active is None or ctime < stats.first_active:
                        stats.first_active = ctime
                    if stats.last_active is None or ctime > stats.last_active:
                        stats.last_active = ctime
                    day = ctime.strftime("%m-%d")
                    daily_counter[day] += 1
                    if uid != _ANON_UID:
                        daily_active_users[day].add(uid)
                    if ctime >= active_since and uid != _ANON_UID:
                        active_7d_users.add(uid)
                    # 全局热力 7×24
                    try:
                        snap.global_heatmap[ctime.weekday()][ctime.hour] += 1
                        stats.heatmap[ctime.weekday()][ctime.hour] += 1
                    except Exception:
                        pass

                if fb >= 0:
                    feedback_sum += fb
                    feedback_count += 1
                    stats.feedback_count += 1
                    prev_avg = stats.avg_feedback or 0.0
                    prev_n = stats.feedback_count - 1
                    stats.avg_feedback = (prev_avg * prev_n + fb) / stats.feedback_count
                    # 累计到分布桶
                    for i, (lo, hi) in enumerate(feedback_edges):
                        if lo <= fb < (hi if hi < 100 else 101):
                            snap.feedback_hist[i] += 1
                            break

                # 模型
                model_name = ""
                if isinstance(meta, dict):
                    model_name = str(meta.get("model") or "").strip()
                if model_name:
                    stats.model_usage[model_name] = stats.model_usage.get(model_name, 0) + 1

                # 对话类型
                if chat_type:
                    stats.chat_type_usage[chat_type] = stats.chat_type_usage.get(chat_type, 0) + 1

                # 工具（agent 才有 intermediate_steps）
                if isinstance(meta, dict):
                    tool_names = _safe_parse_intermediate_steps(meta.get("intermediate_steps"))
                    for t in tool_names:
                        stats.tool_usage[t] = stats.tool_usage.get(t, 0) + 1

                # 知识库（多源拼合）
                conv_name, conv_type = conv_map.get(conv_id, ("", ""))
                kb = _extract_kb_name(chat_type or conv_type, meta if isinstance(meta, dict) else {}, conv_name)
                if kb:
                    stats.kb_usage[kb] = stats.kb_usage.get(kb, 0) + 1

                # 最近 10 条原始提问（用于画像对话框）
                if len(stats.recent_queries) < 10 and q:
                    stats.recent_queries.append(
                        (ctime or datetime.now(), chat_type or "", q[:200], model_name)
                    )

            # 4) 写回 snap 的全局统计
            snap.total_messages = total_messages
            snap.total_tokens = _estimate_tokens(total_chars)
            snap.active_7d = len(active_7d_users)
            snap.total_feedback = feedback_count
            snap.avg_feedback = (feedback_sum / feedback_count) if feedback_count else 0.0

            # 最近 30 天序列
            today = datetime.now()
            labels_30 = [(today - timedelta(days=i)).strftime("%m-%d") for i in range(29, -1, -1)]
            snap.daily_msg = [(d, daily_counter.get(d, 0)) for d in labels_30]
            snap.daily_active = [(d, len(daily_active_users.get(d, set()))) for d in labels_30]

            # 全局热力 max
            snap.global_heatmap_max = max(
                (v for row in snap.global_heatmap for v in row), default=0
            )

            # 聚合 Top10：模型 / 工具 / KB / 对话类型
            model_counter: collections.Counter = collections.Counter()
            tool_counter: collections.Counter = collections.Counter()
            kb_counter: collections.Counter = collections.Counter()
            ct_counter: collections.Counter = collections.Counter()
            for st in user_map.values():
                model_counter.update(st.model_usage)
                tool_counter.update(st.tool_usage)
                kb_counter.update(st.kb_usage)
                ct_counter.update(st.chat_type_usage)
            snap.top_model = model_counter.most_common(10)
            snap.top_tool = tool_counter.most_common(10)
            snap.top_kb = kb_counter.most_common(10)
            snap.top_chat_type = ct_counter.most_common(10)

            # 用户 × 模型 矩阵（展示用：Top10 用户 × Top10 模型）
            top_users_by_tokens = sorted(
                [st for st in user_map.values() if st.msg_count > 0],
                key=lambda s_: s_.est_tokens, reverse=True,
            )[:10]
            top_models = [name for name, _v in snap.top_model[:10]]
            for st in top_users_by_tokens:
                for m in top_models:
                    v = st.model_usage.get(m, 0)
                    if v:
                        snap.user_model_matrix.append((st.user.username, m, v))

            # 保留所有用户（包括无消息的），由 UI 决定展示策略
            snap.users = sorted(
                user_map.values(),
                key=lambda s_: (-s_.est_tokens, s_.user.id),
            )

    except Exception as e:  # noqa: BLE001
        logger.exception("build_snapshot failed: %s", e)

    return snap


# ---------------------------------------------------------------------------
# 管理操作（原有功能保留）
# ---------------------------------------------------------------------------

def _list_users_raw():
    from chayuan.server.auth.service import list_users
    return list_users(limit=500)


def _create(username: str, password: str, role: str, email: str) -> str:
    from chayuan.server.auth.service import create_user
    u = create_user(
        username=username.strip(),
        password=password,
        role=role,
        email=(email or None),
    )
    return f"已创建用户 {u.username} (id={u.id}, role={u.role})"


def _set_password(user_id: int, new_pwd: str) -> str:
    from chayuan.server.auth.service import set_password
    set_password(user_id, new_pwd)
    return f"用户 #{user_id} 密码已更新"


def _set_active(user_id: int, active: bool) -> str:
    from chayuan.server.auth.service import set_active
    set_active(user_id, active)
    return f"用户 #{user_id} 已{'启用' if active else '停用'}"


# ---------------------------------------------------------------------------
# 样式
# ---------------------------------------------------------------------------

_STYLE_FLAG = "_chayuan_users_styles_injected"


def _inject_styles(ui) -> None:
    if getattr(ui, _STYLE_FLAG, False):
        return
    css = r"""
    <style>
      .u-hero {
        background: linear-gradient(135deg, #312e81 0%, #1e3a8a 50%, #155e75 100%);
        color: #e0e7ff;
        border-radius: 16px;
        padding: 18px 22px 14px;
        position: relative;
        overflow: hidden;
      }
      .u-hero::after {
        content:''; position:absolute; inset:0;
        background: radial-gradient(circle at 15% 15%, rgba(59,130,246,0.25) 0%, transparent 55%),
                    radial-gradient(circle at 85% 115%, rgba(236,72,153,0.18) 0%, transparent 50%);
        pointer-events:none;
      }
      .u-hero-title { font-size:18px; font-weight:700; letter-spacing:.02em; }
      .u-hero-sub { font-size:12px; opacity:.8; margin-top:4px; }

      .u-kpi-row { display:grid; grid-template-columns: repeat(4, 1fr); gap:14px; margin-top:14px; position:relative; z-index:1; }
      @media (max-width: 880px) { .u-kpi-row { grid-template-columns: repeat(2, 1fr); } }
      .u-kpi {
        background: rgba(15,23,42,0.45);
        border: 1px solid rgba(148,163,184,0.25);
        border-radius: 10px;
        padding: 10px 14px;
      }
      .u-kpi .kpi-label { font-size:11px; opacity:.8; }
      .u-kpi .kpi-value { font-size:24px; font-weight:700; color:#f8fafc; letter-spacing:-.02em; margin-top:2px; }
      .u-kpi .kpi-sub { font-size:10px; opacity:.75; margin-top:2px; }

      .u-grid { display:grid; grid-template-columns: repeat(auto-fill, 300px); gap:16px; justify-content:center; }
      .u-card {
        width:300px; height:200px;
        background:#fff; border:1px solid #e5e7eb; border-radius:14px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.04);
        padding: 10px 14px 8px; overflow:hidden;
        display:flex; flex-direction:column;
        position: relative;
        transition: box-shadow .2s ease, transform .15s ease;
      }
      .u-card::before {
        content:''; position:absolute; top:0; left:0; right:0; height:3px;
        background: linear-gradient(90deg, var(--u-accent,#3b82f6), transparent 85%); opacity:.85;
      }
      .u-card:hover { box-shadow: 0 8px 20px rgba(59,130,246,0.15); transform: translateY(-1px); }
      .u-card-wide { width: 616px; grid-column: span 2; }
      @media (max-width: 680px) { .u-card-wide { width: 300px; grid-column: auto; } }
      .u-card .u-head {
        display:flex; align-items:center; gap:6px; margin-bottom:4px; flex:0 0 auto;
      }
      .u-card .u-title {
        font-size:13px; font-weight:600; color:#111827;
        flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
      }
      .u-card .u-badge {
        font-size:10px; padding:1px 7px; border-radius:999px;
        background:#f1f5f9; color:#475569;
      }
      .u-card .u-body {
        flex:1; min-height:0; display:flex; flex-direction:column;
      }
      .u-chart { width:100%; height:160px; }
      .u-chart-wide { width:100%; height:160px; }

      /* 用户表 */
      .u-table-wrap { width:100%; overflow:auto; border:1px solid #e5e7eb; border-radius:10px; }
      .u-table { width:100%; border-collapse:collapse; font-size:13px; background:#fff; }
      .u-table th {
        position:sticky; top:0; z-index:1;
        background:#f8fafc; color:#475569; font-weight:600;
        text-align:left; padding:10px 12px; border-bottom:1px solid #e5e7eb;
        font-size:12px; white-space:nowrap;
      }
      .u-table td { padding:8px 12px; border-bottom:1px solid #f1f5f9; vertical-align: middle; }
      .u-table tr:hover td { background:#f9fafb; }
      .u-table .u-user-chip {
        display:inline-flex; align-items:center; gap:8px;
      }
      .u-table .u-avatar {
        width:28px; height:28px; border-radius:50%;
        background: linear-gradient(135deg, var(--u-accent,#3b82f6), #8b5cf6);
        color:#fff; font-size:11px; font-weight:700;
        display:flex; align-items:center; justify-content:center;
        text-transform: uppercase;
        flex: 0 0 auto;
      }
      .u-table .role-admin { color:#b45309; background:rgba(245,158,11,0.12); border:1px solid #fcd34d; padding:1px 8px; border-radius:999px; font-size:11px; }
      .u-table .role-user  { color:#4b5563; background:#f1f5f9; border:1px solid #e5e7eb; padding:1px 8px; border-radius:999px; font-size:11px; }
      .u-table .role-anon  { color:#7c3aed; background:rgba(139,92,246,0.12); border:1px solid #c4b5fd; padding:1px 8px; border-radius:999px; font-size:11px; }
      .u-table .status-on  { color:#15803d; background:rgba(34,197,94,0.12); border:1px solid #86efac; padding:1px 8px; border-radius:999px; font-size:11px; }
      .u-table .status-off { color:#b91c1c; background:rgba(239,68,68,0.12); border:1px solid #fca5a5; padding:1px 8px; border-radius:999px; font-size:11px; }
      .u-table td.num { text-align:right; font-variant-numeric: tabular-nums; }

      /* AI 画像弹窗 */
      .u-persona {
        background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 55%, #4c1d95 100%);
        color: #f1f5f9;
        border-radius: 14px;
        padding: 16px 18px;
        margin-top: 14px;
        position: relative; overflow: hidden;
      }
      .u-persona-body {
        background: rgba(15,23,42,0.45);
        border: 1px solid rgba(148,163,184,0.25);
        border-radius: 10px;
        padding: 12px 14px;
        margin-top: 10px;
        font-size: 13px; line-height: 1.65;
        max-height: 340px; overflow: auto;
        white-space: pre-wrap;
      }
      .u-persona-body h1, .u-persona-body h2, .u-persona-body h3 { color:#e0e7ff; margin-top:10px; }
      .u-persona-body strong { color:#f8fafc; }
      .u-persona-body code { color:#93c5fd; }
    </style>
    """
    ui.add_head_html(css)
    setattr(ui, _STYLE_FLAG, True)


# ---------------------------------------------------------------------------
# 页面入口
# ---------------------------------------------------------------------------

def render_users_page(ui) -> None:
    _inject_styles(ui)

    state: Dict[str, Any] = {
        "snapshot": None,
        "search_text": "",
        "table_area": None,
        "dialog": None,
    }

    def _refresh_snapshot() -> None:
        state["snapshot"] = build_snapshot()

    _refresh_snapshot()

    # ---- Header -----------------------------------------------------------
    with ui.row().classes("items-center w-full no-wrap q-mb-sm"):
        ui.label("用户管理").classes("text-2xl font-semibold")
        ui.space()
        ui.button(
            icon="refresh",
            on_click=lambda: (_refresh_snapshot(), _redraw_all(ui, state)),
        ).props("flat round dense").tooltip("重新抓取全量数据并重绘所有图表")

    ui.label(
        "页面会聚合消息表做用户画像：提问次数、使用的模型 / 工具 / 知识库、估算"
        "token 消耗、活跃时段、反馈分数。每一行都可以点「画像」让大模型给出"
        "单人运营洞察。"
    ).classes("text-sm text-grey-8 q-mb-md")

    # ---- Hero --------------------------------------------------------------
    hero_area = ui.element("div").classes("w-full")
    state["hero_area"] = hero_area
    _render_hero(ui, state)

    # ---- Top 10 网格 -------------------------------------------------------
    ui.label("Top 10 · 用户画像").classes("text-base font-semibold q-mt-md q-mb-xs text-grey-8")
    top10_area = ui.element("div").classes("w-full")
    state["top10_area"] = top10_area
    _render_top10(ui, state)

    # ---- 洞察图表 -----------------------------------------------------------
    ui.label("洞察图表").classes("text-base font-semibold q-mt-md q-mb-xs text-grey-8")
    insight_area = ui.element("div").classes("w-full")
    state["insight_area"] = insight_area
    _render_insight(ui, state)

    # ---- 用户表 -------------------------------------------------------------
    with ui.row().classes("items-center w-full q-mt-md q-mb-xs"):
        ui.label("用户列表").classes("text-base font-semibold text-grey-8")
        ui.space()
        search = ui.input(placeholder="关键字：用户名 / 邮箱 / 角色").props(
            "outlined dense clearable"
        ).classes("w-64")

        def _on_search(_=None) -> None:
            state["search_text"] = str(getattr(search, "value", "") or "").strip().lower()
            _render_table(ui, state)

        search.on("update:model-value", _on_search)

    table_area = ui.element("div").classes("w-full")
    state["table_area"] = table_area
    _render_table(ui, state)

    # ---- 新建用户 + 批量操作 ------------------------------------------------
    ui.separator().classes("q-my-md")
    ui.label("新建用户").classes("text-base font-semibold q-mb-xs")
    with ui.row().classes("items-end w-full q-gutter-sm q-mb-md"):
        uname = ui.input("用户名").classes("w-40")
        email = ui.input("邮箱（可选）").classes("w-56")
        pwd = ui.input("初始密码", password=True).classes("w-40")
        role = ui.select(
            {"user": "普通用户", "admin": "管理员"}, value="user", label="角色",
        ).classes("w-40")

        def _on_create() -> None:
            if not getattr(uname, "value", ""):
                ui.notify("用户名必填", color="warning")
                return
            if not getattr(pwd, "value", ""):
                ui.notify("密码必填", color="warning")
                return
            try:
                msg = _create(uname.value, pwd.value, role.value or "user", email.value or "")
                ui.notify(msg, color="positive")
                uname.value = ""
                email.value = ""
                pwd.value = ""
                _refresh_snapshot()
                _redraw_all(ui, state)
            except Exception as e:  # noqa: BLE001
                ui.notify(f"创建失败：{e!r}", color="negative")

        ui.button("创建", icon="person_add", on_click=_on_create).props("color=primary no-caps")


def _redraw_all(ui, state: Dict[str, Any]) -> None:
    for name, renderer in [
        ("hero_area", _render_hero),
        ("top10_area", _render_top10),
        ("insight_area", _render_insight),
        ("table_area", _render_table),
    ]:
        el = state.get(name)
        if el is not None:
            try:
                el.clear()
                with el:
                    renderer(ui, state, _no_outer=True)
            except Exception as e:  # noqa: BLE001
                logger.warning("redraw %s failed: %s", name, e)


# ---------------------------------------------------------------------------
# Hero 区
# ---------------------------------------------------------------------------

def _render_hero(ui, state: Dict[str, Any], *, _no_outer: bool = False) -> None:
    snap: UsersSnapshot = state["snapshot"]
    host = state.get("hero_area") if not _no_outer else None
    ctx = host if host is not None else None

    def _body() -> None:
        with ui.element("div").classes("u-hero"):
            with ui.row().classes("items-center w-full no-wrap").style(
                "position:relative;z-index:1;gap:12px"
            ):
                ui.html('<i class="material-icons" style="font-size:28px;color:#c7d2fe">groups</i>')
                with ui.column().classes("q-gutter-none").style("flex:1"):
                    ui.html('<div class="u-hero-title">用户使用情况 · 近 30 天</div>')
                    ui.html(
                        '<div class="u-hero-sub">每天的消息量（柱）与日活用户数（折线）；'
                        '活跃高峰多落在工作日白天，低谷在周末清晨。</div>'
                    )

            # 双轴图
            labels = [d for d, _v in snap.daily_msg]
            msg_values = [v for _d, v in snap.daily_msg]
            active_values = [v for _d, v in snap.daily_active]
            option = {
                "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
                "legend": {"top": 4, "textStyle": {"color": "#e0e7ff", "fontSize": 11},
                           "itemWidth": 12, "itemHeight": 8},
                "grid": {"left": 40, "right": 40, "top": 34, "bottom": 28},
                "xAxis": {"type": "category", "data": labels,
                          "axisLabel": {"color": "#c7d2fe", "fontSize": 10, "rotate": 30},
                          "axisLine": {"lineStyle": {"color": "rgba(255,255,255,0.2)"}}},
                "yAxis": [
                    {"type": "value", "name": "消息数",
                     "nameTextStyle": {"color": "#c7d2fe", "fontSize": 10},
                     "axisLabel": {"color": "#c7d2fe", "fontSize": 10},
                     "splitLine": {"lineStyle": {"color": "rgba(148,163,184,0.15)"}}},
                    {"type": "value", "name": "活跃用户",
                     "nameTextStyle": {"color": "#fde68a", "fontSize": 10},
                     "axisLabel": {"color": "#fde68a", "fontSize": 10},
                     "splitLine": {"show": False}},
                ],
                "series": [
                    {"name": "消息数", "type": "bar", "yAxisIndex": 0,
                     "data": msg_values, "barMaxWidth": 14,
                     "itemStyle": {"color": {
                         "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
                         "colorStops": [
                             {"offset": 0, "color": "#60a5fa"},
                             {"offset": 1, "color": "#1e40af"},
                         ],
                     }, "borderRadius": [4, 4, 0, 0]}},
                    {"name": "活跃用户", "type": "line", "yAxisIndex": 1,
                     "data": active_values, "smooth": True,
                     "symbol": "circle", "symbolSize": 6,
                     "lineStyle": {"color": "#fbbf24", "width": 2},
                     "itemStyle": {"color": "#fbbf24"},
                     "areaStyle": {"color": "rgba(251,191,36,0.15)"}},
                ],
            }
            ui.echart(option).classes("w-full").style(
                "height:220px; position:relative; z-index:1"
            )

            # KPI row
            kpis = [
                ("总用户", f"{snap.total_users:,}", "包含管理员 / 普通用户"),
                ("近 7 日活跃", f"{snap.active_7d:,}",
                 f"{(snap.active_7d / max(1, snap.total_users) * 100):.0f}% 周活率"),
                ("总消息数", f"{snap.total_messages:,}",
                 f"近 400 天；均分 {snap.avg_feedback:.1f}"),
                ("估算总 tokens", _fmt_count(snap.total_tokens),
                 f"按 ~{_TOKENS_PER_CHAR:.2f}/字符估算"),
            ]
            with ui.element("div").classes("u-kpi-row"):
                for label, value, sub in kpis:
                    ui.html(
                        f'<div class="u-kpi">'
                        f'<div class="kpi-label">{_html.escape(label)}</div>'
                        f'<div class="kpi-value">{_html.escape(str(value))}</div>'
                        f'<div class="kpi-sub">{_html.escape(sub)}</div>'
                        f'</div>'
                    )

    if ctx is not None:
        with ctx:
            _body()
    else:
        _body()


# ---------------------------------------------------------------------------
# Top 10 卡片
# ---------------------------------------------------------------------------

def _render_top10(ui, state: Dict[str, Any], *, _no_outer: bool = False) -> None:
    snap: UsersSnapshot = state["snapshot"]
    host = state.get("top10_area") if not _no_outer else None

    def _body() -> None:
        with ui.element("div").classes("u-grid"):
            # Top 10 by est_tokens
            user_top_tokens = [
                (st.user.username, st.est_tokens)
                for st in snap.users if st.est_tokens > 0
            ][:10]
            _render_bar_card(ui, "Top 10 用户 · 估算 tokens",
                             user_top_tokens, accent="#3b82f6",
                             label_formatter="{c}", fmt_value=_fmt_count)

            # Top 10 by msg_count
            user_top_msg = sorted(
                [(st.user.username, st.msg_count) for st in snap.users if st.msg_count > 0],
                key=lambda x: -x[1],
            )[:10]
            _render_bar_card(ui, "Top 10 用户 · 消息数",
                             user_top_msg, accent="#8b5cf6")

            # Top 10 模型
            _render_bar_card(ui, "Top 10 模型使用", snap.top_model, accent="#22c55e")

            # Top 10 工具
            _render_bar_card(
                ui, "Top 10 工具使用", snap.top_tool, accent="#f59e0b",
                empty_hint="暂无工具调用数据：仅 agent_chat 会写 intermediate_steps",
            )

            # Top 10 KB
            _render_bar_card(
                ui, "Top 10 知识库使用", snap.top_kb, accent="#ec4899",
                empty_hint="暂无知识库使用记录：kb_chat 当前未落库，待埋点补齐",
            )

            # 对话类型分布（donut）
            _render_pie_card(ui, "对话类型分布", snap.top_chat_type, accent="#0ea5e9")

    if host is not None:
        with host:
            _body()
    else:
        _body()


def _render_bar_card(
    ui,
    title: str,
    data: List[Tuple[str, int]],
    *,
    accent: str = "#3b82f6",
    label_formatter: str = "{c}",
    empty_hint: str = "暂无数据",
    fmt_value: Optional[Callable[[int], str]] = None,
) -> None:
    card = ui.element("div").classes("u-card")
    card.style(f"--u-accent:{accent}")
    with card:
        _card_header(ui, title, badge=f"{len(data)} 项" if data else "—")
        with ui.element("div").classes("u-body"):
            if not data:
                ui.label(empty_hint).classes("text-xs text-grey-6 q-mt-md")
                return
            items = list(reversed(data))  # 横道从下到上
            names = [_truncate(n, 18) for n, _v in items]
            values = [v for _n, v in items]
            label_conf: Dict[str, Any] = {"show": True, "position": "right", "fontSize": 10}
            if fmt_value:
                # 前端做格式化：formatter 传 JS 表达式，用 echart 的字符串模板不太够
                label_conf["formatter"] = (
                    "function(p){var v=p.value;"
                    "if(v>=1e6)return(v/1e6).toFixed(1)+'M';"
                    "if(v>=1e3)return(v/1e3).toFixed(1)+'K';"
                    "return v;}"
                )
            else:
                label_conf["formatter"] = label_formatter
            option = {
                "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
                "grid": {"left": 110, "right": 36, "top": 6, "bottom": 16},
                "xAxis": {"type": "value", "axisLabel": {"fontSize": 10,
                          "formatter": "function(v){return v>=1000?(v/1000).toFixed(1)+'K':v;}"
                          if fmt_value else "{value}"}},
                "yAxis": {"type": "category", "data": names,
                          "axisLabel": {"fontSize": 10}},
                "series": [{
                    "type": "bar",
                    "data": values,
                    "label": label_conf,
                    "itemStyle": {
                        "color": {
                            "type": "linear", "x": 0, "y": 0, "x2": 1, "y2": 0,
                            "colorStops": [
                                {"offset": 0, "color": accent},
                                {"offset": 1, "color": accent + "66"},
                            ],
                        },
                        "borderRadius": [0, 4, 4, 0],
                    },
                }],
            }
            ui.echart(option).classes("u-chart")


def _render_pie_card(
    ui,
    title: str,
    data: List[Tuple[str, int]],
    *,
    accent: str = "#0ea5e9",
) -> None:
    card = ui.element("div").classes("u-card")
    card.style(f"--u-accent:{accent}")
    with card:
        _card_header(ui, title, badge=f"{len(data)} 类" if data else "—")
        with ui.element("div").classes("u-body"):
            if not data:
                ui.label("暂无数据").classes("text-xs text-grey-6 q-mt-md")
                return
            option = {
                "color": ["#3b82f6", "#8b5cf6", "#22c55e", "#f59e0b", "#ef4444",
                          "#06b6d4", "#ec4899", "#84cc16"],
                "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
                "legend": {"bottom": 0, "textStyle": {"fontSize": 10},
                           "itemHeight": 8, "itemWidth": 10},
                "series": [{
                    "type": "pie",
                    "radius": ["45%", "72%"],
                    "center": ["50%", "44%"],
                    "itemStyle": {"borderRadius": 5, "borderColor": "#fff", "borderWidth": 2},
                    "label": {"show": True, "formatter": "{d}%", "fontSize": 10},
                    "data": [{"name": n, "value": v} for n, v in data],
                }],
            }
            ui.echart(option).classes("u-chart")


def _card_header(ui, title: str, *, badge: str = "") -> None:
    with ui.element("div").classes("u-head"):
        ui.html(f'<div class="u-title">{_html.escape(title)}</div>')
        if badge:
            ui.html(f'<span class="u-badge">{_html.escape(badge)}</span>')


# ---------------------------------------------------------------------------
# 洞察图表
# ---------------------------------------------------------------------------

def _render_insight(ui, state: Dict[str, Any], *, _no_outer: bool = False) -> None:
    snap: UsersSnapshot = state["snapshot"]
    host = state.get("insight_area") if not _no_outer else None

    def _body() -> None:
        with ui.element("div").classes("u-grid"):
            # 用户 × 模型 热力（wide 卡）
            _render_user_model_heatmap(ui, snap)
            # 用户活跃度 7×24（wide 卡）
            _render_global_heatmap(ui, snap)
            # 反馈分数雷达
            _render_feedback_radar(ui, snap)

    if host is not None:
        with host:
            _body()
    else:
        _body()


def _render_user_model_heatmap(ui, snap: UsersSnapshot) -> None:
    card = ui.element("div").classes("u-card u-card-wide")
    card.style("--u-accent:#3b82f6")
    with card:
        _card_header(ui, "用户 × 模型 偏好热力图（Top 10 × Top 10）",
                     badge=f"{len(snap.user_model_matrix)} 条")
        with ui.element("div").classes("u-body"):
            if not snap.user_model_matrix:
                ui.label("暂无可用数据。").classes("text-xs text-grey-6")
                return
            users = list({r[0] for r in snap.user_model_matrix})
            models = list({r[1] for r in snap.user_model_matrix})
            # 组装 echart heatmap 的 [x, y, v] 点
            u_idx = {u: i for i, u in enumerate(users)}
            m_idx = {m: i for i, m in enumerate(models)}
            points = [[m_idx[m], u_idx[u], v] for u, m, v in snap.user_model_matrix]
            vmax = max(v for _, _, v in snap.user_model_matrix)
            option = {
                "tooltip": {"position": "top"},
                "grid": {"left": 120, "right": 20, "top": 8, "bottom": 40},
                "xAxis": {"type": "category", "data": models,
                          "axisLabel": {"fontSize": 10, "rotate": 30},
                          "splitArea": {"show": True}},
                "yAxis": {"type": "category", "data": users,
                          "axisLabel": {"fontSize": 10},
                          "splitArea": {"show": True}},
                "visualMap": {
                    "min": 0, "max": vmax, "calculable": True,
                    "orient": "horizontal", "left": "center", "bottom": 4,
                    "itemWidth": 10, "itemHeight": 100,
                    "textStyle": {"fontSize": 9},
                    "inRange": {"color": ["#eff6ff", "#60a5fa", "#1d4ed8"]},
                },
                "series": [{
                    "type": "heatmap", "data": points,
                    "label": {"show": vmax <= 10},
                    "emphasis": {"itemStyle": {"shadowBlur": 8}},
                }],
            }
            ui.echart(option).classes("u-chart-wide")


def _render_global_heatmap(ui, snap: UsersSnapshot) -> None:
    card = ui.element("div").classes("u-card u-card-wide")
    card.style("--u-accent:#8b5cf6")
    with card:
        _card_header(ui, "全局活跃时段（近 90 天 · 星期 × 小时）",
                     badge=f"peak {snap.global_heatmap_max}")
        with ui.element("div").classes("u-body"):
            if snap.global_heatmap_max == 0:
                ui.label("最近无活跃数据。").classes("text-xs text-grey-6")
                return
            weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            hours = [str(h) for h in range(24)]
            points = []
            for d in range(7):
                for h in range(24):
                    points.append([h, d, snap.global_heatmap[d][h]])
            option = {
                "tooltip": {"position": "top"},
                "grid": {"left": 40, "right": 10, "top": 6, "bottom": 30},
                "xAxis": {"type": "category", "data": hours,
                          "axisLabel": {"fontSize": 9},
                          "splitArea": {"show": True}},
                "yAxis": {"type": "category", "data": weekdays,
                          "axisLabel": {"fontSize": 10},
                          "splitArea": {"show": True}},
                "visualMap": {
                    "min": 0, "max": max(snap.global_heatmap_max, 1),
                    "calculable": True, "orient": "horizontal",
                    "left": "center", "bottom": 2,
                    "itemWidth": 100, "itemHeight": 10,
                    "textStyle": {"fontSize": 9},
                    "inRange": {"color": ["#f5f3ff", "#c4b5fd", "#6d28d9"]},
                },
                "series": [{
                    "type": "heatmap", "data": points,
                    "emphasis": {"itemStyle": {"shadowBlur": 8}},
                }],
            }
            ui.echart(option).classes("u-chart-wide")


def _render_feedback_radar(ui, snap: UsersSnapshot) -> None:
    card = ui.element("div").classes("u-card")
    card.style("--u-accent:#ec4899")
    with card:
        _card_header(ui, "反馈分数分布", badge=f"{snap.total_feedback} 条")
        with ui.element("div").classes("u-body"):
            data = snap.feedback_hist
            max_v = max(data + [1])
            if not any(data):
                ui.label("暂无反馈数据。").classes("text-xs text-grey-6")
                return
            labels = ["0-20", "20-40", "40-60", "60-80", "80-100"]
            indicators = [{"name": l, "max": max_v} for l in labels]
            option = {
                "radar": {
                    "indicator": indicators, "radius": "62%",
                    "axisName": {"fontSize": 10, "color": "#475569"},
                    "splitArea": {"areaStyle": {"color": ["rgba(236,72,153,0.05)", "rgba(236,72,153,0.02)"]}},
                },
                "series": [{
                    "type": "radar",
                    "areaStyle": {"color": "rgba(236,72,153,0.35)"},
                    "lineStyle": {"color": "#ec4899"},
                    "itemStyle": {"color": "#ec4899"},
                    "data": [{"value": data, "name": "反馈"}],
                }],
            }
            ui.echart(option).classes("u-chart")


# ---------------------------------------------------------------------------
# 用户表
# ---------------------------------------------------------------------------

def _render_table(ui, state: Dict[str, Any], *, _no_outer: bool = False) -> None:
    snap: UsersSnapshot = state["snapshot"]
    host = state.get("table_area") if not _no_outer else None
    keyword = state.get("search_text", "") or ""

    # 用列表筛选：匿名用户始终展示（如果有消息）
    rows: List[UserStats] = []
    for st in snap.users:
        if st.user.id == _ANON_UID and st.msg_count == 0:
            continue
        if keyword:
            hay = f"{st.user.username} {st.user.email} {st.user.role}".lower()
            if keyword not in hay:
                continue
        rows.append(st)

    # 按 tokens 降序（build_snapshot 已经这么排过）
    def _body() -> None:
        if not rows:
            ui.label("没有匹配的用户。").classes("text-sm text-grey-7")
            return
        # 用 raw HTML 生成表格（ui.table 对自定义行渲染不够方便）
        header = (
            "<tr>"
            "<th>用户</th>"
            "<th>角色 / 状态</th>"
            "<th class='num'>消息</th>"
            "<th class='num'>~tokens</th>"
            "<th class='num'>反馈均分</th>"
            "<th>最近活跃</th>"
            "<th>操作</th>"
            "</tr>"
        )
        body_rows: List[str] = []
        for st in rows:
            initial = (st.user.username[:1] or "?").upper()
            role_cls = {"admin": "role-admin", "user": "role-user"}.get(
                st.user.role, "role-anon",
            )
            status_cls = "status-on" if st.user.is_active else "status-off"
            fb = f"{st.avg_feedback:.1f}" if st.avg_feedback is not None else "—"
            last = st.last_active.strftime("%Y-%m-%d %H:%M") if st.last_active else "—"
            # HTML 行（操作栏的按钮等用 NiceGUI 控件很麻烦，干脆放 data-attr 自定义点击事件）
            body_rows.append(
                f"<tr data-uid='{st.user.id}'>"
                f"<td><div class='u-user-chip'>"
                f"<span class='u-avatar'>{_html.escape(initial)}</span>"
                f"<div><div style='font-weight:600'>{_html.escape(st.user.username)}</div>"
                f"<div style='font-size:11px;color:#6b7280'>{_html.escape(st.user.email or '—')}</div></div>"
                f"</div></td>"
                f"<td><span class='{role_cls}'>{_html.escape(st.user.role)}</span> "
                f"<span class='{status_cls}'>{'启用' if st.user.is_active else '停用'}</span></td>"
                f"<td class='num'>{_fmt_count(st.msg_count)}</td>"
                f"<td class='num'>{_fmt_count(st.est_tokens)}</td>"
                f"<td class='num'>{fb}</td>"
                f"<td style='white-space:nowrap;font-size:12px;color:#475569'>{_html.escape(last)}</td>"
                f"<td id='u-actions-{st.user.id}'></td>"
                f"</tr>"
            )
        ui.html(
            f"<div class='u-table-wrap'><table class='u-table'>"
            f"<thead>{header}</thead>"
            f"<tbody>{''.join(body_rows)}</tbody>"
            f"</table></div>"
        )

        # 再用 NiceGUI 按钮填充每行的"操作"单元——这样点击事件是真正的 Python callback
        with ui.element("div").style("display:none"):
            # 占位 noop；下面循环用 JS 把按钮移进对应 cell
            pass
        # 用一个单独的浮动操作条放在下方；因为在 HTML table 里嵌 NiceGUI 按钮
        # 需要 element-move 技巧，简化处理：给每行加"操作菜单"按钮触发 dialog
        with ui.row().classes("w-full q-mt-sm items-center").style("gap:8px"):
            user_options = {
                f"{st.user.id}": f"#{st.user.id} {st.user.username} (msg={st.msg_count})"
                for st in rows
            }
            sel = ui.select(
                user_options,
                label="针对某个用户执行操作（选择后点击下方按钮）",
            ).classes("flex-1")

            def _with_sel(handler: Callable[[int], None]) -> Callable[[], None]:
                def _inner() -> None:
                    if not getattr(sel, "value", None):
                        ui.notify("请先选择用户", color="warning")
                        return
                    try:
                        handler(int(sel.value))
                    except Exception as e:  # noqa: BLE001
                        ui.notify(f"操作失败：{e!r}", color="negative")
                return _inner

            ui.button(
                "画像 / AI 分析", icon="person_search",
                on_click=_with_sel(lambda uid: _open_persona_dialog(ui, state, uid)),
            ).props("color=primary no-caps").tooltip(
                "打开该用户画像对话框，可让 LLM 生成运营洞察"
            )
            ui.button(
                "改密", icon="key",
                on_click=_with_sel(lambda uid: _open_password_dialog(ui, state, uid)),
            ).props("flat no-caps").tooltip("给该用户设置新密码")
            def _toggle_active(uid: int, active: bool) -> None:
                ui.notify(_set_active(uid, active), color="positive")
                state["snapshot"] = build_snapshot()
                _redraw_all(ui, state)

            ui.button(
                "停用", icon="block",
                on_click=_with_sel(lambda uid: _toggle_active(uid, False)),
            ).props("flat color=negative no-caps")
            ui.button(
                "启用", icon="check_circle",
                on_click=_with_sel(lambda uid: _toggle_active(uid, True)),
            ).props("flat no-caps")

    if host is not None:
        with host:
            _body()
    else:
        _body()


# ---------------------------------------------------------------------------
# 对话框：改密
# ---------------------------------------------------------------------------

def _open_password_dialog(ui, state: Dict[str, Any], uid: int) -> None:
    if uid == _ANON_UID:
        ui.notify("匿名用户无需改密", color="warning")
        return
    with ui.dialog() as dlg, ui.card():
        ui.label(f"为用户 #{uid} 设置新密码").classes("text-base font-semibold")
        pwd = ui.input("新密码", password=True, password_toggle_button=True).classes("w-64")
        with ui.row().classes("w-full justify-end"):
            ui.button("取消", on_click=dlg.close).props("flat")
            def _on_ok() -> None:
                if not getattr(pwd, "value", ""):
                    ui.notify("密码不能为空", color="warning")
                    return
                try:
                    ui.notify(_set_password(uid, pwd.value), color="positive")
                    dlg.close()
                except Exception as e:  # noqa: BLE001
                    ui.notify(f"改密失败：{e!r}", color="negative")
            ui.button("确认", on_click=_on_ok).props("color=primary")
    dlg.open()


# ---------------------------------------------------------------------------
# 对话框：单用户画像 + AI 分析
# ---------------------------------------------------------------------------

def _open_persona_dialog(ui, state: Dict[str, Any], uid: int) -> None:
    snap: UsersSnapshot = state["snapshot"]
    stats = next((st for st in snap.users if st.user.id == uid), None)
    if stats is None:
        ui.notify(f"未找到用户 #{uid}", color="negative")
        return

    persona_state: Dict[str, Any] = {}

    with ui.dialog() as dlg:
        with ui.card().classes("q-pa-md").style(
            "min-width: min(880px, 94vw); max-width: 94vw; "
            "max-height: 94vh; overflow: auto;"
        ):
            # Header
            initial = (stats.user.username[:1] or "?").upper()
            with ui.row().classes("items-center w-full no-wrap").style("gap:12px"):
                ui.html(
                    f'<div style="width:56px;height:56px;border-radius:50%;'
                    f'background:linear-gradient(135deg,#3b82f6,#8b5cf6);'
                    f'color:#fff;font-weight:700;font-size:20px;'
                    f'display:flex;align-items:center;justify-content:center;'
                    f'box-shadow:0 4px 12px rgba(59,130,246,0.3)">{_html.escape(initial)}</div>'
                )
                with ui.column().classes("q-gutter-none").style("flex:1;min-width:0"):
                    ui.label(stats.user.username).classes("text-xl font-semibold")
                    ui.label(
                        f"#{stats.user.id} · 角色 {stats.user.role} · "
                        f"邮箱 {stats.user.email or '—'} · "
                        f"{'启用' if stats.user.is_active else '停用'}"
                    ).classes("text-xs text-grey-7 font-mono")
                ui.button(icon="close", on_click=dlg.close).props("flat round dense")

            ui.separator().classes("q-my-sm")

            # KPI cards (4 cols)
            with ui.element("div").classes("u-kpi-row").style("margin-top:0"):
                kpis = [
                    ("消息数", _fmt_count(stats.msg_count), "近 400 天"),
                    ("估算 tokens", _fmt_count(stats.est_tokens),
                     f"{_fmt_count(stats.total_chars)} 字符"),
                    ("反馈均分",
                     f"{stats.avg_feedback:.1f}" if stats.avg_feedback is not None else "—",
                     f"{stats.feedback_count} 条"),
                    ("最近活跃",
                     stats.last_active.strftime("%m-%d %H:%M") if stats.last_active else "—",
                     f"首次 {stats.first_active.strftime('%Y-%m-%d') if stats.first_active else '—'}"),
                ]
                # 让这里的 KPI 走"亮色"主题，覆盖深色 hero 样式
                for label, value, sub in kpis:
                    ui.html(
                        f'<div style="background:#f8fafc;border:1px solid #e5e7eb;'
                        f'border-radius:10px;padding:10px 14px">'
                        f'<div style="font-size:11px;color:#64748b">{_html.escape(label)}</div>'
                        f'<div style="font-size:22px;font-weight:700;color:#0f172a;'
                        f'margin-top:2px">{_html.escape(str(value))}</div>'
                        f'<div style="font-size:10px;color:#6b7280;margin-top:2px">{_html.escape(sub)}</div>'
                        f'</div>'
                    )

            # Charts grid
            with ui.element("div").classes("u-grid").style("margin-top:14px"):
                # 模型使用 bar
                _render_bar_card(
                    ui, "使用模型分布",
                    list(collections.Counter(stats.model_usage).most_common(10)),
                    accent="#22c55e", empty_hint="未检测到模型字段",
                )
                # 工具使用 bar
                _render_bar_card(
                    ui, "调用工具分布",
                    list(collections.Counter(stats.tool_usage).most_common(10)),
                    accent="#f59e0b",
                    empty_hint="未检测到工具调用（agent_chat 才会写 intermediate_steps）",
                )
                # KB 使用 bar
                _render_bar_card(
                    ui, "知识库使用分布",
                    list(collections.Counter(stats.kb_usage).most_common(10)),
                    accent="#ec4899",
                    empty_hint="未检测到 KB 使用（kb_chat 暂未落库）",
                )
                # 对话类型 pie
                _render_pie_card(
                    ui, "对话类型分布",
                    list(collections.Counter(stats.chat_type_usage).most_common(10)),
                    accent="#0ea5e9",
                )
                # 用户本人热力图
                _render_user_heatmap(ui, stats)

            # 最近 10 条提问
            ui.label("最近提问（Top 10）").classes("text-base font-semibold q-mt-md")
            if not stats.recent_queries:
                ui.label("暂无提问记录。").classes("text-xs text-grey-6")
            else:
                with ui.element("div").classes("u-table-wrap"):
                    header = (
                        "<tr><th>时间</th><th>类型</th><th>模型</th><th>提问</th></tr>"
                    )
                    body_rows: List[str] = []
                    for t, ct, q, m in stats.recent_queries:
                        body_rows.append(
                            "<tr>"
                            f"<td style='white-space:nowrap'>{_html.escape(t.strftime('%m-%d %H:%M'))}</td>"
                            f"<td>{_html.escape(ct or '—')}</td>"
                            f"<td style='font-size:11px;color:#64748b'>{_html.escape(m or '—')}</td>"
                            f"<td style='font-size:12px;color:#334155'>{_html.escape(q)}</td>"
                            "</tr>"
                        )
                    ui.html(
                        f"<table class='u-table'><thead>{header}</thead>"
                        f"<tbody>{''.join(body_rows)}</tbody></table>"
                    )

            # AI 画像分析
            with ui.element("div").classes("u-persona"):
                with ui.row().classes("items-start w-full no-wrap").style(
                    "position:relative;z-index:1;gap:12px"
                ):
                    ui.html('<i class="material-icons" style="font-size:28px;color:#c7d2fe">psychology</i>')
                    with ui.column().classes("q-gutter-none").style("flex:1;min-width:0"):
                        ui.html(
                            '<div style="font-size:16px;font-weight:700">AI 用户画像分析</div>'
                            '<div style="font-size:12px;opacity:.85;margin-top:4px">'
                            '让大模型基于上述数据生成：用户偏好洞察、使用模式、运营建议。</div>'
                        )
                    # 按钮要抓引用，后面异步任务里 disable / 恢复
                    gen_btn = ui.button(
                        "生成画像", icon="auto_awesome",
                    ).props("color=white text-color=primary no-caps dense")
                body = ui.html(
                    '<div class="u-persona-body"><em>点「生成画像」调用大模型分析…</em></div>'
                )
                persona_state["body"] = body
                persona_state["gen_btn"] = gen_btn

                # 关键：异步 handler + ``asyncio.to_thread`` 把 openai HTTP 请求
                # 搬出 NiceGUI 主事件循环；不然 LLM 跑 10-30s 期间整个浏览器端
                # 的关闭按钮 / 背景点击全部无响应。
                async def _on_generate() -> None:
                    await _run_persona_ai_async(ui, stats, persona_state)

                gen_btn.on("click", _on_generate)

            with ui.row().classes("w-full justify-end q-mt-md"):
                ui.button("关闭", on_click=dlg.close).props("flat")
    dlg.open()


def _render_user_heatmap(ui, stats: UserStats) -> None:
    card = ui.element("div").classes("u-card u-card-wide")
    card.style("--u-accent:#8b5cf6")
    with card:
        peak = max((v for row in stats.heatmap for v in row), default=0)
        _card_header(ui, f"{stats.user.username} · 活跃时段", badge=f"peak {peak}")
        with ui.element("div").classes("u-body"):
            if peak == 0:
                ui.label("该用户最近无活跃数据。").classes("text-xs text-grey-6")
                return
            weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            hours = [str(h) for h in range(24)]
            points = []
            for d in range(7):
                for h in range(24):
                    points.append([h, d, stats.heatmap[d][h]])
            option = {
                "tooltip": {"position": "top"},
                "grid": {"left": 40, "right": 10, "top": 6, "bottom": 30},
                "xAxis": {"type": "category", "data": hours,
                          "axisLabel": {"fontSize": 9}, "splitArea": {"show": True}},
                "yAxis": {"type": "category", "data": weekdays,
                          "axisLabel": {"fontSize": 10}, "splitArea": {"show": True}},
                "visualMap": {
                    "min": 0, "max": max(peak, 1),
                    "calculable": True, "orient": "horizontal",
                    "left": "center", "bottom": 2,
                    "itemWidth": 100, "itemHeight": 10,
                    "textStyle": {"fontSize": 9},
                    "inRange": {"color": ["#f5f3ff", "#c4b5fd", "#6d28d9"]},
                },
                "series": [{"type": "heatmap", "data": points}],
            }
            ui.echart(option).classes("u-chart-wide")


# ---------------------------------------------------------------------------
# AI 用户画像分析
# ---------------------------------------------------------------------------

async def _run_persona_ai_async(
    ui, stats: UserStats, persona_state: Dict[str, Any],
) -> None:
    """异步版本：把阻塞的 openai HTTP 调用搬出 NiceGUI 主事件循环。

    旧的同步版本会让 LLM 跑 10-30s 期间整个页面（含关闭按钮 / 背景点击）
    都无响应 —— 用户只会看到画像对话框"卡住关不掉"。这里用
    ``asyncio.to_thread`` 解决。
    """
    import asyncio

    body = persona_state.get("body")
    gen_btn = persona_state.get("gen_btn")
    if body is None:
        return

    # 1) 立即反馈 + 暂时禁用按钮防止重复点击
    _set_html(body, '<div class="u-persona-body"><em>正在请求大模型，请稍候（可以随时关闭对话框）…</em></div>')
    if gen_btn is not None:
        try:
            gen_btn.props("loading disable")
        except Exception:
            pass

    def _restore_btn() -> None:
        if gen_btn is None:
            return
        try:
            gen_btn.props(remove="loading disable")
        except Exception:
            pass

    prompt = _build_persona_prompt(stats)

    try:
        from chayuan.server.config_panel.performance_page import (
            _pick_chat_model, _call_llm, _simple_markdown,
        )
    except Exception as e:  # noqa: BLE001
        _set_html(body, f'<div class="u-persona-body">依赖加载失败：{_html.escape(str(e))}</div>')
        _restore_btn()
        return

    picked = _pick_chat_model()
    if picked is None:
        _set_html(
            body,
            '<div class="u-persona-body">未找到任何 LLM 平台；'
            '请先去「模型配置」页添加至少一个可用的 llm_model。</div>',
        )
        _restore_btn()
        return

    model_name, api_base, api_key = picked

    # 2) 阻塞 HTTP 调用在后台线程跑；主 loop 继续响应 close / esc
    try:
        answer = await asyncio.to_thread(
            _call_llm, model_name, api_base, api_key, prompt,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("persona LLM call failed")
        _set_html(body, f'<div class="u-persona-body">LLM 调用异常：{_html.escape(str(e))}</div>')
        _restore_btn()
        return

    # 3) 回到主 loop 更新 DOM
    html_body = _simple_markdown(answer)
    _set_html(
        body,
        '<div class="u-persona-body">'
        f'<div style="font-size:11px;color:#93c5fd;margin-bottom:6px">模型：{_html.escape(model_name)} · '
        f'完成于 {time.strftime("%H:%M:%S")}</div>'
        f'{html_body}</div>',
    )
    _restore_btn()


def _build_persona_prompt(stats: UserStats) -> str:
    u = stats.user
    model_usage = ", ".join(f"{k}({v})" for k, v in
                            collections.Counter(stats.model_usage).most_common(8)) or "—"
    tool_usage = ", ".join(f"{k}({v})" for k, v in
                           collections.Counter(stats.tool_usage).most_common(8)) or "—"
    kb_usage = ", ".join(f"{k}({v})" for k, v in
                         collections.Counter(stats.kb_usage).most_common(8)) or "—"
    chat_usage = ", ".join(f"{k}({v})" for k, v in
                           collections.Counter(stats.chat_type_usage).most_common(5)) or "—"
    recent = "\n".join(
        f"  - [{t.strftime('%m-%d %H:%M')}] ({ct or '—'}) {q}"
        for t, ct, q, _m in stats.recent_queries[:8]
    ) or "  （无）"

    return f"""你是一名面向 RAG 平台的**用户运营分析师**。基于下方单个用户的使用画像，请输出：

1. **一句话总结**：这个用户大概是什么角色（技术开发者 / 业务分析师 / 普通咨询用户 等）；
2. **兴趣偏好**：他最常问什么类型的问题、最爱用哪些模型 / 工具 / KB；
3. **使用模式**：活跃时段 / 频次是否稳定，是否属于重度用户；
4. **潜在风险**：tokens 消耗是否异常、是否刷屏、是否出现可能的误用；
5. **运营建议**：
   - 推荐功能 / 模型 / KB；
   - 成本优化（如果 tokens 偏高）；
   - 挽回 / 激活策略（如果使用频次下滑）；
6. **一行画像标签**：用 3-5 个中文 hashtag 总结（例 `#Python 开发者` `#高频使用` `#对 KB 依赖大`）。

以 Markdown 输出，每节用加粗小标题。不要把原始数据再抄一遍。

## 用户快照

- 用户：{u.username} (#{u.id}) · 角色 {u.role} · {'启用' if u.is_active else '停用'}
- 邮箱：{u.email or '—'}
- 注册：{u.created_at.strftime('%Y-%m-%d') if u.created_at else '—'}  · 最近登录：{u.last_login_at.strftime('%Y-%m-%d %H:%M') if u.last_login_at else '—'}

### 使用强度

- 消息数：{stats.msg_count}
- 估算 tokens：{stats.est_tokens}（≈ {stats.total_chars} 字符）
- 反馈：{stats.feedback_count} 条，均分 {stats.avg_feedback if stats.avg_feedback is not None else '—'}
- 首次活跃：{stats.first_active.strftime('%Y-%m-%d %H:%M') if stats.first_active else '—'}
- 最近活跃：{stats.last_active.strftime('%Y-%m-%d %H:%M') if stats.last_active else '—'}

### 行为偏好

- 模型使用：{model_usage}
- 工具使用：{tool_usage}
- 知识库使用：{kb_usage}
- 对话类型：{chat_usage}

### 最近提问样本

{recent}
"""


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _fmt_count(n: int) -> str:
    try:
        n = int(n)
    except Exception:
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return f"{n:,}"


def _truncate(s: str, n: int) -> str:
    s = str(s or "")
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _set_html(el, html: str) -> None:
    if el is None:
        return
    try:
        el.content = html
        return
    except Exception:
        pass
    try:
        el.set_content(html)
    except Exception:
        pass


__all__ = [
    "render_users_page",
    "build_snapshot",
    "UsersSnapshot",
    "UserStats",
]
