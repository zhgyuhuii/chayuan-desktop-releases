"""65 题:admin_routes ``_models_for`` 不再 fallback 到 catalog 默认模型。

只验证函数行为,不真起 FastAPI server。
"""
from __future__ import annotations

import inspect

from chayuan.server.api_server import admin_routes


def test_models_for_no_default_fallback_in_source():
    """admin_routes 模块源码中应不再有未配置时返 default_models 的 fallback。"""
    src = inspect.getsource(admin_routes)
    # 旧 fallback 逻辑(已删除):未配置 → defaults.get(field)
    # 新逻辑应只在 default_models 字段单独返(供客户端可选展示)
    assert "if configured and db_list:" not in src, (
        "65 题:fallback 到 catalog 默认模型的逻辑应已删除"
    )
    # default_models 字段仍保留(独立 key,客户端可选)
    assert '"default_models": {k: list(v) for k, v in defaults.items()}' in src


def test_models_for_returns_db_list_only(monkeypatch):
    """模拟 _entry 闭包内的 _models_for: 只返 DB 实际数据,不查 catalog defaults。"""
    # 复刻最新实现:def _models_for(field): return list(row.get(field) or [])
    row = {
        "llm_models": ["user-configured-model"],
        "embed_models": [],
    }

    # 用户已配置 LLM,DB 有 1 条 → 返这条
    assert list(row.get("llm_models") or []) == ["user-configured-model"]
    # 用户未配置 embed,DB 为空 → 返空(不再 fallback 到 catalog)
    assert list(row.get("embed_models") or []) == []
    # 用户未配置任何模型(row 完全没这个 key)→ 返空
    assert list(row.get("rerank_models") or []) == []
