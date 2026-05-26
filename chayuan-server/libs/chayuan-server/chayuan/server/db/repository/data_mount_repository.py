from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import desc
from sqlalchemy.orm import Session

from chayuan.server.db.models.annotation_model import AnnotationTaskModel
from chayuan.server.db.models.data_mount_model import (
    DataMountArtifactModel,
    DataMountHitLogModel,
    DataMountModel,
)
from chayuan.server.db.session import with_session


DEFAULT_MOUNT_MODES = ["preference", "fewshot", "retrieval_boost"]
_POSITIVE_QUALITY = {"good", "excellent", "pass", "approved", "correct"}
_NEGATIVE_QUALITY = {"bad", "poor", "fail", "rejected", "irrelevant", "unsafe"}


def _row_dict(row: DataMountModel) -> Dict[str, Any]:
    return row.to_dict()


def _artifact_dict(row: DataMountArtifactModel) -> Dict[str, Any]:
    return row.to_dict()


def _clean_modes(modes: Sequence[str] | None) -> List[str]:
    allowed = {"preference", "fewshot", "retrieval_boost", "safety_rule", "answer_style"}
    out = []
    for mode in modes or DEFAULT_MOUNT_MODES:
        m = str(mode or "").strip()
        if m in allowed and m not in out:
            out.append(m)
    return out or list(DEFAULT_MOUNT_MODES)


def _label_score(labels: Dict[str, Any]) -> int:
    if not isinstance(labels, dict):
        return 0
    if labels.get("is_correct") is False or labels.get("answer_correct") is False:
        return -1
    if labels.get("retrieval_relevant") is False or labels.get("citation_supported") is False:
        return -1
    for key in ("relevance_score", "faithfulness_score", "answer_quality_score", "style_score"):
        try:
            if key in labels:
                value = float(labels.get(key) or 0)
                if value >= 4:
                    return 1
                if 0 < value <= 2:
                    return -1
        except Exception:
            pass
    quality = str(labels.get("answer_quality") or labels.get("quality") or "").lower()
    if quality in _POSITIVE_QUALITY:
        return 1
    if quality in _NEGATIVE_QUALITY:
        return -1
    if labels.get("is_correct") is True or labels.get("answer_correct") is True:
        return 1
    return 0


def _text(value: Any, limit: int = 1200) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()[:limit]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except Exception:
        return str(value)[:limit]


def _first_text(*values: Any, limit: int = 1200) -> str:
    for value in values:
        text = _text(value, limit=limit)
        if text:
            return text
    return ""


def _doc_vote_key(item: Dict[str, Any]) -> Optional[str]:
    file_name = str(item.get("file_name") or item.get("source") or "").strip()
    if not file_name:
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        file_name = str(meta.get("file_name") or meta.get("source") or "").strip()
    if not file_name:
        return None
    chunk = item.get("chunk_index")
    if chunk in (None, ""):
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        chunk = meta.get("chunk_index")
    return f"{file_name}#chunk={chunk}" if chunk not in (None, "") else file_name


