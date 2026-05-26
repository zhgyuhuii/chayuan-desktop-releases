"""单机模式本地向量库抽象 + 默认 sqlite-vec 实现(Phase 4)。

设计目标(``CLAUDE.md §2.3 / §3.3``):

* **零外部进程**:嵌入式向量库,与业务 SQLite 同库共存,单文件、纯 C 扩展
* **API 通用**:抽象 ``LocalVectorStore`` 接口,默认实现 ``SqliteVecStore``;
  未来想换 LanceDB / 其它本地后端,Phase 5 ``single_machine`` profile 在
  bootstrap 阶段挑实现即可
* **集成路径**:本模块只暴露 ``upsert / search / delete / drop`` 等纯向量操作,
  与现有 ``KBService`` 解耦;后续 ``sqlite_vec_kb_service.py`` 把 ``KBService``
  接口适配到本接口上(Phase 5 接)

**为什么不直接走 langchain VectorStore?**
langchain 的 VectorStore 抽象耦合了 ``Document`` 概念 + LLM 工具链,本模块的
责任只到「向量索引」一层,文档分块 / embedding 生成 / hybrid 召回 在更上层
``retrieval/`` 编排里完成。两层分清楚便于 unit test。

**为什么不依赖 ``sqlalchemy``?**
sqlite-vec 走的是 SQLite ``vec0`` 虚拟表,SQL 语法与 SQLAlchemy ORM 难以适配
(MATCH 操作符 + 距离排序);直接用 ``sqlite3`` stdlib 更简单、调试方便。
业务库的 SQLAlchemy 连接独立维护,本模块只通过 ``data_dir`` 共享同一目录。

**线程安全**:每个 ``SqliteVecStore`` 实例持有一个 ``sqlite3.Connection``,
不跨线程共享。多线程 / asyncio 场景下用 ``LocalVectorStoreFactory.get(data_dir)``
拿一个 thread-local 的连接(实现见 Phase 5)。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import struct
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

logger = logging.getLogger("chayuan.retrieval.vector_store_local")


# ──────────────────────────────────────────────────────────────────────
# 数据类
# ──────────────────────────────────────────────────────────────────────


@dataclass
class VectorEntry:
    """一条待写入的向量记录。

    Attributes:
        id: 向量唯一标识(应用层语义,例如文档分块的 chunk id)。
        vector: 浮点向量;维度需与 collection 创建时一致。
        text: 原始文本(返回结果时回显;也用于 BM25 fallback,本期不实现)。
        payload: 业务自定义元数据(JSON 可序列化);用于 search 时的 filter。
    """

    id: str
    vector: Sequence[float]
    text: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VectorHit:
    """KNN 命中结果(已按距离升序排序)。

    Attributes:
        id: VectorEntry.id 原值。
        distance: 距离值(cosine / l2,语义按 collection 创建时设的度量)。
        score: 归一化相似度,1 / (1 + distance);UI 友好,越大越相似。
        text: VectorEntry.text 原值。
        payload: VectorEntry.payload 原值。
    """

    id: str
    distance: float
    score: float
    text: str
    payload: Dict[str, Any]


@dataclass
class CollectionSpec:
    """创建 collection 的参数。``dim`` 必填,其余有默认。"""

    name: str
    dim: int
    metric: str = "cosine"  # 'cosine' | 'l2' | 'l1'


# ──────────────────────────────────────────────────────────────────────
# 抽象接口
# ──────────────────────────────────────────────────────────────────────


class LocalVectorStore(ABC):
    """单机模式本地向量库统一接口。

    所有方法对未知 collection 的行为:
        * ``upsert``:首次写入时若 collection 不存在,**自动按 ``vector`` 维度
          创建**(metric 默认 cosine);需要显式控制的用 ``ensure_collection``
        * 其它:对不存在 collection 抛 ``KeyError``
    """

    @abstractmethod
    def ensure_collection(self, spec: CollectionSpec) -> None:
        """显式创建 collection(已存在为 no-op)。维度/metric 不一致时抛 ValueError。"""

    @abstractmethod
    def list_collections(self) -> List[str]:
        """列出全部 collection 名(顺序稳定:按创建顺序)。"""

    @abstractmethod
    def drop_collection(self, name: str) -> None:
        """删除 collection 及其全部向量。不存在为 no-op。"""

    @abstractmethod
    def count(self, collection: str) -> int:
        """该 collection 中的向量数量。"""

    @abstractmethod
    def upsert(self, collection: str, entries: Iterable[VectorEntry]) -> int:
        """写入向量。同 id 覆盖(upsert 语义)。返回实际写入条数。"""

    @abstractmethod
    def delete(self, collection: str, ids: Iterable[str]) -> int:
        """按 id 删除。返回实际删除条数。"""

    @abstractmethod
    def search(
        self,
        collection: str,
        query: Sequence[float],
        k: int = 10,
        *,
        filter: Optional[Mapping[str, Any]] = None,
    ) -> List[VectorHit]:
        """KNN 检索。

        Args:
            collection: 目标 collection
            query: 查询向量(维度需与 collection dim 一致)
            k: top-k
            filter: 可选 metadata filter,语义为 payload 的精确匹配(AND);
                值为 ``list / tuple`` 时退化为 IN。**v0 仅支持等值 / IN**;
                范围 / 全文 等高级 filter 留给 Phase 5。

        Returns:
            按距离升序的命中列表;collection 不存在或为空时返回 ``[]``。
        """

    @abstractmethod
    def close(self) -> None:
        """释放底层资源(连接 / 文件句柄)。可重入。"""


# ──────────────────────────────────────────────────────────────────────
# sqlite-vec 默认实现
# ──────────────────────────────────────────────────────────────────────


# 合法的 collection / 列名:仅字母数字下划线,首字符非数字。
# 用于 SQL 标识符拼接前的白名单校验,避免任何形式的 SQL 注入。
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _quote_ident(name: str) -> str:
    """校验 + 双引号包裹的 SQL 标识符。"""
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(
            f"invalid identifier {name!r};仅允许 [A-Za-z_][A-Za-z0-9_]{{0,62}}"
        )
    return f'"{name}"'


def _serialize_vector(v: Sequence[float]) -> bytes:
    """sqlite-vec ``vec0`` 虚拟表把 ``FLOAT[N]`` 列以 little-endian f32 字节流存。

    与 ``sqlite_vec.serialize_float32`` 等价;本地实现避免依赖该包的可选 helper
    (sqlite_vec 包主要导出 load(),helper 在 0.x 版本里有变动)。
    """
    return struct.pack(f"{len(v)}f", *v)


_METRIC_KEYWORDS = {
    "cosine": "cosine",
    "l2": "l2",
    "euclidean": "l2",
    "l1": "l1",
    "manhattan": "l1",
}


class SqliteVecStore(LocalVectorStore):
    """基于 sqlite-vec 的嵌入式向量库实现。

    单文件 SQLite 数据库,虚拟表 ``vec_<col>`` 存向量,普通表 ``payload_<col>``
    存原文 + JSON metadata。两表通过 ``id`` 主键 join。

    维度元信息存在 ``vec_meta`` 表里:
        ``CREATE TABLE vec_meta(name PRIMARY KEY, dim, metric, created_at)``

    使用方式::

        store = SqliteVecStore.open("/path/to/data_dir")
        store.ensure_collection(CollectionSpec("kb_default", dim=1024))
        store.upsert("kb_default", [VectorEntry(id="c1", vector=[...], text="...")])
        hits = store.search("kb_default", query=[...], k=5)
        store.close()
    """

    # 虚拟表 / payload 表的命名前缀。两者前缀不同,避免人工 SHOW TABLES 时混淆。
    _VEC_PREFIX = "vec_"
    _PAYLOAD_PREFIX = "payload_"

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.RLock()
        self._closed = False
        self._bootstrap()

    # ── 工厂 ────────────────────────────────────────────────────────

    @classmethod
    def open(
        cls,
        data_dir: str | Path,
        *,
        filename: str = "vectors.sqlite",
    ) -> "SqliteVecStore":
        """在 ``<data_dir>/`` 下打开(或新建)向量库文件。

        会:
            1. ``mkdir -p <data_dir>``
            2. 开 sqlite3 连接,WAL 模式,加载 ``vec0`` 扩展
            3. 建元数据表

        失败抛 ``RuntimeError`` 并附扩展加载诊断(常见:Python sqlite3 编译时
        ``--disable-loadable-extensions``;改用 conda-forge / pyenv 编译版本)。
        """
        data_path = Path(data_dir)
        data_path.mkdir(parents=True, exist_ok=True)
        db_path = data_path / filename

        conn = sqlite3.connect(
            str(db_path), check_same_thread=False, isolation_level=None
        )
        try:
            conn.enable_load_extension(True)
        except sqlite3.NotSupportedError as e:
            conn.close()
            raise RuntimeError(
                "当前 Python 的 sqlite3 编译时禁用了可加载扩展,无法启用 sqlite-vec。"
                "请改用 conda-forge / pyenv 编译版本,或在 macOS 上 brew install sqlite。"
            ) from e
        try:
            import sqlite_vec  # 延迟 import,允许其它模块在没装 sqlite_vec 时被 import
        except ImportError as e:
            conn.close()
            raise RuntimeError(
                "缺失依赖 sqlite-vec;请 ``pip install sqlite-vec`` 后重试。"
            ) from e
        try:
            sqlite_vec.load(conn)
        except sqlite3.OperationalError as e:
            conn.close()
            raise RuntimeError(f"sqlite-vec 扩展加载失败:{e}") from e
        finally:
            # 即便 load 成功也关掉 enable_load_extension,缩小 SQL 注入表面。
            try:
                conn.enable_load_extension(False)
            except Exception:  # noqa: BLE001
                pass

        # WAL + NORMAL fsync 模式(单机版重构后的标准布局,详见 CLAUDE.md §2.2)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")

        return cls(conn)

    # ── 内部 ────────────────────────────────────────────────────────

    def _bootstrap(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vec_meta (
                    name        TEXT PRIMARY KEY,
                    dim         INTEGER NOT NULL,
                    metric      TEXT NOT NULL,
                    created_at  REAL NOT NULL DEFAULT (julianday('now'))
                )
                """
            )

    def _meta(self, collection: str) -> Optional[Dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT name, dim, metric, created_at FROM vec_meta WHERE name = ?",
            (collection,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"name": row[0], "dim": row[1], "metric": row[2], "created_at": row[3]}

    def _vec_table(self, collection: str) -> str:
        return _quote_ident(self._VEC_PREFIX + collection)

    def _payload_table(self, collection: str) -> str:
        return _quote_ident(self._PAYLOAD_PREFIX + collection)

    # ── 接口实现 ────────────────────────────────────────────────────

    def ensure_collection(self, spec: CollectionSpec) -> None:
        if not _IDENTIFIER_RE.match(spec.name):
            raise ValueError(
                f"invalid collection name {spec.name!r};"
                "仅允许 [A-Za-z_][A-Za-z0-9_]{0,62}"
            )
        metric = _METRIC_KEYWORDS.get(spec.metric.lower())
        if metric is None:
            raise ValueError(
                f"unsupported metric {spec.metric!r};仅 cosine / l2 / l1"
            )
        if spec.dim <= 0:
            raise ValueError(f"dim must be positive, got {spec.dim}")

        with self._lock:
            existing = self._meta(spec.name)
            if existing:
                if existing["dim"] != spec.dim:
                    raise ValueError(
                        f"collection {spec.name!r} 已存在但维度不匹配:"
                        f"{existing['dim']} vs {spec.dim}"
                    )
                if existing["metric"] != metric:
                    raise ValueError(
                        f"collection {spec.name!r} 已存在但 metric 不匹配:"
                        f"{existing['metric']} vs {metric}"
                    )
                return

            vec_t = self._vec_table(spec.name)
            payload_t = self._payload_table(spec.name)

            # vec0 虚拟表;``embedding FLOAT[N] DISTANCE_METRIC=...`` 是 sqlite-vec 0.1.x 语法
            self._conn.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {vec_t}
                USING vec0(
                    id TEXT PRIMARY KEY,
                    embedding FLOAT[{spec.dim}] DISTANCE_METRIC={metric}
                )
                """
            )
            # payload 普通表;FOREIGN KEY 跨虚拟表 sqlite 不允许,靠应用层一致性。
            self._conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {payload_t} (
                    id      TEXT PRIMARY KEY,
                    text    TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL DEFAULT '{{}}'
                )
                """
            )
            self._conn.execute(
                "INSERT INTO vec_meta(name, dim, metric) VALUES (?, ?, ?)",
                (spec.name, spec.dim, metric),
            )

    def list_collections(self) -> List[str]:
        cur = self._conn.execute(
            "SELECT name FROM vec_meta ORDER BY created_at, name"
        )
        return [r[0] for r in cur.fetchall()]

    def drop_collection(self, name: str) -> None:
        with self._lock:
            if not self._meta(name):
                return
            vec_t = self._vec_table(name)
            payload_t = self._payload_table(name)
            self._conn.execute(f"DROP TABLE IF EXISTS {vec_t}")
            self._conn.execute(f"DROP TABLE IF EXISTS {payload_t}")
            self._conn.execute("DELETE FROM vec_meta WHERE name = ?", (name,))

    def count(self, collection: str) -> int:
        meta = self._meta(collection)
        if not meta:
            raise KeyError(collection)
        cur = self._conn.execute(f"SELECT COUNT(*) FROM {self._vec_table(collection)}")
        return int(cur.fetchone()[0])

    def upsert(self, collection: str, entries: Iterable[VectorEntry]) -> int:
        # 抓第一条以推断维度(若 collection 不存在则隐式创建)
        items = list(entries)
        if not items:
            return 0

        with self._lock:
            meta = self._meta(collection)
            if meta is None:
                self.ensure_collection(
                    CollectionSpec(name=collection, dim=len(items[0].vector))
                )
                meta = self._meta(collection)
                assert meta is not None
            dim = meta["dim"]

            vec_t = self._vec_table(collection)
            payload_t = self._payload_table(collection)
            count = 0

            # vec0 不支持 INSERT OR REPLACE;走 DELETE-then-INSERT。
            for e in items:
                if len(e.vector) != dim:
                    raise ValueError(
                        f"vector dim mismatch for {e.id}: expected {dim}, got {len(e.vector)}"
                    )
                blob = _serialize_vector(e.vector)
                # 删 + 插(单条事务,失败不会留半截)
                self._conn.execute(f"DELETE FROM {vec_t} WHERE id = ?", (e.id,))
                self._conn.execute(
                    f"INSERT INTO {vec_t}(id, embedding) VALUES (?, ?)",
                    (e.id, blob),
                )
                self._conn.execute(
                    f"""
                    INSERT INTO {payload_t}(id, text, payload) VALUES (?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET text=excluded.text, payload=excluded.payload
                    """,
                    (e.id, e.text, json.dumps(e.payload, ensure_ascii=False)),
                )
                count += 1
            return count

    def delete(self, collection: str, ids: Iterable[str]) -> int:
        meta = self._meta(collection)
        if meta is None:
            raise KeyError(collection)

        ids_list = list(ids)
        if not ids_list:
            return 0
        vec_t = self._vec_table(collection)
        payload_t = self._payload_table(collection)
        placeholders = ",".join(["?"] * len(ids_list))
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM {vec_t} WHERE id IN ({placeholders})",
                ids_list,
            )
            removed = cur.rowcount or 0
            self._conn.execute(
                f"DELETE FROM {payload_t} WHERE id IN ({placeholders})",
                ids_list,
            )
            return int(removed)

    def search(
        self,
        collection: str,
        query: Sequence[float],
        k: int = 10,
        *,
        filter: Optional[Mapping[str, Any]] = None,
    ) -> List[VectorHit]:
        meta = self._meta(collection)
        if meta is None:
            return []
        if k <= 0:
            return []
        if len(query) != meta["dim"]:
            raise ValueError(
                f"query dim mismatch: expected {meta['dim']}, got {len(query)}"
            )

        vec_t = self._vec_table(collection)
        payload_t = self._payload_table(collection)

        # KNN over-fetch 系数:sqlite-vec 的 ``WHERE k = ?`` 是裁后 limit;
        # 加 filter 时先取多一点再过滤,避免 filter 把 top-k 砍空。
        oversample = 4 if filter else 1
        knn_k = max(k, k * oversample)

        # MATCH-style: SELECT id, distance FROM vec_t WHERE embedding MATCH ? AND k = ?
        # 注意:sqlite-vec KNN 以 ORDER BY distance ASC 暗含,显式写也不出错。
        cur = self._conn.execute(
            f"""
            SELECT v.id, v.distance, p.text, p.payload
            FROM {vec_t} AS v
            JOIN {payload_t} AS p ON v.id = p.id
            WHERE v.embedding MATCH ? AND v.k = ?
            ORDER BY v.distance ASC
            """,
            (_serialize_vector(query), knn_k),
        )

        results: List[VectorHit] = []
        for row in cur:
            payload = json.loads(row[3] or "{}")
            if filter and not _payload_matches(payload, filter):
                continue
            distance = float(row[1])
            score = 1.0 / (1.0 + distance) if distance >= 0 else 1.0
            results.append(
                VectorHit(
                    id=row[0],
                    distance=distance,
                    score=score,
                    text=row[2] or "",
                    payload=payload,
                )
            )
            if len(results) >= k:
                break
        return results

    def close(self) -> None:
        if self._closed:
            return
        with self._lock:
            try:
                self._conn.close()
            finally:
                self._closed = True


