from __future__ import annotations

from chayuan.server.kb_query.authz import Subject
from chayuan.server.kb_query.schemas import SearchRequest
from chayuan.server.kb_query import service


def test_search_generates_stable_unique_content_ids(monkeypatch):
    def fake_unified_search(_subject, req, *, request_id="", job_id=None):
        return {
            "code": 0,
            "msg": "ok",
            "data": {
                "items": [
                    {
                        "content_id": content.content_id or f"item_{idx + 1}",
                        "query": content.text,
                        "results": [],
                    }
                    for idx, content in enumerate(req.contents)
                ]
            },
        }

    monkeypatch.setattr(service, "unified_search", fake_unified_search)

    req = SearchRequest(
        kb_id="doc:samples",
        contents=[
            {"text": "same query"},
            {"text": "same query"},
        ],
        options={"return_diagnostics": True},
    )
    result = service.search(Subject(user=None, mode="guest"), req)

    items = result["data"]["items"]
    assert [item["content_id"] for item in items] == ["item_1", "item_2"]
    assert [item["query"] for item in items] == ["same query", "same query"]
