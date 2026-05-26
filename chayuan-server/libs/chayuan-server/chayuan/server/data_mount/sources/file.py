"""文件 / 文件夹数据源 —— 复用 file_rag.document_loaders。"""
from __future__ import annotations

import glob as _glob
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List

from chayuan.server.data_mount.base import (
    DocumentRecord, ProbeResult, SampleResult, SourceSpec,
)
from chayuan.server.data_mount.schema_analyzer import analyze_schema
from chayuan.server.data_mount.sources._helpers import langchain_doc_to_record, truncate_text

logger = logging.getLogger("chayuan.data_mount.sources.file")


# 后缀 → loader 选择;复用 file_rag 内置的中文友好 loader
_LOADER_BY_EXT: Dict[str, str] = {
    ".pdf":  "chayuan.server.file_rag.document_loaders.mypdfloader.RapidOCRPDFLoader",
    ".doc":  "chayuan.server.file_rag.document_loaders.mydocloader.RapidOCRDocLoader",
    ".docx": "chayuan.server.file_rag.document_loaders.mydocloader.RapidOCRDocLoader",
    ".ppt":  "chayuan.server.file_rag.document_loaders.mypptloader.RapidOCRPPTLoader",
    ".pptx": "chayuan.server.file_rag.document_loaders.mypptloader.RapidOCRPPTLoader",
    ".png":  "chayuan.server.file_rag.document_loaders.myimgloader.RapidOCRLoader",
    ".jpg":  "chayuan.server.file_rag.document_loaders.myimgloader.RapidOCRLoader",
    ".jpeg": "chayuan.server.file_rag.document_loaders.myimgloader.RapidOCRLoader",
    ".csv":  "langchain_community.document_loaders.csv_loader.CSVLoader",
    ".tsv":  "langchain_community.document_loaders.csv_loader.CSVLoader",
    ".json": "langchain_community.document_loaders.JSONLoader",
    ".jsonl":"langchain_community.document_loaders.JSONLoader",
    ".md":   "langchain_community.document_loaders.UnstructuredMarkdownLoader",
    ".txt":  "langchain_community.document_loaders.TextLoader",
    ".html": "langchain_community.document_loaders.UnstructuredHTMLLoader",
}


def _import(qualname: str) -> Any:
    mod_name, _, cls_name = qualname.rpartition(".")
    mod = __import__(mod_name, fromlist=[cls_name])
    return getattr(mod, cls_name)


class FileSource:
    type_id = "file"
    label = "文件 / 文件夹"
    description = "本地路径或 glob 模式;PDF/Doc/PPT/CSV/MD/TXT/HTML/Image 自动分发到对应 loader"
    icon = "folder"
    capabilities = ["corpus", "context", "fewshot"]

    def spec_form(self) -> Dict[str, Any]:
        return {"fields": [
            {"name": "path", "label": "路径或 glob", "type": "string", "required": True,
             "help": "如 /data/docs 或 /data/**/*.pdf;支持单文件或目录"},
            {"name": "recursive", "label": "递归子目录", "type": "bool", "default": True},
        ]}

    def _expand(self, spec: SourceSpec) -> List[Path]:
        path = (spec.options.get("path") or "").strip()
        if not path:
            return []
        recursive = bool(spec.options.get("recursive") if "recursive" in spec.options else True)
        p = Path(path)
        # glob 模式
        if any(ch in path for ch in ("*", "?", "[")):
            return [Path(x) for x in _glob.glob(path, recursive=recursive)]
        if p.is_file():
            return [p]
        if p.is_dir():
            pattern = "**/*" if recursive else "*"
            return [x for x in p.glob(pattern) if x.is_file()]
        return []

    def probe(self, spec: SourceSpec) -> ProbeResult:
        files = self._expand(spec)
        if not files:
            return ProbeResult(status="error",
                               message=f"路径 '{spec.options.get('path')}' 没匹配到文件")
        recognized = sum(1 for f in files if f.suffix.lower() in _LOADER_BY_EXT)
        return ProbeResult(
            status="ok" if recognized else "warning",
            message=f"匹配 {len(files)} 个文件,其中 {recognized} 个有对应 loader",
            counted=len(files),
            extra={"recognized": recognized},
        )

    def sample(self, spec: SourceSpec, n: int = 20) -> SampleResult:
        items: List[DocumentRecord] = []
        for f in self._expand(spec):
            if len(items) >= n:
                break
            items.extend(self._load_one(f, max_items=n - len(items)))
        return SampleResult(
            items=items,
            total_estimate=None,
            fields=analyze_schema(items),
        )

    async def load(self, spec: SourceSpec) -> AsyncIterator[DocumentRecord]:
        max_items = int(spec.max_items or 1000)
        emitted = 0
        for f in self._expand(spec):
            if emitted >= max_items:
                return
            for rec in self._load_one(f, max_items=max_items - emitted):
                yield rec
                emitted += 1
                if emitted >= max_items:
                    return

    # ---- internal ------------------------------------------------------

    def _load_one(self, f: Path, *, max_items: int) -> List[DocumentRecord]:
        ext = f.suffix.lower()
        loader_qual = _LOADER_BY_EXT.get(ext)
        if not loader_qual:
            # 不识别的:按纯文本兜底
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                return [DocumentRecord(
                    text=truncate_text(text, 4000),
                    metadata={"source": str(f), "ext": ext},
                    id=str(f),
                )]
            except OSError:
                return []
        try:
            cls = _import(loader_qual)
            loader = cls(str(f))
            docs = loader.load() or []
        except Exception as e:  # noqa: BLE001
            logger.warning("loader %s 处理 %s 失败: %s", loader_qual, f, e)
            return []
        out: List[DocumentRecord] = []
        for d in docs[:max_items]:
            rec = langchain_doc_to_record(d, default_id=str(f))
            rec.metadata.setdefault("source", str(f))
            rec.metadata.setdefault("ext", ext)
            out.append(rec)
        return out


ADAPTER = FileSource
