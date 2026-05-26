"""langchain-openai 兼容性补丁。

----- thinking-mode reasoning_content 全链路 -----

DeepSeek / Qwen / 阶跃 等带 thinking-mode 的模型,API 在 ``assistant`` 消息里
返回 ``reasoning_content`` 字段;**下一轮请求**(典型场景:工具调用回路,把 tool
结果拼回去再问)必须把 **同一条 assistant** 消息的 ``reasoning_content`` **原样
回传**,否则 API 直接返回 400::

    The `reasoning_content` in the thinking mode must be passed back to the API.

但 langchain-openai **三个转换函数**都没认这个字段:
  1. ``_convert_delta_to_message_chunk``  流式 chunk 入,字段直接丢
  2. ``_convert_dict_to_message``         非流式 message 入,字段直接丢
  3. ``_convert_message_to_dict``         请求出口,additional_kwargs 不写回

只 patch 出口(我们之前那版)没用——因为入口已经把字段丢了,出口在
additional_kwargs 里压根找不到。这里把三处都 patch 上,形成 in→state→out
完整链路。流式时多个 chunk 的 reasoning_content 通过 ``AIMessageChunk + AIMessageChunk``
的字典合并会自动累加(BaseMessageChunk 用 merge_dicts 处理 additional_kwargs)。

非 thinking 模型不受影响:字段缺失时 patch 不写键。
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger("chayuan.compat.langchain_openai")


def _apply_reasoning_content_passthrough() -> None:
    try:
        from langchain_openai.chat_models import base as _m
    except Exception as e:  # noqa: BLE001
        logger.warning("langchain_openai 未装,跳过 reasoning_content patch:%r", e)
        return

    _patch_delta_chunk(_m)
    _patch_dict_to_message(_m)
    _patch_message_to_dict(_m)


# --- 1. 流式 chunk 入 -----------------------------------------------------

def _patch_delta_chunk(_m) -> None:
    orig = getattr(_m, "_convert_delta_to_message_chunk", None)
    if orig is None or getattr(orig, "_chayuan_reasoning_patched", False):
        return

    def _patched(_dict: Mapping[str, Any], default_class):
        chunk = orig(_dict, default_class)
        try:
            rc = _dict.get("reasoning_content") if isinstance(_dict, Mapping) else None
            if rc:
                # AIMessageChunk.additional_kwargs 是 dict;给它写回去
                ak = getattr(chunk, "additional_kwargs", None)
                if isinstance(ak, dict):
                    # 多 chunk 累积:同一条 message 的多个增量 chunk 由 BaseMessageChunk
                    # 的 __add__ 路径合并 additional_kwargs(merge_dicts 把同 key 字符串拼接)
                    prev = ak.get("reasoning_content") or ""
                    ak["reasoning_content"] = prev + str(rc)
        except Exception:  # noqa: BLE001
            logger.exception("delta chunk reasoning_content patch 异常")
        return chunk

    _patched._chayuan_reasoning_patched = True  # type: ignore[attr-defined]
    _m._convert_delta_to_message_chunk = _patched
    logger.info("已注入 reasoning_content patch:_convert_delta_to_message_chunk(流式入)")


# --- 2. 非流式 message 入 -------------------------------------------------

def _patch_dict_to_message(_m) -> None:
    orig = getattr(_m, "_convert_dict_to_message", None)
    if orig is None or getattr(orig, "_chayuan_reasoning_patched", False):
        return

    def _patched(_dict: Mapping[str, Any]):
        msg = orig(_dict)
        try:
            if isinstance(_dict, Mapping) and _dict.get("role") == "assistant":
                rc = _dict.get("reasoning_content")
                if rc:
                    ak = getattr(msg, "additional_kwargs", None)
                    if isinstance(ak, dict):
                        ak["reasoning_content"] = rc
        except Exception:  # noqa: BLE001
            logger.exception("dict_to_message reasoning_content patch 异常")
        return msg

    _patched._chayuan_reasoning_patched = True  # type: ignore[attr-defined]
    _m._convert_dict_to_message = _patched
    logger.info("已注入 reasoning_content patch:_convert_dict_to_message(非流式入)")


# --- 3. 请求出口 ----------------------------------------------------------

def _patch_message_to_dict(_m) -> None:
    orig = getattr(_m, "_convert_message_to_dict", None)
    if orig is None or getattr(orig, "_chayuan_reasoning_patched", False):
        return

    def _patched(message, api: str = "chat/completions"):
        out = orig(message, api=api)
        try:
            if isinstance(out, dict) and out.get("role") == "assistant":
                ak = getattr(message, "additional_kwargs", None) or {}
                rc = ak.get("reasoning_content")
                if rc:
                    out["reasoning_content"] = rc
        except Exception:  # noqa: BLE001
            logger.exception("message_to_dict reasoning_content patch 异常")
        return out

    _patched._chayuan_reasoning_patched = True  # type: ignore[attr-defined]
    _m._convert_message_to_dict = _patched
    logger.info("已注入 reasoning_content patch:_convert_message_to_dict(请求出口)")


_apply_reasoning_content_passthrough()
