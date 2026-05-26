"""备用源池(Mirror Pool)+ 实时探活。

设计意图
========
用户痛点:某些第三方镜像源(docker.1ms.run / mirror.ghproxy.com 等)
有时同步落后或挂掉,直接用就报错。本模块提供:

1. **每类资源(docker / github / pypi / huggingface / conda)有多个候选源**
2. **下载前 HEAD 探活** — 第一个返回 2xx/3xx 的就是"活源"
3. **5 min 内同 pool 的探活结果缓存** — 避免每次都探
4. **并发探活** — 同时发 N 个 HEAD,取最快响应

调用方:
    >>> from chayuan.server.config_panel.mirror_pool import pick_alive
    >>> entry = pick_alive("docker_hub")
    >>> if entry:
    >>>     image_url = f"{entry.url}/michaelf34/infinity:latest"

为什么不每次都探
=================
* HEAD 请求每次 1-3s,用户体验差
* 5 分钟 TTL 足够覆盖一次安装会话(典型 < 5min)
* 缓存失效后再探,不会让"挂掉的源"卡住后续操作

设计权衡:同源同步落后 vs 完全挂掉
=====================================
* 同步落后(layer 缺):HEAD 通过(根 manifest 在),但 docker pull 部分层失败
  → mirror_pool 无法探出,要靠 install_diagnose 的 docker_layer_missing 规则换源
* 完全挂掉:HEAD 失败,mirror_pool 自动跳过该源
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("chayuan.mirror_pool")


@dataclass(frozen=True)
class MirrorEntry:
    """一个备用源条目。"""
    name: str                              # UI 显示名,如 "DaoCloud"
    url: str                               # 主用 URL,如 "docker.m.daocloud.io"
    note: str = ""                         # 备注,如 "国内常用"
    ping_url: str = ""                     # HEAD 探活的完整 URL;空则用默认探活路径
    has_full_mirror: bool = True           # True=完整镜像;False=只部分(如 ghcr 的某些)


# ============================================================================
# 候选池(按优先级排序;探活时**并发**所有,取最快响应的)
# ============================================================================

# Docker Hub 镜像源(含官方 + 国内多个)
# 顺序:历史可靠性高的在前 — 1ms.run 因用户多次反馈拉一半 EOF/层缺,降到中段
_POOL_DOCKER_HUB: List[MirrorEntry] = [
    MirrorEntry(name="DaoCloud", url="docker.m.daocloud.io",
                ping_url="https://docker.m.daocloud.io/v2/",
                note="国内首选,同步及时,完整度高"),
    MirrorEntry(name="Docker Hub 官方", url="registry-1.docker.io",
                ping_url="https://registry-1.docker.io/v2/",
                note="海外快;国内可能慢但完整度最高"),
    MirrorEntry(name="cnb.cool", url="docker.cnb.cool",
                ping_url="https://docker.cnb.cool/v2/",
                note="国内备选;同步较及时"),
    MirrorEntry(name="AtomGit Hub", url="hub.atomgit.com",
                ping_url="https://hub.atomgit.com/v2/",
                note="开源中国托管"),
    MirrorEntry(name="1ms.run", url="docker.1ms.run",
                ping_url="https://docker.1ms.run/v2/",
                note="国内主流但近期反馈拉到一半 EOF / layer 缺,排后"),
    MirrorEntry(name="玄垣镜像", url="docker.xuanyuan.me",
                ping_url="https://docker.xuanyuan.me/v2/",
                note="国内备选;同步偶有延迟"),
    MirrorEntry(name="阿里云 ACR", url="registry.cn-hangzhou.aliyuncs.com",
                ping_url="https://registry.cn-hangzhou.aliyuncs.com/v2/",
                note="阿里云 ACR;路径需带 namespace,部分镜像无法直拉"),
]

# GitHub 代理(用于 git clone / release 下载)
_POOL_GITHUB_PROXY: List[MirrorEntry] = [
    MirrorEntry(name="GitHub 官方", url="github.com",
                ping_url="https://github.com",
                note="海外快;国内不稳"),
    MirrorEntry(name="ghproxy.com", url="mirror.ghproxy.com",
                ping_url="https://mirror.ghproxy.com",
                note="国内主流 GitHub 代理"),
    MirrorEntry(name="gh-proxy.com", url="gh-proxy.com",
                ping_url="https://gh-proxy.com",
                note="国内备选"),
    MirrorEntry(name="ghproxy.cnpmjs.org", url="ghproxy.cnpmjs.org",
                ping_url="https://ghproxy.cnpmjs.org",
                note="国内备选"),
    MirrorEntry(name="ghproxy.net", url="ghproxy.net",
                ping_url="https://ghproxy.net",
                note="国内备选"),
    MirrorEntry(name="hub.gitmirror.com", url="hub.gitmirror.com",
                ping_url="https://hub.gitmirror.com",
                note="GitMirror"),
    MirrorEntry(name="cf.ghproxy.com", url="cf.ghproxy.com",
                ping_url="https://cf.ghproxy.com",
                note="Cloudflare 加速"),
]

# PyPI 镜像
_POOL_PYPI: List[MirrorEntry] = [
    MirrorEntry(name="清华 TUNA", url="https://pypi.tuna.tsinghua.edu.cn/simple",
                ping_url="https://pypi.tuna.tsinghua.edu.cn/simple/",
                note="国内首选"),
    MirrorEntry(name="阿里云", url="https://mirrors.aliyun.com/pypi/simple",
                ping_url="https://mirrors.aliyun.com/pypi/simple/",
                note="国内备选"),
    MirrorEntry(name="腾讯云", url="https://mirrors.cloud.tencent.com/pypi/simple",
                ping_url="https://mirrors.cloud.tencent.com/pypi/simple/",
                note="国内备选"),
    MirrorEntry(name="中科大 USTC", url="https://pypi.mirrors.ustc.edu.cn/simple",
                ping_url="https://pypi.mirrors.ustc.edu.cn/simple/",
                note="教育网"),
    MirrorEntry(name="网易", url="https://mirrors.163.com/pypi/simple",
                ping_url="https://mirrors.163.com/pypi/simple/",
                note="国内备选"),
    MirrorEntry(name="PyPI 官方", url="https://pypi.org/simple",
                ping_url="https://pypi.org/simple/",
                note="海外快;国内慢"),
]

# HuggingFace 镜像(模型权重下载)
_POOL_HUGGINGFACE: List[MirrorEntry] = [
    MirrorEntry(name="hf-mirror.com", url="https://hf-mirror.com",
                ping_url="https://hf-mirror.com",
                note="国内首选 HF 镜像"),
    MirrorEntry(name="HuggingFace 官方", url="https://huggingface.co",
                ping_url="https://huggingface.co",
                note="海外快;国内常被墙"),
    MirrorEntry(name="ModelScope", url="https://modelscope.cn",
                ping_url="https://modelscope.cn",
                note="阿里 ModelScope;部分仓库需 ID 转换",
                has_full_mirror=False),
]

# Conda / Anaconda 镜像
_POOL_CONDA: List[MirrorEntry] = [
    MirrorEntry(name="清华 TUNA", url="https://mirrors.tuna.tsinghua.edu.cn/anaconda",
                ping_url="https://mirrors.tuna.tsinghua.edu.cn/anaconda/",
                note="国内首选"),
    MirrorEntry(name="阿里云", url="https://mirrors.aliyun.com/anaconda",
                ping_url="https://mirrors.aliyun.com/anaconda/",
                note="国内备选"),
    MirrorEntry(name="北外 BFSU", url="https://mirrors.bfsu.edu.cn/anaconda",
                ping_url="https://mirrors.bfsu.edu.cn/anaconda/",
                note="教育网"),
    MirrorEntry(name="Anaconda 官方", url="https://repo.anaconda.com",
                ping_url="https://repo.anaconda.com",
                note="海外"),
]

POOLS: Dict[str, List[MirrorEntry]] = {
    "docker_hub":    _POOL_DOCKER_HUB,
    "github_proxy":  _POOL_GITHUB_PROXY,
    "pypi":          _POOL_PYPI,
    "huggingface":   _POOL_HUGGINGFACE,
    "conda":         _POOL_CONDA,
}


# ============================================================================
# 探活实现 — 缓存 + 并发
# ============================================================================

@dataclass
class _CacheEntry:
    """缓存:某 pool 探活后,记录所有源的 alive 状态 + 时间戳。"""
    timestamp: float
    alive_entries: List[MirrorEntry] = field(default_factory=list)
    dead_names: List[str] = field(default_factory=list)


_CACHE: Dict[str, _CacheEntry] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 300.0  # 5 分钟


def _ping(entry: MirrorEntry, *, timeout: float = 3.0) -> bool:
    """对单个 mirror 发 HEAD/GET 请求探活。

    优先 HEAD;某些源(如 Docker registry v2)不支持 HEAD,fallback GET。
    2xx / 3xx / 401 (registry 鉴权)都视为"活"(401 表示服务在,只是要鉴权)。
    """
    try:
        import httpx
    except ImportError:
        # 主依赖缺失,无法探活 — 直接认为活(避免阻塞)
        return True

    url = entry.ping_url or f"https://{entry.url}"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, verify=True) as c:
            try:
                r = c.head(url)
                if 200 <= r.status_code < 400 or r.status_code == 401:
                    return True
            except Exception:  # noqa: BLE001
                pass
            # HEAD 不行,试 GET(取头部即可)
            r = c.get(url)
            return 200 <= r.status_code < 400 or r.status_code == 401
    except Exception as e:  # noqa: BLE001
        logger.debug("[mirror_pool] ping %s failed: %r", url, e)
        return False


def probe_pool(
    pool_name: str,
    *,
    timeout: float = 3.0,
    max_workers: int = 8,
    force: bool = False,
) -> List[MirrorEntry]:
    """并发探活某 pool,返回所有"活"的 mirror,按响应顺序。

    - 缓存 5 min,同 pool 第二次调直接返回上次结果
    - force=True 跳过缓存
    """
    pool = POOLS.get(pool_name)
    if not pool:
        return []

    if not force:
        with _CACHE_LOCK:
            cached = _CACHE.get(pool_name)
        if cached and (time.time() - cached.timestamp) < _CACHE_TTL:
            return list(cached.alive_entries)

    alive: List[MirrorEntry] = []
    dead: List[str] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(pool))) as ex:
        futures = {ex.submit(_ping, e, timeout=timeout): e for e in pool}
        for fut in as_completed(futures):
            e = futures[fut]
            try:
                if fut.result():
                    alive.append(e)
                else:
                    dead.append(e.name)
            except Exception as ex_:  # noqa: BLE001
                logger.debug("[mirror_pool] ping %s exception: %r", e.name, ex_)
                dead.append(e.name)

    # 按 pool 原始顺序排序(优先级)
    order = {e.name: i for i, e in enumerate(pool)}
    alive.sort(key=lambda e: order.get(e.name, 999))

    with _CACHE_LOCK:
        _CACHE[pool_name] = _CacheEntry(
            timestamp=time.time(), alive_entries=list(alive), dead_names=dead,
        )
    logger.info(
        "[mirror_pool] %s 探活: 活 %d/%d (alive=%s, dead=%s)",
        pool_name, len(alive), len(pool),
        [e.name for e in alive], dead,
    )
    return alive


def pick_alive(
    pool_name: str,
    *,
    timeout: float = 3.0,
    force: bool = False,
) -> Optional[MirrorEntry]:
    """返回**第一个活**的 mirror(按 pool 顺序),没活的返 None。

    适用:下载前快速选源 — 一个就够。
    """
    alive = probe_pool(pool_name, timeout=timeout, force=force)
    return alive[0] if alive else None


def invalidate_cache(pool_name: Optional[str] = None) -> None:
    """清缓存。``pool_name=None`` 时清全部。"""
    with _CACHE_LOCK:
        if pool_name is None:
            _CACHE.clear()
        else:
            _CACHE.pop(pool_name, None)


def list_pools() -> List[str]:
    """所有可用的 pool 名。"""
    return list(POOLS.keys())


__all__ = [
    "MirrorEntry",
    "POOLS",
    "probe_pool",
    "pick_alive",
    "invalidate_cache",
    "list_pools",
]
