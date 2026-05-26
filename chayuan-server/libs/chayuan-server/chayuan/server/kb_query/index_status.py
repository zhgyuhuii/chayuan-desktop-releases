from __future__ import annotations

import os
from typing import Any, Dict


def document_index_status(kb_name: str) -> Dict[str, Any]:
    """Infer document KB index status from existing file metadata.

    This is intentionally conservative: it does not require a vector-store
    connection, so the list API remains useful when Milvus/PG/ES is down.
    """
    try:
        from chayuan.server.db.repository.knowledge_file_repository import get_file_detail
        from chayuan.server.knowledge_base.utils import get_file_path, list_files_from_folder

        folder_files = list_files_from_folder(kb_name)
        indexed = 0
        stale = 0
        failed = 0
        missing_in_db = 0

        for file_name in folder_files:
            detail = get_file_detail(kb_name=kb_name, filename=file_name)
            if not detail:
                missing_in_db += 1
                continue
            indexed += 1
            docs_count = int(detail.get("docs_count") or 0)
            if docs_count <= 0:
                failed += 1
            path = get_file_path(kb_name, file_name)
            try:
                if os.path.exists(path):
                    current_mtime = float(os.path.getmtime(path) or 0)
                    current_size = int(os.path.getsize(path) or 0)
                    db_mtime = float(detail.get("file_mtime") or 0)
                    db_size = int(detail.get("file_size") or 0)
                    if db_mtime and abs(current_mtime - db_mtime) > 1:
                        stale += 1
                    elif db_size and current_size != db_size:
                        stale += 1
            except OSError:
                stale += 1

        total = len(folder_files)
        if total == 0:
            status = "empty"
            reason = "knowledge base has no files"
        elif missing_in_db > 0:
            status = "indexing"
            reason = f"{missing_in_db} file(s) are not indexed yet"
        elif failed > 0:
            status = "failed"
            reason = f"{failed} indexed file(s) have zero chunks"
        elif stale > 0:
            status = "stale"
            reason = f"{stale} file(s) changed after indexing"
        else:
            status = "ready"
            reason = "all files are indexed"

        return {
            "status": status,
            "reason": reason,
            "total_files": total,
            "indexed_files": indexed,
            "missing_in_db": missing_in_db,
            "failed_files": failed,
            "stale_files": stale,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "status": "unknown",
            "reason": f"index status unavailable: {type(e).__name__}: {e}",
            "total_files": 0,
            "indexed_files": 0,
            "missing_in_db": 0,
            "failed_files": 0,
            "stale_files": 0,
        }

