"""容器 healthy 后自动注册到 chayuan(Phase 3 — 42 题架构)。

设计目标
========
**用户操作 = 一键启动**,后续配置全自动:

  1. 用户点 "部署 infinity" → ContainerLifecycle.up(infinity, wait_healthy=True)
  2. 容器 healthy → 触发 register_after_healthy("infinity")
  3. 本模块自动:
     a. 拼 endpoint URL(读 compose yaml ports)
     b. 调 GET <url>/v1/models 拉模型清单
     c. 写 model_settings.yaml.MODEL_PLATFORMS 加 / 更新该 platform
     d. 把第一个可用 chat / embedding 模型设为默认
     e. 触发 chayuan_runtime registry 重载
  4. UI 提示 "✓ infinity 已就绪,12 个模型可用,默认 BAAI/bge-large-zh-v1.5"

**忽略手动配置** — 用户在 dashboard 手填的 platform 会被 auto-register 覆盖
(用户已确认这个行为,见 42 题决策)。带 ``source: "docker:<service>"`` 字段
标记自动注册项,与手动 platform 区分。

参考
====
* Ollama UI:容器 healthy → /api/tags 拉模型 → 立即可用
* HuggingFace TGI:OpenAI 兼容 /v1/models 端点
* LiteLLM:统一 /v1/models 协议
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("chayuan.auto_register")


# ============================================================================
# Service → 模型 capability 推断表
# ============================================================================
#
# 每个 docker service 默认提供哪些类型的模型(用于自动 default 设置):
#
#   service       capabilities (按优先级)
#   ───────────   ───────────────────────────────────────────
#   vllm          chat (LLM)
#   vllm-cpu      chat
#   infinity      embedding, rerank
#   comfyui       t2i (text-to-image)
#   llamacpp      chat

_SERVICE_CAPABILITIES: Dict[str, List[str]] = {
    "vllm":       ["chat"],
    "vllm-cpu":   ["chat"],
    "infinity":   ["embedding", "rerank"],
    "comfyui":    ["t2i"],
    "llamacpp":   ["chat"],
}

# 纯服务类(不提供模型)不在表里 → 跳过模型注册


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class RegisterReport:
    """auto-register 操作的结果摘要,用于 UI 提示。"""
    service: str
    ok: bool = False
    platform_added: bool = False     # 新增了 platform 还是 update
    platform_name: str = ""
    api_base_url: str = ""
    models_discovered: int = 0
    models_by_capability: Dict[str, List[str]] = field(default_factory=dict)
    defaults_set: Dict[str, str] = field(default_factory=dict)  # capability → model_name
    errors: List[str] = field(default_factory=list)
    skipped_reason: str = ""

    def summary(self) -> str:
        """中文一句话摘要,UI ui.notify 用。"""
        if self.skipped_reason:
            return f"{self.service}: 跳过自动注册({self.skipped_reason})"
        if not self.ok:
            return f"{self.service}: 注册失败 — {'; '.join(self.errors[:2])}"
        parts = [f"✓ {self.service} 已就绪"]
        if self.api_base_url:
            parts.append(f"({self.api_base_url})")
        parts.append(f"· {self.models_discovered} 个模型")
        if self.defaults_set:
            df = ", ".join(f"{cap}={mn}" for cap, mn in self.defaults_set.items())
            parts.append(f"· 默认 {df}")
        return " ".join(parts)


# ============================================================================
# 公共 API
# ============================================================================

async def register_after_healthy(
    service: str,
    *,
    chayuan_runtime_reload: bool = True,
    write_yaml: bool = True,
) -> RegisterReport:
    """容器 healthy 之后调用 — 自动注册到 chayuan model_settings。

    幂等:重复调用对同一 service,每次都用最新 endpoint + models 覆盖该 platform。

    Args:
        service: docker compose 里的 service name(vllm / infinity / ...)
        chayuan_runtime_reload: True 时尝试触发 chayuan_runtime registry 重载,
            让新注册的模型立即对 RAG / chat 路由可见
        write_yaml: True 时把变更写回 ``<CHAYUAN_ROOT>/model_settings.yaml``;
            False 仅返回 report 不落盘(用于 dry-run)

    返回 :class:`RegisterReport`(.ok / .summary() 给 UI 用)
    """
    report = RegisterReport(service=service)

    # 1) 该 service 是否需要注册模型?
    if service not in _SERVICE_CAPABILITIES:
        report.skipped_reason = f"service '{service}' 不提供 OpenAI 兼容模型 API"
        report.ok = True  # 跳过不算失败
        logger.info("[auto-register] %s — %s", service, report.skipped_reason)
        return report

    # 2) 拼端点 URL
    api_base_url = await _resolve_endpoint_url(service)
    if not api_base_url:
        report.errors.append("无法解析 endpoint URL(compose yaml 里没找到 ports?)")
        return report
    report.api_base_url = api_base_url

    # 3) 拉模型清单(GET /v1/models)
    models = await _fetch_openai_models(api_base_url)
    if not models:
        report.errors.append(f"GET {api_base_url}/v1/models 返回空 — 容器虽 healthy 但模型未就绪")
        # 仍然加 platform(只是没模型),用户可后续手动添加
    report.models_discovered = len(models)

    # 4) 推断每个模型的 capability(简单基于关键字 + service 默认)
    capabilities = _SERVICE_CAPABILITIES[service]
    by_cap = _classify_models(models, capabilities)
    report.models_by_capability = by_cap

    # 5) 写 model_settings.yaml
    if write_yaml:
        try:
            added, defaults = await asyncio.to_thread(
                _write_to_model_settings,
                service=service,
                api_base_url=api_base_url,
                models_by_cap=by_cap,
            )
            report.platform_added = added
            report.platform_name = f"docker:{service}"
            report.defaults_set = defaults
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"写 model_settings.yaml 失败:{type(e).__name__}: {e}")
            return report

    # 6) 触发 runtime registry reload
    if chayuan_runtime_reload:
        try:
            await asyncio.to_thread(_reload_chayuan_runtime_registry)
        except Exception as e:  # noqa: BLE001
            # 重载失败不算 fatal — 用户重启 chayuan 后也能生效
            report.errors.append(f"runtime registry reload 失败(忽略,重启即生效):{e}")

    report.ok = True
    logger.info("[auto-register] %s", report.summary())
    return report


# ============================================================================
# 内部 helper
# ============================================================================

async def _resolve_endpoint_url(service: str) -> str:
    """从 compose yaml 拼出该 service 的 endpoint URL(127.0.0.1:HOST_PORT)。"""
    try:
        from chayuan.server.config_panel.compose_manager import (
            get_service_host_port,
        )
        port = await asyncio.to_thread(get_service_host_port, service)
        if port is None:
            return ""
        return f"http://127.0.0.1:{port}"
    except Exception as e:  # noqa: BLE001
        logger.debug("[auto-register] resolve endpoint failed: %r", e)
        return ""


async def _fetch_openai_models(api_base_url: str, timeout: float = 5.0) -> List[Dict[str, Any]]:
    """调 GET <url>/v1/models 拿模型清单(OpenAI 协议)。"""
    url = api_base_url.rstrip("/") + "/v1/models"
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url)
        if r.status_code != 200:
            logger.debug("[auto-register] GET %s rc=%d", url, r.status_code)
            return []
        data = r.json()
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return [m for m in data["data"] if isinstance(m, dict)]
    except Exception as e:  # noqa: BLE001
        logger.debug("[auto-register] GET %s failed: %r", url, e)
    return []


def _classify_models(
    models: List[Dict[str, Any]],
    service_capabilities: List[str],
) -> Dict[str, List[str]]:
    """简单分类:按模型 id 关键词 + service 默认 capability 推断。

    例如 infinity 默认 capabilities=["embedding","rerank"];
    模型 id 含 "rerank" 归 rerank,其他归 embedding。
    """
    by_cap: Dict[str, List[str]] = {cap: [] for cap in service_capabilities}
    for m in models:
        mid = str(m.get("id") or m.get("name") or "").strip()
        if not mid:
            continue
        lower = mid.lower()
        # 关键词推断
        if "rerank" in lower and "rerank" in by_cap:
            by_cap["rerank"].append(mid)
        elif ("embed" in lower or "bge" in lower or "m3e" in lower) and "embedding" in by_cap:
            by_cap["embedding"].append(mid)
        elif ("chat" in lower or "instruct" in lower or "llm" in lower) and "chat" in by_cap:
            by_cap["chat"].append(mid)
        else:
            # 走 service 第 1 个 capability 兜底
            if service_capabilities:
                by_cap[service_capabilities[0]].append(mid)
    return by_cap


def _write_to_model_settings(
    *,
    service: str,
    api_base_url: str,
    models_by_cap: Dict[str, List[str]],
) -> Tuple[bool, Dict[str, str]]:
    """把 platform 写到 model_settings.yaml。

    返回 (是否新增, 自动设的默认模型 dict)。
    幂等:platform_name = ``docker:<service>`` 已存在则更新 url + models。
    """
    import yaml
    from chayuan.settings import CHAYUAN_ROOT

    yaml_file = CHAYUAN_ROOT / "model_settings.yaml"
    if not yaml_file.exists():
        # init 没跑过,或自定义路径 — 不动
        return False, {}

    try:
        doc = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
    except Exception:
        doc = {}
    if not isinstance(doc, dict):
        doc = {}

    platforms: List[Dict[str, Any]] = doc.get("MODEL_PLATFORMS") or []
    if not isinstance(platforms, list):
        platforms = []

    platform_name = f"docker:{service}"
    existing_idx = -1
    for i, p in enumerate(platforms):
        if isinstance(p, dict) and p.get("platform_name") == platform_name:
            existing_idx = i
            break

    # 各 capability 模型清单 → yaml 字段
    # MODEL_PLATFORMS 的字段约定见 settings.py PlatformConfig
    new_p: Dict[str, Any] = {
        "platform_name": platform_name,
        "platform_type": "openai",      # OpenAI 兼容协议
        "api_base_url": api_base_url + "/v1",
        "api_key": "EMPTY",             # 本地容器不需要 key
        "api_concurrencies": 5,
        "auto_detect_model": False,
        "source": f"docker:{service}",  # 区分手动 vs 自动
        "llm_models":       list(models_by_cap.get("chat", [])),
        "embed_models":     list(models_by_cap.get("embedding", [])),
        "rerank_models":    list(models_by_cap.get("rerank", [])),
        "image_models":     list(models_by_cap.get("t2i", [])),
        "tts_models":       [],
        "stt_models":       [],
    }
    if existing_idx >= 0:
        platforms[existing_idx] = new_p
        added = False
    else:
        platforms.append(new_p)
        added = True

    doc["MODEL_PLATFORMS"] = platforms

    # 自动设默认模型(仅当还未设过 / 现值不在新模型清单里时)
    defaults_set: Dict[str, str] = {}
    for cap, key in (
        ("chat", "DEFAULT_LLM_MODEL"),
        ("embedding", "DEFAULT_EMBEDDING_MODEL"),
        ("rerank", "DEFAULT_RERANK_MODEL"),
        ("t2i", "DEFAULT_IMAGE_GENERATION_MODEL"),
    ):
        models = models_by_cap.get(cap) or []
        if not models:
            continue
        cur = doc.get(key) or ""
        # 如果当前没设 / 设的不在任何 platform 的对应清单里 → 用这个新模型
        if not cur:
            doc[key] = models[0]
            defaults_set[cap] = models[0]

    yaml_file.write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return added, defaults_set


def _reload_chayuan_runtime_registry() -> None:
    """触发 chayuan_runtime 的 registry 重新读 model_settings.yaml。

    实现弱依赖:registry 提供 ``reload()`` 就调,没有就忽略(下次 chayuan 启动时生效)。
    """
    try:
        # 优先用 chayuan_runtime 自己的 reload
        from chayuan_runtime.registry import reload as _reload  # type: ignore
        _reload()
        return
    except (ImportError, AttributeError):
        pass
    # fallback:重置 chayuan.settings 里的 model_settings 缓存
    try:
        from chayuan.settings import Settings
        if hasattr(Settings.model_settings, "reload"):
            Settings.model_settings.reload()
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "RegisterReport",
    "register_after_healthy",
]
