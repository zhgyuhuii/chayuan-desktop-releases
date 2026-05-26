"""Structured LLM Output 统一适配层（N-2 核心基建）。

取代项目里散布的 ``_extract_json(llm_output)`` 模式。优先使用 LangChain 1.x 的
``ChatModel.with_structured_output(PydanticModel)``，失败时回退到"自由文本 +
json_repair"。三重保障：

1. **首选**：``with_structured_output(schema)`` - Provider 原生 function-calling
   或 JSON mode，解析失败率≈0
2. **次选**：``model_kwargs={"response_format": {"type": "json_object"}}`` -
   某些 Provider 没实现 schema binding 但支持 JSON mode
3. **兜底**：自由文本 → 正则 + json_repair + Pydantic 校验

所有路径都**返回 Pydantic 实例**或 None；上层不再手工处理 dict/None 混合。

**接入点**（N-2 覆盖）：
- sql/text2sql.generate_sql        → Text2SqlGen
- sql/graph_text2sql.node_*        → T2SClassify / T2SSchemaLink / T2SGenerate / T2SRevise
- mongo/text2mongo.generate_query  → MongoQueryGen
- es/text2es.generate_dsl          → EsDslGen
- knowledge_source/router          → SourceRouterResult
- graphrag/extractor               → GraphExtractionResult
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Type, TypeVar, Union

from pydantic import BaseModel

logger = logging.getLogger("chayuan.shared.structured_llm")

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# 通用 JSON 正则兜底（保留现有实现作为 fallback）
# ---------------------------------------------------------------------------

def _regex_extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if m:
        text = m.group(1)
    else:
        m2 = re.search(r"\{.*\}", text, flags=re.S)
        if m2:
            text = m2.group(0)
    try:
        return json.loads(text)
    except Exception:
        try:
            from json_repair import repair_json
            return json.loads(repair_json(text))
        except Exception:
            return None


def _regex_extract_list(text: str) -> Optional[List[Dict[str, Any]]]:
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, flags=re.S)
    if m:
        text = m.group(1)
    else:
        m2 = re.search(r"\[.*\]", text, flags=re.S)
        if m2:
            text = m2.group(0)
    try:
        v = json.loads(text)
        return v if isinstance(v, list) else None
    except Exception:
        try:
            from json_repair import repair_json
            v = json.loads(repair_json(text))
            return v if isinstance(v, list) else None
        except Exception:
            return None


# ---------------------------------------------------------------------------
# 对外：call_structured
# ---------------------------------------------------------------------------

def call_structured(
    *,
    system: str,
    user: str,
    schema: Type[T],
    llm_model: Optional[str] = None,
    temperature: float = 0.0,
    default: Optional[T] = None,
    use_function_calling: bool = True,
) -> Optional[T]:
    """调一次 LLM，返回符合 schema 的 Pydantic 实例。

    - ``schema``：Pydantic BaseModel 子类
    - ``default``：全部路径失败时返回此值；None 表示返回 None

    失败场景：LLM 不可用 / 解析失败 / 校验失败 → ``default``
    """
    # P2-7：统一熔断。底层 provider 失败率突破阈值后直接短路，避免把 worker
    # 线程池锁死在被挂掉的 LLM 上。breaker 按 provider 名（此处抽象为 "llm-default"）
    # 聚合，所有 call_structured 调用点共享同一个计数器。
    try:
        from chayuan.server.resilience import BreakerOpenError, get_breaker
        _breaker = get_breaker("llm-default")
    except Exception:  # noqa: BLE001
        _breaker = None
        BreakerOpenError = RuntimeError  # type: ignore
    if _breaker is not None:
        try:
            _breaker.before_call()
        except BreakerOpenError as e:  # noqa: BLE001
            logger.warning("call_structured 熔断短路：%r", e)
            return default

    _provider_ok = False
    try:
        from chayuan.server.observability.langfuse_integration import (
            inject_into_callbacks,
        )
        from chayuan.server.utils import get_ChatOpenAI, get_default_llm
        model = (llm_model or get_default_llm()).strip()
        callbacks = inject_into_callbacks([])

        # 1) 首选：with_structured_output
        if use_function_calling:
            try:
                llm = get_ChatOpenAI(
                    model_name=model, temperature=temperature, streaming=False,
                    callbacks=callbacks or None,
                )
                # LangChain 1.x：method="function_calling"；部分 provider 支持 "json_schema"
                structured = llm.with_structured_output(schema)
                result = structured.invoke([
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ])
                _provider_ok = True  # 无论 parse 结果如何，provider 通讯是成功的
                if isinstance(result, schema):
                    return result
                if isinstance(result, dict):
                    return schema.model_validate(result)
            except Exception as e:  # noqa: BLE001
                logger.debug("with_structured_output 失败，回退 JSON mode：%r", e)

        # 2) 次选：JSON mode
        try:
            llm = get_ChatOpenAI(
                model_name=model, temperature=temperature, streaming=False,
                callbacks=callbacks or None,
                model_kwargs={"response_format": {"type": "json_object"}},
            )
            resp = llm.invoke([
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ])
            _provider_ok = True
            text = (getattr(resp, "content", None) or "").strip()
            data = json.loads(text)
            return schema.model_validate(data)
        except Exception as e:  # noqa: BLE001
            logger.debug("JSON mode 失败，回退正则：%r", e)

        # 3) 兜底：自由文本 + 正则 + repair
        llm = get_ChatOpenAI(
            model_name=model, temperature=temperature, streaming=False,
            callbacks=callbacks or None,
        )
        resp = llm.invoke([
            {"role": "system", "content": system + "\n\n请只输出 JSON 对象，不要额外文字。"},
            {"role": "user", "content": user},
        ])
        _provider_ok = True
        text = getattr(resp, "content", None) or ""
        data = _regex_extract_json(text)
        if data is None:
            return default
        return schema.model_validate(data)
    except Exception as e:  # noqa: BLE001
        logger.warning("call_structured 全链路失败：%r", e)
        return default
    finally:
        if _breaker is not None:
            try:
                if _provider_ok:
                    _breaker.on_success()
                else:
                    _breaker.on_failure()
            except Exception:  # noqa: BLE001
                logger.debug("breaker bookkeeping failed", exc_info=True)


def call_structured_list(
    *,
    system: str,
    user: str,
    item_schema: Type[T],
    llm_model: Optional[str] = None,
    temperature: float = 0.0,
) -> List[T]:
    """便捷：LLM 返回 JSON 数组，每项解析为 ``item_schema`` 实例。

    由于 OpenAI JSON mode 不支持 top-level array，这里用"包装对象"策略：
    让 LLM 输出 ``{"items": [...]}``，再拆开。
    """
    class _Wrapper(BaseModel):
        items: List[item_schema] = []  # type: ignore[valid-type]

    wrapped_system = system + "\n\n重要：把结果放进 JSON 对象的 \"items\" 字段里，即 {\"items\": [...]}。"
    res = call_structured(
        system=wrapped_system, user=user, schema=_Wrapper,
        llm_model=llm_model, temperature=temperature,
        default=_Wrapper(items=[]),
    )
    if res is None:
        # 最后兜底：自由文本里找 list
        from chayuan.server.utils import get_ChatOpenAI, get_default_llm
        from chayuan.server.observability.langfuse_integration import (
            inject_into_callbacks,
        )
        try:
            model = (llm_model or get_default_llm()).strip()
            llm = get_ChatOpenAI(
                model_name=model, temperature=temperature, streaming=False,
                callbacks=inject_into_callbacks([]) or None,
            )
            resp = llm.invoke([
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ])
            text = getattr(resp, "content", None) or ""
            raw = _regex_extract_list(text) or []
        except Exception:  # noqa: BLE001
            raw = []
        out: List[T] = []
        for item in raw:
            try:
                out.append(item_schema.model_validate(item))
            except Exception:  # noqa: BLE001
                continue
        return out
    return list(res.items)
