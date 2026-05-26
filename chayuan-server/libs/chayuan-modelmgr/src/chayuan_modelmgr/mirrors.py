"""Mirror selection: hf-mirror.com primary, modelscope / hf official as fallbacks.

Mirror routes
=============

* ``hf-mirror.com`` — 国内 HF 镜像;与 HF 官方 API 协议兼容(``/api/models/<repo>``、
  ``/<repo>/resolve/<rev>/<file>``)。**默认首选**。
* ``huggingface.co`` — HF 官方;同协议;境外 / 公司内网走 VPN 时再用。
* ``hf.co`` — HF 短域名别名;某些挂代理只放行 hf.co 时用。
* ``modelscope.cn`` — 阿里达摩院 ModelScope;REST 协议**不同**于 HF
  (``/api/v1/models/<owner/name>/repo?Revision=master&FilePath=<file>``);
  我们要做协议转换。``downloader.py`` 已经按 ``Mirror.kind`` 分流。

切换方式
========

* ``preference="modelscope"``(CLI / API ``mirror=modelscope``)
* 环境变量 ``CHAYUAN_MIRROR=modelscope``
* 配置文件 ``mirrors.primary = "https://www.modelscope.cn"``
* ``runtime.json`` 中的 ``ai_platform.mirror`` 字段(持久化)
* 运行时通过 :func:`set_mirror` 动态切换 + 进程内热更新

ModelScope ID 转换
==================

HF 仓库 ID 常用 ``Owner/Name`` 大小写敏感;ModelScope 默认 owner 全小写。
:func:`modelscope_repo_id` 做最小幅度的转换:

* ``Qwen/Qwen2.5-7B-Instruct`` → ``qwen/Qwen2.5-7B-Instruct``
* ``BAAI/bge-large-zh-v1.5``    → ``baai/bge-large-zh-v1.5``
* 极少数仓库 owner 段实际是大写(如 ``LLM-Research``),由 ``OVERRIDES`` 兜底
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("chayuan_modelmgr.mirrors")


@dataclass(frozen=True)
class Mirror:
    name: str
    endpoint: str
    kind: str  # "hf" | "modelscope" | "internal"


MIRRORS: dict[str, Mirror] = {
    "hf-mirror":   Mirror("hf-mirror",   "https://hf-mirror.com",       "hf"),
    "huggingface": Mirror("huggingface", "https://huggingface.co",      "hf"),
    "hf-co":       Mirror("hf-co",       "https://hf.co",               "hf"),
    "modelscope":  Mirror("modelscope",  "https://www.modelscope.cn",   "modelscope"),
}


# ModelScope owner 段大小写覆盖(HF owner → ModelScope owner)。
# 大多数情况下小写化即可,但少数已知 owner 在 ModelScope 上保留大小写;
# 维护这张表比每次去 ModelScope API 探测便宜很多。
_MODELSCOPE_OWNER_OVERRIDES: dict[str, str] = {
    "LLM-Research": "LLM-Research",
    "AI-ModelScope": "AI-ModelScope",
    "ZhipuAI": "ZhipuAI",
    "MiniMax-AI": "MiniMax-AI",
    "MiniMaxAI": "MiniMaxAI",
}


def modelscope_repo_id(hf_repo_id: str) -> str:
    """Convert HuggingFace repo id to ModelScope repo id.

    HF 用 ``Owner/Name``,owner 段大小写;ModelScope 默认 owner 段小写。
    我们只动 owner 段,Name 段保持原样(因为 ModelScope 多数模型 Name 与 HF 同名)。

    >>> modelscope_repo_id("Qwen/Qwen2.5-7B-Instruct")
    'qwen/Qwen2.5-7B-Instruct'
    >>> modelscope_repo_id("BAAI/bge-large-zh-v1.5")
    'baai/bge-large-zh-v1.5'
    >>> modelscope_repo_id("LLM-Research/Llama-3.2-3B-Instruct")
    'LLM-Research/Llama-3.2-3B-Instruct'
    >>> modelscope_repo_id("simple-name")  # 已经无 owner 段
    'simple-name'
    """
    if "/" not in hf_repo_id:
        return hf_repo_id
    owner, name = hf_repo_id.split("/", 1)
    owner_out = _MODELSCOPE_OWNER_OVERRIDES.get(owner, owner.lower())
    return f"{owner_out}/{name}"


def _mirror_for_endpoint(url: str) -> Mirror:
    """探测一段 URL 应该用哪种协议。

    简化规则:URL 里出现 ``modelscope`` → modelscope 协议;其他都按 HF。
    """
    lower = url.lower()
    if "modelscope" in lower:
        return Mirror("modelscope-custom", url, "modelscope")
    return Mirror("hf-custom", url, "hf")


# 进程内可变状态:set_mirror() 通过它热更新;resolve_mirror() 优先读它。
# 比环境变量更高优先,使用方:UI 切换 → set_mirror() → 立刻生效不重启。
_runtime_override: dict[str, Optional[str]] = {"value": None}
_override_lock = threading.Lock()


def set_mirror(name_or_url: Optional[str]) -> Mirror:
    """运行时动态切换镜像源。

    - ``None`` 清除运行时覆盖,回退到环境变量 / 配置文件。
    - 字符串形式:可以是 ``MIRRORS`` 中的命名 key,也可以是完整 URL。

    返回切换后实际生效的 :class:`Mirror`。
    """
    with _override_lock:
        _runtime_override["value"] = name_or_url
        # 设值时写一次 HF_ENDPOINT 让本进程内 huggingface_hub 立即看到;
        # 不写 CHAYUAN_MIRROR 避免与 _runtime_override 同源不同步,
        # 也不在清空时去删用户原本设的 env(那是用户的 source-of-truth)。
        if name_or_url:
            mirror = _resolve_mirror_for_value(name_or_url)
            os.environ["HF_ENDPOINT"] = mirror.endpoint
    return resolve_mirror()


def _resolve_mirror_for_value(name_or_url: str) -> Mirror:
    m = MIRRORS.get(name_or_url)
    if m:
        return m
    return _mirror_for_endpoint(name_or_url)


def resolve_mirror(preference: str | None = None) -> Mirror:
    """Pick a mirror.

    Priority:
        0. **runtime override** via :func:`set_mirror`
        1. explicit `preference` arg(既可以是名字 ``modelscope``,也可以是完整 URL)
        2. CHAYUAN_MIRROR env var
        3. HF_ENDPOINT env var (back-compat with huggingface_hub)
        4. config.mirrors.primary
        5. hf-mirror
    """
    if preference:
        return _resolve_mirror_for_value(preference)

    with _override_lock:
        runtime = _runtime_override["value"]
    if runtime:
        return _resolve_mirror_for_value(runtime)

    if env := os.environ.get("CHAYUAN_MIRROR"):
        return _resolve_mirror_for_value(env)
    if env := os.environ.get("HF_ENDPOINT"):
        return _mirror_for_endpoint(env)
    try:
        from chayuan_core.config import load_config  # 延迟导入避免循环

        cfg = load_config()
        u = cfg.mirrors.primary
        for m in MIRRORS.values():
            if m.endpoint == u:
                return m
        return _mirror_for_endpoint(u)
    except Exception:
        return MIRRORS["hf-mirror"]


@dataclass
class SpeedtestResult:
    name: str
    endpoint: str
    kind: str
    latency_ms: Optional[float]  # None 表示连接失败
    error: Optional[str] = None

    def is_ok(self) -> bool:
        return self.latency_ms is not None


def speedtest_mirrors(
    timeout: float = 4.0,
    candidates: Optional[List[str]] = None,
) -> List[SpeedtestResult]:
    """对所有候选镜像并发发一个 HEAD,返回延迟排序结果。

    用途:UI "测速并自动选最快源" 按钮的后端实现。

    实现要点:
    * 用 stdlib ``urllib`` 发 HEAD,不引入额外依赖
    * 并发跑,尊重单次 ``timeout``
    * 不调用 ``set_mirror``;调用方决定是否切换
    """
    import concurrent.futures
    import urllib.request

    targets = candidates or list(MIRRORS.keys())
    mirror_list = [MIRRORS[k] for k in targets if k in MIRRORS]

    def _probe(mirror: Mirror) -> SpeedtestResult:
        url = mirror.endpoint.rstrip("/") + "/"
        req = urllib.request.Request(url, method="HEAD")
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout):  # nosec B310 — 内部白名单
                pass
            elapsed = (time.monotonic() - start) * 1000
            return SpeedtestResult(
                name=mirror.name, endpoint=mirror.endpoint, kind=mirror.kind,
                latency_ms=round(elapsed, 1),
            )
        except Exception as e:  # noqa: BLE001
            return SpeedtestResult(
                name=mirror.name, endpoint=mirror.endpoint, kind=mirror.kind,
                latency_ms=None, error=str(e),
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(mirror_list) or 1) as pool:
        results = list(pool.map(_probe, mirror_list))
    # 失败的排到末尾;成功的按延迟升序
    results.sort(key=lambda r: (r.latency_ms is None, r.latency_ms or 0))
    return results


def pick_fastest_mirror(timeout: float = 4.0) -> Optional[Mirror]:
    """测速后返回最快的镜像;全部失败返回 None。"""
    results = speedtest_mirrors(timeout=timeout)
    for r in results:
        if r.is_ok():
            return _resolve_mirror_for_value(r.name)
    return None


__all__ = [
    "Mirror",
    "MIRRORS",
    "SpeedtestResult",
    "modelscope_repo_id",
    "pick_fastest_mirror",
    "resolve_mirror",
    "set_mirror",
    "speedtest_mirrors",
]
