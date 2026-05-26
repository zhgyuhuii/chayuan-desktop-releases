"""标注样本数据源 —— 把已通过的 annotation samples 当成挂载源。

把历史 annotation/datasets/mount 的逻辑封进来,让"训练数据中心"两条入口
(原有"挂载筛选"按钮 + 新"数据挂载"tab)走同一套源适配协议。
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List

from chayuan.server.data_mount.base import (
    DocumentRecord, ProbeResult, SampleResult, SourceSpec,
)
from chayuan.server.data_mount.schema_analyzer import analyze_schema

logger = logging.getLogger("chayuan.data_mount.sources.annotation")


class AnnotationSource:
    type_id = "annotation"
    label = "标注样本"
    description = "训练数据中心已通过的标注任务样本(默认 status=approved)"
    icon = "tag"
    capabilities = ["context", "fewshot", "preference", "safety"]

    def spec_form(self) -> Dict[str, Any]:
        return {"fields": [
            {"name": "task_type", "label": "任务类型", "type": "string", "default": "",
             "help": "留空 = 全部任务类型"},
            {"name": "status", "label": "状态", "type": "select", "default": "approved",
             "options": [
                 {"value": "approved", "label": "已通过"},
                 {"value": "any", "label": "全部"},
             ]},
            {"name": "task_ids", "label": "任务 ID 列表(逗号分隔)", "type": "string",
             "default": ""},
        ]}

    def _query_filter(self, spec: SourceSpec) -> Dict[str, Any]:
        opts = spec.options
        task_ids = [x.strip() for x in (opts.get("task_ids") or "").split(",") if x.strip()]
        return {
            "task_type": (opts.get("task_type") or "").strip() or None,
            "status": opts.get("status") or "approved",
            "task_ids": task_ids,
        }

    def probe(self, spec: SourceSpec) -> ProbeResult:
        try:
            from chayuan.server.db.session import session_scope
            from chayuan.server.db.models.annotation_model import AnnotationTaskModel
        except Exception as e:  # noqa: BLE001
            return ProbeResult(status="error", message=f"annotation 模型加载失败: {e}")
        f = self._query_filter(spec)
        try:
            with session_scope() as session:
                q = session.query(AnnotationTaskModel)
                if f["status"] and f["status"] != "any":
                    q = q.filter(AnnotationTaskModel.status == f["status"])
                if f["task_type"]:
                    q = q.filter(AnnotationTaskModel.task_type == f["task_type"])
                if f["task_ids"]:
                    q = q.filter(AnnotationTaskModel.id.in_(f["task_ids"]))
                count = q.count()
            return ProbeResult(status="ok" if count else "warning",
                               message=f"匹配 {count} 条标注样本", counted=count)
        except Exception as e:  # noqa: BLE001
            return ProbeResult(status="error", message=f"查询失败: {e}")

    def sample(self, spec: SourceSpec, n: int = 20) -> SampleResult:
        items = list(self._fetch(spec, limit=n))
        return SampleResult(items=items, fields=analyze_schema(items))

    async def load(self, spec: SourceSpec) -> AsyncIterator[DocumentRecord]:
        for rec in self._fetch(spec, limit=int(spec.max_items or 1000)):
            yield rec

    def _fetch(self, spec: SourceSpec, *, limit: int) -> List[DocumentRecord]:
        try:
            from chayuan.server.db.session import session_scope
            from chayuan.server.db.models.annotation_model import AnnotationTaskModel
        except Exception as e:  # noqa: BLE001
            logger.warning("annotation models import failed: %s", e)
            return []
        f = self._query_filter(spec)
        out: List[DocumentRecord] = []
        try:
            with session_scope() as session:
                q = session.query(AnnotationTaskModel)
                if f["status"] and f["status"] != "any":
                    q = q.filter(AnnotationTaskModel.status == f["status"])
                if f["task_type"]:
                    q = q.filter(AnnotationTaskModel.task_type == f["task_type"])
                if f["task_ids"]:
                    q = q.filter(AnnotationTaskModel.id.in_(f["task_ids"]))
                q = q.limit(limit)
                for row in q.all():
                    md: Dict[str, Any] = {}
                    sample = getattr(row, "sample", None) or {}
                    if isinstance(sample, dict):
                        md.update(sample)
                    labels = getattr(row, "labels", None) or {}
                    if isinstance(labels, dict):
                        md["labels"] = labels
                    md["task_type"] = getattr(row, "task_type", "")
                    md["status"] = getattr(row, "status", "")
                    text = ""
                    if isinstance(sample, dict):
                        text = str(sample.get("text") or sample.get("query") or "") or ""
                    out.append(DocumentRecord(
                        text=text or md.get("query") or "",
                        metadata=md,
                        id=str(getattr(row, "id", "") or ""),
                    ))
        except Exception as e:  # noqa: BLE001
            logger.warning("annotation fetch failed: %s", e)
        return out


ADAPTER = AnnotationSource
