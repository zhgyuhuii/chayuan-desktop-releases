from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException

from chayuan.server.kb_query.authz import Subject
from chayuan.server.kb_query.schemas import SearchRequest
from chayuan.server.kb_query.service import search


_LOCK = threading.RLock()
_JOBS: Dict[str, Dict[str, Any]] = {}


def _job_root() -> Path:
    try:
        from chayuan.settings import CHAYUAN_ROOT

        root = Path(CHAYUAN_ROOT)
    except Exception:
        root = Path.cwd()
    path = root / "kb_query_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _result_path(job_id: str) -> Path:
    return _job_root() / f"{job_id}.json"


def _input_path(job_id: str) -> Path:
    return _job_root() / f"{job_id}.ndjson"


def _jsonl_result_path(job_id: str) -> Path:
    return _job_root() / f"{job_id}.results.jsonl"


def _now_ms() -> int:
    return int(time.time() * 1000)


def create_job(subject: Subject, req: SearchRequest, *, request_id: str = "") -> Dict[str, Any]:
    job_id = f"kbq_{uuid.uuid4().hex}"
    meta = {
        "job_id": job_id,
        "status": "queued",
        "request_id": request_id,
        "created_at_ms": _now_ms(),
        "updated_at_ms": _now_ms(),
        "content_count": len(req.contents),
        "processed_count": 0,
        "user_id": subject.user_id,
        "job_type": "json",
        "cancel_requested": False,
        "error": "",
        "result_path": str(_result_path(job_id)),
    }
    with _LOCK:
        _JOBS[job_id] = meta
    return dict(meta)


def create_ndjson_job(
    subject: Subject,
    *,
    input_path: Path,
    content_count: int,
    request_id: str = "",
) -> Dict[str, Any]:
    job_id = input_path.stem
    meta = {
        "job_id": job_id,
        "status": "queued",
        "request_id": request_id,
        "created_at_ms": _now_ms(),
        "updated_at_ms": _now_ms(),
        "content_count": int(content_count),
        "processed_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "user_id": subject.user_id,
        "job_type": "ndjson",
        "cancel_requested": False,
        "error": "",
        "input_path": str(input_path),
        "result_path": str(_jsonl_result_path(job_id)),
    }
    with _LOCK:
        _JOBS[job_id] = meta
    return dict(meta)


def allocate_ndjson_input_path() -> tuple[str, Path]:
    job_id = f"kbq_{uuid.uuid4().hex}"
    return job_id, _input_path(job_id)


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            return dict(job)

    json_path = _result_path(job_id)
    jsonl_path = _jsonl_result_path(job_id)
    if not json_path.exists() and not jsonl_path.exists():
        return None
    try:
        if json_path.exists():
            data = json.loads(json_path.read_text(encoding="utf-8"))
            meta = data.get("job") or {}
            return dict(meta) if meta else None
    except Exception:
        return None
    if jsonl_path.exists():
        return {
            "job_id": job_id,
            "status": "succeeded",
            "job_type": "ndjson",
            "result_path": str(jsonl_path),
        }
    return None


def get_job_result(job_id: str) -> Optional[Dict[str, Any]]:
    path = _result_path(job_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"job": get_job(job_id) or {"job_id": job_id}, "error": str(e)}


def get_job_jsonl_path(job_id: str) -> Optional[Path]:
    path = _jsonl_result_path(job_id)
    return path if path.exists() else None


