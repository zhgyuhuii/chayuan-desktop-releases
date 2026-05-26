"""性能与可扩展性主页：硬件体检 + 数据画像 + AI 动态分析。

对比上一版 ``scalability.py``：
- **不再**把"Redis 连接配置"塞在页首；
- 本页面**只做展示**：20+ 张图表以卡片形式铺开，覆盖
    ① 综合健康 / 并发承载估算 / 问题分类；
    ② 硬件实时（CPU / 内存 / 磁盘 / GPU）；
    ③ 存储占用拆分（CHAYUAN_ROOT 子目录）、日志清理、数据库体积；
    ④ 模型全景（可用度、类型、使用频率）；
    ⑤ 流量与延迟（热力图 / 直方 / Top endpoints）；
    ⑥ 知识库画像（桑基 / Top KB / 扩展名）；
    ⑦ "AI 深度分析"卡 —— 点击会调用当前配置的 LLM，
        把整份指标快照喂进去生成承载估算 / 瓶颈诊断 / 改进措施。
- 每张卡的卡头都挂一颗实时健康色点（绿/黄/红），所有阈值都有说明。
- 每行 3+ 张卡；在窄屏自动折行；CPU / GPU 数据采用滚动 sparkline 实现"滑动"效果。

此模块**只读**：不会写任何 yaml、不会改业务数据（仅"清理日志"按钮会删
``$CHAYUAN_ROOT/data/logs`` 下的旧文件，有二次确认）。
"""
from __future__ import annotations

import asyncio
import collections
import logging
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger("chayuan.config_panel.performance")


# ---------------------------------------------------------------------------
# 健康分档：每张图都会标一个 ok / warning / critical 的色点
# ---------------------------------------------------------------------------

HEALTH_OK = "ok"
HEALTH_WARN = "warn"
HEALTH_CRIT = "crit"
HEALTH_INFO = "info"  # 纯展示，不评级（如"数据目录"）

_HEALTH_COLOR = {
    HEALTH_OK:   "#22c55e",
    HEALTH_WARN: "#f59e0b",
    HEALTH_CRIT: "#ef4444",
    HEALTH_INFO: "#64748b",
}
_HEALTH_LABEL = {
    HEALTH_OK:   "健康",
    HEALTH_WARN: "注意",
    HEALTH_CRIT: "告警",
    HEALTH_INFO: "参考",
}

# ECharts 公用调色盘
_PALETTE = [
    "#3b82f6", "#8b5cf6", "#22c55e", "#f59e0b", "#ef4444",
    "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#6366f1",
]


# ---------------------------------------------------------------------------
# 快照数据结构
# ---------------------------------------------------------------------------

@dataclass
class HardwareSnapshot:
    cpu_percent: float = 0.0             # 0-100
    cpu_per_core: List[float] = field(default_factory=list)
    cpu_count_logical: int = 1
    cpu_count_physical: int = 1
    load_1: float = 0.0
    load_5: float = 0.0
    load_15: float = 0.0

    mem_total: int = 0                    # bytes
    mem_used: int = 0
    mem_available: int = 0
    mem_percent: float = 0.0
    swap_total: int = 0
    swap_used: int = 0

    disks: List[Dict[str, Any]] = field(default_factory=list)
    # 每个元素：{mount, total, used, free, percent}

    gpus: List[Dict[str, Any]] = field(default_factory=list)
    # 每个元素：{name, util, mem_total, mem_used, temp}

    net_bytes_sent: int = 0
    net_bytes_recv: int = 0


@dataclass
class StorageSnapshot:
    root: str = ""
    total_bytes: int = 0
    by_subdir: List[Tuple[str, int]] = field(default_factory=list)  # (name, bytes)
    logs_bytes: int = 0
    logs_file_count: int = 0
    db_bytes: int = 0                  # SQLite 文件体积；其它方言 = 0
    db_source: str = ""                # "sqlite" / "postgresql" / ...


@dataclass
class ModelSnapshot:
    total_llm: int = 0
    total_embed: int = 0
    total_rerank: int = 0
    total_image: int = 0
    platforms: List[Dict[str, Any]] = field(default_factory=list)
    # 每个元素：{name, type, reachable, llm_count, embed_count, rerank_count, image_count}
    usage: List[Tuple[str, int]] = field(default_factory=list)  # (model, count)
    default_llm: str = ""
    default_embed: str = ""


@dataclass
class PerfSnapshot:
    hardware: HardwareSnapshot = field(default_factory=HardwareSnapshot)
    storage: StorageSnapshot = field(default_factory=StorageSnapshot)
    models: ModelSnapshot = field(default_factory=ModelSnapshot)
    metrics: Any = None                # monitoring.MetricsSnapshot
    health_counts: Dict[str, int] = field(default_factory=dict)
    health_score: int = 0              # 0-100
    est_concurrent: str = ""
    generated_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# psutil 封装：装不上时返回零值，绝不 crash
# ---------------------------------------------------------------------------

def _get_psutil():
    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("psutil", "psutil>=5.9,<7.0")
    except Exception:
        pass
    try:
        import psutil  # type: ignore
        return psutil
    except Exception:  # noqa: BLE001
        return None


def collect_hardware() -> HardwareSnapshot:
    ps = _get_psutil()
    snap = HardwareSnapshot()
    if ps is None:
        # 没 psutil 也给一个最小可用的降级：stdlib 里能拿到多少算多少
        try:
            snap.cpu_count_logical = os.cpu_count() or 1
            snap.cpu_count_physical = snap.cpu_count_logical
        except Exception:
            pass
        try:
            snap.load_1, snap.load_5, snap.load_15 = os.getloadavg()
        except Exception:
            pass
        return snap

    try:
        # 第一次调 psutil.cpu_percent(interval=None) 会返回 0.0（需要基线），
        # 这里用极短 interval 0.1s 保证第一次调用也有真实值。
        snap.cpu_percent = float(ps.cpu_percent(interval=0.1))
        snap.cpu_per_core = [float(x) for x in ps.cpu_percent(percpu=True)]
        snap.cpu_count_logical = int(ps.cpu_count(logical=True) or 1)
        snap.cpu_count_physical = int(ps.cpu_count(logical=False) or snap.cpu_count_logical)
        if hasattr(ps, "getloadavg"):
            snap.load_1, snap.load_5, snap.load_15 = ps.getloadavg()
    except Exception as e:  # noqa: BLE001
        logger.warning("collect_hardware cpu failed: %s", e)

    try:
        mem = ps.virtual_memory()
        snap.mem_total = int(mem.total)
        snap.mem_used = int(getattr(mem, "used", mem.total - mem.available))
        snap.mem_available = int(mem.available)
        snap.mem_percent = float(mem.percent)
        swap = ps.swap_memory()
        snap.swap_total = int(swap.total)
        snap.swap_used = int(swap.used)
    except Exception as e:  # noqa: BLE001
        logger.warning("collect_hardware mem failed: %s", e)

    try:
        # 只列挂载点里真正带 fstype 的（排除 /snap /var/lib/docker 等虚拟挂载）
        parts = ps.disk_partitions(all=False)
        seen: set = set()
        for p in parts:
            mount = p.mountpoint
            if mount in seen:
                continue
            seen.add(mount)
            try:
                u = ps.disk_usage(mount)
                snap.disks.append({
                    "mount": mount,
                    "total": int(u.total),
                    "used": int(u.used),
                    "free": int(u.free),
                    "percent": float(u.percent),
                })
            except Exception:
                continue
    except Exception as e:  # noqa: BLE001
        logger.warning("collect_hardware disk failed: %s", e)

    try:
        n = ps.net_io_counters()
        snap.net_bytes_sent = int(n.bytes_sent)
        snap.net_bytes_recv = int(n.bytes_recv)
    except Exception:
        pass

    snap.gpus = _collect_gpus()
    return snap


def _collect_gpus() -> List[Dict[str, Any]]:
    """优先 ``nvidia-smi``；无 NVIDIA 时尝试 Apple Metal/torch.mps 信号。"""
    out: List[Dict[str, Any]] = []
    # NVIDIA
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.total,memory.used,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=1.5,
        )
        if r.returncode == 0 and r.stdout.strip():
            for line in r.stdout.strip().splitlines():
                parts = [x.strip() for x in line.split(",")]
                if len(parts) >= 5:
                    out.append({
                        "name": parts[0],
                        "util": float(parts[1] or 0),
                        "mem_total": int(float(parts[2] or 0)) * 1024 * 1024,  # MiB → bytes
                        "mem_used": int(float(parts[3] or 0)) * 1024 * 1024,
                        "temp": float(parts[4] or 0),
                    })
            if out:
                return out
    except Exception:
        pass

    # Apple Silicon：没有通用 util 指标；只在 torch.backends.mps.is_available() 时
    # 登记一个"存在"的 GPU（使用率用进程 CPU 占比做替身）。
    try:
        import torch  # type: ignore
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            out.append({
                "name": "Apple Metal (MPS)",
                "util": 0.0,
                "mem_total": 0,
                "mem_used": 0,
                "temp": 0.0,
            })
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# 存储 / 日志 / DB 体积
# ---------------------------------------------------------------------------

def collect_storage() -> StorageSnapshot:
    from chayuan.settings import CHAYUAN_ROOT, Settings

    snap = StorageSnapshot()
    try:
        root = Path(CHAYUAN_ROOT)
    except Exception:
        return snap
    snap.root = str(root)

    if not root.is_dir():
        return snap

    # 顶层子目录体积（du 式浅计算；只走 2 层，避免万级文件拖慢首页）
    entries: List[Tuple[str, int]] = []
    total = 0
    try:
        for entry in sorted(root.iterdir()):
            # 跳过隐藏项（.DS_Store 等）但保留 ``.chayuan_runtime.json`` 这类
            # 带点的业务文件？暂不区分，统一跳过隐藏文件以免扰乱视图。
            if entry.name.startswith("."):
                continue
            # ``_dir_size_fast`` 对文件 / 目录都返回合理大小，所以一次循环
            # 就覆盖了顶层所有条目，不用再来一次专门扫文件的循环。
            size = _dir_size_fast(entry, max_depth=3)
            entries.append((entry.name, size))
            total += size
    except OSError:
        pass

    # 按大小排序，取 Top 10 + 其它汇总
    entries.sort(key=lambda x: x[1], reverse=True)
    if len(entries) > 10:
        others = sum(e[1] for e in entries[10:])
        entries = entries[:10] + [("其它", others)]
    snap.by_subdir = entries
    snap.total_bytes = total

    # 日志体积：CHAYUAN_ROOT/data/logs
    logs_dir = root / "data" / "logs"
    if logs_dir.is_dir():
        size = 0
        count = 0
        for dirpath, _dirs, files in os.walk(logs_dir):
            for f in files:
                try:
                    size += (Path(dirpath) / f).stat().st_size
                    count += 1
                except OSError:
                    continue
        snap.logs_bytes = size
        snap.logs_file_count = count

    # DB 体积
    uri = str(getattr(Settings.basic_settings, "SQLALCHEMY_DATABASE_URI", "") or "")
    if uri.startswith("sqlite"):
        snap.db_source = "sqlite"
        try:
            p = uri.split(":///", 1)[-1] or uri.split("://", 1)[-1]
            pp = Path(p)
            if pp.is_file():
                snap.db_bytes = pp.stat().st_size
        except Exception:
            pass
    elif uri.startswith(("postgresql", "postgres")):
        snap.db_source = "postgresql"
        snap.db_bytes = _pg_db_size(uri)
    elif uri.startswith(("mysql", "mariadb")):
        snap.db_source = "mysql"
        snap.db_bytes = _mysql_db_size(uri)
    else:
        snap.db_source = uri.split("://", 1)[0] if uri else ""

    return snap