def _checksum(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _query_samples(session: Session, source_filter: Dict[str, Any], limit: int) -> List[AnnotationTaskModel]:
    q = session.query(AnnotationTaskModel).filter(AnnotationTaskModel.status == "approved")
    task_types = source_filter.get("task_types") or source_filter.get("task_type")
    if isinstance(task_types, str):
        task_types = [task_types]
    if isinstance(task_types, list) and task_types:
        q = q.filter(AnnotationTaskModel.task_type.in_([str(x) for x in task_types if str(x).strip()]))
    target_type = str(source_filter.get("target_type") or "").strip()
    if target_type:
        q = q.filter(AnnotationTaskModel.target_type == target_type)
    target_ids = source_filter.get("target_ids") or source_filter.get("target_id")
    if isinstance(target_ids, str):
        target_ids = [target_ids]
    if isinstance(target_ids, list) and target_ids:
        q = q.filter(AnnotationTaskModel.target_id.in_([str(x) for x in target_ids if str(x).strip()]))
    sample_ids = source_filter.get("sample_ids") or source_filter.get("ids")
    if isinstance(sample_ids, str):
        sample_ids = [sample_ids]
    if isinstance(sample_ids, list) and sample_ids:
        q = q.filter(AnnotationTaskModel.id.in_([str(x) for x in sample_ids if str(x).strip()]))
    return q.order_by(desc(AnnotationTaskModel.update_time)).limit(max(1, min(int(limit or 500), 5000))).all()


def _build_preference_profile(samples: Iterable[AnnotationTaskModel]) -> Dict[str, Any]:
    rules: List[str] = []
    styles: List[str] = []
    sample_ids: List[str] = []
    citation_required = False
    for row in samples:
        labels = dict(row.labels or {})
        review = dict(row.review or {})
        meta = dict(row.meta or {})
        score = _label_score(labels)
        if score < 0:
            continue
        sample_ids.append(row.id)
        for key in ("answer_style", "preferred_style", "style_preference", "communication_style"):
            value = labels.get(key) or review.get(key) or meta.get(key)
            if value and str(value) not in styles:
                styles.append(str(value)[:120])
        if labels.get("citation_supported") is True or labels.get("require_citation") is True:
            citation_required = True
        for value in (
            labels.get("preference"),
            labels.get("rule"),
            review.get("comment"),
            meta.get("preference"),
            meta.get("instruction"),
            row.note,
        ):
            text = _text(value, 240)
            if text and text not in rules:
                rules.append(text)
    return {
        "rules": rules[:12],
        "styles": styles[:6],
        "citation_required": citation_required,
        "sample_ids": sample_ids[:100],
    }


def _build_fewshot_examples(samples: Iterable[AnnotationTaskModel], limit: int) -> Dict[str, Any]:
    examples: List[Dict[str, Any]] = []
    for row in samples:
        labels = dict(row.labels or {})
        if _label_score(labels) < 0:
            continue
        inputs = dict(row.inputs or {})
        output = dict(row.model_output or {})
        query = _first_text(inputs.get("query"), inputs.get("question"), inputs.get("input"), inputs.get("value"), limit=500)
        answer = _first_text(
            labels.get("correct_answer"),
            labels.get("preferred_answer"),
            output.get("answer"),
            output.get("result"),
            output.get("value"),
            limit=1400,
        )
        if not query or not answer:
            continue
        examples.append({
            "sample_id": row.id,
            "task_type": row.task_type,
            "query": query,
            "answer": answer,
            "score": _label_score(labels),
        })
        if len(examples) >= limit:
            break
    return {"examples": examples}


def _build_retrieval_boost_map(samples: Iterable[AnnotationTaskModel]) -> Dict[str, Any]:
    boosts: Dict[str, Dict[str, Any]] = {}
    for row in samples:
        score = _label_score(dict(row.labels or {}))
        if score == 0:
            continue
        output = dict(row.model_output or {})
        items = output.get("retrieved_items") or output.get("chunks") or []
        if not isinstance(items, list):
            continue
        for item in items[:30]:
            if not isinstance(item, dict):
                continue
            key = _doc_vote_key(item)
            if not key:
                continue
            cur = boosts.setdefault(key, {"positive": 0, "negative": 0, "sample_ids": []})
            if score > 0:
                cur["positive"] = int(cur.get("positive") or 0) + 1
            else:
                cur["negative"] = int(cur.get("negative") or 0) + 1
            ids = list(cur.get("sample_ids") or [])
            if row.id not in ids:
                ids.append(row.id)
            cur["sample_ids"] = ids[:20]
    return {"boosts": boosts}


def _build_safety_rules(samples: Iterable[AnnotationTaskModel]) -> Dict[str, Any]:
    rules: List[str] = []
    sample_ids: List[str] = []
    for row in samples:
        labels = dict(row.labels or {})
        review = dict(row.review or {})
        score = _label_score(labels)
        if score >= 0 and not row.error_tags:
            continue
        sample_ids.append(row.id)
        for tag in row.error_tags or []:
            text = str(tag or "").strip()
            if text and f"避免：{text}" not in rules:
                rules.append(f"避免：{text}")
        for value in (labels.get("avoid"), labels.get("error_reason"), review.get("comment"), row.note):
            text = _text(value, 240)
            if text and text not in rules:
                rules.append(text)
    return {"rules": rules[:20], "sample_ids": sample_ids[:100]}


def _materialize_payloads(mount: DataMountModel, samples: List[AnnotationTaskModel]) -> List[Tuple[str, Dict[str, Any]]]:
    modes = set(_clean_modes(mount.mount_modes))
    limit = max(1, int(mount.max_items or 20))
    payloads: List[Tuple[str, Dict[str, Any]]] = []
    if "preference" in modes or "answer_style" in modes:
        payloads.append(("preference_profile", _build_preference_profile(samples)))
    if "fewshot" in modes:
        payloads.append(("fewshot_examples", _build_fewshot_examples(samples, limit=limit)))
    if "retrieval_boost" in modes:
        payloads.append(("retrieval_boost_map", _build_retrieval_boost_map(samples)))
    if "safety_rule" in modes:
        payloads.append(("safety_rules", _build_safety_rules(samples)))
    return payloads


@with_session
def create_mount(
    session: Session,
    *,
    name: str,
    description: str = "",
    scope_type: str = "global",
    scope_id: str = "",
    source_filter: Optional[Dict[str, Any]] = None,
    mount_modes: Optional[Sequence[str]] = None,
    priority: int = 0,
    max_items: int = 20,
    max_tokens: int = 1600,
    created_by: Optional[int] = None,
) -> Dict[str, Any]:
    row = DataMountModel(
        id=str(uuid.uuid4()),
        name=(name or "训练数据挂载")[:128],
        description=description or "",
        scope_type=(scope_type or "global")[:32],
        scope_id=(scope_id or "")[:128],
        source_filter=source_filter or {},
        mount_modes=_clean_modes(mount_modes),
        priority=int(priority or 0),
        max_items=max(1, min(int(max_items or 20), 200)),
        max_tokens=max(200, min(int(max_tokens or 1600), 12000)),
        enabled=True,
        status="draft",
        version=1,
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(row)
    session.flush()
    return _row_dict(row)


@with_session
def update_mount(
    session: Session,
    mount_id: str,
    *,
    patch: Dict[str, Any],
    actor_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    row = session.get(DataMountModel, mount_id)
    if row is None:
        return None
    for key in ("name", "description", "scope_type", "scope_id", "source_filter", "priority", "max_items", "max_tokens", "enabled"):
        if key in patch:
            value = patch[key]
            if key == "name":
                row.name = str(value or row.name)[:128]
            elif key == "description":
                row.description = str(value or "")
            elif key == "scope_type":
                row.scope_type = str(value or "global")[:32]
            elif key == "scope_id":
                row.scope_id = str(value or "")[:128]
            elif key == "source_filter" and isinstance(value, dict):
                row.source_filter = value
            elif key == "priority":
                row.priority = int(value or 0)
            elif key == "max_items":
                row.max_items = max(1, min(int(value or 20), 200))
            elif key == "max_tokens":
                row.max_tokens = max(200, min(int(value or 1600), 12000))
            elif key == "enabled":
                row.enabled = bool(value)
    if "mount_modes" in patch:
        row.mount_modes = _clean_modes(patch.get("mount_modes") or [])
    row.updated_by = actor_id
    session.flush()
    return _row_dict(row)


@with_session
def get_mount(session: Session, mount_id: str, *, include_artifacts: bool = False) -> Optional[Dict[str, Any]]:
    row = session.get(DataMountModel, mount_id)
    if row is None:
        return None
    out = _row_dict(row)
    if include_artifacts:
        artifacts = (
            session.query(DataMountArtifactModel)
            .filter(DataMountArtifactModel.mount_id == row.id)
            .filter(DataMountArtifactModel.version == row.version)
            .order_by(DataMountArtifactModel.artifact_type.asc())
            .all()
        )
        out["artifacts"] = [_artifact_dict(x) for x in artifacts]
    return out


@with_session
def list_mounts(
    session: Session,
    *,
    status: Optional[str] = None,
    scope_type: Optional[str] = None,
    enabled: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    q = session.query(DataMountModel)
    if status:
        q = q.filter(DataMountModel.status == status)
    if scope_type:
        q = q.filter(DataMountModel.scope_type == scope_type)
    if enabled is not None:
        q = q.filter(DataMountModel.enabled == bool(enabled))
    total = q.count()
    rows = (
        q.order_by(DataMountModel.priority.desc(), DataMountModel.update_time.desc())
        .offset(max(0, int(offset or 0)))
        .limit(max(1, min(int(limit or 100), 500)))
        .all()
    )
    return [_row_dict(x) for x in rows], int(total)


@with_session
def preview_mount(session: Session, mount_id: str) -> Optional[Dict[str, Any]]:
    row = session.get(DataMountModel, mount_id)
    if row is None:
        return None
    sf = dict(row.source_filter or {})

    # 新路径: 用 adapter.sample 出 200 条预览
    if _has_data_mount_spec(sf):
        from chayuan.server.data_mount import SourceSpec, get_registry
        spec = SourceSpec.from_dict(sf.get("spec") or {})
        adapter = get_registry().get(spec.source_type)
        if adapter is None:
            return {"mount": _row_dict(row), "sample_count": 0, "sample_ids": [],
                    "artifacts": [], "error": f"unknown source_type: {spec.source_type}"}
        sample = adapter.sample(spec, n=int(row.max_items or 20))
        # 同时跑一次 materialize 做"如果发布会得到什么 artifact"的预览
        try:
            triples = _materialize_via_data_mount(row, sf)
        except Exception as e:  # noqa: BLE001
            triples = []
            err = str(e)
        else:
            err = None
        return {
            "mount": _row_dict(row),
            "sample_count": len(sample.items),
            "sample_ids": [it.id or "" for it in sample.items[:50]],
            "fields": [f.to_dict() for f in sample.fields],
            "preview_records": [it.to_dict() for it in sample.items[:50]],
            "artifacts": [{"artifact_type": t, "payload": p, "stats": s}
                          for t, p, s in triples],
            **({"error": err} if err else {}),
        }

    # 旧路径
    samples = _query_samples(session, sf, limit=int(row.max_items or 20))
    payloads = _materialize_payloads(row, samples)
    return {
        "mount": _row_dict(row),
        "sample_count": len(samples),
        "sample_ids": [x.id for x in samples[:50]],
        "artifacts": [{"artifact_type": t, "payload": p} for t, p in payloads],
    }


def _has_data_mount_spec(source_filter: Dict[str, Any]) -> bool:
    """source_filter.spec.source_type 存在 → 走新 data_mount 12 源体系。

    这是路径分流的关键开关:
        旧路径 (annotation only) ←→ 新路径 (data_mount sources)
    向后兼容:既有 annotation mount 不带 spec.source_type, 自然走旧路径。
    """
    spec = (source_filter or {}).get("spec") or {}
    return bool(spec.get("source_type"))


def _materialize_via_data_mount(
    row: DataMountModel,
    source_filter: Dict[str, Any],
) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    """走新 data_mount.materializer 路径。

    返回 ``[(artifact_type, payload, stats), ...]``。
    """
    import asyncio

    from chayuan.server.data_mount import SourceSpec, materialize_mount

    spec = SourceSpec.from_dict(source_filter.get("spec") or {})
    target_kb = source_filter.get("target_kb") or None
    modes = list(_clean_modes(row.mount_modes))
    # 把旧 mode 名映射成新 mode 名,保持 UI 选项稳定
    mode_alias = {
        "preference": "preference",
        "fewshot": "fewshot",
        "retrieval_boost": "context",
        "safety_rule": "safety",
        "safety": "safety",
        "corpus": "corpus",
        "context": "context",
        "answer_style": "preference",
    }
    new_modes = [mode_alias.get(m, m) for m in modes]
    artifacts = asyncio.run(materialize_mount(
        spec, new_modes, target_kb=target_kb, scope_hint=row.scope_type or "",
    ))
    out: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    for art in artifacts:
        out.append((
            art.get("artifact_type") or "unknown",
            art.get("payload") or {},
            art.get("stats") or {},
        ))
    return out


@with_session
def publish_mount(session: Session, mount_id: str, *, actor_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    row = session.get(DataMountModel, mount_id)
    if row is None:
        return None
    sf = dict(row.source_filter or {})
    version = int(row.version or 1) + (1 if row.status == "published" else 0)

    # ---- 新路径: 12 种 data_mount 源 ----
    if _has_data_mount_spec(sf):
        triples = _materialize_via_data_mount(row, sf)
        for artifact_type, payload, stats in triples:
            artifact = DataMountArtifactModel(
                id=str(uuid.uuid4()),
                mount_id=row.id,
                version=version,
                artifact_type=artifact_type,
                payload=payload,
                stats=stats,
                checksum=_checksum(payload),
            )
            session.add(artifact)
    else:
        # ---- 旧路径: annotation samples ----
        samples = _query_samples(session, sf, limit=max(int(row.max_items or 20) * 5, 100))
        payloads = _materialize_payloads(row, samples)
        for artifact_type, payload in payloads:
            stats = {
                "sample_count": len(samples),
                "item_count": len(payload.get("examples") or payload.get("rules") or payload.get("boosts") or []),
            }
            artifact = DataMountArtifactModel(
                id=str(uuid.uuid4()),
                mount_id=row.id,
                version=version,
                artifact_type=artifact_type,
                payload=payload,
                stats=stats,
                checksum=_checksum(payload),
            )
            session.add(artifact)

    row.status = "published"
    row.enabled = True
    row.version = version
    row.published_at = datetime.utcnow()
    row.updated_by = actor_id
    session.flush()
    return get_mount.__wrapped__(session, mount_id, include_artifacts=True)  # type: ignore[attr-defined]


@with_session
def set_mount_enabled(session: Session, mount_id: str, *, enabled: bool, actor_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    row = session.get(DataMountModel, mount_id)
    if row is None:
        return None
    row.enabled = bool(enabled)
    row.updated_by = actor_id
    session.flush()
    return _row_dict(row)


@with_session
def list_pending_corpus_for_kb(
    session: Session, kb_name: str,
) -> List[Dict[str, Any]]:
    """列出目标 KB 的所有 corpus_pending artifact (含 mount 元信息)。

    供 KB 详情页"待 ingest 任务"区使用。
    """
    rows = (
        session.query(DataMountArtifactModel, DataMountModel)
        .join(DataMountModel, DataMountArtifactModel.mount_id == DataMountModel.id)
        .filter(DataMountArtifactModel.artifact_type == "corpus_pending")
        .filter(DataMountModel.enabled.is_(True))
        .order_by(DataMountArtifactModel.create_time.desc())
        .limit(200)
        .all()
    )
    out: List[Dict[str, Any]] = []
    for art, mount in rows:
        payload = art.payload or {}
        target = (payload.get("target_kb") or "").strip()
        # 仅当目标 KB 匹配时保留;target_kb 为空时认为"未指定",显式标出让 UI 处理
        if target and target != kb_name:
            continue
        item = _artifact_dict(art)
        item["mount"] = _row_dict(mount)
        item["item_count"] = len(payload.get("items") or [])
        out.append(item)
    return out


@with_session
def get_artifact(session: Session, artifact_id: str) -> Optional[Dict[str, Any]]:
    art = session.get(DataMountArtifactModel, artifact_id)
    if art is None:
        return None
    out = _artifact_dict(art)
    out["payload"] = art.payload  # 用于实际 ingest 时取 items
    return out


@with_session
def mark_artifact_disabled(
    session: Session, artifact_id: str, *, reason: str = "",
) -> Optional[Dict[str, Any]]:
    """把 artifact 标记为 disabled (rejected/已 ingest)。

    实现:在 stats 加 ``status=disabled`` + ``disabled_reason``;不删除行,
    便于审计追溯。
    """
    art = session.get(DataMountArtifactModel, artifact_id)
    if art is None:
        return None
    stats = dict(art.stats or {})
    stats["status"] = "disabled"
    if reason:
        stats["disabled_reason"] = reason[:240]
    art.stats = stats
    session.flush()
    return _artifact_dict(art)


@with_session
def active_mounts_with_artifacts(session: Session) -> List[Dict[str, Any]]:
    mounts = (
        session.query(DataMountModel)
        .filter(DataMountModel.status == "published")
        .filter(DataMountModel.enabled.is_(True))
        .order_by(DataMountModel.priority.desc(), DataMountModel.update_time.desc())
        .limit(500)
        .all()
    )
    out: List[Dict[str, Any]] = []
    for mount in mounts:
        artifacts = (
            session.query(DataMountArtifactModel)
            .filter(DataMountArtifactModel.mount_id == mount.id)
            .filter(DataMountArtifactModel.version == mount.version)
            .all()
        )
        item = _row_dict(mount)
        item["artifacts"] = [_artifact_dict(x) for x in artifacts]
        out.append(item)
    return out


@with_session
def record_hit(
    session: Session,
    *,
    request_id: str,
    conversation_id: str,
    user_id: Optional[int],
    mount_id: str,
    artifact_type: str,
    sample_ids: Sequence[str],
    hit_count: int,
    token_count: int,
    effect_summary: Dict[str, Any],
) -> Dict[str, Any]:
    row = DataMountHitLogModel(
        id=str(uuid.uuid4()),
        request_id=(request_id or "")[:64],
        conversation_id=(conversation_id or "")[:64],
        user_id=user_id,
        mount_id=mount_id,
        artifact_type=(artifact_type or "")[:48],
        sample_ids=list(sample_ids or [])[:50],
        hit_count=int(hit_count or 0),
        token_count=int(token_count or 0),
        effect_summary=effect_summary or {},
    )
    session.add(row)
    session.flush()
    return row.to_dict()


@with_session
def list_hits(session: Session, mount_id: str, *, limit: int = 100) -> List[Dict[str, Any]]:
    rows = (
        session.query(DataMountHitLogModel)
        .filter(DataMountHitLogModel.mount_id == mount_id)
        .order_by(desc(DataMountHitLogModel.create_time))
        .limit(max(1, min(int(limit or 100), 500)))
        .all()
    )
    return [x.to_dict() for x in rows]