def _persist_job_meta(job_id: str, meta: Dict[str, Any]) -> None:
    _result_path(job_id).write_text(
        json.dumps({"job": dict(meta)}, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def cancel_job(subject: Subject, job_id: str) -> Dict[str, Any]:
    job = assert_job_access(subject, job_id)
    status = str(job.get("status") or "")
    if status in ("succeeded", "failed", "cancelled"):
        return job
    with _LOCK:
        meta = _JOBS.get(job_id, job)
        meta.update(
            {
                "cancel_requested": True,
                "status": "cancelling" if status == "running" else "cancelled",
                "updated_at_ms": _now_ms(),
            }
        )
        _JOBS[job_id] = meta
        out = dict(meta)
    _persist_job_meta(job_id, out)
    return out


def _cancel_requested(job_id: str) -> bool:
    with _LOCK:
        return bool((_JOBS.get(job_id) or {}).get("cancel_requested"))


def assert_job_access(subject: Subject, job_id: str) -> Dict[str, Any]:
    """Only the creator (or admin) can inspect a job.

    Guest jobs cannot be tied to an account. They are still protected by the
    unguessable job id, but once a token-bound subject is present we enforce
    ownership strictly.
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    owner_id = job.get("user_id")
    if subject.is_admin:
        return job
    if owner_id is None and subject.user_id is None:
        return job
    if owner_id is not None and subject.user_id == int(owner_id):
        return job
    raise HTTPException(403, "permission denied")


def run_job(job_id: str, subject: Subject, req: SearchRequest) -> None:
    with _LOCK:
        if job_id in _JOBS:
            if _JOBS[job_id].get("cancel_requested"):
                _JOBS[job_id]["status"] = "cancelled"
                _JOBS[job_id]["updated_at_ms"] = _now_ms()
                _persist_job_meta(job_id, _JOBS[job_id])
                return
            _JOBS[job_id]["status"] = "running"
            _JOBS[job_id]["updated_at_ms"] = _now_ms()

    try:
        request_id = str((_JOBS.get(job_id) or {}).get("request_id") or "")
        result = search(subject, req, request_id=request_id, job_id=job_id)
        with _LOCK:
            meta = _JOBS.get(job_id, {"job_id": job_id})
            if meta.get("cancel_requested"):
                meta.update({"status": "cancelled", "updated_at_ms": _now_ms()})
                _JOBS[job_id] = meta
                out = {"job": dict(meta), "result": result}
                _result_path(job_id).write_text(
                    json.dumps(out, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                return
            meta.update(
                {
                    "status": "succeeded",
                    "updated_at_ms": _now_ms(),
                    "success_count": result.get("data", {}).get("usage", {}).get("success_count", 0),
                    "failed_count": result.get("data", {}).get("usage", {}).get("failed_count", 0),
                }
            )
            _JOBS[job_id] = meta
            out = {"job": dict(meta), "result": result}
        _result_path(job_id).write_text(
            json.dumps(out, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001
        with _LOCK:
            meta = _JOBS.get(job_id, {"job_id": job_id})
            meta.update({"status": "failed", "updated_at_ms": _now_ms(), "error": str(e)})
            _JOBS[job_id] = meta
            out = {"job": dict(meta), "error": str(e)}
        _result_path(job_id).write_text(
            json.dumps(out, ensure_ascii=False, default=str),
            encoding="utf-8",
        )


def _request_from_ndjson_line(obj: Dict[str, Any]) -> SearchRequest:
    text = str(obj.get("text") or obj.get("query") or obj.get("content") or "")
    content_id = obj.get("content_id") or obj.get("id")
    metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    return SearchRequest(
        knowledge_base=obj.get("knowledge_base"),
        kb_id=obj.get("kb_id"),
        contents=[
            {
                "content_id": str(content_id) if content_id is not None else None,
                "text": text,
                "metadata": metadata,
            }
        ],
        options=obj.get("options") if isinstance(obj.get("options"), dict) else {},
    )


def run_ndjson_job(job_id: str, subject: Subject, input_path: str) -> None:
    with _LOCK:
        if job_id in _JOBS:
            if _JOBS[job_id].get("cancel_requested"):
                _JOBS[job_id]["status"] = "cancelled"
                _JOBS[job_id]["updated_at_ms"] = _now_ms()
                _persist_job_meta(job_id, _JOBS[job_id])
                return
            _JOBS[job_id]["status"] = "running"
            _JOBS[job_id]["updated_at_ms"] = _now_ms()

    request_id = str((_JOBS.get(job_id) or {}).get("request_id") or "")
    result_path = _jsonl_result_path(job_id)
    success_count = 0
    failed_count = 0
    processed_count = 0

    try:
        with Path(input_path).open("r", encoding="utf-8") as src, result_path.open(
            "w", encoding="utf-8"
        ) as out:
            for line_no, line in enumerate(src, start=1):
                if _cancel_requested(job_id):
                    out.write(
                        json.dumps(
                            {
                                "line_no": line_no,
                                "ok": False,
                                "cancelled": True,
                                "message": "job cancelled",
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    break
                line = line.strip()
                if not line:
                    continue
                processed_count += 1
                try:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        raise ValueError("each NDJSON line must be an object")
                    req = _request_from_ndjson_line(obj)
                    result = search(subject, req, request_id=request_id, job_id=job_id)
                    ok = result.get("data", {}).get("usage", {}).get("failed_count", 0) == 0
                    if ok:
                        success_count += 1
                    else:
                        failed_count += 1
                    row = {"line_no": line_no, "ok": ok, "result": result}
                except Exception as e:  # noqa: BLE001
                    failed_count += 1
                    row = {
                        "line_no": line_no,
                        "ok": False,
                        "error": {"code": type(e).__name__, "message": str(e)},
                    }
                out.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                if processed_count % 20 == 0:
                    with _LOCK:
                        meta = _JOBS.get(job_id, {"job_id": job_id})
                        meta.update(
                            {
                                "processed_count": processed_count,
                                "success_count": success_count,
                                "failed_count": failed_count,
                                "updated_at_ms": _now_ms(),
                            }
                        )
                        _JOBS[job_id] = meta

        with _LOCK:
            meta = _JOBS.get(job_id, {"job_id": job_id})
            final_status = "cancelled" if meta.get("cancel_requested") else "succeeded"
            meta.update(
                {
                    "status": final_status,
                    "processed_count": processed_count,
                    "success_count": success_count,
                    "failed_count": failed_count,
                    "updated_at_ms": _now_ms(),
                    "result_path": str(result_path),
                }
            )
            _JOBS[job_id] = meta
            final_meta = dict(meta)
        _result_path(job_id).write_text(
            json.dumps(
                {
                    "job": final_meta,
                    "result": {"format": "jsonl", "path": str(result_path)},
                },
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )

    except Exception as e:  # noqa: BLE001
        with _LOCK:
            meta = _JOBS.get(job_id, {"job_id": job_id})
            meta.update({"status": "failed", "updated_at_ms": _now_ms(), "error": str(e)})
            _JOBS[job_id] = meta
            final_meta = dict(meta)
        result_path.write_text(
            json.dumps(
                {"line_no": 0, "ok": False, "error": {"code": type(e).__name__, "message": str(e)}},
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        _result_path(job_id).write_text(
            json.dumps(
                {
                    "job": final_meta,
                    "error": str(e),
                    "result": {"format": "jsonl", "path": str(result_path)},
                },
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )


def cleanup_expired_jobs(subject: Subject, *, older_than_hours: int = 24) -> Dict[str, Any]:
    if not subject.is_admin:
        raise HTTPException(403, "admin only")
    cutoff_ms = _now_ms() - max(1, int(older_than_hours)) * 3600 * 1000
    removed: list[str] = []

    with _LOCK:
        for job_id, meta in list(_JOBS.items()):
            if int(meta.get("updated_at_ms") or meta.get("created_at_ms") or 0) >= cutoff_ms:
                continue
            removed.append(job_id)
            _JOBS.pop(job_id, None)

    root = _job_root()
    for path in root.iterdir():
        try:
            if not path.is_file():
                continue
            if int(path.stat().st_mtime * 1000) >= cutoff_ms:
                continue
            stem = path.name.split(".", 1)[0]
            removed.append(stem)
            path.unlink(missing_ok=True)
        except Exception:
            continue

    return {"removed_count": len(set(removed)), "removed_job_ids": sorted(set(removed))}