def _dir_size_fast(path: Path, *, max_depth: int = 3) -> int:
    """计算目录体积，但限制递归深度防止拖慢。

    对于 node_modules / site-packages / vector_store 这类深层目录，
    走一层 os.walk 就够出"量级"了（面板显示单位是 MB/GB）。
    """
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        for dirpath, dirs, files in os.walk(path):
            depth = dirpath.count(os.sep) - str(path).count(os.sep)
            if depth >= max_depth:
                # 截断进一步递归
                dirs[:] = []
            for f in files:
                try:
                    total += (Path(dirpath) / f).stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


def _pg_db_size(uri: str) -> int:
    # 函数名明确针对 PG;调用方传非 PG URI 时直接返 0,避免对 SQLite 传
    # connect_timeout 触发 TypeError。
    if not uri.startswith(("postgresql", "postgres")):
        return 0
    try:
        from sqlalchemy import create_engine, text  # type: ignore
    except Exception:
        return 0
    try:
        eng = create_engine(uri, connect_args={"connect_timeout": 3}, pool_pre_ping=True)
        with eng.connect() as conn:
            row = conn.execute(text("SELECT pg_database_size(current_database())")).first()
            if row:
                return int(row[0] or 0)
    except Exception:
        return 0
    finally:
        try:
            eng.dispose()  # type: ignore[name-defined]
        except Exception:
            pass
    return 0


def _mysql_db_size(uri: str) -> int:
    # 同 _pg_db_size:仅对 MySQL URI 生效
    if not uri.startswith("mysql"):
        return 0
    try:
        from sqlalchemy import create_engine, text  # type: ignore
    except Exception:
        return 0
    try:
        eng = create_engine(uri, connect_args={"connect_timeout": 3}, pool_pre_ping=True)
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT COALESCE(SUM(data_length + index_length), 0) "
                "FROM information_schema.tables WHERE table_schema = DATABASE()"
            )).first()
            if row:
                return int(row[0] or 0)
    except Exception:
        return 0
    finally:
        try:
            eng.dispose()  # type: ignore[name-defined]
        except Exception:
            pass
    return 0


# ---------------------------------------------------------------------------
# 模型全景
# ---------------------------------------------------------------------------

def collect_models() -> ModelSnapshot:
    from chayuan.settings import Settings

    ms = Settings.model_settings
    platforms = list(getattr(ms, "MODEL_PLATFORMS", []) or [])
    snap = ModelSnapshot()
    snap.default_llm = str(getattr(ms, "DEFAULT_LLM_MODEL", "") or "")
    snap.default_embed = str(getattr(ms, "DEFAULT_EMBEDDING_MODEL", "") or "")

    def _get(p: Any, k: str, default: Any = None) -> Any:
        if isinstance(p, dict):
            return p.get(k, default)
        return getattr(p, k, default)

    for p in platforms:
        name = str(_get(p, "platform_name", "") or "")
        ptype = str(_get(p, "platform_type", "") or "")
        api_base = str(_get(p, "api_base_url", "") or "")
        llm = list(_get(p, "llm_models", []) or [])
        emb = list(_get(p, "embed_models", []) or [])
        rer = list(_get(p, "rerank_models", []) or [])
        img = list(_get(p, "text2image_models", []) or [])

        reachable = _probe_model_endpoint(api_base) if api_base else False
        snap.platforms.append({
            "name": name, "type": ptype, "api_base": api_base,
            "reachable": reachable,
            "llm_count": len(llm), "embed_count": len(emb),
            "rerank_count": len(rer), "image_count": len(img),
        })
        snap.total_llm += len(llm)
        snap.total_embed += len(emb)
        snap.total_rerank += len(rer)
        snap.total_image += len(img)

    # 模型使用频率：从监控快照里拿（来自 meta_data.model 字段 TopN）
    try:
        from chayuan.server.config_panel.monitoring import load_metrics
        metrics = load_metrics()
        snap.usage = [(b.label, int(b.value)) for b in metrics.models]
    except Exception:
        pass

    return snap


def _probe_model_endpoint(api_base: str, timeout: float = 1.0) -> bool:
    """快速探活模型服务：TCP 可达即算。不做 HTTP，是为了 openai / ollama /
    vllm 的健康端点差异太大，socket 足够给轻量状态提示。"""
    try:
        from urllib.parse import urlparse
        p = urlparse(api_base)
        host = p.hostname or "127.0.0.1"
        port = p.port or (443 if (p.scheme or "").endswith("s") else 80)
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 健康评分
# ---------------------------------------------------------------------------

_CPU_WARN = 70.0
_CPU_CRIT = 88.0
_MEM_WARN = 70.0
_MEM_CRIT = 90.0
_DISK_WARN = 80.0
_DISK_CRIT = 92.0
_LOG_WARN_BYTES = 500 * 1024 * 1024      # 500 MB
_LOG_CRIT_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


def _health_of_percent(value: float, warn: float, crit: float) -> str:
    if value >= crit:
        return HEALTH_CRIT
    if value >= warn:
        return HEALTH_WARN
    return HEALTH_OK


def _health_of_logs(size: int) -> str:
    if size >= _LOG_CRIT_BYTES:
        return HEALTH_CRIT
    if size >= _LOG_WARN_BYTES:
        return HEALTH_WARN
    return HEALTH_OK


def build_snapshot(history: Optional[Dict[str, Deque[float]]] = None) -> PerfSnapshot:
    """抓一份完整的性能快照。可选传入 ``history`` 字典让时序卡滚动。"""
    snap = PerfSnapshot()
    snap.hardware = collect_hardware()
    snap.storage = collect_storage()
    snap.models = collect_models()
    try:
        from chayuan.server.config_panel.monitoring import load_metrics
        snap.metrics = load_metrics()
    except Exception as e:  # noqa: BLE001
        logger.warning("monitoring load_metrics failed: %s", e)

    # 静态配置体检分数（复用 scalability）
    try:
        from chayuan.server.config_panel.scalability import build_report
        rep = build_report()
        counts = {"critical": 0, "warning": 0, "info": 0, "ok": 0}
        for c in rep.checks:
            counts[c.severity] = counts.get(c.severity, 0) + 1
        snap.health_counts = counts
        snap.est_concurrent = rep.est_concurrent_users
    except Exception:
        pass

    # 把硬件实时指标也合并进综合评分：每 10% 内超阈扣 10 分
    base = 100
    if snap.hardware.cpu_percent >= _CPU_CRIT:
        base -= 20
    elif snap.hardware.cpu_percent >= _CPU_WARN:
        base -= 8
    if snap.hardware.mem_percent >= _MEM_CRIT:
        base -= 20
    elif snap.hardware.mem_percent >= _MEM_WARN:
        base -= 8
    for d in snap.hardware.disks:
        if float(d.get("percent", 0)) >= _DISK_CRIT:
            base -= 10
            break
        if float(d.get("percent", 0)) >= _DISK_WARN:
            base -= 4
            break
    base -= snap.health_counts.get("critical", 0) * 10
    base -= snap.health_counts.get("warning", 0) * 3
    snap.health_score = max(0, min(100, base))

    # 记录时序（传入的 history 由调用方在 state 里维护）
    if history is not None:
        _push_bounded(history.setdefault("cpu", collections.deque(maxlen=60)), snap.hardware.cpu_percent)
        _push_bounded(history.setdefault("mem", collections.deque(maxlen=60)), snap.hardware.mem_percent)
        gpu_util = snap.hardware.gpus[0]["util"] if snap.hardware.gpus else 0.0
        _push_bounded(history.setdefault("gpu", collections.deque(maxlen=60)), float(gpu_util))

    return snap


def _push_bounded(q: Deque[float], v: float) -> None:
    q.append(float(v))


# ---------------------------------------------------------------------------
# 工具：字节格式化 / markdown 渲染
# ---------------------------------------------------------------------------

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


def _fmt_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


# ---------------------------------------------------------------------------
# 样式注入
# ---------------------------------------------------------------------------

_STYLE_FLAG = "_chayuan_perf_styles_injected"


