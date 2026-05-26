"""Arq 任务实现。

注意：
- 每个任务的第一个参数是 Arq 传入的 ``ctx``（同步降级时为 None）；
- 耗时操作（update_docs）里会同步做 embedding + 写向量库；
- 把进度 / 结果写回 Redis，供 `/knowledge_base/tasks/{id}` 查询。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("chayuan.ingest_queue.tasks")


def _write_status(task_id: str, patch: Dict[str, Any]) -> None:
    """任务运行中的状态写回 Redis，并 publish 到 pub/sub 频道。

    - key:    ``chayuan:ingest:task:{task_id}``  保存累积 state，供轮询
    - topic:  ``chayuan:ingest:task:{task_id}:events``  publish 本次 patch，供 SSE 订阅
    """
    try:
        from chayuan.server.ingest_queue.backend import local_patch_status
        local_patch_status(task_id, patch)
    except Exception:  # noqa: BLE001
        pass
    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("redis", "redis>=5.0,<6.0")
        import redis  # type: ignore
        from chayuan.settings import Settings
        url = (getattr(Settings.basic_settings, "REDIS_URL", "") or "").strip()
        if not url:
            return
        client = redis.Redis.from_url(url, decode_responses=True)
        key = f"chayuan:ingest:task:{task_id}"
        existing = client.get(key)
        state = {}
        if existing:
            try:
                state = json.loads(existing)
            except Exception:
                state = {}
        state.update(patch)
        client.set(key, json.dumps(state, ensure_ascii=False), ex=24 * 3600)
        try:
            client.publish(
                f"{key}:events",
                json.dumps({"task_id": task_id, "patch": patch, "state": state},
                           ensure_ascii=False, default=str),
            )
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        logger.debug("write task status failed: %r", e)


def _is_cancelled(task_id: str) -> bool:
    try:
        from chayuan.server.ingest_queue.backend import is_task_cancelled
        return is_task_cancelled(task_id)
    except Exception:
        return False


def _cancel_status(task_id: str, reason: str = "user_cancelled") -> Dict[str, Any]:
    _write_status(task_id, {
        "state": "cancelled",
        "reason": reason,
        "finished_at": int(time.time()),
    })
    return {"ok": False, "cancelled": True, "reason": reason}


def _dead_letter(task_id: str, name: str, payload: Dict[str, Any], error: str) -> None:
    try:
        from chayuan.server.ingest_queue.backend import _write_dead_letter
        _write_dead_letter(task_id, name, payload, error)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 任务函数
# ---------------------------------------------------------------------------

def _ingest_upload_docs_sync_impl(
    task_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """真正干活的同步实现；供 arq async 包装 与 降级路径 复用。"""
    from chayuan.server.knowledge_base.kb_doc_api import update_docs
    from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory

    kb_name = payload["kb_name"]
    file_names = payload.get("file_names") or []
    docs_dict = payload.get("docs") or {}
    file_hashes = payload.get("file_hashes") or {}
    start = time.time()

    _write_status(task_id, {
        "state": "running",
        "started_at": int(start),
        "kb_name": kb_name,
        "file_count": len(file_names),
    })

    kb = KBServiceFactory.get_service_by_name(kb_name)
    if kb is None:
        _write_status(task_id, {
            "state": "failed",
            "error": f"knowledge base not found: {kb_name}",
            "finished_at": int(time.time()),
        })
        return {"ok": False, "error": "kb not found"}

    if not file_names:
        _write_status(task_id, {
            "state": "failed",
            "error": "file_names is empty",
            "finished_at": int(time.time()),
        })
        return {"ok": False, "error": "file_names is empty"}

    try:
        failed = {}
        ok_count = 0
        for idx, file_name in enumerate(file_names, start=1):
            if _is_cancelled(task_id):
                return _cancel_status(task_id)
            _write_status(task_id, {
                "state": "running",
                "current_file": file_name,
                "processed": idx - 1,
                "file_count": len(file_names),
                "ok_count": ok_count,
                "failed_files": failed,
            })
            one_docs = {file_name: docs_dict.get(file_name)} if file_name in docs_dict else {}
            one_hashes = {file_name: file_hashes.get(file_name, "")}
            result = update_docs(
                knowledge_base_name=kb_name,
                file_names=[file_name],
                override_custom_docs=True,
                chunk_size=payload.get("chunk_size"),
                chunk_overlap=payload.get("chunk_overlap"),
                zh_title_enhance=payload.get("zh_title_enhance"),
                docs=json.dumps(one_docs, ensure_ascii=False),
                file_hashes=one_hashes,
                uploader_id=payload.get("user_id"),
                not_refresh_vs_cache=True,
            )
            one_failed = {}
            try:
                if hasattr(result, "data") and isinstance(result.data, dict):
                    one_failed = result.data.get("failed_files") or {}
            except Exception:  # noqa: BLE001
                pass
            if one_failed:
                failed.update(one_failed)
            else:
                ok_count += 1
            _write_status(task_id, {
                "state": "running",
                "current_file": file_name,
                "processed": idx,
                "file_count": len(file_names),
                "ok_count": ok_count,
                "failed_files": failed,
            })
        try:
            kb.save_vector_store()
        except Exception as e:  # noqa: BLE001
            logger.warning("save_vector_store failed (non-fatal): %r", e)

        _write_status(task_id, {
            "state": "success" if not failed else "partial_success",
            "finished_at": int(time.time()),
            "elapsed_s": round(time.time() - start, 2),
            "failed_files": failed,
            "file_count": len(file_names),
            "processed": len(file_names),
            "ok_count": ok_count,
        })
        return {"ok": not failed, "ok_count": ok_count, "failed_files": failed}
    except Exception as e:  # noqa: BLE001
        logger.exception("ingest task failed: %s", task_id)
        _write_status(task_id, {
            "state": "failed",
            "error": f"{type(e).__name__}: {e}",
            "finished_at": int(time.time()),
        })
        _dead_letter(task_id, "ingest_upload_docs", payload, f"{type(e).__name__}: {e}")
        return {"ok": False, "error": str(e)}


def _reindex_kb_files_sync_impl(
    task_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """删除指定文件旧向量后,基于源文件重新切片 + embedding + 写入向量库。"""
    from chayuan.server.knowledge_base.kb_doc_api import update_docs
    from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory

    kb_name = payload["kb_name"]
    file_names = payload.get("file_names") or []
    start = time.time()

    _write_status(task_id, {
        "state": "running",
        "kind": "reindex_kb_files",
        "started_at": int(start),
        "kb_name": kb_name,
        "file_count": len(file_names),
    })

    kb = KBServiceFactory.get_service_by_name(kb_name)
    if kb is None:
        _write_status(task_id, {
            "state": "failed",
            "error": f"knowledge base not found: {kb_name}",
            "finished_at": int(time.time()),
        })
        return {"ok": False, "error": "kb not found"}

    if not file_names:
        _write_status(task_id, {
            "state": "failed",
            "error": "file_names is empty",
            "finished_at": int(time.time()),
        })
        return {"ok": False, "error": "file_names is empty"}

    try:
        failed = {}
        ok_count = 0
        for idx, file_name in enumerate(file_names, start=1):
            if _is_cancelled(task_id):
                return _cancel_status(task_id)
            _write_status(task_id, {
                "state": "running",
                "current_file": file_name,
                "processed": idx - 1,
                "file_count": len(file_names),
                "ok_count": ok_count,
                "failed_files": failed,
            })
            result = update_docs(
                knowledge_base_name=kb_name,
                file_names=[file_name],
                override_custom_docs=True,
                chunk_size=payload.get("chunk_size"),
                chunk_overlap=payload.get("chunk_overlap"),
                zh_title_enhance=payload.get("zh_title_enhance"),
                docs="{}",
                not_refresh_vs_cache=True,
            )
            one_failed = {}
            try:
                if hasattr(result, "data") and isinstance(result.data, dict):
                    one_failed = result.data.get("failed_files") or {}
            except Exception:  # noqa: BLE001
                pass
            if one_failed:
                failed.update(one_failed)
            else:
                ok_count += 1
            _write_status(task_id, {
                "state": "running",
                "current_file": file_name,
                "processed": idx,
                "file_count": len(file_names),
                "ok_count": ok_count,
                "failed_files": failed,
            })
        try:
            kb.save_vector_store()
        except Exception as e:  # noqa: BLE001
            logger.warning("save_vector_store failed (non-fatal): %r", e)

        _write_status(task_id, {
            "state": "success" if not failed else "partial_success",
            "finished_at": int(time.time()),
            "elapsed_s": round(time.time() - start, 2),
            "failed_files": failed,
            "file_count": len(file_names),
            "processed": len(file_names),
            "ok_count": ok_count,
        })
        return {"ok": not failed, "ok_count": ok_count, "failed_files": failed}
    except Exception as e:  # noqa: BLE001
        logger.exception("reindex task failed: %s", task_id)
        _write_status(task_id, {
            "state": "failed",
            "error": f"{type(e).__name__}: {e}",
            "finished_at": int(time.time()),
        })
        _dead_letter(task_id, "reindex_kb_files", payload, f"{type(e).__name__}: {e}")
        return {"ok": False, "error": str(e)}


async def ingest_upload_docs(
    ctx: Optional[Dict[str, Any]], task_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    """Arq 入口：把同步实现丢到 executor 里跑，避免阻塞 worker 的 event loop。"""
    import asyncio

    return await asyncio.to_thread(_ingest_upload_docs_sync_impl, task_id, payload)


async def reindex_kb_files(
    ctx: Optional[Dict[str, Any]], task_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    import asyncio

    return await asyncio.to_thread(_reindex_kb_files_sync_impl, task_id, payload)


# ---------------------------------------------------------------------------
# N-3：RAPTOR / GraphRAG 异步构建任务
# ---------------------------------------------------------------------------

def _raptor_build_sync_impl(task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    _write_status(task_id, {
        "state": "running", "kind": "raptor_build",
        "started_at": int(time.time()),
        "kb_name": payload.get("kb_name", ""),
    })
    try:
        from chayuan.server.file_rag.raptor.builder import build_raptor_for_kb
        report = build_raptor_for_kb(
            kb_name=payload["kb_name"],
            target_cluster_size=int(payload.get("target_cluster_size") or 5),
            max_levels=int(payload.get("max_levels") or 3),
            llm_model=payload.get("llm_model"),
        )
        ok = not report.error
        _write_status(task_id, {
            "state": "success" if ok else "failed",
            "error": report.error or "",
            "finished_at": int(time.time()),
            "summaries_added": int(report.summaries_added),
            "levels": int(report.levels),
            "elapsed_s": float(report.elapsed_sec or 0.0),
        })
        return {
            "ok": ok, "summaries_added": report.summaries_added,
            "levels": report.levels, "error": report.error or "",
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("raptor build failed: %s", task_id)
        err = f"{type(e).__name__}: {e}"
        _write_status(task_id, {
            "state": "failed", "error": err,
            "finished_at": int(time.time()),
        })
        _dead_letter(task_id, "raptor_build", payload, err)
        return {"ok": False, "error": str(e)}


async def raptor_build(
    ctx: Optional[Dict[str, Any]], task_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    import asyncio
    return await asyncio.to_thread(_raptor_build_sync_impl, task_id, payload)


def _graphrag_build_sync_impl(task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    _write_status(task_id, {
        "state": "running", "kind": "graphrag_build",
        "started_at": int(time.time()),
        "kb_name": payload.get("kb_name", ""),
    })
    try:
        from chayuan.server.file_rag.graphrag.builder import build_graphrag_for_kb
        report = build_graphrag_for_kb(
            kb_name=payload["kb_name"],
            max_chunks=int(payload.get("max_chunks") or 10000),
            community_min_size=int(payload.get("community_min_size") or 2),
            llm_model=payload.get("llm_model"),
        )
        ok = not report.error
        _write_status(task_id, {
            "state": "success" if ok else "failed",
            "error": report.error or "",
            "finished_at": int(time.time()),
            "entities": int(report.entities),
            "relations": int(report.relations),
            "communities": int(report.communities),
            "chunks_processed": int(report.chunks_processed),
            "elapsed_s": float(report.elapsed_sec or 0.0),
        })
        return {
            "ok": ok, "entities": report.entities,
            "relations": report.relations, "communities": report.communities,
            "error": report.error or "",
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("graphrag build failed: %s", task_id)
        err = f"{type(e).__name__}: {e}"
        _write_status(task_id, {
            "state": "failed", "error": err,
            "finished_at": int(time.time()),
        })
        _dead_letter(task_id, "graphrag_build", payload, err)
        return {"ok": False, "error": str(e)}


async def graphrag_build(
    ctx: Optional[Dict[str, Any]], task_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    import asyncio
    return await asyncio.to_thread(_graphrag_build_sync_impl, task_id, payload)


def _image_model_download_sync_impl(
    task_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    _write_status(task_id, {
        "state": "running", "kind": "image_model_download",
        "started_at": int(time.time()),
        "model_name": payload.get("model_name", ""),
    })
    try:
        from chayuan.server.image_source.model_manager import download_model
        ret = download_model(
            payload["model_name"],
            progress_cb=lambda msg: _write_status(task_id, {"progress": msg[:200]}),
        )
        ok = bool(ret.get("ok"))
        _write_status(task_id, {
            "state": "success" if ok else "failed",
            "error": ret.get("error") or "",
            "finished_at": int(time.time()),
            "local_dir": ret.get("local_dir") or "",
            "size_mb": ret.get("size_mb") or 0,
        })
        return ret
    except Exception as e:  # noqa: BLE001
        logger.exception("image_model_download failed: %s", task_id)
        err = f"{type(e).__name__}: {e}"
        _write_status(task_id, {
            "state": "failed", "error": err,
            "finished_at": int(time.time()),
        })
        _dead_letter(task_id, "image_model_download", payload, err)
        return {"ok": False, "error": str(e)}


async def image_model_download(
    ctx: Optional[Dict[str, Any]], task_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    import asyncio
    return await asyncio.to_thread(_image_model_download_sync_impl, task_id, payload)


def _image_model_smoke_test_sync_impl(
    task_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    _write_status(task_id, {
        "state": "running", "kind": "image_model_smoke_test",
        "started_at": int(time.time()),
        "model_name": payload.get("model_name", ""),
    })
    try:
        from chayuan.server.image_source.model_manager import smoke_test_model
        ret = smoke_test_model(payload["model_name"])
        ok = bool(ret.get("ok"))
        _write_status(task_id, {
            "state": "success" if ok else "failed",
            "error": ret.get("error") or "",
            "finished_at": int(time.time()),
            "dim": ret.get("dim") or 0,
            "msg": ret.get("msg") or "",
        })
        return ret
    except Exception as e:  # noqa: BLE001
        logger.exception("image_model_smoke_test failed: %s", task_id)
        err = f"{type(e).__name__}: {e}"
        _write_status(task_id, {
            "state": "failed", "error": err,
            "finished_at": int(time.time()),
        })
        _dead_letter(task_id, "image_model_smoke_test", payload, err)
        return {"ok": False, "error": str(e)}


async def image_model_smoke_test(
    ctx: Optional[Dict[str, Any]], task_id: str, payload: Dict[str, Any]
) -> Dict[str, Any]:
    import asyncio
    return await asyncio.to_thread(_image_model_smoke_test_sync_impl, task_id, payload)


# ---------------------------------------------------------------------------
# Arq hooks
# ---------------------------------------------------------------------------

async def on_job_start(ctx: Dict[str, Any]) -> None:
    logger.info("arq job start: %s", ctx.get("job_id"))


async def on_job_end(ctx: Dict[str, Any]) -> None:
    logger.info("arq job end: %s", ctx.get("job_id"))