def _payload_matches(payload: Mapping[str, Any], flt: Mapping[str, Any]) -> bool:
    """``filter`` 语义:全部 AND;每键的值为 list/tuple 时表示 IN。"""
    for k, v in flt.items():
        actual = payload.get(k)
        if isinstance(v, (list, tuple, set)):
            if actual not in v:
                return False
        else:
            if actual != v:
                return False
    return True


# ──────────────────────────────────────────────────────────────────────
# 工厂(便于 Phase 5 bootstrap 从 env 挑实现)
# ──────────────────────────────────────────────────────────────────────


def open_default_local_vector_store(data_dir: str | Path) -> LocalVectorStore:
    """单机模式默认本地向量库。

    ``CHAYUAN_VECTOR_STORE`` env 选择实现:
        * 未设 / ``sqlite-vec``:``SqliteVecStore``
        * 其它:暂不支持(``lancedb`` 等留给 Phase 5)

    单一入口便于 Phase 5 bootstrap 在不同 profile 切换实现,业务层只 ``import``
    ``LocalVectorStore`` 类型即可。
    """
    import os

    backend = os.environ.get("CHAYUAN_VECTOR_STORE", "sqlite-vec").strip().lower()
    if backend in ("sqlite-vec", "sqlitevec", "sqlite_vec"):
        return SqliteVecStore.open(data_dir)
    raise NotImplementedError(
        f"local vector store backend {backend!r} 未实现;当前仅支持 sqlite-vec"
    )


__all__ = [
    "VectorEntry",
    "VectorHit",
    "CollectionSpec",
    "LocalVectorStore",
    "SqliteVecStore",
    "open_default_local_vector_store",
]
