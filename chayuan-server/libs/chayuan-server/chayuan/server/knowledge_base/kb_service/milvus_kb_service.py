import logging
import os
import threading
from typing import Dict, List, Optional

from langchain_core.documents import Document

logger = logging.getLogger("chayuan.kb.milvus")

# 进程级锁:序列化 pymilvus.connections 的注册/断开。
# 知识中心并行查询多个 KB 时,_process_one_ku 通过 asyncio.to_thread 并发触发
# 多个 MilvusKBService._load_milvus,它们共享同一个 pymilvus.connections 单例。
# 没有锁时,A 的 disconnect / re-register 会把 B 正在用的 handler 抹掉,
# B 在 _extract_fields → Collection(name, using=alias) 时拿到 ConnectionNotExist。
_MILVUS_REGISTRY_LOCK = threading.RLock()

# langchain 0.2 起 langchain.vectorstores.Milvus 已弃用,1.0 会移除。
# 优先用新包 langchain-milvus(API 兼容);未安装时回退老 community,日志一条提示。
try:
    from langchain_milvus import Milvus  # type: ignore
    _MILVUS_BACKEND = "langchain-milvus"
except ImportError:  # noqa: F401
    from langchain_community.vectorstores import Milvus  # type: ignore[no-redef]
    _MILVUS_BACKEND = "langchain-community(deprecated)"
    import logging as _logging
    _logging.getLogger(__name__).info(
        "langchain_milvus 未安装,回退 langchain_community.vectorstores.Milvus(已弃用)。"
        "建议:pip install -U langchain-milvus"
    )

from chayuan.settings import Settings
from chayuan.server.db.repository import list_file_num_docs_id_by_kb_name_and_file_name
from chayuan.server.utils import get_Embeddings
from chayuan.server.file_rag.utils import get_Retriever
from chayuan.server.knowledge_base.kb_service.base import (
    KBService,
    SupportedVSType,
    score_threshold_process,
)
from chayuan.server.knowledge_base.utils import KnowledgeFile, safe_vs_collection_name


