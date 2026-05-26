"""图像向量存储(state-aware + 双索引)。

变更点(2026-05-16):
- 元数据 schema 加 state/progress/ocr_text/ocr_lang/ocr_confidence/has_text_vector/
  image_vector_id/text_vector_id 字段
- 双向量矩阵:image_matrix (CLIP, 512 dim) + text_matrix (bge-m3, 1024 dim)
- 老 meta.json 缺字段时,_load 自动补默认值
- insert_placeholder / update / get / add_image_vector / add_text_vector /
  search_image / search_text 替代老 add / search
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("chayuan.image_source.store")


def _image_indexes_root() -> Path:
    base = os.environ.get("CHAYUAN_ROOT")
    p = Path(base) if base else Path.home() / "chayuan_data"
    root = p / "data" / "image_indexes"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _md5_of(path: str) -> str:
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        return ""


_LEGACY_DEFAULTS = {
    "state": "ready",
    "progress": 100,
    "error": None,
    "ocr_text": None,
    "ocr_lang": None,
    "ocr_confidence": None,
    "has_text_vector": False,
    "image_vector_id": None,
    "text_vector_id": None,
}


class ImageStore:
    """一个 source 的图像索引。numpy brute-force(<= 10 万图)。

    持久化文件(<CHAYUAN_ROOT>/data/image_indexes/<source_name>/):
        meta.json        ← items 列表
        vectors.npy      ← CLIP image 向量矩阵(N_img, 512)
        text_vectors.npy ← bge-m3 文本向量矩阵(N_text, 1024)
    """

    def __init__(self, source_name: str):
        self.source_name = source_name
        self.root = _image_indexes_root() / source_name
        self.root.mkdir(parents=True, exist_ok=True)
        self._meta_path = self.root / "meta.json"
        self._vec_path = self.root / "vectors.npy"
        self._text_vec_path = self.root / "text_vectors.npy"
        self._meta: List[Dict[str, Any]] = []
        self._matrix = None       # np.ndarray (N_img, dim_img)
        self._text_matrix = None  # np.ndarray (N_text, dim_text)
        self._dim = 0
        self._text_dim = 0
        self._lock = threading.Lock()
        self._load()

    # ---- 持久化 ----

    def _load(self) -> None:
        try:
            if self._meta_path.exists():
                raw = json.loads(self._meta_path.read_text(encoding="utf-8"))
                # 老数据迁移:每条 item 缺字段补默认
                self._meta = [self._migrate_record(r) for r in raw]
        except Exception as e:  # noqa: BLE001
            logger.warning("读取 meta 失败:%r", e)
            self._meta = []
        import numpy as np
        try:
            if self._vec_path.exists():
                self._matrix = np.load(str(self._vec_path))
                if self._matrix is not None and self._matrix.shape:
                    self._dim = int(self._matrix.shape[1])
        except Exception as e:  # noqa: BLE001
            logger.warning("读取 image vectors 失败:%r", e)
            self._matrix = None
        try:
            if self._text_vec_path.exists():
                self._text_matrix = np.load(str(self._text_vec_path))
                if self._text_matrix is not None and self._text_matrix.shape:
                    self._text_dim = int(self._text_matrix.shape[1])
        except Exception as e:  # noqa: BLE001
            logger.warning("读取 text vectors 失败:%r", e)
            self._text_matrix = None

    @staticmethod
    def _migrate_record(rec: Dict[str, Any]) -> Dict[str, Any]:
        for k, v in _LEGACY_DEFAULTS.items():
            rec.setdefault(k, v)
        # 老数据有 path 没 image_vector_id;给个默认推断
        if rec.get("image_vector_id") is None and "path" in rec:
            # 老数据按 _meta 顺序对应 _matrix 行,不再用 image_vector_id 索引,
            # 留 None 表示"按行号匹配的老布局",新数据才填整型 id。
            pass
        return rec

    def _save(self) -> None:
        import numpy as np
        self._meta_path.write_text(
            json.dumps(self._meta, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        if self._matrix is not None:
            np.save(str(self._vec_path), self._matrix)
        if self._text_matrix is not None:
            np.save(str(self._text_vec_path), self._text_matrix)

    def flush(self) -> None:
        with self._lock:
            self._save()

    # ---- 元数据 CRUD ----

    def insert_placeholder(
        self, *, item_id: str, filename: str, mime_type: str,
        size_bytes: int, path: str, thumbnail_path: str = "",
        md5: str = "", tags: str = "",
    ) -> Dict[str, Any]:
        """插入一条占位 item(state=queued)。返回完整 record。"""
        with self._lock:
            for existing in self._meta:
                if existing.get("id") == item_id:
                    return existing
            rec: Dict[str, Any] = {
                "id": item_id,
                "filename": filename,
                "mime_type": mime_type,
                "size_bytes": int(size_bytes),
                "path": path,
                "thumbnail_path": thumbnail_path,
                "md5": md5 or (_md5_of(path) if os.path.isfile(path) else ""),
                "tags": tags or "",
                "created_at": time.time(),
                "updated_at": time.time(),
                **dict(_LEGACY_DEFAULTS),
                "state": "queued",
                "progress": 0,
            }
            self._meta.append(rec)
            self._save()
            return rec

    def update(self, item_id: str, **fields) -> Optional[Dict[str, Any]]:
        """部分更新 item 字段;不存在返 None。自动 bump updated_at。"""
        with self._lock:
            for rec in self._meta:
                if rec.get("id") == item_id:
                    rec.update(fields)
                    rec["updated_at"] = time.time()
                    self._save()
                    return rec
            return None

    def get(self, item_id: str) -> Optional[Dict[str, Any]]:
        for rec in self._meta:
            if rec.get("id") == item_id:
                return rec
        return None

    def remove(self, item_id: str) -> bool:
        """删 item + 同步两个向量矩阵。"""
        import numpy as np
        with self._lock:
            idx = next(
                (i for i, r in enumerate(self._meta) if r.get("id") == item_id),
                None,
            )
            if idx is None:
                return False
            rec = self._meta.pop(idx)
            img_vid = rec.get("image_vector_id")
            txt_vid = rec.get("text_vector_id")
            # 删向量矩阵的对应行 —— 严格 bounds-check。矩阵跟 meta 不同步时
            # (partial 图从没拿到 CLIP 向量、.npy 丢失 / 被清空等)只删 meta、
            # 不碰矩阵 —— 不能让一次删除把整个端点打成 500。
            if isinstance(img_vid, int):
                # 新布局:image_vector_id 即矩阵行号
                if self._matrix is not None and 0 <= img_vid < self._matrix.shape[0]:
                    self._matrix = np.delete(self._matrix, img_vid, axis=0)
                    # 后续行的 image_vector_id 减 1
                    for r in self._meta:
                        v = r.get("image_vector_id")
                        if isinstance(v, int) and v > img_vid:
                            r["image_vector_id"] = v - 1
                else:
                    logger.warning(
                        "remove(%s): image_vector_id=%s 越界 / 矩阵为空,跳过矩阵删除",
                        item_id, img_vid,
                    )
            elif self._matrix is not None and 0 <= idx < self._matrix.shape[0]:
                # 老布局:按 _meta 行号对应 _matrix 行
                self._matrix = np.delete(self._matrix, idx, axis=0)
            if isinstance(txt_vid, int):
                if (self._text_matrix is not None
                        and 0 <= txt_vid < self._text_matrix.shape[0]):
                    self._text_matrix = np.delete(self._text_matrix, txt_vid, axis=0)
                    for r in self._meta:
                        v = r.get("text_vector_id")
                        if isinstance(v, int) and v > txt_vid:
                            r["text_vector_id"] = v - 1
                else:
                    logger.warning(
                        "remove(%s): text_vector_id=%s 越界 / 文本矩阵为空,跳过矩阵删除",
                        item_id, txt_vid,
                    )
            self._save()
            return True

    # ---- 向量 ----

    def add_image_vector(self, item_id: str, vector) -> int:
        import numpy as np
        with self._lock:
            vec = np.asarray(vector, dtype="float32").reshape(-1)
            if self._matrix is None:
                self._matrix = vec.reshape(1, -1)
                self._dim = int(vec.shape[0])
            else:
                if int(vec.shape[0]) != self._dim:
                    raise ValueError(
                        f"image 向量维度不一致:{self._dim} vs {vec.shape[0]}"
                    )
                self._matrix = np.vstack([self._matrix, vec.reshape(1, -1)])
            new_id = int(self._matrix.shape[0]) - 1
            for rec in self._meta:
                if rec.get("id") == item_id:
                    rec["image_vector_id"] = new_id
                    rec["updated_at"] = time.time()
                    break
            self._save()
            return new_id

    def add_text_vector(self, item_id: str, vector) -> int:
        import numpy as np
        with self._lock:
            vec = np.asarray(vector, dtype="float32").reshape(-1)
            if self._text_matrix is None:
                self._text_matrix = vec.reshape(1, -1)
                self._text_dim = int(vec.shape[0])
            else:
                if int(vec.shape[0]) != self._text_dim:
                    raise ValueError(
                        f"text 向量维度不一致:{self._text_dim} vs {vec.shape[0]}"
                    )
                self._text_matrix = np.vstack([self._text_matrix, vec.reshape(1, -1)])
            new_id = int(self._text_matrix.shape[0]) - 1
            for rec in self._meta:
                if rec.get("id") == item_id:
                    rec["text_vector_id"] = new_id
                    rec["has_text_vector"] = True
                    rec["updated_at"] = time.time()
                    break
            self._save()
            return new_id

    # ---- 检索 ----

    def search_image(self, query_vec, top_k: int = 5) -> List[Tuple[Dict[str, Any], float]]:
        return self._search_generic(
            self._matrix, query_vec, top_k,
            row_id_field="image_vector_id",
        )

    def search_text(self, query_vec, top_k: int = 5) -> List[Tuple[Dict[str, Any], float]]:
        return self._search_generic(
            self._text_matrix, query_vec, top_k,
            row_id_field="text_vector_id",
        )

    def _search_generic(
        self, matrix, query_vec, top_k, row_id_field: str,
    ) -> List[Tuple[Dict[str, Any], float]]:
        import numpy as np
        if matrix is None or not len(self._meta):
            return []
        q = np.asarray(query_vec, dtype="float32").reshape(-1)
        qn = float(np.linalg.norm(q)) or 1.0
        q = q / qn
        sims = matrix @ q
        order = np.argsort(-sims)
        out: List[Tuple[Dict[str, Any], float]] = []
        # 反向映射:row → item
        row_to_item: Dict[int, Dict[str, Any]] = {}
        for i, rec in enumerate(self._meta):
            v = rec.get(row_id_field)
            if isinstance(v, int):
                row_to_item[v] = rec
            elif row_id_field == "image_vector_id" and i < matrix.shape[0]:
                # 老布局:行号 == _meta 下标
                row_to_item.setdefault(i, rec)
        for row in order[: int(top_k)]:
            rec = row_to_item.get(int(row))
            if rec is None:
                continue
            out.append((rec, float(sims[int(row)])))
        return out

    # ---- 兼容旧 API ----

    def count(self) -> int:
        return len(self._meta)

    def dim(self) -> int:
        return int(self._dim)

    def text_dim(self) -> int:
        return int(self._text_dim)

    def all_paths(self) -> List[str]:
        return [m.get("path") or "" for m in self._meta]

    def list_items(self, limit: int = 200) -> List[Dict[str, Any]]:
        return list(self._meta[-int(limit):])


# ---- 单例 per source ----

_STORES: Dict[str, ImageStore] = {}
_STORE_LOCK = threading.Lock()


def get_store(source_name: str) -> ImageStore:
    with _STORE_LOCK:
        if source_name not in _STORES:
            _STORES[source_name] = ImageStore(source_name)
        return _STORES[source_name]


def invalidate(source_name: str = "") -> None:
    with _STORE_LOCK:
        if source_name:
            _STORES.pop(source_name, None)
        else:
            _STORES.clear()
