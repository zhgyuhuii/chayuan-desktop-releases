"""LangChain 回调：统一的 LLM 调用指标采集。

思路：
- LangChain 对所有 LLM 都发 `on_llm_start / on_llm_end / on_llm_error`；
- 我们用一个 callback handler 接住这三个事件，算耗时 + 状态，
  转发到 ``observability.metrics.record_llm_call``；
- 不影响现有的流式 / 同步 / 异步调用路径。

若 LangChain 版本不兼容（异常导入），下沉为 no-op，不影响业务。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional
from uuid import UUID

logger = logging.getLogger("chayuan.observability.llm_callback")

try:
    from langchain_core.callbacks import BaseCallbackHandler  # type: ignore
    _LC_OK = True
except ImportError:
    try:
        from langchain.callbacks.base import BaseCallbackHandler  # type: ignore
        _LC_OK = True
    except ImportError:
        BaseCallbackHandler = object  # type: ignore
        _LC_OK = False


class MetricsCallbackHandler(BaseCallbackHandler):  # type: ignore[misc]
    """按 run_id 记录 LLM 调用开始时间 → 结束时算耗时 + 状态。"""

    def __init__(self) -> None:
        self._start: Dict[str, float] = {}
        self._meta: Dict[str, Dict[str, str]] = {}

    @staticmethod
    def _extract_meta(serialized: Optional[Dict[str, Any]], invocation_params: Optional[Dict[str, Any]]) -> Dict[str, str]:
        platform = "openai"
        model = "-"
        try:
            if invocation_params:
                model = str(invocation_params.get("model") or invocation_params.get("model_name") or model)
            if serialized and isinstance(serialized, dict):
                cls_path = ".".join(serialized.get("id") or [])
                if "ChatOpenAI" in cls_path or "openai" in cls_path.lower():
                    platform = "openai"
                elif cls_path:
                    platform = cls_path.rsplit(".", 1)[-1]
        except Exception:  # noqa: BLE001
            pass
        return {"platform": platform, "model": model}

    def on_llm_start(  # type: ignore[override]
        self,
        serialized: Dict[str, Any],
        prompts,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags=None,
        metadata=None,
        invocation_params: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        key = str(run_id)
        self._start[key] = time.perf_counter()
        self._meta[key] = self._extract_meta(serialized, invocation_params or kwargs.get("invocation_params"))

    def on_chat_model_start(  # type: ignore[override]
        self,
        serialized: Dict[str, Any],
        messages,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags=None,
        metadata=None,
        invocation_params: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        self.on_llm_start(
            serialized, messages,
            run_id=run_id, parent_run_id=parent_run_id,
            tags=tags, metadata=metadata,
            invocation_params=invocation_params, **kwargs,
        )

    def on_llm_end(self, response, *, run_id: UUID, **kwargs: Any) -> None:  # type: ignore[override]
        self._record(run_id, "success", response=response)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:  # type: ignore[override]
        status = "timeout" if isinstance(error, TimeoutError) else "error"
        self._record(run_id, status)

    @staticmethod
    def _extract_token_usage(response: Any) -> Optional[Dict[str, int]]:
        """从 LLMResult / ChatResult 里尽力提取 token 用量。

        兼容多家 LLM Provider 的不同返回字段名：
        - OpenAI: ``llm_output.token_usage = {prompt_tokens, completion_tokens, total_tokens}``
        - Anthropic: ``llm_output.usage = {input_tokens, output_tokens}``
        - 兜底：generations[*].message.usage_metadata（LangChain 1.x 统一字段）
        """
        try:
            out = getattr(response, "llm_output", None) or {}
            usage = out.get("token_usage") or out.get("usage") or {}
            prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            completion = int(
                usage.get("completion_tokens") or usage.get("output_tokens") or 0
            )
            if prompt or completion:
                return {"prompt": prompt, "completion": completion}
        except Exception:  # noqa: BLE001
            pass
        # generations[*].message.usage_metadata
        try:
            gens = getattr(response, "generations", None) or []
            prompt = 0
            completion = 0
            for row in gens:
                for g in row or []:
                    msg = getattr(g, "message", None)
                    if msg is None:
                        continue
                    meta = getattr(msg, "usage_metadata", None) or {}
                    prompt += int(meta.get("input_tokens") or 0)
                    completion += int(meta.get("output_tokens") or 0)
            if prompt or completion:
                return {"prompt": prompt, "completion": completion}
        except Exception:  # noqa: BLE001
            pass
        return None

    def _record(self, run_id: UUID, status: str, response: Any = None) -> None:
        key = str(run_id)
        start = self._start.pop(key, None)
        meta = self._meta.pop(key, {"platform": "-", "model": "-"})
        if start is None:
            return
        try:
            from chayuan.server.observability.metrics import (
                record_llm_call, record_llm_tokens,
            )
            record_llm_call(
                meta["platform"], meta["model"], status,
                time.perf_counter() - start,
            )
            if response is not None:
                usage = self._extract_token_usage(response)
                if usage:
                    record_llm_tokens(
                        meta["model"],
                        prompt_tokens=usage["prompt"],
                        completion_tokens=usage["completion"],
                    )
        except Exception:  # noqa: BLE001
            logger.debug("record_llm_call failed", exc_info=True)


def build_metrics_handler() -> Optional[MetricsCallbackHandler]:
    """启用时返回 handler；LangChain 包缺失或指标关闭时返回 None。"""
    if not _LC_OK:
        return None
    try:
        from chayuan.server.observability.metrics import is_metrics_enabled
        if not is_metrics_enabled():
            return None
    except Exception:  # noqa: BLE001
        pass
    return MetricsCallbackHandler()