def _inject_styles(ui) -> None:
    if getattr(ui, _STYLE_FLAG, False):
        return
    css = r"""
    <style>
      /* 300 × 200 横向卡片网格；窄屏自动折行居中 */
      .perf-grid-3 {
        display:grid;
        grid-template-columns: repeat(auto-fill, 300px);
        gap: 16px;
        width: 100%;
        justify-content: center;
      }
      .perf-card {
        background:#fff;
        border-radius:14px;
        border:1px solid #e5e7eb;
        box-shadow: 0 2px 10px rgba(0,0,0,0.04);
        padding: 10px 14px 8px;
        width: 300px;
        height: 200px;
        position: relative;
        overflow: hidden;
        display: flex;
        flex-direction: column;
        transition: box-shadow .2s ease, transform .15s ease;
      }
      /* 桑基 / 热力这类需要更多画布的卡，横跨两列（= 2×300 + 一个 gap） */
      .perf-card-wide {
        width: 616px;
        height: 200px;
        grid-column: span 2;
      }
      @media (max-width: 680px) {
        .perf-card-wide { width: 300px; grid-column: auto; }
      }
      .perf-card::before {
        content:'';
        position:absolute; top:0; left:0; right:0; height:3px;
        background: linear-gradient(90deg, var(--perf-accent,#3b82f6), transparent 85%);
        opacity: .85;
      }
      .perf-card:hover {
        box-shadow: 0 8px 20px rgba(59,130,246,0.15);
        transform: translateY(-1px);
      }
      .perf-card .perf-head {
        display:flex; align-items:center; gap:6px; margin-bottom:4px;
        flex: 0 0 auto;
      }
      .perf-card .perf-title {
        font-size:13px; font-weight:600; color:#111827;
        flex: 1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
      }
      .perf-card .perf-dot {
        width:9px; height:9px; border-radius:50%;
        background: var(--perf-dot-color, #9ca3af);
        flex: 0 0 auto;
      }
      .perf-card .perf-pill {
        display:inline-flex; align-items:center; gap:4px;
        padding: 1px 7px; border-radius: 999px;
        font-size:10px; font-weight: 500;
        border: 1px solid transparent;
        white-space: nowrap;
      }
      .perf-card .perf-pill.h-ok   { color:#15803d; background:rgba(34,197,94,0.10);  border-color:#86efac; }
      .perf-card .perf-pill.h-warn { color:#b45309; background:rgba(245,158,11,0.12); border-color:#fcd34d; }
      .perf-card .perf-pill.h-crit { color:#b91c1c; background:rgba(239,68,68,0.12);  border-color:#fca5a5; animation: perf-pulse 1.3s infinite; }
      .perf-card .perf-pill.h-info { color:#475569; background:rgba(148,163,184,0.14);border-color:#cbd5e1; }

      /* 主体内容区：垂直排布，可滚动避免超出 200 高度时破版 */
      .perf-card .perf-body {
        flex: 1; min-height: 0;
        display: flex; flex-direction: column;
        gap: 2px;
        overflow: hidden;
      }

      /* 紧凑 KPI + 环形副图的 "横向 hero"：左大数字 右小圆环 */
      .perf-hero {
        display:flex; align-items:center; gap:10px;
      }
      .perf-hero .perf-hero-num {
        font-size: 28px; font-weight:700; color:#0f172a;
        line-height: 1.05; letter-spacing: -0.02em;
        font-variant-numeric: tabular-nums;
      }
      .perf-hero .perf-hero-ring {
        width: 60px; height: 60px; flex: 0 0 auto;
      }

      /* KPI 单独一行时用的大数字 */
      .perf-kpi {
        font-size: 26px; font-weight:700; color:#0f172a;
        line-height: 1.05;
        letter-spacing: -0.02em;
        font-variant-numeric: tabular-nums;
      }
      .perf-kpi .unit {
        font-size:12px; font-weight:500; color:#64748b; margin-left:4px;
      }
      .perf-sub {
        font-size:11px; color:#6b7280; margin-top:2px;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }

      /* 滚动 sparkline（CPU / 内存 / GPU 的"流动"动画主体） */
      .perf-live-chart {
        width:100%; height: 70px;
      }
      /* 每核心小柱条 */
      .perf-core-strip {
        display:flex; gap:2px; align-items:flex-end;
        height:22px;
      }
      .perf-core-bar {
        flex: 1; min-width:3px;
        background: linear-gradient(180deg, var(--perf-accent, #3b82f6), rgba(59,130,246,0.3));
        border-radius: 2px 2px 0 0;
        transition: height .5s cubic-bezier(.4,0,.2,1);
      }
      .perf-core-bar.hot { background: linear-gradient(180deg, #ef4444, #fca5a5); }
      .perf-core-bar.warm { background: linear-gradient(180deg, #f59e0b, #fde68a); }

      /* 通用图表容器 —— 内部 echart 会填满 */
      .perf-chart-sm { width:100%; height: 140px; }
      .perf-chart-md { width:100%; height: 160px; }
      .perf-chart-wide { width:100%; height: 160px; }

      @keyframes perf-pulse {
        0%,100% { box-shadow: 0 0 0 0 rgba(239,68,68,0.55); }
        50%     { box-shadow: 0 0 0 4px rgba(239,68,68,0.0); }
      }

      /* AI 分析卡：特殊的霓虹渐变；保持全宽且可随内容拉高 */
      .perf-ai-card {
        background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 55%, #4c1d95 100%);
        color: #f1f5f9;
        border: 0;
        border-radius: 16px;
        padding: 18px 22px;
        overflow: hidden;
        position: relative;
        width: 100%;
      }
      .perf-ai-card::after {
        content:''; position:absolute; inset:0;
        background: radial-gradient(circle at 20% 20%, rgba(59,130,246,0.25) 0%, transparent 60%),
                    radial-gradient(circle at 80% 120%, rgba(139,92,246,0.22) 0%, transparent 55%);
        pointer-events:none;
      }
      .perf-ai-card .perf-ai-title {
        font-size: 18px; font-weight:700; letter-spacing:0.02em;
      }
      .perf-ai-card .perf-ai-sub { font-size:12px; opacity:.8; margin-top:4px; }
      .perf-ai-card .perf-ai-body {
        background: rgba(15,23,42,0.45);
        border: 1px solid rgba(148,163,184,0.25);
        border-radius: 10px;
        padding: 12px 14px;
        margin-top: 14px;
        min-height: 60px;
        font-size: 13px;
        line-height: 1.65;
        white-space: pre-wrap;
        max-height: 420px;
        overflow: auto;
      }
      .perf-ai-card .perf-ai-body em,
      .perf-ai-card .perf-ai-body code { color:#93c5fd; }
      .perf-ai-card .perf-ai-body h1,
      .perf-ai-card .perf-ai-body h2,
      .perf-ai-card .perf-ai-body h3 { color:#e0e7ff; margin-top:10px; }
      .perf-ai-card .perf-ai-body strong { color:#f8fafc; }
    </style>
    """
    ui.add_head_html(css)
    setattr(ui, _STYLE_FLAG, True)


# ---------------------------------------------------------------------------
# 卡片基础元素
# ---------------------------------------------------------------------------

def _card_header(ui, title: str, health: str, *, accent: str = "#3b82f6") -> None:
    """每张卡顶部统一的 header：小色点 + 标题 + 右上 pill。"""
    color = _HEALTH_COLOR.get(health, _HEALTH_COLOR[HEALTH_INFO])
    label = _HEALTH_LABEL.get(health, "")
    with ui.element("div").classes("perf-head"):
        ui.html(
            f'<span class="perf-dot" style="background:{color}"></span>'
        )
        ui.html(f'<span class="perf-title">{_escape(title)}</span>')
        ui.html(f'<span class="perf-pill h-{health}">{label}</span>')


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _card(ui, *, accent: str = "#3b82f6") -> Any:
    """生成一张 .perf-card 容器。"""
    el = ui.element("div").classes("perf-card")
    el.style(f"--perf-accent:{accent}")
    return el


# ---------------------------------------------------------------------------
# 渲染入口
# ---------------------------------------------------------------------------

def render_performance_page(ui) -> None:
    """性能体检主页入口。在 NiceGUI 页面上下文内调用。

    关键：**不要**在这里同步执行 ``build_snapshot()``。该函数会做大量阻塞 I/O
    （递归扫 ``CHAYUAN_ROOT`` 目录、TCP 探活模型平台、``nvidia-smi`` 子进程、
    psutil cpu_percent 小睡等），单次耗时常在 1–10 秒，远超 Socket.IO 心跳
    超时，会让浏览器误判连接死亡并主动断开（日志里只有 ``connection closed``
    → 立刻 reconnect，却看不到任何异常堆栈）。

    解法：首次进入先用**空快照**把骨架画出来，随后用 ``asyncio.to_thread``
    把真正的采集放到线程里跑，跑完再增量更新卡片。后续的手动刷新 / 3s / 30s
    周期刷新同样走线程化 handler，永不阻塞主事件循环。
    """
    _inject_styles(ui)

    # state 共享：history 用于 sparkline，card_refs 支持增量刷新
    state: Dict[str, Any] = {
        "history": {},           # sid -> deque
        "cards": {},             # sid -> {element refs}
        "snapshot": PerfSnapshot(),   # 首帧用空快照占位，渲染后再异步填充
        "auto_switch": None,
        "last_update_label": None,
        "refreshing": False,     # 防抖：避免多个慢任务叠加跑
        "ai": {},                # AI 分析卡的状态
    }

    # ---- Header -------------------------------------------------------------
    with ui.row().classes("items-center w-full no-wrap q-mb-xs"):
        ui.label("性能与可扩展性").classes("text-2xl font-semibold")
        ui.space()
        state["last_update_label"] = ui.label("首次加载中…").classes(
            "text-xs text-grey-7 font-mono"
        )
        state["auto_switch"] = ui.switch("自动刷新", value=True).props("dense")
        state["auto_switch"].tooltip("开启后每 3 秒刷新 CPU/内存/GPU 动画，30 秒刷新一次重数据")

        async def _on_refresh_click() -> None:
            await _refresh_all_async(ui, state)

        ui.button(
            icon="refresh",
            on_click=_on_refresh_click,
        ).props("flat round dense").tooltip("立即重新抓取全部指标")
        header_ai_btn = ui.button(
            "AI 深度分析", icon="auto_awesome",
        ).props("color=primary no-caps")
        header_ai_btn.tooltip(
            "把当前所有指标快照喂给已配置的 LLM，生成承载估算 / 瓶颈诊断 / 改进建议"
        )
        state["ai"]["header_btn"] = header_ai_btn

        async def _on_header_ai() -> None:
            await _run_ai_analysis_async(ui, state)

        header_ai_btn.on("click", _on_header_ai)

    ui.label(
        "本页面只读展示：硬件实时指标（CPU / 内存 / 磁盘 / GPU）来自进程本机，"
        "每张卡右上角的色点实时反映健康度（绿=健康 / 黄=注意 / 红=告警 / 灰=参考）。"
        "如需修改配置请去对应功能页；点右上角「AI 深度分析」可让大模型给出硬件容量评估。"
    ).classes("text-sm text-grey-8 q-mb-md")

    # ---- Overview Row -------------------------------------------------------
    ui.label("综合概览").classes("text-base font-semibold q-mt-sm q-mb-xs text-grey-8")
    with ui.element("div").classes("perf-grid-3"):
        _render_overall_health_card(ui, state)
        _render_severity_radar_card(ui, state)
        _render_capacity_card(ui, state)

    ui.label("硬件实时").classes("text-base font-semibold q-mt-md q-mb-xs text-grey-8")
    with ui.element("div").classes("perf-grid-3"):
        _render_cpu_card(ui, state)
        _render_memory_card(ui, state)
        _render_disk_card(ui, state)
        _render_gpu_card(ui, state)
        _render_network_card(ui, state)

    ui.label("数据与存储").classes("text-base font-semibold q-mt-md q-mb-xs text-grey-8")
    with ui.element("div").classes("perf-grid-3"):
        _render_storage_card(ui, state)
        _render_logs_card(ui, state)
        _render_db_size_card(ui, state)

    ui.label("模型全景").classes("text-base font-semibold q-mt-md q-mb-xs text-grey-8")
    with ui.element("div").classes("perf-grid-3"):
        _render_model_availability_card(ui, state)
        _render_model_usage_card(ui, state)
        _render_model_types_card(ui, state)

    ui.label("流量与反馈").classes("text-base font-semibold q-mt-md q-mb-xs text-grey-8")
    with ui.element("div").classes("perf-grid-3"):
        # 热力图用 .perf-card-wide 横跨 2 列；外加两张 300×200 饼 / 雷达
        _render_traffic_heatmap_card(ui, state)
        _render_chat_type_card(ui, state)
        _render_feedback_radar_card(ui, state)

    ui.label("知识库画像").classes("text-base font-semibold q-mt-md q-mb-xs text-grey-8")
    with ui.element("div").classes("perf-grid-3"):
        # 桑基图 .perf-card-wide 横跨 2 列；Top KB 横道 + 扩展散点各 300×200
        _render_kb_sankey_card(ui, state)
        _render_top_kbs_card(ui, state)
        _render_file_ext_card(ui, state)

    # ---- AI 分析卡 ----------------------------------------------------------
    ui.label("AI 硬件容量分析").classes("text-base font-semibold q-mt-md q-mb-xs text-grey-8")
    _render_ai_card(ui, state)

    # ---- 首帧异步填充 + 自动刷新 -------------------------------------------
    # 页面挂载后立刻发起第一次后台抓取，不阻塞事件循环；随后 3s 只刷硬件、30s
    # 全量刷。全部走 ``asyncio.to_thread``，保证 socket.io 心跳不掉。
    async def _initial_load() -> None:
        await _refresh_all_async(ui, state)

    ui.timer(0.05, _initial_load, once=True)
    ui.timer(3.0, lambda: _tick_hardware_async(ui, state))
    ui.timer(30.0, lambda: _tick_full_async(ui, state))


def _update_last_label(state: Dict[str, Any]) -> None:
    try:
        ts = time.strftime("%H:%M:%S", time.localtime(state["snapshot"].generated_at))
        state["last_update_label"].set_text(f"最近刷新：{ts}")
    except Exception:
        pass


