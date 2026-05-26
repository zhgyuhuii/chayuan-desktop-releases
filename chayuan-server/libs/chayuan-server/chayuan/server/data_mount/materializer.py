"""把 adapter 输出的 Documents 按 mount_modes 转成 5 类 artifact。

5 种模式
========

1. ``corpus_pending``  —— 候选 ingest 任务;**不直接写 KB**,落到
   ``data_mount_artifact`` (artifact_type="corpus_pending"),用户在 KB
   页确认后才走 file_rag ingest。
   ⚠️ 这是 v6 的新行为:之前没有 corpus 路径;按用户确认"corpus 后者"约定。
2. ``runtime_context`` —— 高置信片段,chat 时按 query 相似度匹配后注入 prompt
3. ``runtime_fewshot`` —— Q-A 对,chat 时作为 in-context examples
4. ``runtime_safety``  —— 硬约束规则,chat 时附在 system prompt
5. ``runtime_preference`` —— 偏好对(prompt + chosen + rejected),长期对齐

artifact 写入流程
==================

::

    [adapter.load(spec)] → [DocumentRecord stream]
            ↓ 每条按 mount_modes 分发
    [corpus_pending list]   ← text + metadata + 目标 KB
    [context chunks list]   ← text + score + citation
    [fewshot examples list] ← {query, answer, ...}
    [safety rules list]     ← {pattern, action}
    [preference pairs list] ← {prompt, chosen, rejected}
            ↓
    repository.upsert_artifact(mount_id, version, artifact_type, payload, stats)

调用方
======

* ``data_mount_routes.publish_data_mount`` —— 现已通过
  ``data_mount_repository.publish_mount`` 走 annotation 路径;新版要分发:
  - source_filter 含 ``spec.source_type`` → 走本模块
  - 否则 → fallback 到旧 annotation 路径(向后兼容)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

from chayuan.server.data_mount.base import DocumentRecord, SourceSpec
from chayuan.server.data_mount.registry import get_registry

logger = logging.getLogger("chayuan.data_mount.materializer")


# artifact_type 命名:
#   * corpus_pending     — 候选 ingest 任务(新增,KB 页用户确认后才真 ingest)
#   * retrieval_chunks   — 高置信片段(新增,chat 注入 prompt;不与老
#                           "retrieval_boost_map" 冲突,后者是 thumbs-vote 加权)
#   * fewshot_examples   — 与老格式同名同 schema,共 service.py 一套逻辑
#   * safety_rules       — 同上
#   * preference_profile — 同上(payload 用 prompt+chosen+rejected pairs 而非
#                           老 styles/rules,service.py 加新分支处理)
ARTIFACT_TYPES: Dict[str, str] = {
    "corpus":     "corpus_pending",
    "context":    "retrieval_chunks",
    "fewshot":    "fewshot_examples",
    "safety":     "safety_rules",
    "preference": "preference_profile",
}


class MountMaterializer:
    """按 mount_modes 把 records 物化成 artifact 列表。

    ``materialize`` 同步调用,内部用 ``asyncio.run`` 跑 adapter.load();
    若调用方已在事件循环里(FastAPI 路由)请改用 :meth:`amaterialize`。
    """

    def __init__(self, *, max_corpus: int = 5000, max_context: int = 200,
                 max_fewshot: int = 100, max_safety: int = 50,
                 max_preference: int = 100) -> None:
        self.max_corpus = max_corpus
        self.max_context = max_context
        self.max_fewshot = max_fewshot
        self.max_safety = max_safety
        self.max_preference = max_preference

    def materialize(
        self,
        spec: SourceSpec,
        mount_modes: Sequence[str],
        *,
        target_kb: Optional[str] = None,
        scope_hint: str = "",
    ) -> List[Dict[str, Any]]:
        """同步入口;FastAPI 路由请用 :meth:`amaterialize`。"""
        try:
            return asyncio.run(self.amaterialize(
                spec, mount_modes, target_kb=target_kb, scope_hint=scope_hint,
            ))
        except RuntimeError as e:
            # "asyncio.run() cannot be called from a running event loop"
            if "running event loop" in str(e).lower():
                raise RuntimeError(
                    "已在事件循环中,请改调 amaterialize() 而非 materialize()"
                ) from e
            raise

    async def amaterialize(
        self,
        spec: SourceSpec,
        mount_modes: Sequence[str],
        *,
        target_kb: Optional[str] = None,
        scope_hint: str = "",
    ) -> List[Dict[str, Any]]:
        adapter = get_registry().get(spec.source_type)
        if adapter is None:
            raise ValueError(f"未知数据源: {spec.source_type}")

        modes = {m.strip() for m in mount_modes if m and m.strip()}
        if not modes:
            modes = {"context"}  # 默认最低风险:仅注入 context

        corpus: List[Dict[str, Any]] = []
        context: List[Dict[str, Any]] = []
        fewshot: List[Dict[str, Any]] = []
        safety: List[Dict[str, Any]] = []
        preference: List[Dict[str, Any]] = []

        async for rec in self._iter(adapter, spec):
            # corpus: 直接当 ingest 候选
            if "corpus" in modes and len(corpus) < self.max_corpus:
                corpus.append({
                    "id": rec.id,
                    "text": rec.text,
                    "metadata": rec.metadata,
                    "target_kb": target_kb,
                    "source_type": spec.source_type,
                })

            # context: 取 text 主体 + citation
            if "context" in modes and len(context) < self.max_context:
                context.append({
                    "doc_id": rec.id,
                    "text": rec.text[:1200],
                    "score": float((rec.metadata or {}).get("score") or 1.0),
                    "citation": {
                        "title": (rec.metadata or {}).get("title") or rec.id or "",
                        "source": (rec.metadata or {}).get("source") or spec.source_type,
                        "meta": rec.metadata,
                    },
                })

            # fewshot: 期待 record 自带 query / answer 字段
            if "fewshot" in modes and len(fewshot) < self.max_fewshot:
                fs = self._extract_fewshot(rec)
                if fs:
                    fewshot.append(fs)

            # safety: 期待 record 自带 pattern / action
            if "safety" in modes and len(safety) < self.max_safety:
                sf = self._extract_safety(rec)
                if sf:
                    safety.append(sf)

            # preference: 期待 prompt + chosen + rejected
            if "preference" in modes and len(preference) < self.max_preference:
                pf = self._extract_preference(rec)
                if pf:
                    preference.append(pf)

            # 总上限剪枝
            if (len(corpus) >= self.max_corpus and
                len(context) >= self.max_context and
                len(fewshot) >= self.max_fewshot and
                len(safety) >= self.max_safety and
                len(preference) >= self.max_preference):
                break

        artifacts: List[Dict[str, Any]] = []
        if "corpus" in modes:
            artifacts.append({
                "artifact_type": ARTIFACT_TYPES["corpus"],
                "payload": {"items": corpus, "target_kb": target_kb,
                            "source_type": spec.source_type},
                "stats": {"count": len(corpus)},
            })
        if "context" in modes and context:
            artifacts.append({
                "artifact_type": ARTIFACT_TYPES["context"],
                "payload": {"chunks": context},
                "stats": {"count": len(context)},
            })
        if "fewshot" in modes and fewshot:
            artifacts.append({
                "artifact_type": ARTIFACT_TYPES["fewshot"],
                "payload": {"examples": fewshot},
                "stats": {"count": len(fewshot)},
            })
        if "safety" in modes and safety:
            artifacts.append({
                "artifact_type": ARTIFACT_TYPES["safety"],
                "payload": {"rules": safety},
                "stats": {"count": len(safety)},
            })
        if "preference" in modes and preference:
            artifacts.append({
                "artifact_type": ARTIFACT_TYPES["preference"],
                "payload": {"pairs": preference},
                "stats": {"count": len(preference)},
            })
        return artifacts

    # ---- helpers --------------------------------------------------------

    async def _iter(self, adapter: Any, spec: SourceSpec) -> AsyncIterator[DocumentRecord]:
        result = adapter.load(spec)
        if hasattr(result, "__aiter__"):
            async for r in result:
                yield r
        else:
            for r in result:  # type: ignore[assignment]
                yield r

    @staticmethod
    def _extract_fewshot(rec: DocumentRecord) -> Optional[Dict[str, Any]]:
        md = rec.metadata or {}
        # 偏好对 (含 rejected/dispreferred) 走 _extract_preference 而非 fewshot;
        # 否则同一条记录会被双重抽取造成数据膨胀
        if md.get("rejected") or md.get("dispreferred"):
            return None
        # 1) 直接结构: query+answer
        q = md.get("query") or md.get("question") or md.get("prompt")
        a = md.get("answer") or md.get("response") or md.get("chosen")
        if q and a:
            return {
                "query": str(q)[:500],
                "answer": str(a)[:1500],
                "labels": md.get("labels") or {},
                "doc_id": rec.id,
            }
        # 2) 文本内 Q:/A: 自动切
        text = rec.text or ""
        if text.startswith(("Q:", "Q :", "问:", "问 :")):
            parts = text.split("\n", 1)
            if len(parts) == 2 and parts[1].lstrip().startswith(("A:", "A :", "答:", "答 :")):
                return {
                    "query": parts[0].split(":", 1)[1].strip()[:500],
                    "answer": parts[1].split(":", 1)[1].strip()[:1500],
                    "doc_id": rec.id,
                }
        return None

    @staticmethod
    def _extract_safety(rec: DocumentRecord) -> Optional[Dict[str, Any]]:
        md = rec.metadata or {}
        pattern = md.get("pattern") or md.get("rule")
        action = md.get("action") or md.get("response")
        if pattern and action:
            return {
                "pattern": str(pattern)[:300],
                "action": str(action)[:600],
                "severity": md.get("severity") or "warn",
            }
        return None

    @staticmethod
    def _extract_preference(rec: DocumentRecord) -> Optional[Dict[str, Any]]:
        md = rec.metadata or {}
        prompt = md.get("prompt") or md.get("query")
        chosen = md.get("chosen") or md.get("preferred") or md.get("answer")
        rejected = md.get("rejected") or md.get("dispreferred")
        if prompt and chosen and rejected:
            return {
                "prompt": str(prompt)[:500],
                "chosen": str(chosen)[:1500],
                "rejected": str(rejected)[:1500],
                "labels": md.get("labels") or {},
            }
        return None


_DEFAULT = MountMaterializer()


async def materialize_mount(
    spec: SourceSpec,
    mount_modes: Sequence[str],
    *,
    target_kb: Optional[str] = None,
    scope_hint: str = "",
) -> List[Dict[str, Any]]:
    """模块级单例的 async 包装,FastAPI 路由直接 await 这个。"""
    return await _DEFAULT.amaterialize(
        spec, mount_modes, target_kb=target_kb, scope_hint=scope_hint,
    )


__all__ = ["ARTIFACT_TYPES", "MountMaterializer", "materialize_mount"]
