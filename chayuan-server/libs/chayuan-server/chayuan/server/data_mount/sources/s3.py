"""S3 / MinIO 数据源 —— 走 langchain S3FileLoader / S3DirectoryLoader。"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List

from chayuan.server.data_mount.base import (
    DocumentRecord, ProbeResult, SampleResult, SourceSpec,
)
from chayuan.server.data_mount.schema_analyzer import analyze_schema
from chayuan.server.data_mount.sources._helpers import langchain_doc_to_record

logger = logging.getLogger("chayuan.data_mount.sources.s3")


class S3Source:
    type_id = "s3"
    label = "S3 / MinIO"
    description = "AWS S3 或 S3 兼容(MinIO / 阿里 OSS endpoint);可挂前缀整目录"
    icon = "cloud"
    capabilities = ["corpus", "context"]

    def spec_form(self) -> Dict[str, Any]:
        return {"fields": [
            {"name": "bucket", "label": "Bucket", "type": "string", "required": True},
            {"name": "prefix", "label": "前缀(可选)", "type": "string", "default": ""},
            {"name": "endpoint_url", "label": "Endpoint URL", "type": "string",
             "default": "", "help": "MinIO / OSS 必填;原生 S3 留空"},
            {"name": "access_key", "label": "Access Key", "type": "string", "default": ""},
            {"name": "secret_key", "label": "Secret Key", "type": "password", "default": ""},
        ]}

    def _client(self, spec: SourceSpec):
        import boto3  # type: ignore

        opts = spec.options
        return boto3.client(
            "s3",
            endpoint_url=(opts.get("endpoint_url") or "").strip() or None,
            aws_access_key_id=(opts.get("access_key") or "").strip() or None,
            aws_secret_access_key=(opts.get("secret_key") or "").strip() or None,
        )

    def probe(self, spec: SourceSpec) -> ProbeResult:
        bucket = (spec.options.get("bucket") or "").strip()
        if not bucket:
            return ProbeResult(status="error", message="缺 bucket")
        try:
            cli = self._client(spec)
            cli.head_bucket(Bucket=bucket)
            resp = cli.list_objects_v2(
                Bucket=bucket,
                Prefix=(spec.options.get("prefix") or "").strip(),
                MaxKeys=1,
            )
            return ProbeResult(
                status="ok",
                message=f"bucket {bucket} 可访问",
                counted=resp.get("KeyCount") or 0,
            )
        except Exception as e:  # noqa: BLE001
            return ProbeResult(status="error", message=f"S3 连接失败: {e}")

    def sample(self, spec: SourceSpec, n: int = 20) -> SampleResult:
        items = self._fetch(spec, limit=n)
        return SampleResult(items=items, fields=analyze_schema(items))

    async def load(self, spec: SourceSpec) -> AsyncIterator[DocumentRecord]:
        for rec in self._fetch(spec, limit=int(spec.max_items or 500)):
            yield rec

    def _fetch(self, spec: SourceSpec, *, limit: int) -> List[DocumentRecord]:
        try:
            from langchain_community.document_loaders import S3DirectoryLoader
        except ImportError:
            logger.warning("langchain_community.document_loaders.S3DirectoryLoader 不可用")
            return []
        opts = spec.options
        try:
            loader = S3DirectoryLoader(
                bucket=opts.get("bucket"),
                prefix=opts.get("prefix") or "",
                endpoint_url=opts.get("endpoint_url") or None,
                aws_access_key_id=opts.get("access_key") or None,
                aws_secret_access_key=opts.get("secret_key") or None,
            )
            docs = loader.load() or []
        except Exception as e:  # noqa: BLE001
            logger.warning("S3 load 失败: %s", e)
            return []
        return [langchain_doc_to_record(d) for d in docs[:limit]]


ADAPTER = S3Source