class MilvusKBService(KBService):
    milvus: Milvus

    # Milvus collection 名必须 ^[A-Za-z_][A-Za-z0-9_]*$；用户的 kb_name 可能是中文（如
    # "成语大全"），这里统一走 safe_vs_collection_name 做确定性映射，既保留 UI 侧原名，
    # 又满足 Milvus 的硬约束。存量 ASCII 合法的 kb_name 会原样透传，兼容老库。
    def _collection_name(self) -> str:
        return self.vector_namespace

    @staticmethod
    def get_collection(milvus_name):
        from pymilvus import Collection, connections

        raw_args = dict(Settings.kb_settings.kbs_config.get("milvus") or {})
        raw_args.pop("alias", None)
        conn_args = MilvusKBService._normalize_connection_args(raw_args)
        alias = MilvusKBService._predict_alias(conn_args)
        if not MilvusKBService._has_live_connection(connections, alias):
            MilvusKBService._connect_alias(connections, alias, conn_args)
        return Collection(safe_vs_collection_name(milvus_name), using=alias)

    def get_doc_by_ids(self, ids: List[str]) -> List[Document]:
        result = []
        if self.milvus.col:
            # ids = [int(id) for id in ids]  # for milvus if needed #pr 2725
            data_list = self.milvus.col.query(
                expr=f"pk in {[int(_id) for _id in ids]}", output_fields=["*"]
            )
            for data in data_list:
                text = data.pop("text")
                result.append(Document(page_content=text, metadata=data))
        return result

    def del_doc_by_ids(self, ids: List[str]) -> bool:
        self.milvus.col.delete(expr=f"pk in {ids}")

    @staticmethod
    def search(milvus_name, content, limit=3):
        # 跟随 settings 里 milvus_kwargs.search_params 的 metric_type;漏配回退 COSINE
        # (新默认)。Milvus 2.6 server 端要求 search 与 index 的 metric 必须一致。
        cfg_sp = (Settings.kb_settings.kbs_config.get("milvus_kwargs") or {}).get("search_params") or {}
        metric = str(cfg_sp.get("metric_type") or "COSINE").upper()
        search_params = {
            "metric_type": metric,
            "params": dict(cfg_sp.get("params") or {"nprobe": 10}),
        }
        c = MilvusKBService.get_collection(safe_vs_collection_name(milvus_name))
        return c.search(
            content, "embeddings", search_params, limit=limit, output_fields=["content"]
        )

    def do_create_kb(self):
        pass

    def vs_type(self) -> str:
        return SupportedVSType.MILVUS

    @staticmethod
    def _normalize_connection_args(raw: Dict) -> Dict:
        """老式 host/port → 新式 uri。

        langchain-milvus 0.2+ 内部 MilvusClient(**connection_args) 只认 uri 类参数,
        host/port 走 **kwargs 透传到 connections.connect 后会和默认 uri
        ('http://localhost:19530')共存,触发 __get_full_address 的优先级 / 缓存
        地址不一致问题。这里统一拼成一根 uri 干掉歧义。
        """
        args: Dict = {}
        # 优先沿用用户显式 uri;否则用 host/port 拼
        uri = raw.get("uri")
        if uri:
            args["uri"] = str(uri)
        else:
            host = str(raw.get("host") or "127.0.0.1").strip() or "127.0.0.1"
            port_raw = raw.get("port", 19530)
            try:
                port = int(port_raw)
            except (TypeError, ValueError):
                port = 19530
            scheme = "https" if bool(raw.get("secure", False)) else "http"
            args["uri"] = f"{scheme}://{host}:{port}"

        # MilvusClient 认的 keep-list:其它 host/port/secure 一律抛掉避免冲突
        for k in ("user", "password", "db_name", "token", "timeout"):
            v = raw.get(k)
            if v not in (None, ""):
                args[k] = v
        return args

    @staticmethod
    def _normalize_index_params(raw: Optional[Dict]) -> Optional[Dict]:
        """补齐 index_params 必填字段。

        Milvus 2.6 对索引参数挑剔:
          - HNSW 需要 params.M (默认 16) + params.efConstruction (默认 200)
          - IVF_FLAT/SQ8/PQ 需要 params.nlist
          - AUTOINDEX 不需要 params(server 端自动挑)—— 推荐默认
        历史踩坑:老配置 {metric_type:L2, index_type:HNSW} 漏 params 让 _create_index
        失败,collection 没索引也没 load,后续 add_documents → _load() 直接炸,
        在某些路径冒充 ConnectionNotExist 上抛。
        """
        if not raw:
            return None
        out = dict(raw)
        idx = str(out.get("index_type") or "AUTOINDEX").upper()
        params = dict(out.get("params") or {})
        if idx == "HNSW":
            params.setdefault("M", 16)
            params.setdefault("efConstruction", 200)
        elif idx in ("IVF_FLAT", "IVF_SQ8", "IVF_PQ"):
            params.setdefault("nlist", 1024)
            if idx == "IVF_PQ":
                params.setdefault("m", 8)
        elif idx == "DISKANN":
            pass  # DiskANN 默认参数即可
        # AUTOINDEX / FLAT 等不强制
        out["params"] = params
        out.setdefault("metric_type", "COSINE")
        return out

    @staticmethod
    def _normalize_search_params(raw: Optional[Dict], index_params: Optional[Dict]) -> Optional[Dict]:
        """search_params 的 metric_type 必须和 index_params 一致,否则 server 报错。

        params 部分按 index_type 给个最小可用默认(HNSW.ef / IVF.nprobe)。
        """
        if not raw:
            return None
        out = dict(raw)
        if index_params and "metric_type" in index_params:
            out["metric_type"] = index_params["metric_type"]
        params = dict(out.get("params") or {})
        idx = str((index_params or {}).get("index_type", "")).upper()
        if idx == "HNSW":
            params.setdefault("ef", 64)
        elif idx in ("IVF_FLAT", "IVF_SQ8", "IVF_PQ"):
            params.setdefault("nprobe", 16)
        out["params"] = params
        return out

    def _load_milvus(self):
        # 关键修复:把老式 host/port 配置归一成 uri,langchain-milvus 0.2+ 内部
        # 走 MilvusClient(**connection_args) 而 MilvusClient 只认 uri/user/password/
        # db_name/token —— host/port 会被吞进 **kwargs 透传到底层 connections.connect。
        # 部分 pymilvus / langchain_milvus 组合下,uri 默认("http://localhost:19530")
        # + 透传的 host/port 在 __get_full_address 里互相覆盖产生地址不一致,触发
        # ConnDiffConf 或在后续 Collection(using=alias) 时拿到 ConnectionNotExist。
        # 这里一律预先拼 uri,把 host/port 抹掉,根除歧义。
        #
        # 同时:MilvusClient 负责生成实际 alias,但 langchain_milvus 随后会走 ORM
        # Collection(using=alias);因此我们要确保这个 alias 在 pymilvus.connections
        # 里有真实 handler,不能只看 has_connection 的配置残留。
        from pymilvus import connections

        raw_args = dict(Settings.kb_settings.kbs_config.get("milvus") or {})
        raw_args.pop("alias", None)  # 防 dict-spread 残留

        conn_args = self._normalize_connection_args(raw_args)

        kbs_kwargs = Settings.kb_settings.kbs_config.get("milvus_kwargs") or {}
        index_params = self._normalize_index_params(kbs_kwargs.get("index_params"))
        search_params = self._normalize_search_params(
            kbs_kwargs.get("search_params"), index_params
        )

        def _build_milvus():
            self._install_langchain_milvus_orm_bridge(connections)
            return Milvus(
                embedding_function=get_Embeddings(self.embed_model),
                collection_name=self._collection_name(),
                connection_args=conn_args,
                index_params=index_params,
                search_params=search_params,
                auto_id=True,
            )

        uri_for_log = conn_args.get("uri", "<unknown>")

        # —— 关键 self-heal:pymilvus issue #1643 ——
        # 现象:langchain_milvus 0.2+ 内部 MilvusClient 自己生成 UUID alias,
        # 但不一定登记到 pymilvus.connections._alias_handlers。后续
        # Collection(name, using=client._using) 时找不到该 alias 的 handler,
        # 抛 ConnectionNotExist("should create connection first")。
        #
        # 真正可靠的对策(2026-04 加固):
        #   1. 先 _MILVUS_REGISTRY_LOCK 把 connections 操作串成线性 — 知识中心并行
        #      查多 KB 时 N 条线程都在抢这个全局单例,没有锁就会互相把 handler 抹掉。
        #   2. pre-register 一个**稳定 alias**(基于 URI / db / user)。langchain-milvus
        #      0.3._create_connection_alias 会按 address+user 复用现存连接,因此
        #      这个 alias 会被它直接选上。
        #   3. 顺手把同 address 的"僵尸 probe 别名"清掉 — vs_config / ext_vs 的
        #      验证连接 finally 里 disconnect 偶发失败,残留进 list_connections
        #      会让 lc-milvus 复用到一个 grpc handler 已经 GC 的 alias。
        global_alias = self._predict_alias(conn_args)

        with _MILVUS_REGISTRY_LOCK:
            self._cleanup_stale_probe_aliases(connections, conn_args)

            try:
                if not self._has_live_connection(connections, global_alias):
                    self._connect_alias(connections, global_alias, conn_args)
                    logger.info("Milvus pre-registered global alias=%s uri=%s", global_alias, uri_for_log)
            except Exception as _pre_e:  # noqa: BLE001
                logger.info("pre-register alias failed (将由 _build_milvus 兜底):%r", _pre_e)

            # 把 alias 塞进 conn_args 仅作为信息携带 — langchain-milvus 0.3
            # 内部不会消费它,但 0.2 旧版有路径会用,保留兼容。
            conn_args["alias"] = global_alias

            try:
                self.milvus = _build_milvus()
            except Exception as e:  # noqa: BLE001
                ename = type(e).__name__
                if "ConnectionNotExist" not in ename and "should create connection first" not in str(e):
                    # 真 TCP / 认证 / 协议问题:给清晰可读的 RuntimeError,_kb_call 转 503
                    raise RuntimeError(
                        f"无法连接 Milvus(uri={uri_for_log!r}):{ename}: {e}。"
                        f"请确认 Milvus 服务已启动 + 客户端版本匹配。{self._diag_suffix()}"
                    ) from e
                # ConnectionNotExist:**仅**重做我们自己这条 alias,绝不 disconnect-all
                # —— 之前那版本会把同进程其它 KB 正在用的 handler 也一起抹掉,引发雪崩。
                logger.warning("Milvus 首建 ConnectionNotExist,定向重建 alias=%s。原因:%r",
                               global_alias, e)
                try:
                    connections.disconnect(global_alias)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self._connect_alias(connections, global_alias, conn_args)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self.milvus = _build_milvus()
                except Exception as e2:  # noqa: BLE001
                    # 把所有诊断信息一次塞到 RuntimeError msg 里 —— 用户可直接复制全文给我看
                    import traceback as _tb
                    tb_text = "".join(_tb.format_exception(type(e2), e2, e2.__traceback__))[-2000:]
                    raise RuntimeError(
                        f"Milvus 客户端构造失败(uri={uri_for_log!r}):{type(e2).__name__}: {e2}。"
                        f"{self._diag_suffix()}\n"
                        f"--- 诊断 traceback (尾 2000 字) ---\n{tb_text}"
                    ) from e2

            # —— 关键 self-heal 第二段:langchain_milvus 构造完后,再次确认它选用的
            # alias 在全局 connections 里能找到 handler;找不到就再补登记一次。
            # 因为 langchain_milvus 内部的 MilvusClient._using 可能跟我们 _predict_alias
            # 算的不完全一样(取决于它的版本),所以以 self.milvus.alias 实际值为准。
            try:
                actual = getattr(self.milvus, "alias", None) or global_alias
                if actual and not self._has_live_connection(connections, actual):
                    self._connect_alias(connections, actual, conn_args)
                    logger.info("Milvus post-register alias=%s (issue #1643 workaround)", actual)
            except Exception as _post_e:  # noqa: BLE001
                logger.info("post-register alias failed: %r", _post_e)

            # 记录 lc 当前用的 alias,后续操作 keep-alive 用
            self._lc_alias: Optional[str] = None
            try:
                self._lc_alias = getattr(self.milvus, "alias", None)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _connection_kwargs(conn_args: Dict) -> Dict:
        """Translate LangChain connection_args into pymilvus.connections.connect kwargs."""
        kwargs = {}
        for key in ("uri", "user", "password", "db_name", "token", "timeout"):
            value = conn_args.get(key)
            if value not in (None, ""):
                kwargs[key] = value
        return kwargs

    @classmethod
    def _connect_alias(cls, connections, alias: str, conn_args: Dict) -> None:
        try:
            connections.disconnect(alias)
        except Exception:  # noqa: BLE001
            pass
        connections.connect(alias=alias, **cls._connection_kwargs(conn_args))

    @staticmethod
    def _has_live_connection(connections, alias: str) -> bool:
        """connections.has_connection may see config-only aliases; verify handler exists."""
        try:
            connections._fetch_handler(alias)  # type: ignore[attr-defined]
            return True
        except Exception:  # noqa: BLE001
            return False

    @classmethod
    def _install_langchain_milvus_orm_bridge(cls, connections) -> None:
        """Bridge langchain_milvus' MilvusClient alias to pymilvus ORM Collection.

        pymilvus 2.6.10+ moved MilvusClient to a new connection manager alias
        (usually ``cm-*``). langchain_milvus 0.3 still calls ORM
        ``Collection(..., using=self.alias)`` inside ``Milvus.__init__``. That
        alias is not necessarily present in legacy ``connections._alias_handlers``,
        so constructor-time ``Collection`` access raises ConnectionNotExist before
        any post-build repair can run.

        The stable workaround is to make langchain_milvus' ``col`` property use an
        ORM alias derived from the same ``connection_args`` and ensure that alias
        has a live legacy handler before creating ``Collection``.
        """
        if _MILVUS_BACKEND != "langchain-milvus":
            return
        if getattr(Milvus, "_chayuan_orm_bridge_installed", False):
            return

        original_col = getattr(Milvus, "col", None)
        if not isinstance(original_col, property) or original_col.fget is None:
            return

        def bridged_col(store):
            from pymilvus import Collection

            conn_args = dict(getattr(store, "_connection_args", {}) or {})
            orm_alias = cls._predict_alias(conn_args)
            if not cls._has_live_connection(connections, orm_alias):
                cls._connect_alias(connections, orm_alias, conn_args)

            cache_key = f"{store.collection_name}:{orm_alias}"
            if (
                getattr(store, "_cache_key", None) == cache_key
                and getattr(store, "_col_cache", None) is not None
            ):
                return getattr(store, "_col_cache")

            if store.client.has_collection(store.collection_name):
                col = Collection(store.collection_name, using=orm_alias)
                if getattr(store, "collection_properties", None) is not None:
                    col.set_properties(store.collection_properties)
                store._col_cache = col
                store._cache_key = cache_key
                return col

            store._col_cache = None
            store._cache_key = None
            return None

        Milvus.col = property(
            bridged_col,
            original_col.fset,
            original_col.fdel,
            original_col.__doc__,
        )
        setattr(Milvus, "_chayuan_orm_bridge_installed", True)

    @staticmethod
    def _cleanup_stale_probe_aliases(connections, conn_args: Dict) -> None:
        """清理 vs_config / ext_vs 验证连接残留的 _chayuan_probe_* / _chayuan_ext_vs_probe_*
        别名 — 它们走的是 host/port,addr 和我们目标 uri 同一个 endpoint,
        会被 langchain-milvus._create_connection_alias 按 address+user 复用,
        但其底层 handler 可能已经被 finally 半 disconnect 过(残留在 _kwargs 没残留 handler)
        从而后续 _fetch_handler 抛 ConnectionNotExist。
        """
        try:
            cons = list(connections.list_connections() or [])
        except Exception:  # noqa: BLE001
            return
        for con in cons:
            alias = con[0] if isinstance(con, (tuple, list)) else con
            if not isinstance(alias, str):
                continue
            if not (alias.startswith("_chayuan_probe_") or alias.startswith("_chayuan_ext_vs_probe_")):
                continue
            try:
                connections.disconnect(alias)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _predict_alias(conn_args: Dict) -> str:
        """模拟 pymilvus.create_connection 的 alias 生成规则,这样我们的 pre-register
        和 langchain_milvus 内部 MilvusClient 用的是同一个 alias。

        pymilvus 2.6 规则:`-`.join(non_empty([uri, db_name, user_or_token_md5]))。
        我们大多数场景没有 db_name / 鉴权 → 直接退化成 uri。
        """
        parts = []
        uri = (conn_args.get("uri") or "").strip()
        if uri:
            parts.append(uri)
        if conn_args.get("db_name"):
            parts.append(str(conn_args["db_name"]))
        if conn_args.get("user"):
            parts.append(str(conn_args["user"]))
        return "-".join(parts) if parts else "default"

    @staticmethod
    def _diag_suffix() -> str:
        """构造一段"环境诊断"附在错误消息末尾,方便用户一次性把所有上下文反馈过来。"""
        try:
            import pymilvus
            pmv = getattr(pymilvus, "__version__", "unknown")
        except Exception:  # noqa: BLE001
            pmv = "<not installed>"
        try:
            from importlib.metadata import version

            lcv = version("langchain-milvus")
        except Exception:  # noqa: BLE001
            lcv = "<not installed>"
        try:
            from pymilvus import connections as _c
            cons = list(_c.list_connections() or [])
            cons_text = ", ".join(
                (str(x[0]) if isinstance(x, (tuple, list)) else str(x))
                for x in cons[:10]
            ) or "<empty>"
        except Exception:  # noqa: BLE001
            cons_text = "<unable to list>"
        return (
            f" 诊断:pymilvus={pmv} / langchain_milvus={lcv} / "
            f"connections=[{cons_text}]。"
            f"建议:① 确认 docker logs milvus 已就绪;② pip install -U "
            f"'pymilvus>=2.6,<2.7' 'langchain-milvus>=0.3,<0.4' 与 Milvus v2.6.x 服务匹配;"
            f"③ 临时改用 faiss / chromadb 本地向量库。"
        )

    def _ensure_collection_loaded(self) -> None:
        """add/search 前确保 collection 已 load 进内存。

        Milvus 2.6 起,新建 collection 后必须 create_index → load 才能 search。
        langchain-milvus._init 会负责这条链,但有些边界(进程重启 / 连接重建后
        col 实例还是旧的)会让 col 处于"已 create_index 但还没 load"状态,
        search 立刻 ConnectionNotExist 风格报错。这里主动调一次 load。
        """
        col = getattr(self.milvus, "col", None)
        if col is None:
            return
        try:
            # has_index() 在没索引时返 False,langchain_milvus 之后 _init 也会 _create_index
            if not col.has_index():
                return
            col.load()
        except Exception:  # noqa: BLE001
            # 任何 load 失败都不致命 —— 调用方拿到错误会自行 reload;这里静默
            pass

    def _ensure_milvus_handles(self) -> None:
        """操作前真探活 lc-milvus 当前 alias;死了就整个 _load_milvus 重建。

        我们不再自己注册 alias — alias 完全由 langchain_milvus 用 uuid 管理。
        操作前探一下:list_collections 通就放过;不通就触发 _load_milvus 重建,
        让 lc 自己用新 uuid 重新 connect。这样比手动 disconnect+reconnect 更安全
        (避免触发 dict spread bug)。
        """
        from pymilvus import connections

        lc_alias = getattr(self, "_lc_alias", None) or getattr(self.milvus, "alias", None)
        if not lc_alias:
            return  # 还没构造好,_load_milvus 调用方会先建

        try:
            if not connections.has_connection(lc_alias):
                self._load_milvus()
                return
            handler = connections._fetch_handler(lc_alias)  # type: ignore[attr-defined]
            if handler is None:
                self._load_milvus()
                return
            handler.list_collections(timeout=2.0)
        except Exception:  # noqa: BLE001
            # 探活失败,整体重建(_load_milvus 内部已经包了重试 + 友好 RuntimeError)
            self._load_milvus()

    def do_init(self):
        """KB 服务实例化时调用。

        2026-04 改 lazy:Milvus 暂不可达时不抛错 — 让 list_files / delete_kb 等
        不依赖 Milvus 连接的操作仍能工作(它们走 DB / FileStorage,不查 Milvus)。
        do_search / do_add_doc / do_drop_kb 已经在各自入口主动 _load_milvus,
        那时再抛具体错误,_kb_call 会转成友好的 503。
        """
        try:
            self._load_milvus()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Milvus do_init lazy:%s。list/delete 仍可用,search/add 真用时再报错。",
                f"{type(e).__name__}: {e}",
            )
            self.milvus = None

    def do_drop_kb(self):
        # 触发 .col 会让 langchain_milvus 跑一次 _init → _extract_fields → Collection(name, using=alias);
        # 任何路径来这里之前都先 _load_milvus 把 pymilvus 连接登记好,避免
        # ConnectionNotExistException("should create connection first")。
        # 2026-04:Milvus 不可达时静默放弃 collection 清理,base.drop_kb 会继续
        # 清磁盘 + DB 行,KB 仍能成功删除。collection 残留留给用户后续手动处理。
        try:
            self._load_milvus()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "do_drop_kb: Milvus 不可达,跳过 collection 清理。kb=%s err=%r",
                self.kb_name, e,
            )
            return
        if self.milvus is not None and self.milvus.col is not None:
            try:
                self.milvus.col.release()
                self.milvus.col.drop()
            except Exception as e:  # noqa: BLE001
                # collection 已被外部删 / 名字对不上 / 权限不足 都进这里 — 不阻塞 KB 删除
                logger.warning(
                    "do_drop_kb: drop collection 失败,跳过。kb=%s err=%r",
                    self.kb_name, e,
                )

    def do_search(self, query: str, top_k: int, score_threshold: float):
        self._load_milvus()
        self._ensure_collection_loaded()
        # embed_func = get_Embeddings(self.embed_model)
        # embeddings = embed_func.embed_query(query)
        # docs = self.milvus.similarity_search_with_score_by_vector(embeddings, top_k)
        retriever = get_Retriever("milvusvectorstore").from_vectorstore(
            self.milvus,
            top_k=top_k,
            score_threshold=score_threshold,
        )
        docs = retriever.get_relevant_documents(query)
        return docs

    def do_add_doc(self, docs: List[Document], **kwargs) -> List[Dict]:
        # add_documents → add_texts → add_embeddings → self._init(**kwargs) →
        # _extract_fields → Collection(name, using=alias)
        # 上面这条链里 langchain_milvus 可能用一个跟我们不同的 alias(uuid4 派生),
        # 我们看不见也没法预防。策略:进函数先重做 _load_milvus;_ensure_milvus_handles
        # 把主 / lc 两个 alias 都保活;一旦 add_documents 抛 ConnectionNotExistException,
        # 全部 disconnect + 完整重新构造一次再重试。
        self._load_milvus()
        self._ensure_milvus_handles()

        # 关键:搜集要从 metadata 里"避开"的字段名 — 这些字段不能由我们填默认值,
        # 否则会和 schema 撞类型(尤其 pk 是 int64,我们 setdefault 填 "" 会让
        # 第二次以后的上传炸 DataNotMatchException)。
        # auto_id=True 的情况下 _primary_field 由 Milvus 服务端生成,绝不能往
        # row 里塞;_text_field / _vector_field 由 langchain_milvus 走专用路径写入。
        skip_fields = set()
        for attr in ("_primary_field", "_text_field", "_vector_field"):
            v = getattr(self.milvus, attr, None)
            if v:
                skip_fields.add(v)

        for doc in docs:
            for k, v in doc.metadata.items():
                doc.metadata[k] = str(v)
            for field in self.milvus.fields:
                if field in skip_fields:
                    continue
                doc.metadata.setdefault(field, "")
            # 兜底:就算 metadata 里历史残留了 pk / text / vector 也清掉
            for sf in skip_fields:
                doc.metadata.pop(sf, None)

        try:
            ids = self.milvus.add_documents(docs)
        except Exception as e:  # noqa: BLE001
            ename = type(e).__name__
            if "ConnectionNotExist" in ename or "should create connection first" in str(e):
                # 仅对"alias 没注册"这一类失败做一次重试 — _load_milvus 内部
                # 已经用 lc 自管 uuid 的方式重新 connect,这里不需要手动 disconnect 串。
                self._load_milvus()
                ids = self.milvus.add_documents(docs)
            else:
                raise
        # 首次写入后 langchain-milvus._init 已经 _create_index + _load。
        # 但如果 col 在重建路径里被换过引用,显式再 ensure 一次防止后续 search 漏 load。
        self._ensure_collection_loaded()
        doc_infos = [{"id": id, "metadata": doc.metadata} for id, doc in zip(ids, docs)]
        return doc_infos

    def do_delete_doc(self, kb_file: KnowledgeFile, **kwargs):
        # 同 do_add_doc:.col 也会触发底层 Collection(...);先 ensure 连接
        self._load_milvus()
        id_list = list_file_num_docs_id_by_kb_name_and_file_name(
            kb_file.kb_name, kb_file.filename
        )
        if self.milvus.col:
            self.milvus.col.delete(expr=f"pk in {id_list}")

        # Issue 2846, for windows
        # if self.milvus.col:
        #     file_path = kb_file.filepath.replace("\\", "\\\\")
        #     file_name = os.path.basename(file_path)
        #     id_list = [item.get("pk") for item in
        #                self.milvus.col.query(expr=f'source == "{file_name}"', output_fields=["pk"])]
        #     self.milvus.col.delete(expr=f'pk in {id_list}')

    def do_clear_vs(self):
        # 2026-04:与 do_drop_kb 同策略,Milvus 不可达不抛(base.clear_vs 会继续清 DB)
        try:
            self._load_milvus()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "do_clear_vs: Milvus 不可达,跳过向量清理。kb=%s err=%r",
                self.kb_name, e,
            )
            return
        if self.milvus is not None and self.milvus.col is not None:
            try:
                self.do_drop_kb()
                self.do_init()
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "do_clear_vs: 向量清理失败,跳过。kb=%s err=%r",
                    self.kb_name, e,
                )


if __name__ == "__main__":
    # 测试建表使用
    from chayuan.server.db.base import Base, engine

    Base.metadata.create_all(bind=engine)
    milvusService = MilvusKBService("test")
    # milvusService.add_doc(KnowledgeFile("README.md", "test"))

    print(milvusService.get_doc_by_ids(["444022434274215486"]))
    # milvusService.delete_doc(KnowledgeFile("README.md", "test"))
    # milvusService.do_drop_kb()
    # print(milvusService.search_docs("如何启动api服务"))