async def _tick_hardware_async(ui, state: Dict[str, Any]) -> None:
    """3s 周期：只刷硬件指标（``ps.cpu_percent`` 会阻塞 100ms，依然放进线程）。"""
    if not bool(getattr(state.get("auto_switch"), "value", True)):
        return
    # 刷新正忙（首帧全量加载尚未完成）时跳过，避免叠加阻塞
    if state.get("refreshing"):
        return
    try:
        hw = await asyncio.to_thread(collect_hardware)
    except Exception as e:  # noqa: BLE001
        logger.warning("tick_hardware failed: %s", e)
        return
    snap: PerfSnapshot = state["snapshot"]
    snap.hardware = hw
    snap.generated_at = time.time()
    _push_bounded(
        state["history"].setdefault("cpu", collections.deque(maxlen=60)), hw.cpu_percent
    )
    _push_bounded(
        state["history"].setdefault("mem", collections.deque(maxlen=60)), hw.mem_percent
    )
    gpu_util = hw.gpus[0]["util"] if hw.gpus else 0.0
    _push_bounded(
        state["history"].setdefault("gpu", collections.deque(maxlen=60)), float(gpu_util)
    )

    try:
        _update_hardware_cards(state)
        _update_last_label(state)
    except Exception as e:  # noqa: BLE001
        logger.warning("tick_hardware update failed: %s", e)


async def _tick_full_async(ui, state: Dict[str, Any]) -> None:
    """30s 周期：全量刷新（快照放线程里跑，绝不阻塞主循环）。"""
    if not bool(getattr(state.get("auto_switch"), "value", True)):
        return
    await _refresh_all_async(ui, state)


async def _refresh_all_async(ui, state: Dict[str, Any]) -> None:
    """全量刷新快照。

    ``build_snapshot`` 会递归扫目录 + 探活多个平台 + 跑 ``nvidia-smi`` 等，
    单次常在数秒级别。这里用 ``asyncio.to_thread`` 把它挪到线程池跑，主循环
    得以继续响应 socket.io 心跳；否则 NiceGUI 的 WebSocket 会被浏览器判为
    超时并断开重连（症状：点击菜单后界面卡死一段时间、日志里只有
    ``connection closed``、没有任何异常堆栈）。
    """
    if state.get("refreshing"):
        # 已有一次刷新在跑——此次忽略，不叠加压力
        return
    state["refreshing"] = True
    try:
        try:
            snap = await asyncio.to_thread(build_snapshot, state["history"])
        except Exception as e:  # noqa: BLE001
            logger.warning("refresh_all failed: %s", e)
            return
        state["snapshot"] = snap
        try:
            _update_hardware_cards(state)
            _update_overview_cards(state)
            _update_storage_cards(state)
            _update_model_cards(state)
            _update_traffic_cards(state)
            _update_kb_cards(state)
            _update_last_label(state)
        except Exception as e:  # noqa: BLE001
            logger.warning("refresh_all update failed: %s", e)
    finally:
        state["refreshing"] = False


