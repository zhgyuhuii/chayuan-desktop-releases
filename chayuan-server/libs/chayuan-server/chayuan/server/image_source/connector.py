"""ImageConnector — 把图像知识源接入 KnowledgeSource 抽象。

对 orchestrator / Text2X pipeline / 多源并行来说，图像源只是 kind=image 的一种
BaseConnector 实现；这里把文本查询 → text embedding → cosine 搜图 → 归一
RetrievalChunk 的流程收敛。

**核心契约**：
- ``search(NLQuery)`` 只消费 ``query`` 字段（文本）；查图逻辑独立暴露
- ``search_by_image(bytes_or_path, top_k)`` 以图搜图
- test_connection / introspect 轻量：报当前模型 + index 记录数
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, List, Optional, Tuple
from urllib.parse import urlencode

from chayuan.server.image_source.embedder import default_model_name, get_embedder
from chayuan.server.image_source.store import get_store
from chayuan.server.knowledge_source.base import (
    BaseConnector, ConnectionSpec, ConnectorError,
)
from chayuan.server.knowledge_source.types import (
    Citation, NLQuery, RetrievalChunk, SchemaSnapshot, SourceKind, TableInfo,
)

logger = logging.getLogger("chayuan.image_source.connector")


class ImageConnector(BaseConnector):
    """kind=image 的 Connector。

    ConnectionSpec.options 支持的键：
    - ``embedder_model``：向量化模型名（如 ``google/siglip2-base-patch16-224``）；
      未填用全局默认
    - ``source_name``：索引存盘名（默认 spec.database）
    """

    dialects = ("image",)
    source_kind = "image"

    def __init__(self, spec: ConnectionSpec, source_id: int = 0):
        super().__init__(spec, source_id)
        self._model_name = (self.spec.options or {}).get("embedder_model") or default_model_name()
        # 与列表 / 详情端点一致的解析顺序:
        #   options.source_name → spec.database → "src_{id}"
        self.source_name = (
            (self.spec.options or {}).get("source_name")
            or self.spec.database
            or f"src_{self.source_id}"
        )
        # 老内部代号保留,避免一次性大改
        self._source_name = self.source_name

    # ---- 检验 ----

    def test_connection(self) -> Tuple[bool, str]:
        emb = get_embedder(self._model_name)
        if not emb.is_available():
            return (False,
                    "未安装 torch / transformers / Pillow；"
                    "请 `pip install 'chayuan-server[image]'` 并准备模型。")
        try:
            # 懒加载一次，验证模型文件就绪
            _ = emb.embed_text("test")
            return True, f"图像模型就绪：{self._model_name}"
        except Exception as e:  # noqa: BLE001
            return False, f"模型加载失败：{type(e).__name__}: {e}"

    def introspect(self, sample_rows: int = 3) -> SchemaSnapshot:
        store = get_store(self._source_name)
        count = store.count()
        table = TableInfo(
            name=f"image_index:{self._source_name}",
            comment=f"图像向量索引，共 {count} 条；模型 {self._model_name}，维度 {store.dim()}",
            columns=[],
            sample_rows=[{"path": p} for p in store.all_paths()[: int(sample_rows)]],
            row_count_estimate=count,
        )
        return SchemaSnapshot(
            source_id=self.source_id, source_kind=self.source_kind,
            dialect="image", tables=[table],
        )

    # ---- 一致性守护 ----------------------------------------------------

    def _check_consistency(self, emb) -> Optional[str]:
        """检索/入库前校验：当前 embedder 的维度必须与已有索引的维度一致。

        KB 创建时绑定了 embedder_model，如果用户之后在 spec.options 里**手改**
        模型名（或同名 HF 仓库升级了维度），会让库里旧向量与新查询向量彻底不
        可比——结果就是"搜不到"。此处提前发现并返回清晰错误，比默默返错结果好。
        """
        store = get_store(self._source_name)
        store_dim = int(store.dim() or 0)
        emb_dim = int(getattr(emb, "dim", 0) or 0)
        if store_dim == 0:
            return None  # 空库，怎么写都行
        # emb 可能没加载（dim=0）；只要 store 非空、emb 已知 dim 就校验
        if emb_dim and store_dim != emb_dim:
            return (
                f"图像知识库向量维度不匹配：索引内 {store_dim} 维，"
                f"当前模型 {self._model_name} 是 {emb_dim} 维。"
                "这通常意味着 KB 的 embedder_model 被更改——已创建的图像 KB "
                "不允许更换模型，请新建 KB 或重建索引。"
            )
        return None

    # ---- 文本 → 图像 ----

    async def search(self, query: NLQuery) -> List[RetrievalChunk]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._search_sync, query)

    def _search_sync(self, query: NLQuery) -> List[RetrievalChunk]:
        emb = get_embedder(self._model_name)
        if not emb.is_available():
            hint = ""
            try:
                hint = emb.missing_deps_hint() or ""
            except Exception:  # noqa: BLE001
                pass
            # torch 未装 / 不可用是「图像 Embedder 未就绪」最常见根因 ——
            # 给出可执行指引(去设置页装 PyTorch),而不是只丢个 pip 命令。
            torch_hint = ""
            try:
                from chayuan.server.runtime.pytorch_installer import (
                    torch_runtime_unavailable_reason,
                )
                torch_hint = torch_runtime_unavailable_reason() or ""
            except Exception:  # noqa: BLE001
                pass
            return [_error_chunk(
                "图像 Embedder 未就绪"
                + (f"。{torch_hint}" if torch_hint else "")
                + (f" 缺少依赖,可安装:`{hint}`。" if hint else "（缺 torch/transformers）")
                + "或到「模型配置」页下载其他已就绪模型。",
                self.source_id,
            )]
        # 能力检查：仅视觉模型（DINOv2/ResNet）不支持文本查图
        caps = getattr(emb, "capabilities", None)
        if caps is not None and not caps.text:
            return [_error_chunk(
                f"模型 {self._model_name} 仅支持以图搜图（纯视觉编码器），"
                "不支持用文本查图；请在调用端切到 /image/search_by_image 或"
                "更换为 CLIP/SigLIP 等跨模态模型重建 KB。",
                self.source_id,
            )]
        try:
            q_vec = emb.embed_text(query.query or "")
        except Exception as e:  # noqa: BLE001
            return [_error_chunk(f"文本向量化失败：{e}", self.source_id)]

        mismatch = self._check_consistency(emb)
        if mismatch:
            return [_error_chunk(mismatch, self.source_id)]

        # ⚠ store 公开 API 是 search_image / search_text;没有裸 search() —
        # 调 .search() 会抛 AttributeError 被路由 500 兜住,用户看到"无法搜出结
        # 果"且无具体诊断。这里走 search_image:text → image 跨模态查图,query
        # 向量空间跟 image_vector 同向(用户 KB 用 CLIP/SigLIP 时成立)。
        store = get_store(self._source_name)
        hits = store.search_image(q_vec, top_k=max(1, int(query.top_k or 5)))
        if not hits:
            total = store.count()
            if total == 0:
                msg = f"图像索引 {self._source_name!r} 为空,请先上传图片完成索引。"
            else:
                msg = (
                    f"图像索引 {self._source_name!r} 共 {total} 张,但当前查询"
                    "无匹配。可能原因:图像未完成视觉向量化(看 KB 详情页有无"
                    "「未索引」标记,修好 image-embedding 模型后重新上传)。"
                )
            return [_error_chunk(msg, self.source_id)]
        return [self._hit_to_chunk(rec, score) for rec, score in hits]

    # ---- 图像 → 图像 ----

    def search_by_image(self, src, top_k: int = 5) -> List[RetrievalChunk]:
        emb = get_embedder(self._model_name)
        if not emb.is_available():
            return [_error_chunk("图像向量模型未就绪,请到「设置 → 图像嵌入」检查。", self.source_id)]
        try:
            q_vec = emb.embed_image(src)
        except Exception as e:  # noqa: BLE001
            return [_error_chunk(f"图像向量化失败:{e}", self.source_id)]
        mismatch = self._check_consistency(emb)
        if mismatch:
            return [_error_chunk(mismatch, self.source_id)]
        # 同上:走 search_image 而非不存在的 .search()
        store = get_store(self._source_name)
        hits = store.search_image(q_vec, top_k=max(1, int(top_k or 5)))
        if not hits:
            total = store.count()
            if total == 0:
                msg = f"图像知识库为空,请先上传图片。"
            else:
                msg = (
                    f"知识库共 {total} 张图,但没找到相似的。可能图像未完成"
                    "视觉向量化,看 KB 详情页有无「未索引」标记。"
                )
            return [_error_chunk(msg, self.source_id)]
        return [self._hit_to_chunk(rec, score) for rec, score in hits]

    # ---- 添加 / 移除 ----

    def add_image(self, path: str, *, ocr_text: str = "", tags: str = "") -> str:
        """把一张本地图像 embed 后加入索引，返回 id。

        **索引元数据**会同时写 ``embedder_model`` / ``embedder_dim`` /
        ``capabilities``——这是搜索一致性的基石：检索端用同名 embedder 拿
        query 向量，store 侧可以在 dim 层面立即发现错配。

        历史 bug：本方法原先调 ``store.add()``，但 store.py 的 schema 改造后
        公开 API 已换成 ``insert_placeholder`` + ``add_image_vector``，``add()``
        被删 → 文件夹同步(folder_sync)的图像 ingest 每次都 ``AttributeError``，
        图片入不了库且不走 CLIP。这里改用现有 store API：先插占位记录，再写
        CLIP 图像向量，让 folder_sync 路径与 HTTP upload 流水线保持一致。
        """
        if not os.path.isfile(path):
            raise ConnectorError(f"图像文件不存在：{path}", code="not_found", dialect="image")
        import hashlib

        emb = get_embedder(self._model_name)
        # CLIP 图像向量 —— 失败直接抛 ConnectorError，**不静默**，让 folder_sync
        # uploader 把它记进 errors（CLAUDE.md：图像数据必须带诊断，不静默丢弃）。
        try:
            vec = emb.embed_image(path)
        except Exception as e:  # noqa: BLE001
            # 把底层 stack 翻译成可执行指引:torch / image-embedding 运行时不可用
            # 时,明确告诉用户去「设置 → 本地模型服务 → PyTorch」装。
            raw = f"{type(e).__name__}: {e}"
            friendly = ""
            try:
                from chayuan.server.image_source.pipeline import _friendly_clip_error
                friendly = _friendly_clip_error(raw)
            except Exception:  # noqa: BLE001
                pass
            msg = friendly or f"图像向量化失败（CLIP embedder 不可用）：{raw}"
            raise ConnectorError(msg, code="embed_failed", dialect="image") from e
        caps = getattr(emb, "capabilities", None)

        with open(path, "rb") as f:
            data = f.read()
        md5_full = hashlib.md5(data).hexdigest()
        item_id = f"img_{md5_full[:12]}"
        store = get_store(self._source_name)
        # 去重：同 hash 已在库直接返回，不重复写向量
        existing = store.get(item_id)
        if existing is not None and existing.get("image_vector_id") is not None:
            return item_id

        if existing is None:
            store.insert_placeholder(
                item_id=item_id,
                filename=os.path.basename(path),
                mime_type="image/" + (os.path.splitext(path)[-1].lstrip(".").lower() or "octet-stream"),
                size_bytes=len(data),
                path=path,
                md5=md5_full,
                tags=(tags or "")[:200],
            )
        store.add_image_vector(item_id, vec)
        store.update(
            item_id,
            state="ready",
            progress=100,
            ocr_text=(ocr_text or "")[:2000] or None,
            embedder_model=self._model_name,
            embedder_dim=int(getattr(emb, "dim", 0) or 0),
            embedder_capabilities=caps.to_dict() if caps else None,
        )
        store.flush()
        return item_id

    # ---- 格式化 ----

    def _hit_to_chunk(self, record: dict, score: float) -> RetrievalChunk:
        path = record.get("path") or ""
        fname = os.path.basename(path) if path else "(unknown)"
        download_url, preview_url = _download_urls(self.source_id, record.get("id", ""))
        content_lines = [
            f"**图像**：`{fname}`",
            f"**模型**：{record.get('embedder_model') or self._model_name}",
            f"**得分**：{score:.4f}",
        ]
        ocr = record.get("ocr_text") or ""
        if ocr:
            content_lines.append(f"\n**OCR 文本**：{ocr[:400]}")
        tags = record.get("tags") or ""
        if tags:
            content_lines.append(f"**标签**：{tags}")
        return RetrievalChunk(
            content="\n".join(content_lines),
            citation=Citation(
                title=fname,
                source_id=self.source_id,
                source_kind=self.source_kind,
                download_url=download_url,
                preview_url=preview_url,
                meta={
                    "path": path, "image_id": record.get("id"),
                    "md5": record.get("md5"),
                    "embedder_model": record.get("embedder_model"),
                },
            ),
            score=float(score),
            source_id=self.source_id,
            source_kind=self.source_kind,
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _error_chunk(msg: str, source_id: int) -> RetrievalChunk:
    return RetrievalChunk(
        content=msg,
        citation=Citation(
            title="图像源错误", source_id=source_id, source_kind="image",
            meta={"error": True},
        ),
        score=0.0, source_id=source_id, source_kind="image",
    )


def _download_urls(source_id: int, image_id: str) -> Tuple[str, str]:
    if not image_id:
        return "", ""
    base = f"/knowledge_source/{int(source_id)}/image"
    return (
        f"{base}/{image_id}?download=true",
        f"{base}/{image_id}?download=false",
    )
