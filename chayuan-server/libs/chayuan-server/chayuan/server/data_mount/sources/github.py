"""GitHub 仓库数据源 —— 走 langchain GitLoader。"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List

from chayuan.server.data_mount.base import (
    DocumentRecord, ProbeResult, SampleResult, SourceSpec,
)
from chayuan.server.data_mount.schema_analyzer import analyze_schema
from chayuan.server.data_mount.sources._helpers import langchain_doc_to_record

logger = logging.getLogger("chayuan.data_mount.sources.github")


class GitHubSource:
    type_id = "github"
    label = "Git 仓库"
    description = "git clone 后用 GitLoader 取代码 / 文档;支持后缀过滤"
    icon = "git-branch"
    capabilities = ["corpus", "context"]

    def spec_form(self) -> Dict[str, Any]:
        return {"fields": [
            {"name": "repo_url", "label": "仓库 URL", "type": "string", "required": True,
             "help": "https / ssh 都行,需要本机有 git"},
            {"name": "branch", "label": "分支", "type": "string", "default": "main"},
            {"name": "include_extensions", "label": "包含后缀(逗号分隔)",
             "type": "string", "default": ".py,.md,.tsx,.ts,.json"},
        ]}

    def probe(self, spec: SourceSpec) -> ProbeResult:
        if not spec.options.get("repo_url"):
            return ProbeResult(status="error", message="缺 repo_url")
        return ProbeResult(status="ok", message="配置完整(clone 在 load 阶段)")

    def sample(self, spec: SourceSpec, n: int = 20) -> SampleResult:
        items = self._fetch(spec, limit=n)
        return SampleResult(items=items, fields=analyze_schema(items))

    async def load(self, spec: SourceSpec) -> AsyncIterator[DocumentRecord]:
        for rec in self._fetch(spec, limit=int(spec.max_items or 500)):
            yield rec

    def _fetch(self, spec: SourceSpec, *, limit: int) -> List[DocumentRecord]:
        try:
            from langchain_community.document_loaders import GitLoader
        except ImportError:
            logger.warning("GitLoader 不可用 (pip install GitPython)")
            return []
        opts = spec.options
        exts = {x.strip() for x in (opts.get("include_extensions") or "").split(",") if x.strip()}
        tmp = tempfile.mkdtemp(prefix="chayuan-git-")
        try:
            loader = GitLoader(
                clone_url=opts["repo_url"],
                repo_path=tmp,
                branch=opts.get("branch") or "main",
                file_filter=(lambda fp: (Path(fp).suffix in exts)) if exts else None,
            )
            docs = loader.load() or []
        except Exception as e:  # noqa: BLE001
            logger.warning("GitLoader 失败: %s", e)
            shutil.rmtree(tmp, ignore_errors=True)
            return []
        out = [langchain_doc_to_record(d) for d in docs[:limit]]
        # 不删除临时目录;materialize 完后由 GC / OS 回收
        # (若立即删除,后续 GitLoader 缓存 / commit 信息会失效)
        return out


ADAPTER = GitHubSource