def _refresh_all(ui, state: Dict[str, Any]) -> None:
    """兼容同步入口：内部 schedule 一次异步刷新，调用方无需 await。

    早先的 `_do_clear_logs` 等同步 handler 会直接调用 `_refresh_all`，为避免
    改动调用点太多，这里保留同名函数但把真正的工作 fire-and-forget 给事件循环。
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        logger.warning("refresh_all called outside event loop; skip")
        return
    if loop.is_running():
        loop.create_task(_refresh_all_async(ui, state))
    else:
        loop.run_until_complete(_refresh_all_async(ui, state))


# ---------------------------------------------------------------------------
# Overview Row
# ---------------------------------------------------------------------------

def _render_overall_health_card(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    health = _score_health(snap.health_score)
    with _card(ui, accent="#3b82f6"):
        _card_header(ui, "综合健康评分", health, accent="#3b82f6")
        with ui.element("div").classes("perf-body"):
            option = _gauge_option(snap.health_score, max_value=100, label="分")
            chart = ui.echart(option).classes("perf-chart-md")
            state["cards"]["overall_health"] = {"chart": chart}
            ui.label(
                f"严重 {snap.health_counts.get('critical', 0)} · "
                f"警告 {snap.health_counts.get('warning', 0)} · "
                f"建议 {snap.health_counts.get('info', 0)} · "
                f"达标 {snap.health_counts.get('ok', 0)}"
            ).classes("perf-sub")


def _score_health(score: int) -> str:
    if score >= 80:
        return HEALTH_OK
    if score >= 60:
        return HEALTH_WARN
    return HEALTH_CRIT


def _gauge_option(value: float, *, max_value: float = 100.0, label: str = "%") -> Dict[str, Any]:
    # ECharts radial gauge
    pct = max(0, min(100, (value / max_value) * 100.0))
    return {
        "series": [{
            "type": "gauge",
            "startAngle": 210,
            "endAngle": -30,
            "min": 0, "max": max_value,
            "progress": {"show": True, "width": 14},
            "axisLine": {"lineStyle": {"width": 14, "color": [[pct / 100, _score_color(pct)], [1, "#e5e7eb"]]}},
            "pointer": {"show": False},
            "axisTick": {"show": False},
            "splitLine": {"show": False},
            "axisLabel": {"show": False},
            "anchor": {"show": False},
            "title": {"show": False},
            "detail": {
                "valueAnimation": True,
                "offsetCenter": [0, "10%"],
                "fontSize": 28, "fontWeight": 700,
                "color": _score_color(pct),
                "formatter": f"{{value}}{label}",
            },
            "data": [{"value": round(value, 1)}],
        }]
    }


def _score_color(pct: float) -> str:
    if pct >= 80:
        return "#22c55e"
    if pct >= 60:
        return "#f59e0b"
    return "#ef4444"


def _render_severity_radar_card(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    counts = snap.health_counts or {"critical": 0, "warning": 0, "info": 0, "ok": 0}
    max_v = max(1, max(counts.values()) if counts else 1)
    health = (
        HEALTH_CRIT if counts.get("critical") else
        HEALTH_WARN if counts.get("warning") else HEALTH_OK
    )
    with _card(ui, accent="#8b5cf6"):
        _card_header(ui, "问题分类（雷达图）", health, accent="#8b5cf6")
        with ui.element("div").classes("perf-body"):
            option = {
                "radar": {
                    "indicator": [
                        {"name": "严重", "max": max_v},
                        {"name": "警告", "max": max_v},
                        {"name": "建议", "max": max_v},
                        {"name": "达标", "max": max_v},
                    ],
                    "radius": "62%",
                    "axisName": {"color": "#475569", "fontSize": 10},
                    "splitArea": {"areaStyle": {"color": ["rgba(139,92,246,0.05)", "rgba(139,92,246,0.02)"]}},
                },
                "series": [{
                    "type": "radar",
                    "areaStyle": {"color": "rgba(139,92,246,0.35)"},
                    "lineStyle": {"color": "#8b5cf6"},
                    "itemStyle": {"color": "#8b5cf6"},
                    "symbolSize": 5,
                    "data": [{
                        "value": [
                            counts.get("critical", 0),
                            counts.get("warning", 0),
                            counts.get("info", 0),
                            counts.get("ok", 0),
                        ],
                        "name": "配置体检",
                    }],
                }],
            }
            chart = ui.echart(option).classes("perf-chart-md")
            state["cards"]["severity_radar"] = {"chart": chart}


def _render_capacity_card(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    est = snap.est_concurrent or "—"
    health = HEALTH_OK if "5000" in est else HEALTH_WARN if "1000" in est or "几百" in est else HEALTH_CRIT
    with _card(ui, accent="#06b6d4"):
        _card_header(ui, "当前并发承载估算", health, accent="#06b6d4")
        # 推荐升级维度 —— 用一行一项，且最多 3 项，保证 200 高度不溢出
        bullets = []
        hw = snap.hardware
        if hw.cpu_percent >= _CPU_WARN:
            bullets.append(f"CPU {hw.cpu_percent:.0f}% 偏高，增 worker / 升核")
        if hw.mem_percent >= _MEM_WARN:
            bullets.append(f"内存 {hw.mem_percent:.0f}% 偏高，扩内存")
        for d in hw.disks:
            if float(d.get("percent", 0)) >= _DISK_WARN:
                bullets.append(f"磁盘 {d['mount']} {d['percent']:.0f}%，清理 / 扩容")
                break
        if snap.health_counts.get("critical"):
            bullets.append(f"体检 {snap.health_counts['critical']} 项严重，优先修")
        if not bullets:
            bullets.append("现状无明显瓶颈；压测后按瓶颈扩容")
        bullets = bullets[:3]
        with ui.element("div").classes("perf-body"):
            ui.html(
                f'<div class="perf-kpi">{_escape(est)}</div>'
                f'<div class="perf-sub">基于配置体检 + 硬件现状粗估</div>'
                "<ul style='margin:8px 0 0;padding-left:16px;font-size:11px;color:#475569;line-height:1.55;'>"
                + "".join(f"<li>{_escape(b)}</li>" for b in bullets)
                + "</ul>"
            )


def _update_overview_cards(state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    oh = state["cards"].get("overall_health")
    if oh:
        _set_chart(oh["chart"], _gauge_option(snap.health_score, max_value=100, label="分"))
    sr = state["cards"].get("severity_radar")
    if sr:
        counts = snap.health_counts or {}
        max_v = max(1, max(counts.values()) if counts else 1)
        _set_chart(sr["chart"], {
            "radar": {
                "indicator": [
                    {"name": "严重", "max": max_v},
                    {"name": "警告", "max": max_v},
                    {"name": "建议", "max": max_v},
                    {"name": "达标", "max": max_v},
                ],
            },
            "series": [{
                "type": "radar",
                "areaStyle": {"color": "rgba(139,92,246,0.35)"},
                "lineStyle": {"color": "#8b5cf6"},
                "itemStyle": {"color": "#8b5cf6"},
                "data": [{
                    "value": [
                        counts.get("critical", 0), counts.get("warning", 0),
                        counts.get("info", 0), counts.get("ok", 0),
                    ], "name": "配置体检",
                }],
            }],
        })


def _set_chart(chart_el, option: Dict[str, Any]) -> None:
    try:
        chart_el.options = option
        chart_el.update()
    except Exception:
        try:
            chart_el.set_options(option)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Hardware Row
# ---------------------------------------------------------------------------

def _render_cpu_card(ui, state: Dict[str, Any]) -> None:
    """CPU 卡（300×200）：大数字 % + 滚动 sparkline + 每核心小柱 + 元信息。

    去掉了之前的环形 gauge，因为 200 高度装不下环 + 条 + 柱三样。数字本身
    已经足够直观；配色 + 滚动动画提供了动态感。
    """
    snap: PerfSnapshot = state["snapshot"]
    hw = snap.hardware
    health = _health_of_percent(hw.cpu_percent, _CPU_WARN, _CPU_CRIT)
    with _card(ui, accent="#3b82f6") as card:
        _card_header(ui, "CPU 使用率", health, accent="#3b82f6")
        with ui.element("div").classes("perf-body"):
            kpi = ui.html(
                f'<div class="perf-kpi">{hw.cpu_percent:.1f}<span class="unit">%</span></div>'
            )
            opt_line = _sparkline_option(state["history"].get("cpu"))
            line = ui.echart(opt_line).classes("perf-live-chart")
            core_wrap = ui.html(_core_strip_html(hw.cpu_per_core, _CPU_WARN, _CPU_CRIT))
            sub = ui.html(
                f'<div class="perf-sub">{hw.cpu_count_logical}核 · '
                f'Load {hw.load_1:.2f}/{hw.load_5:.2f}/{hw.load_15:.2f}</div>'
            )
        state["cards"]["cpu"] = {
            "card": card, "kpi": kpi, "line": line, "cores": core_wrap, "sub": sub,
        }


def _sparkline_option(values: Optional[Deque[float]]) -> Dict[str, Any]:
    data = list(values or [])
    # 固定 60 点宽度，前面用 None 填充，避免从左边开始"拉长"的动画抖动
    if len(data) < 60:
        data = [None] * (60 - len(data)) + data  # type: ignore[list-item]
    return {
        "animation": True,
        "animationDuration": 400,
        "grid": {"left": 4, "right": 4, "top": 4, "bottom": 4},
        "xAxis": {"type": "category", "show": False, "data": list(range(60)), "boundaryGap": False},
        "yAxis": {"type": "value", "show": False, "min": 0, "max": 100},
        "tooltip": {"trigger": "axis", "formatter": "{c}%"},
        "series": [{
            "type": "line",
            "data": data,
            "smooth": True,
            "symbol": "none",
            "lineStyle": {"color": "#3b82f6", "width": 2},
            "areaStyle": {"color": {
                "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
                "colorStops": [
                    {"offset": 0, "color": "rgba(59,130,246,0.35)"},
                    {"offset": 1, "color": "rgba(59,130,246,0.02)"},
                ],
            }},
        }],
    }


def _core_strip_html(values: List[float], warn: float, crit: float) -> str:
    if not values:
        return '<div class="perf-sub">per-core 数据不可用</div>'
    bars = []
    for v in values:
        cls = "perf-core-bar"
        if v >= crit:
            cls += " hot"
        elif v >= warn:
            cls += " warm"
        bars.append(f'<div class="{cls}" style="height:{max(4, v)}%"></div>')
    return (
        '<div class="perf-core-strip" title="每个物理核心使用率">'
        + "".join(bars)
        + "</div>"
    )


def _render_memory_card(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    hw = snap.hardware
    health = _health_of_percent(hw.mem_percent, _MEM_WARN, _MEM_CRIT)
    with _card(ui, accent="#8b5cf6") as card:
        _card_header(ui, "内存使用率", health, accent="#8b5cf6")
        with ui.element("div").classes("perf-body"):
            kpi = ui.html(
                f'<div class="perf-kpi">{hw.mem_percent:.1f}<span class="unit">%</span></div>'
            )
            opt_line = _sparkline_option(state["history"].get("mem"))
            opt_line["series"][0]["lineStyle"]["color"] = "#8b5cf6"
            opt_line["series"][0]["areaStyle"]["color"]["colorStops"] = [
                {"offset": 0, "color": "rgba(139,92,246,0.35)"},
                {"offset": 1, "color": "rgba(139,92,246,0.02)"},
            ]
            line = ui.echart(opt_line).classes("perf-live-chart")
            sub = ui.html(
                f'<div class="perf-sub">已用 {_fmt_bytes(hw.mem_used)} / '
                f'{_fmt_bytes(hw.mem_total)}'
                + (f' · Swap {_fmt_bytes(hw.swap_used)}/{_fmt_bytes(hw.swap_total)}'
                   if hw.swap_total > 0 else "")
                + '</div>'
            )
        state["cards"]["mem"] = {"card": card, "kpi": kpi, "line": line, "sub": sub}


def _render_disk_card(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    hw = snap.hardware
    worst = max((float(d.get("percent", 0)) for d in hw.disks), default=0.0)
    health = _health_of_percent(worst, _DISK_WARN, _DISK_CRIT)
    with _card(ui, accent="#f59e0b") as card:
        _card_header(ui, "磁盘占用", health, accent="#f59e0b")
        with ui.element("div").classes("perf-body"):
            opt = _disk_option(hw.disks)
            chart = ui.echart(opt).classes("perf-chart-md")
            ui.label(
                f"共 {len(hw.disks)} 个挂载点；占用最高 {worst:.0f}%"
            ).classes("perf-sub")
        state["cards"]["disk"] = {"card": card, "chart": chart}


def _disk_option(disks: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not disks:
        return {"series": []}
    # 按已用率倒序，最多展示 Top 5 挂载；其它用一行"其它 N 个"合并
    sorted_disks = sorted(disks, key=lambda d: float(d.get("percent", 0)), reverse=True)
    shown = sorted_disks[:5]
    rest = sorted_disks[5:]

    names: List[str] = []
    used: List[float] = []
    free: List[float] = []
    for d in shown:
        label = d["mount"]
        if len(label) > 12:
            label = "…" + label[-11:]
        names.append(label)
        used.append(round(d["used"] / 1024 / 1024 / 1024, 2))
        free.append(round(d["free"] / 1024 / 1024 / 1024, 2))
    if rest:
        names.append(f"其它 {len(rest)} 个")
        used.append(round(sum(r["used"] for r in rest) / 1024 / 1024 / 1024, 2))
        free.append(round(sum(r["free"] for r in rest) / 1024 / 1024 / 1024, 2))
    return {
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "grid": {"left": 70, "right": 10, "top": 6, "bottom": 26},
        "xAxis": {"type": "value", "axisLabel": {"formatter": "{value}G", "fontSize": 10}},
        "yAxis": {"type": "category", "data": names, "axisLabel": {"fontSize": 10}},
        "series": [
            {"name": "已用", "type": "bar", "stack": "disk", "data": used,
             "itemStyle": {"color": "#ef4444", "borderRadius": [0, 2, 2, 0]}},
            {"name": "可用", "type": "bar", "stack": "disk", "data": free,
             "itemStyle": {"color": "#22c55e", "borderRadius": [0, 4, 4, 0]}},
        ],
    }


def _render_gpu_card(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    hw = snap.hardware
    if not hw.gpus:
        with _card(ui, accent="#64748b"):
            _card_header(ui, "GPU", HEALTH_INFO, accent="#64748b")
            with ui.element("div").classes("perf-body"):
                ui.html(
                    '<div class="perf-kpi">未检测到</div>'
                    '<div class="perf-sub">无 nvidia-smi / 无 torch MPS；有 NVIDIA 卡请装驱动</div>'
                )
        return
    gpu = hw.gpus[0]
    util = float(gpu.get("util", 0))
    health = _health_of_percent(util, _CPU_WARN, _CPU_CRIT)
    with _card(ui, accent="#ec4899") as card:
        # 名称太长会挤掉 pill，截断处理
        name = gpu.get("name", "")
        title = f"GPU · {name[:18]}{'…' if len(name) > 18 else ''}"
        _card_header(ui, title, health, accent="#ec4899")
        with ui.element("div").classes("perf-body"):
            kpi = ui.html(
                f'<div class="perf-kpi">{util:.0f}<span class="unit">%</span></div>'
            )
            opt_line = _sparkline_option(state["history"].get("gpu"))
            opt_line["series"][0]["lineStyle"]["color"] = "#ec4899"
            opt_line["series"][0]["areaStyle"]["color"]["colorStops"] = [
                {"offset": 0, "color": "rgba(236,72,153,0.35)"},
                {"offset": 1, "color": "rgba(236,72,153,0.02)"},
            ]
            line = ui.echart(opt_line).classes("perf-live-chart")
            mem_total = int(gpu.get("mem_total", 0) or 0)
            mem_used = int(gpu.get("mem_used", 0) or 0)
            if mem_total > 0:
                sub_text = (
                    f'显存 {_fmt_bytes(mem_used)}/{_fmt_bytes(mem_total)} · '
                    f'温度 {float(gpu.get("temp", 0)):.0f}°C'
                )
            else:
                sub_text = "显存 / 温度由平台决定，当前不可用"
            sub = ui.html(f'<div class="perf-sub">{_escape(sub_text)}</div>')
        state["cards"]["gpu"] = {"card": card, "kpi": kpi, "line": line, "sub": sub}


def _render_network_card(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    hw = snap.hardware
    total = hw.net_bytes_recv + hw.net_bytes_sent
    with _card(ui, accent="#06b6d4"):
        _card_header(ui, "网络累计流量", HEALTH_INFO, accent="#06b6d4")
        with ui.element("div").classes("perf-body"):
            ui.html(
                f'<div class="perf-kpi">{_escape(_fmt_bytes(total))}</div>'
                f'<div class="perf-sub">入 {_escape(_fmt_bytes(hw.net_bytes_recv))} · '
                f'出 {_escape(_fmt_bytes(hw.net_bytes_sent))}</div>'
            )
            opt = {
                "grid": {"left": 40, "right": 10, "top": 4, "bottom": 22},
                "xAxis": {"type": "value", "axisLabel": {"formatter": "{value}G", "fontSize": 10}},
                "yAxis": {"type": "category", "data": ["入", "出"],
                          "axisLabel": {"fontSize": 11}},
                "series": [{
                    "type": "bar",
                    "data": [
                        round(hw.net_bytes_recv / 1024 / 1024 / 1024, 2),
                        round(hw.net_bytes_sent / 1024 / 1024 / 1024, 2),
                    ],
                    "itemStyle": {"color": "#06b6d4", "borderRadius": [0, 4, 4, 0]},
                    "label": {"show": True, "position": "right",
                              "formatter": "{c}G", "fontSize": 10},
                }],
            }
            ui.echart(opt).classes("w-full").style("height:100px")


def _update_hardware_cards(state: Dict[str, Any]) -> None:
    """每 3 秒刷一次：CPU / 内存 / GPU 的 KPI 数字 + sparkline + 副标题。"""
    snap: PerfSnapshot = state["snapshot"]
    hw = snap.hardware

    # CPU
    cpu_refs = state["cards"].get("cpu")
    if cpu_refs:
        _set_html(
            cpu_refs["kpi"],
            f'<div class="perf-kpi">{hw.cpu_percent:.1f}<span class="unit">%</span></div>',
        )
        line_opt = _sparkline_option(state["history"].get("cpu"))
        line_opt["series"][0]["lineStyle"]["color"] = "#3b82f6"
        line_opt["series"][0]["areaStyle"]["color"]["colorStops"] = _area_stops("#3b82f6")
        _set_chart(cpu_refs["line"], line_opt)
        _set_html(cpu_refs["cores"], _core_strip_html(hw.cpu_per_core, _CPU_WARN, _CPU_CRIT))
        _set_html(
            cpu_refs["sub"],
            f'<div class="perf-sub">{hw.cpu_count_logical}核 · '
            f'Load {hw.load_1:.2f}/{hw.load_5:.2f}/{hw.load_15:.2f}</div>',
        )

    # 内存
    mem_refs = state["cards"].get("mem")
    if mem_refs:
        _set_html(
            mem_refs["kpi"],
            f'<div class="perf-kpi">{hw.mem_percent:.1f}<span class="unit">%</span></div>',
        )
        line_opt = _sparkline_option(state["history"].get("mem"))
        line_opt["series"][0]["lineStyle"]["color"] = "#8b5cf6"
        line_opt["series"][0]["areaStyle"]["color"]["colorStops"] = _area_stops("#8b5cf6")
        _set_chart(mem_refs["line"], line_opt)
        _set_html(
            mem_refs["sub"],
            f'<div class="perf-sub">已用 {_fmt_bytes(hw.mem_used)} / '
            f'{_fmt_bytes(hw.mem_total)}'
            + (f' · Swap {_fmt_bytes(hw.swap_used)}/{_fmt_bytes(hw.swap_total)}'
               if hw.swap_total > 0 else "")
            + '</div>',
        )

    # GPU（可能这次没有）
    gpu_refs = state["cards"].get("gpu")
    if gpu_refs and hw.gpus:
        util = float(hw.gpus[0].get("util", 0))
        _set_html(
            gpu_refs["kpi"],
            f'<div class="perf-kpi">{util:.0f}<span class="unit">%</span></div>',
        )
        line_opt = _sparkline_option(state["history"].get("gpu"))
        line_opt["series"][0]["lineStyle"]["color"] = "#ec4899"
        line_opt["series"][0]["areaStyle"]["color"]["colorStops"] = _area_stops("#ec4899")
        _set_chart(gpu_refs["line"], line_opt)


def _area_stops(color: str) -> List[Dict[str, Any]]:
    # 简单从 hex 生成 35% / 2% 透明度版本。为了不引入新依赖，用固定映射。
    table = {
        "#3b82f6": [(0, "rgba(59,130,246,0.35)"), (1, "rgba(59,130,246,0.02)")],
        "#8b5cf6": [(0, "rgba(139,92,246,0.35)"), (1, "rgba(139,92,246,0.02)")],
        "#ec4899": [(0, "rgba(236,72,153,0.35)"), (1, "rgba(236,72,153,0.02)")],
    }
    stops = table.get(color, [(0, "rgba(59,130,246,0.3)"), (1, "rgba(59,130,246,0.02)")])
    return [{"offset": o, "color": c} for o, c in stops]


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


# ---------------------------------------------------------------------------
# Storage Row
# ---------------------------------------------------------------------------

def _render_storage_card(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    st = snap.storage
    with _card(ui, accent="#0ea5e9") as card:
        _card_header(ui, "CHAYUAN_ROOT 占用分布", HEALTH_INFO, accent="#0ea5e9")
        with ui.element("div").classes("perf-body"):
            opt = _storage_pie_option(st.by_subdir)
            chart = ui.echart(opt).classes("perf-chart-md")
            ui.label(f"总体积 {_fmt_bytes(st.total_bytes)}").classes("perf-sub")
        state["cards"]["storage_pie"] = {"card": card, "chart": chart}


def _storage_pie_option(entries: List[Tuple[str, int]]) -> Dict[str, Any]:
    if not entries:
        return {"series": [{"type": "pie", "data": []}]}
    data = [{"name": name, "value": round(size / 1024 / 1024, 2)} for name, size in entries]
    return {
        "color": _PALETTE,
        "tooltip": {"trigger": "item", "formatter": "{b}<br/>{c} MB ({d}%)"},
        "legend": {"bottom": 0, "left": "center", "type": "scroll", "textStyle": {"fontSize": 11}},
        "series": [{
            "type": "pie",
            "radius": ["40%", "70%"],
            "roseType": "area",
            "avoidLabelOverlap": True,
            "itemStyle": {"borderRadius": 6, "borderColor": "#fff", "borderWidth": 2},
            "label": {"show": True, "formatter": "{b}\n{d}%", "fontSize": 10},
            "labelLine": {"show": True},
            "data": data,
        }],
    }


def _render_logs_card(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    st = snap.storage
    health = _health_of_logs(st.logs_bytes)
    with _card(ui, accent="#f97316") as card:
        _card_header(ui, "日志体积", health, accent="#f97316")
        with ui.element("div").classes("perf-body"):
            kpi_el = ui.html(
                f'<div class="perf-kpi">{_escape(_fmt_bytes(st.logs_bytes))}</div>'
                f'<div class="perf-sub">{st.logs_file_count} 个文件 · data/logs</div>'
            )
            ui.label(
                "清理保留当前活跃日志；删除 7 天前历史 .log"
            ).classes("perf-sub").style("margin-top:auto")
            with ui.row().classes("w-full items-center").style("gap:6px"):
                ui.space()
                ui.button(
                    "清理旧日志", icon="auto_delete",
                    on_click=lambda: _confirm_clear_logs(ui, state),
                ).props("color=warning dense size=sm no-caps")
        state["cards"]["logs"] = {"card": card, "kpi": kpi_el}


def _render_db_size_card(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    st = snap.storage
    with _card(ui, accent="#22c55e") as card:
        _card_header(ui, "数据库体积", HEALTH_INFO, accent="#22c55e")
        meta = snap.metrics
        # 只显示 3 条业务统计，保证总高度不超
        rows: List[Tuple[str, int]] = []
        if meta:
            rows = [
                ("会话", meta.overview.conversation_count),
                ("提问", meta.overview.question_count),
                ("KB 文件", meta.overview.kb_file_count),
            ]
        with ui.element("div").classes("perf-body"):
            ui.html(
                f'<div class="perf-kpi">{_escape(_fmt_bytes(st.db_bytes))}'
                f'<span class="unit"> · {_escape(st.db_source or "—")}</span></div>'
                + "".join(
                    f'<div class="perf-sub">{_escape(k)}：{_fmt_count(v)}</div>'
                    for k, v in rows
                )
            )
        state["cards"]["db_size"] = {"card": card}


def _update_storage_cards(state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    st = snap.storage
    sp = state["cards"].get("storage_pie")
    if sp:
        _set_chart(sp["chart"], _storage_pie_option(st.by_subdir))
    logs = state["cards"].get("logs")
    if logs:
        _set_html(
            logs["kpi"],
            f'<div class="perf-kpi">{_escape(_fmt_bytes(st.logs_bytes))}</div>'
            f'<div class="perf-sub">{st.logs_file_count} 个文件 · $CHAYUAN_ROOT/data/logs</div>',
        )


def _confirm_clear_logs(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    logs_dir = Path(snap.storage.root) / "data" / "logs"
    with ui.dialog() as dlg, ui.card():
        ui.label("确定要清理旧日志吗？").classes("text-base font-semibold")
        ui.label(
            f"将删除 {logs_dir} 下 7 天前的文件（保留 .log / .out 当前活跃文件）。"
            "此操作不可撤销。"
        ).classes("text-sm text-grey-8")
        with ui.row().classes("w-full justify-end"):
            ui.button("取消", on_click=dlg.close).props("flat")
            ui.button(
                "确认清理",
                color="warning",
                on_click=lambda: (_do_clear_logs(ui, state, logs_dir), dlg.close()),
            )
    dlg.open()


def _do_clear_logs(ui, state: Dict[str, Any], logs_dir: Path) -> None:
    if not logs_dir.is_dir():
        ui.notify("日志目录不存在，无需清理", color="info")
        return
    cutoff = time.time() - 7 * 24 * 3600
    freed = 0
    deleted = 0
    for dirpath, _dirs, files in os.walk(logs_dir):
        for f in files:
            try:
                p = Path(dirpath) / f
                # 活跃日志保留
                if f.endswith(".log") or f.endswith(".out"):
                    if p.stat().st_mtime > cutoff:
                        continue
                if p.stat().st_mtime > cutoff:
                    continue
                sz = p.stat().st_size
                p.unlink()
                freed += sz
                deleted += 1
            except OSError:
                continue
    ui.notify(
        f"已清理 {deleted} 个文件，释放 {_fmt_bytes(freed)}",
        color="positive", timeout=6000,
    )
    _refresh_all(ui, state)


# ---------------------------------------------------------------------------
# Models Row
# ---------------------------------------------------------------------------

def _render_model_availability_card(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    ms = snap.models
    reachable = sum(1 for p in ms.platforms if p.get("reachable"))
    unreachable = len(ms.platforms) - reachable
    health = (
        HEALTH_CRIT if len(ms.platforms) == 0 or (unreachable and reachable == 0) else
        HEALTH_WARN if unreachable else HEALTH_OK
    )
    with _card(ui, accent="#22c55e") as card:
        _card_header(ui, "模型平台可用度", health, accent="#22c55e")
        with ui.element("div").classes("perf-body"):
            option = {
                "tooltip": {"trigger": "item"},
                "series": [{
                    "type": "pie",
                    "radius": ["60%", "80%"],
                    "avoidLabelOverlap": True,
                    "label": {
                        "show": True, "position": "center",
                        "formatter": f"{{total|{reachable}/{len(ms.platforms)}}}\n{{desc|可达平台}}",
                        "rich": {
                            "total": {"fontSize": 22, "fontWeight": 700, "color": "#0f172a"},
                            "desc": {"fontSize": 10, "color": "#64748b"},
                        },
                    },
                    "data": [
                        {"name": "可达", "value": reachable, "itemStyle": {"color": "#22c55e"}},
                        {"name": "不可达", "value": unreachable, "itemStyle": {"color": "#ef4444"}},
                    ],
                }],
            }
            chart = ui.echart(option).classes("perf-chart-md")
            ui.label(
                f"LLM {ms.total_llm} · Embed {ms.total_embed} · Rerank {ms.total_rerank} · Image {ms.total_image}"
            ).classes("perf-sub")
        state["cards"]["model_availability"] = {"card": card, "chart": chart}


def _render_model_usage_card(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    usage = snap.models.usage
    with _card(ui, accent="#6366f1") as card:
        _card_header(ui, "模型使用频率 Top 5", HEALTH_INFO, accent="#6366f1")
        with ui.element("div").classes("perf-body"):
            if not usage:
                ui.label("meta_data.model 暂无数据；新对话起会自动记录。").classes("perf-sub")
                chart = ui.echart({"series": []}).classes("perf-chart-md")
            else:
                data = list(reversed(usage[:5]))
                option = {
                    "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
                    "grid": {"left": 100, "right": 18, "top": 4, "bottom": 18},
                    "xAxis": {"type": "value", "axisLabel": {"fontSize": 10}},
                    "yAxis": {"type": "category", "data": [k for k, _v in data],
                              "axisLabel": {"fontSize": 10}},
                    "series": [{
                        "type": "bar",
                        "data": [v for _k, v in data],
                        "label": {"show": True, "position": "right", "fontSize": 10},
                        "itemStyle": {"color": "#6366f1", "borderRadius": [0, 4, 4, 0]},
                    }],
                }
                chart = ui.echart(option).classes("perf-chart-md")
        state["cards"]["model_usage"] = {"card": card, "chart": chart}


def _render_model_types_card(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    ms = snap.models
    with _card(ui, accent="#f59e0b") as card:
        _card_header(ui, "模型类型分布", HEALTH_INFO, accent="#f59e0b")
        with ui.element("div").classes("perf-body"):
            option = {
                "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
                "legend": {"bottom": 0, "textStyle": {"fontSize": 10}, "itemHeight": 8, "itemWidth": 10},
                "grid": {"left": 66, "right": 10, "top": 4, "bottom": 26},
                "xAxis": {"type": "value", "axisLabel": {"fontSize": 10}},
                "yAxis": {"type": "category",
                          "data": [p["name"] for p in ms.platforms] or ["（无）"],
                          "axisLabel": {"fontSize": 10}},
                "series": [
                    {"name": "LLM", "type": "bar", "stack": "t",
                     "data": [p["llm_count"] for p in ms.platforms] or [0],
                     "itemStyle": {"color": "#3b82f6"}},
                    {"name": "Embed", "type": "bar", "stack": "t",
                     "data": [p["embed_count"] for p in ms.platforms] or [0],
                     "itemStyle": {"color": "#22c55e"}},
                    {"name": "Rerank", "type": "bar", "stack": "t",
                     "data": [p["rerank_count"] for p in ms.platforms] or [0],
                     "itemStyle": {"color": "#f59e0b"}},
                    {"name": "Image", "type": "bar", "stack": "t",
                     "data": [p["image_count"] for p in ms.platforms] or [0],
                     "itemStyle": {"color": "#ec4899"}},
                ],
            }
            chart = ui.echart(option).classes("perf-chart-md")
        state["cards"]["model_types"] = {"card": card, "chart": chart}


def _update_model_cards(state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    ma = state["cards"].get("model_availability")
    if ma:
        ms = snap.models
        reachable = sum(1 for p in ms.platforms if p.get("reachable"))
        unreachable = len(ms.platforms) - reachable
        _set_chart(ma["chart"], {
            "tooltip": {"trigger": "item"},
            "legend": {"bottom": 0, "left": "center"},
            "series": [{
                "type": "pie", "radius": ["58%", "78%"],
                "label": {"show": True, "position": "center",
                          "formatter": f"{{total|{reachable}/{len(ms.platforms)}}}\n{{desc|可达}}",
                          "rich": {"total": {"fontSize": 22, "fontWeight": 700},
                                   "desc": {"fontSize": 11, "color": "#64748b"}}},
                "data": [
                    {"name": "可达", "value": reachable, "itemStyle": {"color": "#22c55e"}},
                    {"name": "不可达", "value": unreachable, "itemStyle": {"color": "#ef4444"}},
                ],
            }],
        })


# ---------------------------------------------------------------------------
# Traffic Row
# ---------------------------------------------------------------------------

def _render_traffic_heatmap_card(ui, state: Dict[str, Any]) -> None:
    """流量热力图（7 × 24）—— 数据量密，走 ``.perf-card-wide`` 横跨 2 列。"""
    snap: PerfSnapshot = state["snapshot"]
    metrics = snap.metrics
    card = ui.element("div").classes("perf-card perf-card-wide")
    card.style(f"--perf-accent:#3b82f6")
    with card:
        _card_header(ui, "流量热力图（近 90 天 · 星期 × 小时）", HEALTH_INFO, accent="#3b82f6")
        with ui.element("div").classes("perf-body"):
            if metrics is None or metrics.heatmap_max == 0:
                ui.label("最近 90 天无提问记录。").classes("perf-sub")
                chart = ui.echart({"series": []}).classes("perf-chart-wide")
            else:
                weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                hours = [f"{h}" for h in range(24)]
                points = []
                for d in range(7):
                    for h in range(24):
                        v = metrics.heatmap[d][h]
                        points.append([h, d, v])
                vmax = max(metrics.heatmap_max, 1)
                option = {
                    "tooltip": {"position": "top"},
                    "grid": {"left": 40, "right": 10, "top": 4, "bottom": 30},
                    "xAxis": {"type": "category", "data": hours,
                              "axisLabel": {"fontSize": 9},
                              "splitArea": {"show": True}},
                    "yAxis": {"type": "category", "data": weekdays,
                              "axisLabel": {"fontSize": 10},
                              "splitArea": {"show": True}},
                    "visualMap": {
                        "min": 0, "max": vmax, "calculable": True,
                        "orient": "horizontal", "left": "center", "bottom": 0,
                        "itemHeight": 10, "itemWidth": 100,
                        "textStyle": {"fontSize": 10},
                        "inRange": {"color": ["#eff6ff", "#60a5fa", "#1d4ed8"]},
                    },
                    "series": [{
                        "type": "heatmap", "data": points,
                        "emphasis": {"itemStyle": {"shadowBlur": 10}},
                    }],
                }
                chart = ui.echart(option).classes("perf-chart-wide")
        state["cards"]["traffic_heatmap"] = {"card": card, "chart": chart}


def _render_chat_type_card(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    metrics = snap.metrics
    with _card(ui, accent="#06b6d4") as card:
        _card_header(ui, "对话类型分布", HEALTH_INFO, accent="#06b6d4")
        with ui.element("div").classes("perf-body"):
            data = []
            if metrics is not None:
                data = [{"name": b.label, "value": b.value} for b in metrics.chat_types]
            if not data:
                ui.label("暂无对话数据。").classes("perf-sub")
                chart = ui.echart({"series": []}).classes("perf-chart-md")
            else:
                option = {
                    "color": _PALETTE,
                    "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
                    "legend": {"bottom": 0, "textStyle": {"fontSize": 10},
                               "itemHeight": 8, "itemWidth": 10},
                    "series": [{
                        "type": "pie",
                        "radius": ["45%", "70%"],
                        "center": ["50%", "45%"],
                        "itemStyle": {"borderRadius": 5, "borderColor": "#fff", "borderWidth": 2},
                        "label": {"show": True, "formatter": "{d}%", "fontSize": 10},
                        "data": data,
                    }],
                }
                chart = ui.echart(option).classes("perf-chart-md")
        state["cards"]["chat_type"] = {"card": card, "chart": chart}


def _render_feedback_radar_card(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    metrics = snap.metrics
    with _card(ui, accent="#ec4899") as card:
        _card_header(ui, "反馈分数雷达", HEALTH_INFO, accent="#ec4899")
        with ui.element("div").classes("perf-body"):
            data = []
            max_v = 1
            if metrics is not None:
                data = [b.value for b in metrics.feedback_hist]
                max_v = max(data + [1])
            if not data or all(v == 0 for v in data):
                ui.label("暂无反馈数据。").classes("perf-sub")
                chart = ui.echart({"series": []}).classes("perf-chart-md")
            else:
                indicators = [{"name": f"{lo}-{hi}", "max": max_v} for lo, hi in
                              [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]]
                option = {
                    "radar": {"indicator": indicators,
                              "radius": "60%",
                              "axisName": {"color": "#475569", "fontSize": 10},
                              "splitArea": {"areaStyle": {"color": ["rgba(236,72,153,0.05)", "rgba(236,72,153,0.02)"]}}},
                    "series": [{
                        "type": "radar",
                        "areaStyle": {"color": "rgba(236,72,153,0.35)"},
                        "lineStyle": {"color": "#ec4899"},
                        "itemStyle": {"color": "#ec4899"},
                        "data": [{"value": data, "name": "反馈"}],
                    }],
                }
                chart = ui.echart(option).classes("perf-chart-md")
        state["cards"]["feedback_radar"] = {"card": card, "chart": chart}


def _update_traffic_cards(state: Dict[str, Any]) -> None:
    # 重渲染开销相对高且监控本身 5s 缓存；这里简化为全量重建图表选项
    pass


# ---------------------------------------------------------------------------
# KB Row
# ---------------------------------------------------------------------------

def _render_kb_sankey_card(ui, state: Dict[str, Any]) -> None:
    """桑基图数据密度大，用 ``.perf-card-wide``（横跨 2 列 = 616px）。"""
    snap: PerfSnapshot = state["snapshot"]
    metrics = snap.metrics
    card = ui.element("div").classes("perf-card perf-card-wide")
    card.style("--perf-accent:#8b5cf6")
    with card:
        _card_header(ui, "知识库 → 文件类型 桑基图", HEALTH_INFO, accent="#8b5cf6")
        with ui.element("div").classes("perf-body"):
            if metrics is None or not metrics.top_kbs:
                ui.label("暂无知识库数据。").classes("perf-sub")
                chart = ui.echart({"series": []}).classes("perf-chart-wide")
            else:
                nodes: set = set()
                links = []
                exts = metrics.file_exts or []
                ext_total = sum(b.value for b in exts) or 1
                for kb in metrics.top_kbs:
                    nodes.add(kb.label)
                    for ext in exts:
                        ext_name = ext.label or "(无)"
                        nodes.add(ext_name)
                        val = round(kb.value * (ext.value / ext_total), 2)
                        if val > 0:
                            links.append({"source": kb.label, "target": ext_name, "value": val})
                option = {
                    "tooltip": {"trigger": "item", "triggerOn": "mousemove"},
                    "series": [{
                        "type": "sankey",
                        "left": 10, "right": 10, "top": 4, "bottom": 4,
                        "emphasis": {"focus": "adjacency"},
                        "data": [{"name": n} for n in sorted(nodes)],
                        "links": links,
                        "lineStyle": {"color": "gradient", "curveness": 0.5},
                        "label": {"fontSize": 10},
                    }],
                }
                chart = ui.echart(option).classes("perf-chart-wide")
        state["cards"]["kb_sankey"] = {"card": card, "chart": chart}


def _render_top_kbs_card(ui, state: Dict[str, Any]) -> None:
    snap: PerfSnapshot = state["snapshot"]
    metrics = snap.metrics
    with _card(ui, accent="#22c55e") as card:
        _card_header(ui, "知识库文件数 Top 5", HEALTH_INFO, accent="#22c55e")
        with ui.element("div").classes("perf-body"):
            if metrics is None or not metrics.top_kbs:
                ui.label("暂无知识库。").classes("perf-sub")
                chart = ui.echart({"series": []}).classes("perf-chart-md")
            else:
                data = list(reversed(metrics.top_kbs[:5]))
                option = {
                    "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
                    "grid": {"left": 90, "right": 24, "top": 4, "bottom": 18},
                    "xAxis": {"type": "value", "axisLabel": {"fontSize": 10}},
                    "yAxis": {"type": "category", "data": [b.label for b in data],
                              "axisLabel": {"fontSize": 10}},
                    "series": [{
                        "type": "bar",
                        "data": [b.value for b in data],
                        "label": {"show": True, "position": "right", "fontSize": 10},
                        "itemStyle": {"color": "#22c55e", "borderRadius": [0, 4, 4, 0]},
                    }],
                }
                chart = ui.echart(option).classes("perf-chart-md")
        state["cards"]["top_kbs"] = {"card": card, "chart": chart}


def _render_file_ext_card(ui, state: Dict[str, Any]) -> None:
    """文件扩展名分布 —— 散点图：x 排名、y 文件数、气泡大小 = 数量。"""
    snap: PerfSnapshot = state["snapshot"]
    metrics = snap.metrics
    with _card(ui, accent="#f59e0b") as card:
        _card_header(ui, "文件扩展名分布", HEALTH_INFO, accent="#f59e0b")
        with ui.element("div").classes("perf-body"):
            if metrics is None or not metrics.file_exts:
                ui.label("暂无知识库文件。").classes("perf-sub")
                chart = ui.echart({"series": []}).classes("perf-chart-md")
            else:
                exts = metrics.file_exts[:8]
                vmax = max(b.value for b in exts)
                points = [[i + 1, b.value, b.value, b.label] for i, b in enumerate(exts)]
                option = {
                    "tooltip": {"trigger": "item",
                                "formatter": "function(p){return p.data[3]+': '+p.data[1];}"},
                    "grid": {"left": 36, "right": 14, "top": 6, "bottom": 22},
                    "xAxis": {"type": "value", "minInterval": 1, "axisLabel": {"fontSize": 9}},
                    "yAxis": {"type": "value", "axisLabel": {"fontSize": 9}},
                    "series": [{
                        "type": "scatter",
                        "data": points,
                        "symbolSize": f"function(v){{return Math.sqrt(v[1]/{vmax})*30+6;}}",
                        "itemStyle": {"color": "#f59e0b",
                                      "shadowBlur": 8,
                                      "shadowColor": "rgba(245,158,11,0.4)"},
                        "label": {"show": True, "position": "top",
                                  "formatter": "function(p){return p.data[3];}",
                                  "fontSize": 9},
                    }],
                }
                chart = ui.echart(option).classes("perf-chart-md")
        state["cards"]["file_ext"] = {"card": card, "chart": chart}


def _update_kb_cards(state: Dict[str, Any]) -> None:
    # 保持简单：KB 类数据变化慢，下一次全量刷新重建即可
    pass


# ---------------------------------------------------------------------------
# AI 分析卡
# ---------------------------------------------------------------------------

def _render_ai_card(ui, state: Dict[str, Any]) -> None:
    """霓虹渐变大卡：输入 prompt → 调 LLM → 流式渲染结论。

    关键修正：handler 是 ``async def``，用 ``asyncio.to_thread`` 跑阻塞的
    ``openai.OpenAI(...).chat.completions.create`` —— 否则 NiceGUI 主事件
    循环会被阻塞 10-30s，期间所有 dialog 的关闭按钮 / Esc / 背景点击都无响应。
    """
    with ui.element("div").classes("perf-ai-card"):
        with ui.row().classes("items-start w-full no-wrap").style("gap:12px;position:relative;z-index:1"):
            ui.html('<i class="material-icons" style="font-size:32px;color:#93c5fd">auto_awesome</i>')
            with ui.column().classes("q-gutter-none").style("flex:1;min-width:0"):
                ui.html('<div class="perf-ai-title">AI 硬件容量分析</div>')
                ui.html(
                    '<div class="perf-ai-sub">把当前硬件 / 存储 / 模型 / 流量快照喂给已配置的 '
                    '对话模型；大模型会给出并发承载估算、瓶颈诊断、改进建议。</div>'
                )
            ai_btn = ui.button(
                "开始分析", icon="rocket_launch",
            ).props("color=white text-color=primary dense no-caps")
            ai_btn.tooltip(
                "点一次调用 LLM；过程需 5-30 秒（视模型而定）"
            )
        body = ui.html(
            '<div class="perf-ai-body"><em>点右上角的「开始分析」按钮生成报告…</em></div>'
        )
        state["ai"]["body"] = body
        state["ai"]["card_btn"] = ai_btn
        state["ai"]["history"] = []

        async def _on_start_click() -> None:
            await _run_ai_analysis_async(ui, state)

        ai_btn.on("click", _on_start_click)


async def _run_ai_analysis_async(ui, state: Dict[str, Any]) -> None:
    """异步版：把 openai HTTP 调用搬进 ``asyncio.to_thread``，保证主事件循环
    在 LLM 运行期间仍然响应 UI 事件（Esc / 关闭 / 滚动）。
    """
    import asyncio

    body = state.get("ai", {}).get("body")
    if body is None:
        ui.notify("AI 分析卡未就绪", color="warning")
        return

    # 两个入口按钮都在 state.ai 里存引用；一个存在就尝试 disable
    btns = [state["ai"].get("header_btn"), state["ai"].get("card_btn")]

    def _set_busy(busy: bool) -> None:
        for b in btns:
            if b is None:
                continue
            try:
                if busy:
                    b.props("loading disable")
                else:
                    b.props(remove="loading disable")
            except Exception:
                pass

    snap: PerfSnapshot = state["snapshot"]
    prompt = _build_ai_prompt(snap)

    _set_html(
        body,
        '<div class="perf-ai-body"><em>正在请求大模型，请稍候（页面依然可以滚动 / 刷新）…</em></div>',
    )
    ui.notify("已发起 AI 分析请求", color="info")
    _set_busy(True)

    try:
        model_info = _pick_chat_model()
    except Exception as e:  # noqa: BLE001
        _set_html(body, f'<div class="perf-ai-body">调用失败：{_escape(str(e))}</div>')
        ui.notify(f"调用失败：{e}", color="negative")
        _set_busy(False)
        return
    if model_info is None:
        _set_html(
            body,
            '<div class="perf-ai-body">未找到任何已配置的 LLM；'
            '请先去「模型配置」页添加至少一个 platform + 至少一个 llm_model。</div>',
        )
        ui.notify("未找到可用 LLM", color="warning")
        _set_busy(False)
        return

    model_name, api_base, api_key = model_info
    try:
        answer = await asyncio.to_thread(
            _call_llm, model_name, api_base, api_key, prompt,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("LLM call failed")
        _set_html(body, f'<div class="perf-ai-body">LLM 调用异常：{_escape(str(e))}</div>')
        ui.notify(f"LLM 调用异常：{e}", color="negative")
        _set_busy(False)
        return

    html = _simple_markdown(answer)
    _set_html(
        body,
        f'<div class="perf-ai-body">'
        f'<div style="font-size:11px;color:#93c5fd;margin-bottom:6px">模型：{_escape(model_name)}  ·  '
        f'完成于 {time.strftime("%H:%M:%S")}</div>'
        f'{html}</div>',
    )
    ui.notify("AI 分析完成", color="positive")
    _set_busy(False)


def _build_ai_prompt(snap: PerfSnapshot) -> str:
    hw = snap.hardware
    st = snap.storage
    ms = snap.models
    me = snap.metrics

    disks_txt = "\n".join(
        f"  - {d['mount']}  总 {_fmt_bytes(d['total'])}，已用 {_fmt_bytes(d['used'])} "
        f"({d['percent']:.1f}%)，可用 {_fmt_bytes(d['free'])}"
        for d in hw.disks
    ) or "  （未检测到磁盘）"
    gpus_txt = "\n".join(
        f"  - {g['name']}  util={g.get('util', 0):.0f}%  显存 "
        f"{_fmt_bytes(g.get('mem_used', 0))}/{_fmt_bytes(g.get('mem_total', 0))}  温度 {g.get('temp', 0):.0f}°C"
        for g in hw.gpus
    ) or "  （未检测到 GPU）"
    platforms_txt = "\n".join(
        f"  - {p['name']} ({p['type']})  可达={p['reachable']}  "
        f"LLM={p['llm_count']} Embed={p['embed_count']} Rerank={p['rerank_count']}"
        for p in ms.platforms
    ) or "  （未配置任何模型平台）"
    usage_txt = ", ".join(f"{k}({v})" for k, v in ms.usage[:5]) or "（无使用数据）"
    metrics_txt = ""
    if me is not None:
        metrics_txt = (
            f"历史数据：对话 {me.overview.conversation_count}，提问 {me.overview.question_count}，"
            f"回答 {me.overview.answer_count}，KB 文件 {me.overview.kb_file_count}（{_fmt_bytes(me.overview.kb_total_bytes)}），"
            f"反馈 {me.overview.feedback_count} 条（均分 {me.overview.feedback_avg:.1f}）\n"
        )

    return f"""你是一名资深的服务端性能工程师。下面是某台部署了 chayuan（本地 RAG 服务）机器的
