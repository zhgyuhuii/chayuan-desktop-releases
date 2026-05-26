"""data_mount /import 端点的示例行过滤逻辑单测.

不打整个 FastAPI app — 直接测 ``_parse_import_payload`` 与 ``_is_sample_row``。
"""
from __future__ import annotations

import json

import pytest

from chayuan.server.api_server.data_mount_routes import (
    _is_sample_row,
    _parse_import_payload,
    _strip_sample_meta,
)


# ---------------------------------------------------------------------------
# _is_sample_row
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("row,expected", [
    ({"_example": True}, True),
    ({"_sample": "yes"}, True),
    ({"_template": 1}, True),
    ({"_is_example": True}, True),
    ({"name": "[示例] my mount"}, True),
    ({"name": "[example] foo"}, True),
    ({"name": "[Example] case insensitive"}, True),
    ({"name": "[模板] xx"}, True),
    ({"name": "[sample] yy"}, True),
    ({"source_type": "__example__kb"}, True),
    # 反例:正常行
    ({"name": "real mount", "source_type": "kb"}, False),
    ({}, False),
    ({"name": "示例 (放在中间)"}, False),  # 不是开头 → 不过滤
])
def test_is_sample_row(row, expected):
    assert _is_sample_row(row) is expected


def test_is_sample_row_handles_non_dict():
    assert _is_sample_row(None) is False
    assert _is_sample_row("string") is False
    assert _is_sample_row(["list"]) is False


# ---------------------------------------------------------------------------
# _strip_sample_meta
# ---------------------------------------------------------------------------

def test_strip_meta_drops_underscore_keys():
    row = {"name": "x", "_example": True, "_comment": "ignore", "scope_type": "user"}
    out = _strip_sample_meta(row)
    assert "name" in out and "scope_type" in out
    assert "_example" not in out and "_comment" not in out


def test_strip_meta_drops_hash_keys():
    row = {"name": "x", "#note": "header"}
    out = _strip_sample_meta(row)
    assert "#note" not in out


# ---------------------------------------------------------------------------
# _parse_import_payload (JSON)
# ---------------------------------------------------------------------------

def test_parse_json_envelope_form():
    """{"_meta":..., "items": [...]} 形式应当只取 items."""
    payload = json.dumps({
        "_meta": {"_comment": "header"},
        "items": [
            {"name": "real one", "source_type": "kb", "mount_modes": ["corpus"]},
        ],
    })
    out = _parse_import_payload("json", payload)
    assert len(out) == 1
    assert out[0]["name"] == "real one"


def test_parse_json_skips_example_items():
    payload = json.dumps([
        {"name": "[示例] one", "_example": True, "source_type": "kb"},
        {"name": "real", "source_type": "annotation"},
    ])
    out = _parse_import_payload("json", payload)
    assert len(out) == 1
    assert out[0]["name"] == "real"


def test_parse_json_strips_underscore_meta():
    payload = json.dumps([{
        "name": "x",
        "_example": False,
        "_comment": "should not leak",
        "source_type": "kb",
    }])
    out = _parse_import_payload("json", payload)
    assert "_example" not in out[0]
    assert "_comment" not in out[0]


def test_parse_json_invalid_raises_400():
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _parse_import_payload("json", "{not json")


# ---------------------------------------------------------------------------
# _parse_import_payload (CSV)
# ---------------------------------------------------------------------------

CSV_TEMPLATE = """# 顶部注释
# 这行也会被忽略
name,description,scope_type,source_type,mount_modes,kb_name
"[示例] sample row","skip me",user,kb,corpus,legal_kb
real mount,real,user,annotation,"context,fewshot",
"""


def test_parse_csv_skips_comment_and_sample_rows():
    out = _parse_import_payload("csv", CSV_TEMPLATE)
    assert len(out) == 1
    real = out[0]
    assert real["name"] == "real mount"
    assert real["mount_modes"] == ["context", "fewshot"]
    # source_type 走 source_filter.spec.source_type
    assert real["source_filter"]["spec"]["source_type"] == "annotation"


def test_parse_csv_unrecognized_columns_become_options():
    csv_text = (
        "name,source_type,mount_modes,kb_name,query,custom_field\n"
        "x,kb,corpus,my_kb,my query,extra value\n"
    )
    out = _parse_import_payload("csv", csv_text)
    spec = out[0]["source_filter"]["spec"]
    # kb_name / query / custom_field 这些非保留字 → spec.options
    assert spec["options"]["kb_name"] == "my_kb"
    assert spec["options"]["query"] == "my query"
    assert spec["options"]["custom_field"] == "extra value"


def test_parse_csv_underscore_columns_excluded_from_options():
    csv_text = (
        "name,source_type,mount_modes,_example,_comment,kb_name\n"
        "real,kb,corpus,,,my_kb\n"
    )
    out = _parse_import_payload("csv", csv_text)
    assert len(out) == 1
    options = out[0]["source_filter"]["spec"]["options"]
    assert "_example" not in options
    assert "_comment" not in options
    assert options["kb_name"] == "my_kb"


def test_parse_unsupported_format_raises():
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _parse_import_payload("xml", "<doc/>")
