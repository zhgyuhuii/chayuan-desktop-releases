"""Web URL 数据源 —— 用 langchain WebBaseLoader / RecursiveUrlLoader。"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List

from chayuan.server.data_mount.base import (
    DocumentRecord, ProbeResult, SampleResult, SourceSpec,
)
from chayuan.server.data_mount.schema_analyzer import analyze_schema
from chayuan.server.data_mount.sources._helpers import langchain_doc_to_record

logger = logging.getLogger("chayuan.data_mount.sources.web")


class WebSource:
    type_id = "web"
    label = "Web 网页"
    description = "URL 抓取(单页 / 站点递归);走 langchain WebBaseLoader"
    icon = "globe"
    capabilities = ["corpus", "context"]

    def spec_form(self) -> Dict[str, Any]:
        return {"fields": [
            {"name": "urls", "label": "URL(逗号或换行分隔)", "type": "string", "required": True},
            {"name": "recursive", "label": "递归整站", "type": "bool", "default": False,
             "help": "开启后走 RecursiveUrlLoader,只允许同 origin"},
            {"name": "max_depth", "label": "递归深度", "type": "int", "default": 2},
        ]}

    def _urls(self, spec: SourceSpec) -> List[str]:
        raw = (spec.options.get("urls") or "").strip()
        if not raw:
            return []
        urls = [x.strip() for x in raw.replace("\n", ",").split(",") if x.strip()]
        return urls

    def probe(self, spec: SourceSpec) -> ProbeResult:
        urls = self._urls(spec)
        if not urls:
            return ProbeResult(status="error", message="缺 URL")
        # 简单 HEAD 探一下
        import urllib.request
        ok = 0
        for u in urls[:3]:
            try:
                req = urllib.request.Request(u, method="HEAD")
                urllib.request.urlopen(req, timeout=4.0)  # noqa: S310
                ok += 1
            except Exception:  # noqa: BLE001
                pass
        return ProbeResult(
            status="ok" if ok else "warning",
            message=f"前 3 个 URL 探活成功 {ok}/{min(len(urls), 3)}",
            counted=len(urls),
        )

    def sample(self, spec: SourceSpec, n: int = 20) -> SampleResult:
        items = self._load(spec, max_items=n)
        return SampleResult(items=items, fields=analyze_schema(items))

    async def load(self, spec: SourceSpec) -> AsyncIterator[DocumentRecord]:
        for rec in self._load(spec, max_items=int(spec.max_items or 200)):
            yield rec

    def _load(self, spec: SourceSpec, *, max_items: int) -> List[DocumentRecord]:
        urls = self._urls(spec)
        if not urls:
            return []
        recursive = bool(spec.options.get("recursive") or False)
        max_depth = int(spec.options.get("max_depth") or 2)
        out: List[DocumentRecord] = []
        try:
            if recursive:
                from langchain_community.document_loaders import RecursiveUrlLoader
                for u in urls:
                    if len(out) >= max_items:
                        break
                    loader = RecursiveUrlLoader(url=u, max_depth=max_depth)
                    docs = loader.load() or []
                    for d in docs:
                        if len(out) >= max_items:
                            break
                        out.append(langchain_doc_to_record(d))
            else:
                from langchain_community.document_loaders import WebBaseLoader
                loader = WebBaseLoader(urls)
                docs = loader.load() or []
                for d in docs[:max_items]:
                    out.append(langchain_doc_to_record(d))
        except ImportError:
            logger.warning("langchain_community.document_loaders 未安装,Web 源不可用")
        except Exception as e:  # noqa: BLE001
            logger.warning("WebSource load 失败: %s", e)
        return out


ADAPTER = WebSource