**实时快照**。请基于这些信息给出分析，要求：

1. 先用一句话总结整体健康；
2. 估算当前硬件能支撑多少同时在线用户（粗略档位即可，给出理由）；
3. 列出 Top 3 硬件瓶颈 / 配置短板（每项带诊断依据）；
4. 分「紧急 / 可计划 / 长期」三档给出改进措施；
5. 最后用 1 行指出："如果要支撑 5000 人同时在线，还缺什么"。
请用 Markdown 输出；每节用加粗小标题。不要重复堆列原始数据。

## 快照

- 综合健康评分：{snap.health_score}/100
- 配置体检：严重 {snap.health_counts.get('critical', 0)} / 警告 {snap.health_counts.get('warning', 0)} / 建议 {snap.health_counts.get('info', 0)} / 达标 {snap.health_counts.get('ok', 0)}
- 当前承载估算（基于规则）：{snap.est_concurrent or '—'}

### 硬件

- CPU：{hw.cpu_percent:.1f}%（{hw.cpu_count_logical} 逻辑核 / {hw.cpu_count_physical} 物理核），Load {hw.load_1:.2f}/{hw.load_5:.2f}/{hw.load_15:.2f}
- 内存：{hw.mem_percent:.1f}%（已用 {_fmt_bytes(hw.mem_used)} / 总 {_fmt_bytes(hw.mem_total)}，Swap {_fmt_bytes(hw.swap_used)}/{_fmt_bytes(hw.swap_total)}）
- 磁盘：
{disks_txt}
- GPU：
{gpus_txt}

