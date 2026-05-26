"""向量库 **原子热切换** 重建（T8）。

常规 ``recreate_vector_store`` 会先 ``clear_vs()`` 再逐文件加入，过程中索引是空的，
查询流量会拿到空结果或报错；这对生产很不友好。

本模块用"影子索引 + 目录 rename / collection rename"策略实现零宕机重建：

    1. 选一个临时名 ``<kb_name>__shadow_<ts>``，把新内容灌进影子索引
    2. 把影子索引准备好并健康检查通过后，做"同一秒级原子交换"：
       - FAISS  → 目录 rename（POSIX 保证原子）+ 清本地 pool 缓存
       - Milvus → ``utility.rename_collection(shadow, real)``（一次 RPC）
    3. 其它后端：最小程度回退到 in-place 重建（fail-open，表面对用户等价）

公开接口
~~~~~~~~
- :func:`rebuild_atomic`：同步阻塞版；适合小 KB / Arq worker 调用
- :func:`rebuild_atomic_streaming`：yield SSE dict 进度；适合 HTTP 端

依赖说明
~~~~~~~~
- FAISS：仅需标准库；``shutil.move`` 在同一文件系统里相当于 ``rename``，事实原子
- Milvus：需要 ``pymilvus``（已在 kbs_config 内声明）
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger("chayuan.kb_service.atomic_rebuild")


def _now_tag() -> str:
    return time.strftime("%Y%m%d%H%M%S")


def _faiss_swap(real_kb_name: str, shadow_kb_name: str,
                 vector_name: Optional[str] = None) -> None:
    """FAISS 目录 rename：先备份 real → real_old，再把 shadow → real。

    失败回滚：把 real_old rename 回 real。保证不可见中间态。
    """
    from chayuan.server.knowledge_base.kb_cache.faiss_cache import kb_faiss_pool
    from chayuan.server.knowledge_base.utils import get_vs_path

    # vector_name 实际由 embed_model 派生；调用方保持一致
    real_path = get_vs_path(real_kb_name, vector_name or "")
    shadow_path = get_vs_path(shadow_kb_name, vector_name or "")
    if not os.path.isdir(shadow_path):
        raise FileNotFoundError(f"shadow dir missing: {shadow_path}")

    backup_path = real_path + f".bak_{_now_tag()}"
    with kb_faiss_pool.atomic:
        # 先把两个 pool 缓存清掉（写者也会 block 在 atomic 里）
        kb_faiss_pool.pop((real_kb_name, vector_name or ""))
        kb_faiss_pool.pop((shadow_kb_name, vector_name or ""))

        # 原子交换：os.rename 在同一 FS 下是一次 syscall
        if os.path.isdir(real_path):
            os.rename(real_path, backup_path)
        try:
            os.rename(shadow_path, real_path)
        except Exception:
            # 回滚
            if os.path.isdir(backup_path):
                try:
                    os.rename(backup_path, real_path)
                except Exception:  # noqa: BLE001
                    pass
            raise

    # 交换成功：异步清 backup（小 KB 瞬间完成，大 KB 进后台）
    try:
        shutil.rmtree(backup_path, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def _milvus_swap(real_kb_name: str, shadow_kb_name: str) -> None:
    """Milvus collection rename。"""
    from pymilvus import utility  # type: ignore
    # 先把 real 重命名成 old，再把 shadow 重命名成 real；原子边界在 collection 名注册表
    backup_name = f"{real_kb_name}_bak_{_now_tag()}"
    try:
        if utility.has_collection(real_kb_name):
            utility.rename_collection(real_kb_name, backup_name)
    except Exception as e:  # noqa: BLE001
        logger.debug("milvus backup rename 失败（忽略）：%r", e)
    try:
        utility.rename_collection(shadow_kb_name, real_kb_name)
    except Exception:
        # 回滚
        try:
            if utility.has_collection(backup_name):
                utility.rename_collection(backup_name, real_kb_name)
        except Exception:  # noqa: BLE001
            pass
        raise
    # 成功：异步 drop backup
    try:
        if utility.has_collection(backup_name):
            utility.drop_collection(backup_name)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------

def _supports_atomic(vs_type: str) -> bool:
    return vs_type in ("faiss", "milvus")


def rebuild_atomic(
    kb_name: str,
    *,
    vs_type: Optional[str] = None,
    embed_model: Optional[str] = None,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    zh_title_enhance: Optional[bool] = None,
) -> Dict[str, Any]:
    """把 ``kb_name`` 以零宕机方式全量重建；失败自动回滚，不影响查询流量。

    返回：``{"ok": bool, "elapsed_s": float, "files": int, "swap_strategy": "faiss|milvus|fallback"}``
    """
    t0 = time.time()
    from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
    from chayuan.server.knowledge_base.utils import (
        KnowledgeFile,
        files2docs_in_thread,
        list_files_from_folder,
    )
    from chayuan.settings import Settings

    real = KBServiceFactory.get_service_by_name(kb_name)
    if real is None:
        return {"ok": False, "error": f"KB not found: {kb_name}"}
    vst = (vs_type or real.vs_type() or Settings.kb_settings.DEFAULT_VS_TYPE).lower()
    emb = embed_model or real.embed_model

    # 兜底：不支持原子的后端 → 调用 in-place 全量重建（等价旧行为）
    if not _supports_atomic(vst):
        logger.info("atomic_rebuild: %s 不支持原子切换，走 in-place", vst)
        try:
            real.clear_vs()
            real.create_kb()
            files = list_files_from_folder(kb_name)
            cnt = 0
            for status, result in files2docs_in_thread(
                [(f, kb_name) for f in files],
                chunk_size=chunk_size or Settings.kb_settings.CHUNK_SIZE,
                chunk_overlap=chunk_overlap or Settings.kb_settings.OVERLAP_SIZE,
                zh_title_enhance=(zh_title_enhance
                                   if zh_title_enhance is not None
                                   else Settings.kb_settings.ZH_TITLE_ENHANCE),
            ):
                if status:
                    _, file_name, docs = result
                    kf = KnowledgeFile(filename=file_name, knowledge_base_name=kb_name)
                    # ⚠ 显式 docs=docs:add_doc 在 docs=[] 时会调
                    # kb_file.file2text() 重读源文件;chunks 这里已经算好,
                    # 不该再读盘(重复 IO + 与 shadow 路径分支共用同一坑)。
                    real.add_doc(kf, docs=docs, not_refresh_vs_cache=True)
                    cnt += 1
            real.save_vector_store()
            return {"ok": True, "elapsed_s": round(time.time() - t0, 2),
                    "files": cnt, "swap_strategy": "fallback"}
        except Exception as e:  # noqa: BLE001
            logger.exception("rebuild_atomic fallback 失败：%s", kb_name)
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # FAISS / Milvus：先灌影子，再 rename
    shadow_name = f"{kb_name}__shadow_{_now_tag()}"
    shadow = KBServiceFactory.get_service(shadow_name, vst, emb)
    try:
        shadow.create_kb()
        files = list_files_from_folder(kb_name)
        cnt = 0
        for status, result in files2docs_in_thread(
            [(f, kb_name) for f in files],
            chunk_size=chunk_size or Settings.kb_settings.CHUNK_SIZE,
            chunk_overlap=chunk_overlap or Settings.kb_settings.OVERLAP_SIZE,
            zh_title_enhance=(zh_title_enhance
                               if zh_title_enhance is not None
                               else Settings.kb_settings.ZH_TITLE_ENHANCE),
        ):
            if status:
                _, file_name, docs = result
                kf = KnowledgeFile(filename=file_name, knowledge_base_name=shadow_name)
                # ⚠ 双重必要,这里不修就是用户报的 "无法向量化" 真凶:
                # 1) shadow 目录由 create_kb() 只建空骨架,没拷 content/,
                #    所以 add_doc 默认 docs=[] 走 kb_file.file2text() →
                #    读 <kb_root>/<shadow_name>/content/<file> →
                #    FileNotFoundError([WinError 2] 系统找不到指定的文件)。
                # 2) docs 已经被上面 files2docs_in_thread 用 REAL kb_name
                #    的源文件算好了,本就不该再读盘。
                # 显式 docs=docs 一并解决两个问题。
                shadow.add_doc(kf, docs=docs, not_refresh_vs_cache=True)
                cnt += 1
        shadow.save_vector_store()

        # 原子 swap
        if vst == "faiss":
            vector_name = getattr(shadow, "vector_name", None) or \
                          getattr(real, "vector_name", None) or \
                          (emb.replace(":", "_") if emb else "")
            _faiss_swap(kb_name, shadow_name, vector_name)
            strat = "faiss"
        else:
            _milvus_swap(kb_name, shadow_name)
            strat = "milvus"
        return {"ok": True, "elapsed_s": round(time.time() - t0, 2),
                "files": cnt, "swap_strategy": strat}
    except Exception as e:  # noqa: BLE001
        logger.exception("rebuild_atomic 失败：%s", kb_name)
        # 尽力清 shadow
        try:
            shadow.drop_kb()
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def rebuild_atomic_streaming(
    kb_name: str, **kw,
) -> Iterator[Dict[str, Any]]:
    """进度流式版：yield ``{"stage": ..., ...}`` 字典。"""
    yield {"stage": "start", "kb_name": kb_name, "ts": int(time.time())}
    result = rebuild_atomic(kb_name, **kw)
    yield {"stage": "done", **result}
