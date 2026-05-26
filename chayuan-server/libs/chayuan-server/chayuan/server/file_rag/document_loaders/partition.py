from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List


_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")

#: \u6587\u6863(docx / pdf / pptx)\u5185\u5d4c\u56fe\u7247 OCR \u51fa\u6765\u7684\u6587\u5b57,\u7edf\u4e00\u52a0\u8fd9\u4e2a\u524d\u7f00\u505a\u6765\u6e90\u6807\u6ce8\u3002
#: \u68c0\u7d22 / \u5f15\u7528\u65f6\u4e00\u773c\u770b\u51fa\u8fd9\u6bb5\u6587\u5b57\u6765\u81ea\u6587\u6863\u91cc\u7684\u56fe\u7247(\u622a\u56fe / \u56fe\u8868 / \u62cd\u7167),
#: \u800c\u4e0d\u662f\u6587\u6863\u6b63\u6587\u3002\u4e09\u4e2a RapidOCR* \u52a0\u8f7d\u5668\u5171\u7528,\u6539\u6587\u6848\u53ea\u6539\u8fd9\u4e00\u5904\u3002
IMAGE_OCR_PREFIX = "\u3010\u6587\u6863\u5185\u56fe\u7247\u3011"


@dataclass
class _PlainTextElement:
    text: str

    def __str__(self) -> str:
        return self.text

    def to_dict(self) -> Dict[str, Any]:
        return {"type": "Text", "text": self.text, "metadata": {}}


def _plain_text_elements(text: str) -> List:
    parts = [p.strip() for p in re.split(r"\n{2,}", text or "") if p.strip()]
    if not parts and (text or "").strip():
        parts = [(text or "").strip()]
    if not parts:
        return []
    try:
        from unstructured.documents.elements import Text
        return [Text(p) for p in parts]
    except Exception:
        return [_PlainTextElement(p) for p in parts]


def partition_ocr_text(text: str, unstructured_kwargs: Dict[str, Any]) -> List:
    """Partition OCR text without requiring NLTK's English POS tagger for CJK docs."""
    from unstructured.partition.text import partition_text

    kwargs = dict(unstructured_kwargs)
    if "languages" not in kwargs and "language" not in kwargs and _CJK_RE.search(text or ""):
        kwargs["languages"] = ["zho"]

    try:
        return partition_text(text=text, **kwargs)
    except LookupError as exc:
        if "averaged_perceptron_tagger" not in str(exc):
            raise
        return _plain_text_elements(text)
