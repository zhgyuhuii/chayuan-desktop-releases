"""模型元数据自动补全 —— 用 DB 已配置的某个 LLM 厂商批量出 JSON,落 model_metadata。

对外只暴露一个 ``enrich_models_with_llm`` 协程,封装:
  1. 选一个可用 LLM(优先 deepseek/zhipu/openai,任意一个 enabled + 有 llm_models +
     有 api_key 的厂商即可);
  2. 用一段固定提示词批量喂 model_id 列表,要求返回 JSON dict
     ``{"<model_id>": {"description": "...", "release_date": "...",
                       "context_length": ...,  "performance_note": "..."}}``;
  3. 解析 JSON(用模型支持的 json mode;不支持就用宽松正则),写 model_metadata。

健壮性:
  - 任何模型不在 LLM 知识里 → 字段留空,而不是瞎猜;
  - JSON 解析失败 → 返回部分成功;
  - 调用厂商超时/出错 → 直接抛 HTTPException,前端能给出"先配置一个 LLM 厂商"。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("chayuan.api.admin.enrich")


# 选 LLM 用的优先级(越靠前越优先);列表末位是兜底"任意有 llm_models 的厂商"
_PREFERRED_LLM_PLATFORMS: Tuple[str, ...] = (
    "deepseek", "zhipu", "bailian", "moonshot", "openai", "anthropic", "siliconflow",
)


@dataclass
class _PickedLLM:
    platform_name: str
    model_id: str
    api_base_url: str
    api_key: str
    api_proxy: str


def _pick_llm_for_enrich(skip_platform: Optional[str] = None) -> Optional[_PickedLLM]:
    """从 get_config_platforms() 里挑一个可用 LLM。

    跳过 ``skip_platform`` 自身——避免"想给 deepseek 补全简介,但 deepseek 还没配 key"
    这种循环依赖把请求挂死。
    """
    try:
        from chayuan.server.utils import get_config_platforms
    except Exception:  # noqa: BLE001
        return None

    platforms = get_config_platforms() or {}

    def _ok(name: str, p: Dict[str, Any]) -> Optional[_PickedLLM]:
        if not p.get("enabled", True):
            return None
        models = list(p.get("llm_models") or [])
        if not models:
            return None
        api_key = (p.get("api_key") or "").strip()
        api_base = (p.get("api_base_url") or "").strip()
        if not api_key or not api_base:
            return None
        return _PickedLLM(
            platform_name=name,
            model_id=models[0],
            api_base_url=api_base,
            api_key=api_key,
            api_proxy=(p.get("api_proxy") or "").strip(),
        )

    # 1) 优先列表
    for pname in _PREFERRED_LLM_PLATFORMS:
        if skip_platform and pname == skip_platform:
            continue
        p = platforms.get(pname)
        if not p:
            continue
        picked = _ok(pname, p)
        if picked:
            return picked

    # 2) 兜底:任意配齐了 LLM 的厂商
    for pname, p in platforms.items():
        if skip_platform and pname == skip_platform:
            continue
        picked = _ok(pname, p)
        if picked:
            return picked
    return None


_PROMPT_TEMPLATE = """\
你是 AI 模型档案专家。下面给你一份模型 id 清单(来自服务商 {platform_display_name}),\
请基于公开信息为每个 id 输出元信息,不允许编造未知字段。

**输入** 模型 id 清单(JSON 数组):
{models_json}

**输出** 严格的 JSON 对象,不要多余文字,不要 markdown 代码块。键 = 输入中的 model_id,\
值 = 一个对象,包含字段:
  - description: 中文 2-3 句话简介(模型定位 / 主要能力 / 典型用法),≤120 字
  - release_date: "YYYY-MM" 或 "YYYY-MM-DD";不知道时填空字符串 ""
  - context_length: 上下文窗口的 tokens 数(整数);不知道时填 null
  - performance_note: 一句中文短语描述能力档位(如"旗舰对话、复杂推理"、"轻量、快速、低成本");≤30 字

如果某个 id 你完全不认识(不在公开资料里),所有字段都给空字符串(context_length 用 null),\
不要瞎猜。

