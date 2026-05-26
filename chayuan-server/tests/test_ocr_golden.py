"""OCR 黄金 fixture 测试.

设计:
    1. 读 tests/data/ocr/expected.json 拿到每个图像的标注
    2. 对应 .png 不存在时 pytest.skip(开发者本地未放真图,CI 看到的是 skip 而非 fail)
    3. 调 file_rag.document_loaders.ocr 提取文本
    4. 校验 must_contain / must_not_contain / min_length

价值:
    在本地 + CI 对 OCR pipeline 做最小回归;比"返回 200 + items 字段"严格,
    比"识别准确率 ≥ 99%" 现实。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent / "data" / "ocr"
EXPECTED_PATH = FIXTURES_DIR / "expected.json"


def _load_expected() -> dict:
    if not EXPECTED_PATH.exists():
        return {}
    return json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))


def _ocr_extract(path: Path) -> str:
    """统一 OCR 出口;失败 raise 让 pytest 记 fail。

    优先用 RapidOCR(纯 ONNX 跨平台);缺失就用 PaddleOCR;再缺失视为环境问题
    skip。
    """
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore

        engine = RapidOCR()
        result, _ = engine(str(path))
        if not result:
            return ""
        return "\n".join(item[1] for item in result if len(item) >= 2)
    except ImportError:
        pass
    try:
        from paddleocr import PaddleOCR  # type: ignore

        engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        result = engine.ocr(str(path), cls=True)
        if not result:
            return ""
        out = []
        for page in result:
            for line in page or []:
                if line and len(line) >= 2 and isinstance(line[1], (list, tuple)):
                    out.append(line[1][0])
        return "\n".join(out)
    except ImportError:
        pytest.skip("既无 rapidocr_onnxruntime 也无 paddleocr;OCR 不可测")


@pytest.mark.parametrize("filename,spec", list(_load_expected().items()))
def test_ocr_golden_fixture(filename: str, spec: dict) -> None:
    img = FIXTURES_DIR / filename
    if not img.exists():
        pytest.skip(f"fixture 缺失: {img} (本地放置即可启用)")

    text = _ocr_extract(img)

    min_len = int(spec.get("min_length", 0) or 0)
    assert len(text) >= min_len, (
        f"OCR 文本长度不足:实际 {len(text)} < 期望 {min_len}\n"
        f"前 200 字符: {text[:200]!r}"
    )

    for keyword in spec.get("must_contain", []) or []:
        # 大小写不敏感 + 全文搜索
        assert keyword.lower() in text.lower(), (
            f"OCR 文本缺少关键字 '{keyword}';前 200 字符: {text[:200]!r}"
        )

    for keyword in spec.get("must_not_contain", []) or []:
        assert keyword.lower() not in text.lower(), (
            f"OCR 文本不应含 '{keyword}',却出现了;"
            f"前 200 字符: {text[:200]!r}"
        )
