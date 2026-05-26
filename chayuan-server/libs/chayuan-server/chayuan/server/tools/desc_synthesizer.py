"""为新创建的工具自动生成中文描述与 LLM Prompt。

三层兜底,职责单一:
  1. ``from_spec_meta``  —— 直接利用 OpenAPI/spec 自带的 summary/description
  2. ``from_llm``        —— 让本地可用 LLM 重写为"工具友好"中文描述
                            (P1 暂留接口,默认 disabled,P4 接入)
  3. ``from_signature``  —— ``{method} {url}`` 兜底,绝不 raise

公开入口:
    synthesize(method, url, summary=None, description=None, params=None) -> str

返回的字符串可直接喂给:
- ``CustomToolSpec.description``(被 LLM 工具选择时读)
- 自定义工具卡片(展示给用户)
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

logger = logging.getLogger("chayuan.tools.desc_synthesizer")


def from_spec_meta(
    summary: Optional[str], description: Optional[str], *, max_len: int = 200
) -> Optional[str]:
    """直接拼 spec 自带文案。两者都无 → None,让上层走下一层兜底。"""
    parts = []
    if summary and summary.strip():
        parts.append(summary.strip())
    if description and description.strip() and description.strip() != (summary or "").strip():
        parts.append(description.strip())
    if not parts:
        return None
    out = " — ".join(parts)
    if len(out) > max_len:
        out = out[: max_len - 1].rstrip() + "…"
    return out


def from_signature(method: str, url: str, params: Optional[Iterable[str]] = None) -> str:
    """spec 完全没文案时的兜底。永不 raise。

    形态::

        调用 GET https://api.example.com/users 接口(参数:id, name)。
    """
    method = (method or "GET").upper().strip()
    url = (url or "").strip()
    out = f"调用 {method} {url} 接口"
    p_list = [str(p).strip() for p in (params or []) if str(p).strip()]
    if p_list:
        out += f"(参数:{', '.join(p_list[:8])}{'…' if len(p_list) > 8 else ''})"
    out += "。"
    return out


def synthesize(
    *,
    method: str,
    url: str,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    params: Optional[Iterable[str]] = None,
    use_llm: bool = False,
) -> str:
    """三层兜底主入口。

    ``use_llm=True`` 留作 P4 钩子;P1 阶段恒走 spec → signature 两层。
    """
    text = from_spec_meta(summary, description)
    if text:
        return text

    if use_llm:
        try:
            llm = _from_llm(method=method, url=url, summary=summary, description=description, params=params)
            if llm:
                return llm
        except Exception:  # noqa: BLE001
            logger.exception("LLM 描述生成失败,继续走 signature 兜底")

    return from_signature(method, url, params=params)


def _from_llm(*args, **kwargs) -> Optional[str]:  # noqa: ARG001
    """P4 接入 LLM 重写。P1 占位。"""
    return None