只输出顶层 JSON,例如:
{{"deepseek-chat": {{"description":"...", "release_date":"2024-12", "context_length":128000, "performance_note":"..."}}}}\
"""


def _build_prompt(platform_display_name: str, models: List[str]) -> str:
    return _PROMPT_TEMPLATE.format(
        platform_display_name=platform_display_name,
        models_json=json.dumps(models, ensure_ascii=False),
    )


def _extract_json(s: str) -> Dict[str, Any]:
    """从模型回复里抠出第一个完整 JSON 对象。

    优先直接 json.loads;失败回退到"找第一个 { 配对"的宽松抽取。
    """
    s = (s or "").strip()
    # 去掉 markdown 代码围栏
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        pass
    # 宽松抽取最外层 { ... }
    start = s.find("{")
    if start < 0:
        return {}
    depth = 0
    for i in range(start, len(s)):
        c = s[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                snippet = s[start : i + 1]
                try:
                    return json.loads(snippet)
                except Exception:  # noqa: BLE001
                    return {}
    return {}


async def enrich_models_with_llm(
    *,
    target_platform: str,
    target_display_name: str,
    models: List[str],
    timeout_s: float = 60.0,
) -> Dict[str, Any]:
    """对 models 调一次 LLM,返回 ``{"updated": [...], "skipped": [...], "llm_used": "..."}``。

    成功后已写入 ``model_metadata``;失败抛异常,调用方决定是否包成 HTTP error。
    """
    if not models:
        return {"updated": [], "skipped": [], "llm_used": ""}

    picked = _pick_llm_for_enrich(skip_platform=target_platform)
    if picked is None:
        # 调用方一般会包成 HTTPException(503,"请先配置任意一个有 LLM 的厂商再用 AI 补全")
        raise RuntimeError("no LLM platform available for enrichment")

    import openai
    import httpx

    prompt = _build_prompt(target_display_name or target_platform, models)

    params: Dict[str, Any] = {
        "base_url": picked.api_base_url,
        "api_key": picked.api_key,
    }
    if picked.api_proxy:
        params["http_client"] = httpx.AsyncClient(
            proxies=picked.api_proxy,
            timeout=timeout_s,
            transport=httpx.HTTPTransport(local_address="0.0.0.0"),
        )
    client = openai.AsyncClient(**params)

    raw_text = ""
    try:
        # 大多数 OpenAI 兼容服务都支持 response_format=json_object;不支持的会
        # 报错,捕获后退化成普通调用。
        try:
            resp = await client.chat.completions.create(
                model=picked.model_id,
                messages=[
                    {"role": "system", "content": "你只输出严格 JSON,不要任何解释或 markdown。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                timeout=timeout_s,
                response_format={"type": "json_object"},
            )
        except Exception:  # noqa: BLE001
            resp = await client.chat.completions.create(
                model=picked.model_id,
                messages=[
                    {"role": "system", "content": "你只输出严格 JSON,不要任何解释或 markdown。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                timeout=timeout_s,
            )
        raw_text = (resp.choices[0].message.content or "") if resp.choices else ""
    finally:
        # AsyncClient 不强制 close;让它走 GC
        pass

    parsed = _extract_json(raw_text)

    from chayuan.server.db.repository import model_metadata_repository as repo

    updated: List[str] = []
    skipped: List[str] = []
    for model_id in models:
        info = parsed.get(model_id) or parsed.get(model_id.lower())
        if not isinstance(info, dict):
            skipped.append(model_id)
            continue
        # 字段清洗:LLM 偶尔会把整数封装成字符串
        ctx_raw = info.get("context_length")
        ctx_val: Optional[int] = None
        try:
            if isinstance(ctx_raw, (int, float)) and ctx_raw > 0:
                ctx_val = int(ctx_raw)
            elif isinstance(ctx_raw, str) and ctx_raw.strip().isdigit():
                ctx_val = int(ctx_raw.strip())
        except Exception:  # noqa: BLE001
            ctx_val = None

        repo.upsert(
            platform_name=target_platform,
            model_id=model_id,
            description=str(info.get("description") or "").strip() or None,
            release_date=str(info.get("release_date") or "").strip() or None,
            context_length=ctx_val,
            performance_note=str(info.get("performance_note") or "").strip() or None,
            source="llm",
            source_model=picked.model_id,
        )
        updated.append(model_id)

    logger.info(
        "enrich %s: updated=%d skipped=%d via %s/%s",
        target_platform, len(updated), len(skipped),
        picked.platform_name, picked.model_id,
    )
    return {
        "updated": updated,
        "skipped": skipped,
        "llm_used": f"{picked.platform_name}:{picked.model_id}",
    }