### 存储

- 数据目录 {st.root}  总体积 {_fmt_bytes(st.total_bytes)}
- Top 子目录：{', '.join(f'{n}({_fmt_bytes(s)})' for n, s in st.by_subdir[:5]) or '（空）'}
- 日志：{_fmt_bytes(st.logs_bytes)}，{st.logs_file_count} 文件
- 数据库：{st.db_source or '—'}，体积 {_fmt_bytes(st.db_bytes)}

### 模型

- LLM={ms.total_llm}  Embed={ms.total_embed}  Rerank={ms.total_rerank}  Image={ms.total_image}
- 平台：
{platforms_txt}
- Top 使用：{usage_txt}

### 业务指标
{metrics_txt}"""


def _pick_chat_model() -> Optional[Tuple[str, str, str]]:
    """返回 (model_name, api_base, api_key)。

    策略：优先选 DEFAULT_LLM_MODEL 所在的 platform；否则选第一个 llm_models 非空的。
    """
    from chayuan.settings import Settings

    ms = Settings.model_settings
    platforms = list(getattr(ms, "MODEL_PLATFORMS", []) or [])
    default = str(getattr(ms, "DEFAULT_LLM_MODEL", "") or "")

    def _get(p: Any, k: str, default: Any = None) -> Any:
        if isinstance(p, dict):
            return p.get(k, default)
        return getattr(p, k, default)

    # 先扫一遍找 default 所在的 platform
    if default:
        for p in platforms:
            llm_models = list(_get(p, "llm_models", []) or [])
            if default in llm_models:
                return default, str(_get(p, "api_base_url", "")), str(_get(p, "api_key", "EMPTY") or "EMPTY")

    # 否则取第一个有 llm_models 的
    for p in platforms:
        llm_models = list(_get(p, "llm_models", []) or [])
        if llm_models:
            return llm_models[0], str(_get(p, "api_base_url", "")), str(_get(p, "api_key", "EMPTY") or "EMPTY")

    return None


def _call_llm(model: str, api_base: str, api_key: str, prompt: str) -> str:
    """OpenAI 兼容接口调用，同步阻塞。支持 openai / ollama (v1) / vllm / xinference。"""
    try:
        import openai  # type: ignore
    except ImportError:
        return "未安装 openai 客户端包：pip install 'openai>=1.0'"

    if not api_base:
        return "模型平台未配置 api_base_url"
    # openai 客户端要求 /v1 结尾；自动补
    base = api_base.rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"

    try:
        client = openai.OpenAI(api_key=api_key or "EMPTY", base_url=base, timeout=120.0)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一名服务端性能分析专家，中文回复。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=1200,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.exception("LLM call failed")
        return f"LLM 调用失败：{type(e).__name__}: {e}"


def _simple_markdown(text: str) -> str:
    """最小 markdown → HTML 转换。专门为 AI 输出设计（标题 / 粗体 / 代码 / 列表）。

    完整 markdown 交给 :class:`ui.markdown` 会更安全，但它默认白底不适合本卡的
    霓虹渐变底，这里手搓一版保留样式一致性。
    """
    import html as _html
    import re
    lines = text.split("\n")
    out: List[str] = []
    in_code = False
    for ln in lines:
        if ln.strip().startswith("```"):
            if in_code:
                out.append("</pre>")
                in_code = False
            else:
                out.append("<pre style='background:rgba(15,23,42,0.6);padding:8px;border-radius:6px;overflow-x:auto'>")
                in_code = True
            continue
        if in_code:
            out.append(_html.escape(ln))
            continue
        s = _html.escape(ln)
        # 粗体 **x**
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        # 斜体 *x*
        s = re.sub(r"(?<![\*\w])\*([^*\s].*?)\*(?!\w)", r"<em>\1</em>", s)
        # 行内代码 `x`
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        # 标题 #, ##, ###
        m = re.match(r"^(#{1,3})\s+(.*)$", s)
        if m:
            level = len(m.group(1))
            s = f"<h{level}>{m.group(2)}</h{level}>"
        # 列表
        elif re.match(r"^[-*]\s+", ln):
            s = "• " + s.lstrip("-* ").lstrip()
        out.append(s + "<br/>")
    return "".join(out)


__all__ = [
    "render_performance_page",
    "build_snapshot",
    "collect_hardware",
    "collect_storage",
    "collect_models",
    "PerfSnapshot",
]
