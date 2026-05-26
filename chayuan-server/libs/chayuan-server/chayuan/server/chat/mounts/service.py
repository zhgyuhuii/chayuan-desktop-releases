from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List, Optional, Set

from chayuan.server.chat.graph.state import ChatRequest, ChatState
from chayuan.server.db.repository import data_mount_repository


def _tokens(text: str) -> Set[str]:
    out: Set[str] = set()
    for token in re.split(r"\s+", text or ""):
        token = token.strip().lower()
        if len(token) >= 2:
            out.add(token)
        if re.search(r"[\u4e00-\u9fff]", token):
            for i in range(max(0, len(token) - 1)):
                out.add(token[i:i + 2])
    return out


def _similarity(a: str, b: str) -> float:
    aa, bb = _tokens(a), _tokens(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(len(aa), len(bb))


def _req_scope_candidates(req: ChatRequest) -> Set[str]:
    values: Set[str] = set()
    if req.user_id is not None:
        values.add(f"user:{req.user_id}")
    for kb in req.kb_names or []:
        if kb:
            values.add(f"kb:{kb}")
            values.add(f"doc:{kb}")
    if req.kb_name:
        values.add(f"kb:{req.kb_name}")
        values.add(f"doc:{req.kb_name}")
    for ku_id in req.ku_ids or []:
        if ku_id:
            values.add(str(ku_id))
            if str(ku_id).startswith("doc:"):
                values.add("kb:" + str(ku_id).removeprefix("doc:"))
    for source_id in req.source_ids or []:
        values.add(f"src:{source_id}")
    return values


def _mount_matches_req(mount: Dict[str, Any], req: ChatRequest) -> bool:
    scope_type = str(mount.get("scope_type") or "global")
    scope_id = str(mount.get("scope_id") or "")
    if scope_type == "global":
        return True
    if scope_type == "user":
        return req.user_id is not None and scope_id == str(req.user_id)
    if scope_type == "kb":
        return scope_id in _req_scope_candidates(req)
    # group/app scopes need request-side group/app context. Keep published data inert
    # until those fields are wired, instead of accidentally applying them globally.
    return False


def _artifact(mount: Dict[str, Any], artifact_type: str) -> Optional[Dict[str, Any]]:
    for artifact in mount.get("artifacts") or []:
        if artifact.get("artifact_type") == artifact_type:
            return artifact
    return None


def _merge_unique(dst: List[str], values: Iterable[str], limit: int) -> None:
    for value in values:
        text = str(value or "").strip()
        if text and text not in dst:
            dst.append(text)
        if len(dst) >= limit:
            return


def _select_examples(examples: List[Dict[str, Any]], query: str, limit: int) -> List[Dict[str, Any]]:
    scored = []
    for ex in examples:
        score = _similarity(query, str(ex.get("query") or ""))
        scored.append((score, ex))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, ex in scored:
        if score <= 0 and len(out) >= 1:
            continue
        item = dict(ex)
        item["match_score"] = score
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _chunk_vote_key(chunk: Dict[str, Any]) -> str:
    citation = chunk.get("citation") if isinstance(chunk.get("citation"), dict) else {}
    meta = citation.get("meta") if isinstance(citation.get("meta"), dict) else {}
    raw_name = (
        meta.get("source")
        or meta.get("file_name")
        or citation.get("title")
        or chunk.get("source")
        or chunk.get("file_name")
        or ""
    )
    file_name = str(raw_name or "").strip()
    chunk_index = meta.get("chunk_index") or chunk.get("chunk_index")
    if chunk_index not in (None, "") and file_name:
        return f"{file_name}#chunk={chunk_index}"
    return file_name


class MountedContextService:
    """Build runtime context from published training-data mounts."""

    def build(self, req: ChatRequest, state: ChatState) -> Dict[str, Any]:
        try:
            mounts = data_mount_repository.active_mounts_with_artifacts()
        except Exception:
            mounts = []
        matched = [m for m in mounts if _mount_matches_req(m, req)]
        if not matched:
            return {
                "enabled": False,
                "mounts": [],
                "preferences": {},
                "fewshot_examples": [],
                "retrieval_boosts": {},
                "safety_rules": [],
                "sources_meta": [],
                "hit_summary": {"mount_count": 0},
            }

        rules: List[str] = []
        styles: List[str] = []
        safety_rules: List[str] = []
        citation_required = False
        all_examples: List[Dict[str, Any]] = []
        boosts: Dict[str, Dict[str, Any]] = {}
        sources_meta: List[Dict[str, Any]] = []
        mount_ids: List[str] = []

        max_items = 20
        for mount in matched:
            mount_ids.append(str(mount.get("id")))
            max_items = max(max_items, int(mount.get("max_items") or 20))
            pref = _artifact(mount, "preference_profile")
            if pref:
                payload = pref.get("payload") or {}
                _merge_unique(rules, payload.get("rules") or [], 20)
                _merge_unique(styles, payload.get("styles") or [], 10)
                citation_required = citation_required or bool(payload.get("citation_required"))
                sources_meta.append({
                    "mount_id": mount.get("id"),
                    "name": mount.get("name"),
                    "artifact_type": "preference_profile",
                    "sample_ids": list(payload.get("sample_ids") or [])[:10],
                })
            fewshot = _artifact(mount, "fewshot_examples")
            if fewshot:
                payload = fewshot.get("payload") or {}
                for ex in payload.get("examples") or []:
                    if isinstance(ex, dict):
                        item = {**ex, "mount_id": mount.get("id"), "mount_name": mount.get("name")}
                        all_examples.append(item)
            boost = _artifact(mount, "retrieval_boost_map")
            if boost:
                payload = boost.get("payload") or {}
                for key, value in (payload.get("boosts") or {}).items():
                    if not isinstance(value, dict):
                        continue
                    cur = boosts.setdefault(str(key), {"positive": 0, "negative": 0, "sample_ids": []})
                    cur["positive"] = int(cur.get("positive") or 0) + int(value.get("positive") or 0)
                    cur["negative"] = int(cur.get("negative") or 0) + int(value.get("negative") or 0)
                    cur["sample_ids"] = list(dict.fromkeys(
                        list(cur.get("sample_ids") or []) + list(value.get("sample_ids") or [])
                    ))[:20]
            safety = _artifact(mount, "safety_rules")
            if safety:
                payload = safety.get("payload") or {}
                # 新格式: rules 是 [{pattern, action, severity}, ...] (来自 data_mount)
                # 老格式: rules 是 [str, ...]
                rules_payload = payload.get("rules") or []
                if rules_payload and isinstance(rules_payload[0], dict):
                    _merge_unique(safety_rules,
                                  [str(r.get("pattern") or "") + ": " + str(r.get("action") or "")
                                   for r in rules_payload], 20)
                else:
                    _merge_unique(safety_rules, rules_payload, 20)

            # 新增: data_mount.materializer 写的 retrieval_chunks
            chunks_art = _artifact(mount, "retrieval_chunks")
            if chunks_art:
                payload = chunks_art.get("payload") or {}
                for ch in payload.get("chunks") or []:
                    if not isinstance(ch, dict):
                        continue
                    key = str(ch.get("doc_id") or ch.get("id") or "") or None
                    if not key:
                        continue
                    cur = boosts.setdefault(key, {"positive": 0, "negative": 0, "sample_ids": []})
                    # data_mount chunks 默认权重 +1(用户钦定的高置信片段)
                    cur["positive"] = int(cur.get("positive") or 0) + 1
                    sample_ids = list(cur.get("sample_ids") or [])
                    if mount.get("id") and str(mount.get("id")) not in sample_ids:
                        sample_ids.append(str(mount.get("id")))
                    cur["sample_ids"] = sample_ids[:20]

            # 新增: data_mount preference_profile (新 schema: pairs)
            new_pref = _artifact(mount, "preference_profile")
            if new_pref:
                pl = new_pref.get("payload") or {}
                if isinstance(pl.get("pairs"), list):
                    # 把 chosen 答案视作 fewshot 注入(轻量集成)
                    for pair in pl["pairs"][:5]:
                        if isinstance(pair, dict) and pair.get("prompt") and pair.get("chosen"):
                            all_examples.append({
                                "query": str(pair["prompt"])[:500],
                                "answer": str(pair["chosen"])[:1500],
                                "labels": pair.get("labels") or {},
                                "mount_id": mount.get("id"),
                                "mount_name": mount.get("name"),
                                "_source": "preference_chosen",
                            })

            # corpus_pending 不参与 chat 注入(它在等待 KB 页确认 ingest);
            # 仅记入 sources_meta 让前端可见
            corpus_art = _artifact(mount, "corpus_pending")
            if corpus_art:
                stats = corpus_art.get("stats") or {}
                sources_meta.append({
                    "mount_id": mount.get("id"),
                    "name": mount.get("name"),
                    "artifact_type": "corpus_pending",
                    "pending_count": int(stats.get("count") or 0),
                    "target_kb": (corpus_art.get("payload") or {}).get("target_kb") or "",
                })

        examples = _select_examples(all_examples, req.query or "", limit=min(5, max_items))
        return {
            "enabled": True,
            "mounts": [{"id": m.get("id"), "name": m.get("name"), "scope_type": m.get("scope_type")} for m in matched],
            "preferences": {
                "rules": rules,
                "styles": styles,
                "citation_required": citation_required,
            },
            "fewshot_examples": examples,
            "retrieval_boosts": boosts,
            "safety_rules": safety_rules,
            "sources_meta": sources_meta,
            "hit_summary": {
                "mount_count": len(matched),
                "mount_ids": mount_ids,
                "fewshot_count": len(examples),
                "boost_count": len(boosts),
                "safety_rule_count": len(safety_rules),
            },
        }

    def apply_ranking(self, chunks: List[Dict[str, Any]], mounted_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        boosts = mounted_context.get("retrieval_boosts") or {}
        if not chunks or not boosts:
            return chunks
        adjusted: List[Dict[str, Any]] = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                adjusted.append(chunk)
                continue
            key = _chunk_vote_key(chunk)
            basename = os.path.basename(key.split("#chunk=", 1)[0]) if key else ""
            vote = boosts.get(key) or boosts.get(basename) or boosts.get(key.split("#chunk=", 1)[0] if key else "")
            if vote:
                pos = int(vote.get("positive") or 0)
                neg = int(vote.get("negative") or 0)
                net = pos - neg
                score = float(chunk.get("score") or 0.0)
                chunk = {
                    **chunk,
                    "score": score + (0.015 * net),
                    "mount_signal": {
                        "positive": pos,
                        "negative": neg,
                        "net": net,
                        "sample_ids": list(vote.get("sample_ids") or [])[:5],
                    },
                }
            adjusted.append(chunk)
        adjusted.sort(key=lambda x: float(x.get("score") or 0.0) if isinstance(x, dict) else 0.0, reverse=True)
        return adjusted


_SERVICE = MountedContextService()


def build_mounted_context(req: ChatRequest, state: ChatState) -> Dict[str, Any]:
    return _SERVICE.build(req, state)


def apply_mounted_ranking(chunks: List[Dict[str, Any]], mounted_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    return _SERVICE.apply_ranking(chunks, mounted_context)
