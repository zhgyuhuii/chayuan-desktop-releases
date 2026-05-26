"""Config Panel · 知识库管理页（完整版）。

与 `kb_acl_page.py`（只管可见性/授权）相互独立：本页聚焦在「知识库本身的生命周期」
以及「库内文档的全链路管理」：
- 列表视图：卡片化展示所有知识库 + 基础增删改；
- 详情视图：某个知识库下的文件上传 / 预览 / 分片查看 / 检索调试 / 向量库重建。

设计原则
--------
- **面板以 admin 身份直接调用** `chayuan.server.knowledge_base.*`，不再走 HTTP + JWT，
  与同目录的 `users_page.py` / `kb_acl_page.py` 保持同一风格。
- **卡片网格 + 顶部工具条** 的列表；**三 Tab（文件 / 检索 / 参数）** 的详情。
- **所有写操作** 都弹对话框二次确认。

与代码库的连接点
-----------------
- 读：`KBServiceFactory` + `get_kb_file_details()` + `KBService.list_docs()`
- 写：`create_kb` / `delete_kb` / `upload_docs` / `delete_docs` / `update_docs` /
       `recreate_vector_store`（来自 kb_api、kb_doc_api）
- 切分预览：`KnowledgeFile.file2text()`（真实执行 loader + splitter）
- 检索：`search_docs()`
- 元信息：`update_info()`
- 可见性 / Owner：直接改 `knowledge_base` 表
- 预览：依赖 `app.py` 挂载的 `/static/kb_content/<kb>/content/<file>`
"""
from __future__ import annotations

import logging
import os
import urllib.parse
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("chayuan.config_panel.kb_manage")


# ---------------------------------------------------------------------------
# 数据访问封装（走后端原语，不经 HTTP）
# ---------------------------------------------------------------------------

def _vs_type_choices() -> Dict[str, str]:
    return {
        "faiss":    "FAISS（本地）",
        "milvus":   "Milvus",
        "zilliz":   "Zilliz Cloud",
        "pg":       "PostgreSQL + pgvector",
        "relyt":    "Relyt",
        "es":       "Elasticsearch",
        "chromadb": "ChromaDB（本地）",
    }


def _make_vs_input(ui, f):
    """根据 vs_config.VsField 的描述构造 NiceGUI 控件，返回控件对象。

    和 ``vs_config.render_vs_card`` 里 `_render_fields` 的渲染规则一致，只是
    这里把 single-field 提取成独立函数，供 kb_manage_page 的"新建向量库源"
    对话框和 vs_config 自己共用字段配置（VS_TYPES）——字段的 label / default /
    help 字符串只在 vs_config.py 一处维护。
    """
    kw = {"label": f.label}
    if f.placeholder:
        kw["placeholder"] = f.placeholder
    if f.widget == "password":
        el = ui.input(value=str(f.default or ""), password=True,
                      password_toggle_button=True, **kw)
    elif f.widget == "number":
        el = ui.number(value=f.default, **kw)
    elif f.widget == "switch":
        el = ui.switch(text=f.label, value=bool(f.default))
    elif f.widget == "select":
        choices = list(f.choices or [])
        el = ui.select(choices, value=f.default, label=f.label)
    elif f.widget == "textarea":
        el = ui.textarea(value=str(f.default or ""), **kw).props("autogrow")
    else:
        el = ui.input(value=str(f.default or ""), **kw)
    try:
        el.props("dense outlined")
    except Exception:
        pass
    if f.help:
        try:
            el.tooltip(f.help)
        except Exception:
            pass
    return el


def _kb_settings() -> Dict[str, Any]:
    """从 Settings.kb_settings 读默认参数，失败时给出安全缺省。"""
    try:
        from chayuan.settings import Settings
        s = Settings.kb_settings
        return {
            "default_vs_type": (getattr(s, "DEFAULT_VS_TYPE", "faiss") or "faiss").lower(),
            "chunk_size": int(getattr(s, "CHUNK_SIZE", 750)),
            "chunk_overlap": int(getattr(s, "OVERLAP_SIZE", 150)),
            "zh_title_enhance": bool(getattr(s, "ZH_TITLE_ENHANCE", False)),
            "top_k": int(getattr(s, "VECTOR_SEARCH_TOP_K", 3)),
            "score_threshold": float(getattr(s, "SCORE_THRESHOLD", 2.0)),
            "kkfileview_url": (getattr(s, "KKFILEVIEW_URL", "") or "").strip(),
        }
    except Exception:  # noqa: BLE001
        return {
            "default_vs_type": "faiss",
            "chunk_size": 750,
            "chunk_overlap": 150,
            "zh_title_enhance": False,
            "top_k": 3,
            "score_threshold": 2.0,
            "kkfileview_url": "",
        }


def _probe_kkfileview(url: str, timeout: float = 4.0) -> Dict[str, Any]:
    """对 kkFileView 旁车做一次轻量探活。

    成功标准:GET <url>/ 或 /onlinePreview 返回 2xx/3xx,且 body 含
    "kkFileView" / "OnlineView" / "<title>" 等典型标记。

    返回:{ok: bool, msg: str, latency_ms: int, server: str}
    """
    import time
    import urllib.error
    import urllib.request

    if not (url or "").strip():
        return {"ok": False, "msg": "URL 为空", "latency_ms": 0, "server": ""}

    base = url.rstrip("/")
    # 优先探主页;主页 200 即认为服务在跑
    candidates = [f"{base}/", f"{base}/index"]
    last_err = ""
    for u in candidates:
        t0 = time.time()
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "chayuan-probe/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                latency = int((time.time() - t0) * 1000)
                status = getattr(resp, "status", 200)
                if status >= 200 and status < 400:
                    body = resp.read(4096).decode("utf-8", errors="ignore")
                    server_hint = (
                        resp.headers.get("Server") or resp.headers.get("X-Powered-By") or ""
                    )
                    is_kk = (
                        "kkfileview" in body.lower()
                        or "kk-fileview" in body.lower()
                        or "onlinepreview" in body.lower()
                        or "<title>" in body.lower()  # kkFileView 主页有 title
                    )
                    if is_kk:
                        return {
                            "ok": True,
                            "msg": f"连通正常 ({status})",
                            "latency_ms": latency,
                            "server": server_hint,
                        }
                    return {
                        "ok": False,
                        "msg": f"返回 {status} 但内容不像 kkFileView 主页",
                        "latency_ms": latency,
                        "server": server_hint,
                    }
                last_err = f"HTTP {status}"
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            last_err = f"网络错误: {e.reason}"
        except TimeoutError:
            last_err = f"超时({timeout}s)"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
    return {"ok": False, "msg": last_err or "未知错误", "latency_ms": 0, "server": ""}


def _default_embedding() -> str:
    try:
        from chayuan.server.utils import get_default_embedding
        return get_default_embedding() or ""
    except Exception:  # noqa: BLE001
        return ""


def _available_embeddings() -> List[str]:
    try:
        from chayuan.server.utils import get_config_models
        return sorted(list(get_config_models(model_type="embed").keys()))
    except Exception:  # noqa: BLE001
        return []


def _kb_root_path() -> str:
    try:
        from chayuan.settings import Settings
        return (Settings.basic_settings.KB_ROOT_PATH or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _list_users_lite() -> Dict[int, str]:
    try:
        from chayuan.server.auth.service import list_users
        return {u.id: u.username for u in list_users(limit=500)}
    except Exception:  # noqa: BLE001
        return {}


def _list_kbs_with_meta() -> List[Dict[str, Any]]:
    """合并 DB + 磁盘的 KB 元信息。"""
    from chayuan.server.db.session import session_scope
    from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel

    rows: List[Dict[str, Any]] = []
    users = _list_users_lite()
    with session_scope() as s:
        kbs = s.query(KnowledgeBaseModel).order_by(KnowledgeBaseModel.id.asc()).all()
        for kb in kbs:
            rows.append({
                "id": kb.id,
                "kb_name": kb.kb_name or "",
                "kb_info": kb.kb_info or "",
                "vs_type": kb.vs_type or "",
                "embed_model": kb.embed_model or "",
                "file_count": int(kb.file_count or 0),
                "owner_id": kb.owner_id,
                "owner_name": users.get(kb.owner_id, "-") if kb.owner_id else "-",
                "visibility": (kb.visibility or "private"),
                "create_time": kb.create_time.isoformat() if kb.create_time else "",
            })

    try:
        from chayuan.server.knowledge_base.kb_service.base import get_kb_details
        full = get_kb_details() or []
        name_set = {r["kb_name"] for r in rows}
        for item in full:
            if not isinstance(item, dict):
                continue
            kn = item.get("kb_name")
            if kn and kn not in name_set:
                rows.append({
                    "id": None,
                    "kb_name": kn,
                    "kb_info": item.get("kb_info") or "",
                    "vs_type": item.get("vs_type") or "",
                    "embed_model": item.get("embed_model") or "",
                    "file_count": int(item.get("file_count") or 0),
                    "owner_id": None,
                    "owner_name": "-",
                    "visibility": "private",
                    "create_time": str(item.get("create_time") or ""),
                    "orphan": True,
                })
    except Exception:  # noqa: BLE001
        logger.debug("get_kb_details failed (tolerated)", exc_info=True)
    return rows


def _list_kb_files(kb_name: str) -> List[Dict[str, Any]]:
    try:
        from chayuan.server.knowledge_base.kb_service.base import get_kb_file_details
        data = get_kb_file_details(kb_name) or []
        return [d for d in data if isinstance(d, dict)]
    except Exception:  # noqa: BLE001
        logger.debug("get_kb_file_details failed", exc_info=True)
        return []


def _create_kb(kb_name: str, vs_type: str, embed_model: str, kb_info: str,
               visibility: str, owner_id: Optional[int]) -> str:
    from chayuan.server.knowledge_base.kb_api import create_kb as _create
    res = _create(
        knowledge_base_name=kb_name,
        vector_store_type=vs_type,
        kb_info=kb_info or "",
        embed_model=embed_model or _default_embedding(),
    )
    code = getattr(res, "code", None)
    msg = getattr(res, "msg", "") or ""
    if code not in (200, 0, None):
        raise RuntimeError(msg or f"创建失败（code={code}）")

    if visibility in ("private", "public") or owner_id is not None:
        from chayuan.server.db.session import session_scope
        from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
        with session_scope() as s:
            kb = s.query(KnowledgeBaseModel).filter(
                KnowledgeBaseModel.kb_name == kb_name
            ).one_or_none()
            if kb is not None:
                if visibility in ("private", "public"):
                    kb.visibility = visibility
                if owner_id:
                    kb.owner_id = int(owner_id)
    return msg or f"已创建 {kb_name}"


def _delete_kb(kb_name: str, *, orphan: bool = False) -> str:
    """删除知识库，兼容「磁盘孤儿」场景。

    - 正常情况：走 ``kb_api.delete_kb``（清向量库 + 删 DB 记录 + 删磁盘目录）。
    - DB 记录缺失（面板标记为「磁盘孤儿」、或底层返回 404）时：直接清理
      ``KB_ROOT_PATH/{kb_name}/`` 目录；否则页面列表会一直显示不掉，只能手工 rm。
    - 真的两边都找不到：抛 RuntimeError，让上层按「未找到」提示。
    """

    # 孤儿路径：跳过 kb_api（它依赖 DB 记录），直接清目录。
    if orphan:
        if _purge_orphan_kb_folder(kb_name):
            return f"已清理磁盘孤儿 {kb_name}（DB 记录不存在）"
        raise RuntimeError(f"未找到知识库 {kb_name}（DB 与磁盘均无残留）")

    from chayuan.server.knowledge_base.kb_api import delete_kb as _del
    res = _del(knowledge_base_name=kb_name)
    code = getattr(res, "code", None)
    msg = getattr(res, "msg", "") or ""
    if code in (200, 0, None):
        return msg or f"已删除 {kb_name}"

    # DB 里没这条记录：底层返回 404；回落到孤儿清理，避免页面「删不掉」
    if code == 404:
        if _purge_orphan_kb_folder(kb_name):
            return f"已清理磁盘孤儿 {kb_name}（DB 记录不存在）"
        raise RuntimeError(msg or f"未找到知识库 {kb_name}")

    raise RuntimeError(msg or f"删除失败（code={code}）")


def _purge_orphan_kb_folder(kb_name: str) -> bool:
    """删除 ``KB_ROOT_PATH/{kb_name}/`` 目录。

    返回 True 表示真的删了；False 表示目录本来就不存在（无需清理）。
    失败会抛异常，由上层负责提示。
    """
    import shutil

    from chayuan.server.knowledge_base.utils import get_kb_path

    path = get_kb_path(kb_name)
    if not os.path.isdir(path):
        return False
    try:
        shutil.rmtree(path)
        logger.info("purged orphan kb folder: %s", path)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("purge orphan kb folder failed: %s", path)
        raise


def _update_kb_info(kb_name: str, kb_info: str) -> str:
    from chayuan.server.knowledge_base.kb_doc_api import update_info
    res = update_info(knowledge_base_name=kb_name, kb_info=kb_info or "")
    return getattr(res, "msg", "") or "已更新"


def _update_meta(kb_id: int, *, visibility: Optional[str] = None,
                 owner_id: Optional[int] = None) -> None:
    from chayuan.server.db.session import session_scope
    from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
    with session_scope() as s:
        kb = s.get(KnowledgeBaseModel, int(kb_id))
        if kb is None:
            raise ValueError("kb not found")
        if visibility in ("private", "public"):
            kb.visibility = visibility
        if owner_id is not None:
            kb.owner_id = int(owner_id) if owner_id else None


def _delete_docs(kb_name: str, file_names: List[str]) -> str:
    from chayuan.server.knowledge_base.kb_doc_api import delete_docs
    res = delete_docs(
        knowledge_base_name=kb_name,
        file_names=file_names,
        delete_content=True,
        not_refresh_vs_cache=False,
    )
    return getattr(res, "msg", "") or "已删除"


def _save_upload_to_disk(
    kb_name: str, filename: str, content: bytes, override: bool,
) -> Dict[str, Any]:
    """绕过 FastAPI UploadFile，直接把字节流写到 KB content 目录。

    返回 {"ok": bool, "msg": str, "path": str}。
    """
    from chayuan.server.knowledge_base.utils import (
        get_file_path, validate_kb_name,
    )
    if not validate_kb_name(kb_name):
        return {"ok": False, "msg": "非法 kb_name", "path": ""}
    file_path = get_file_path(knowledge_base_name=kb_name, doc_name=filename)
    if not file_path:
        return {
            "ok": False,
            "msg": "路径解析失败（可能含 '../' 等非法片段）",
            "path": "",
        }
    if os.path.isfile(file_path) and not override:
        if os.path.getsize(file_path) == len(content):
            return {
                "ok": False,
                "msg": f"{filename} 已存在（大小相同），已跳过；如需覆盖请勾选",
                "path": file_path,
            }
    parent = os.path.dirname(file_path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    try:
        with open(file_path, "wb") as f:
            f.write(content)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": f"写磁盘失败：{e}", "path": file_path}
    return {"ok": True, "msg": "已保存", "path": file_path}


def _vectorize_files(
    kb_name: str,
    file_names: List[str],
    *,
    chunk_size: int,
    chunk_overlap: int,
    zh_title_enhance: bool,
) -> Dict[str, Any]:
    """对已落盘的文件触发向量化（走 update_docs 的"新加入"分支）。"""
    from chayuan.server.knowledge_base.kb_doc_api import update_docs
    res = update_docs(
        knowledge_base_name=kb_name,
        file_names=file_names,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        zh_title_enhance=zh_title_enhance,
        override_custom_docs=True,
        docs="",
        not_refresh_vs_cache=False,
    )
    data = getattr(res, "data", None) or {}
    return {
        "ok": True,
        "msg": getattr(res, "msg", "") or "完成",
        "failed_files": data.get("failed_files") or {},
    }


def _search_docs(
    kb_name: str, query: str, top_k: int, score_threshold: float,
) -> List[Dict[str, Any]]:
    from chayuan.server.knowledge_base.kb_doc_api import search_docs as _do
    try:
        return list(_do(
            query=query,
            knowledge_base_name=kb_name,
            top_k=int(top_k),
            score_threshold=float(score_threshold),
            file_name="",
            metadata={},
        ) or [])
    except Exception:  # noqa: BLE001
        logger.exception("search_docs failed")
        return []


def _list_file_chunks(kb_name: str, file_name: str) -> List[Dict[str, Any]]:
    """按 file_name 从已入库的分片里拉出全部 chunk（含 metadata + content）。"""
    from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
    kb = KBServiceFactory.get_service_by_name(kb_name)
    if kb is None:
        return []
    try:
        docs = kb.list_docs(file_name=file_name) or []
    except Exception:  # noqa: BLE001
        logger.exception("list_docs failed")
        return []
    out: List[Dict[str, Any]] = []
    for i, d in enumerate(docs):
        meta = dict(getattr(d, "metadata", {}) or {})
        meta.pop("vector", None)
        out.append({
            "index": i,
            "id": getattr(d, "id", "") or meta.get("id", ""),
            "page_content": getattr(d, "page_content", "") or "",
            "metadata": meta,
        })
    return out


def _update_chunk(
    kb_name: str,
    chunk_id: str,
    page_content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """就地更新一个分片：保留 id，重新向量化正文 + 替换 metadata。

    走 KBService.update_doc_by_ids —— 内部 = del_doc_by_ids + do_add_doc（同 id）。
    对 FAISS 会同时触发 save_vector_store；Milvus / pg / es 已经是持久化的。
    """
    if not (chunk_id or "").strip():
        return {"ok": False, "msg": "分片没有 ID，无法原位更新（切分预览模式不支持保存）。"}
    if not (page_content or "").strip():
        return {"ok": False, "msg": "正文不能为空。若要删除请使用『删除此分片』按钮。"}

    try:
        from langchain_core.documents import Document
        from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": f"依赖加载失败：{e}"}

    kb = KBServiceFactory.get_service_by_name(kb_name)
    if kb is None:
        return {"ok": False, "msg": f"知识库 {kb_name} 不存在"}

    embed_ok, embed_msg = kb.check_embed_model()
    if not embed_ok:
        return {"ok": False, "msg": f"Embedding 不可用：{embed_msg}"}

    # metadata 传进来可能已被前端改过；做一个"最小字段保留"：
    # - 若用户误删 source，就从原 doc metadata 回填（防止检索时按 file_name 丢失）；
    # - vector 永远不保留（向量在 do_add_doc 里重新计算）。
    clean_meta: Dict[str, Any] = dict(metadata or {})
    clean_meta.pop("vector", None)

    try:
        doc = Document(page_content=page_content, metadata=clean_meta)
        ok = kb.update_doc_by_ids({chunk_id: doc})
    except Exception as e:  # noqa: BLE001
        logger.exception("update_doc_by_ids failed")
        return {"ok": False, "msg": f"{type(e).__name__}: {e}"}

    # FAISS 需要显式落盘；其它方言是 no-op
    try:
        kb.save_vector_store()
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": bool(ok),
        "msg": "已重新向量化并保存" if ok else "保存失败（请检查 embedding 日志）",
    }


def _delete_chunk(kb_name: str, chunk_id: str) -> Dict[str, Any]:
    """按 id 删除单个分片。"""
    if not (chunk_id or "").strip():
        return {"ok": False, "msg": "分片没有 ID，无法删除。"}
    try:
        from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": f"依赖加载失败：{e}"}
    kb = KBServiceFactory.get_service_by_name(kb_name)
    if kb is None:
        return {"ok": False, "msg": f"知识库 {kb_name} 不存在"}
    try:
        ok = kb.del_doc_by_ids([chunk_id])
    except Exception as e:  # noqa: BLE001
        logger.exception("del_doc_by_ids failed")
        return {"ok": False, "msg": f"{type(e).__name__}: {e}"}
    try:
        kb.save_vector_store()
    except Exception:
        pass
    return {"ok": bool(ok), "msg": "已删除分片" if ok else "删除失败"}


def _preview_split(
    kb_name: str, file_name: str,
    *, chunk_size: int, chunk_overlap: int, zh_title_enhance: bool,
) -> Dict[str, Any]:
    """用指定 chunk 参数在内存里做一次切分预览，不落盘。"""
    try:
        from chayuan.server.knowledge_base.utils import KnowledgeFile
        kf = KnowledgeFile(filename=file_name, knowledge_base_name=kb_name)
        if not os.path.exists(kf.filepath):
            return {"ok": False, "msg": "文件不在磁盘上", "chunks": []}
        chunks = kf.file2text(
            zh_title_enhance=zh_title_enhance,
            refresh=True,
            chunk_size=int(chunk_size),
            chunk_overlap=int(chunk_overlap),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("preview split failed")
        return {"ok": False, "msg": f"{type(e).__name__}: {e}", "chunks": []}

    out: List[Dict[str, Any]] = []
    for i, d in enumerate(chunks or []):
        meta = dict(getattr(d, "metadata", {}) or {})
        out.append({
            "index": i,
            "page_content": getattr(d, "page_content", "") or "",
            "metadata": meta,
        })
    return {"ok": True, "msg": f"切分出 {len(out)} 个分片", "chunks": out}


def _read_text_file(path: str, *, max_bytes: int = 512 * 1024) -> Dict[str, Any]:
    """尽力把文件当文本读出来（兼容 GBK/UTF-8 等）。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            raw = f.read(max_bytes)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": str(e), "text": "", "truncated": False}
    # 编码探测：优先 chardet（项目已依赖），失败回退 utf-8/ignore
    text = ""
    try:
        import chardet
        enc = (chardet.detect(raw) or {}).get("encoding") or "utf-8"
        text = raw.decode(enc, errors="replace")
    except Exception:  # noqa: BLE001
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            text = ""
    return {
        "ok": True,
        "msg": "",
        "text": text,
        "truncated": size > max_bytes,
        "size": size,
    }


# ---------------------------------------------------------------------------
# UI 渲染
# ---------------------------------------------------------------------------

def render_kb_manage_page(
    ui,
    *,
    mark_restart_needed: Optional[Callable[[], None]] = None,
) -> None:
    """在 dashboard 右侧主区渲染知识库管理页（列表 / 详情 双视图）。"""
    ui.add_head_html(
        """
        <style>
        .kb-card {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            padding: 14px 16px;
            display: flex; flex-direction: column; gap: 8px;
            transition: border-color 160ms ease, box-shadow 160ms ease,
                        transform 160ms ease;
        }
        .kb-card:hover {
            border-color: #bfdbfe;
            box-shadow: 0 6px 18px rgba(37, 99, 235, 0.08);
            transform: translateY(-1px);
        }
        .kb-chip {
            display: inline-flex; align-items: center; padding: 2px 10px;
            border-radius: 999px; font-size: 11px; line-height: 1.5;
            font-weight: 600; white-space: nowrap;
        }
        .kb-chip.vs   { background: #eef2ff; color: #4338ca; }
        .kb-chip.emb  { background: #ecfdf5; color: #047857; }
        .kb-chip.pub  { background: #fef3c7; color: #92400e; }
        .kb-chip.priv { background: #f1f5f9; color: #475569; }
        .kb-chip.orph { background: #fee2e2; color: #991b1b; }
        .kb-chip.ok   { background: #dcfce7; color: #166534; }
        .kb-chip.warn { background: #fef3c7; color: #92400e; }
        .kb-chip.err  { background: #fee2e2; color: #991b1b; }
        .kb-meta {
            color: #6b7280; font-size: 12px;
            display: flex; align-items: center; gap: 6px;
        }
        .kb-meta .material-icons { font-size: 14px; }
        .kb-stat {
            font-size: 12px; color: #374151;
            background: #f9fafb; padding: 3px 10px; border-radius: 6px;
            border: 1px solid #e5e7eb;
        }
        .kb-chunk {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            padding: 12px 14px;
            margin-bottom: 10px;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
            transition: border-color 160ms ease, box-shadow 160ms ease;
        }
        .kb-chunk:hover {
            border-color: #c7d2fe;
            box-shadow: 0 4px 12px rgba(67, 56, 202, 0.08);
        }
        .kb-chunk pre {
            margin: 8px 0 0 0;
            padding: 10px 12px;
            background: #f8fafc;
            border-radius: 8px;
            border: 1px solid #eef2f7;
            font-family: ui-monospace, Menlo, monospace;
            font-size: 13px;
            line-height: 1.65;
            white-space: pre-wrap;
            word-break: break-word;
            max-height: 360px;
            overflow: auto;
        }

        /* === 分片卡片网格（分片详情对话框专用） === */
        .kb-chunk-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 14px;
            width: 100%;
        }
        .kb-chunk-tile {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            padding: 0;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            cursor: pointer;
            position: relative;
            transition: border-color 160ms ease, box-shadow 160ms ease,
                        transform 160ms ease;
            min-height: 240px;
        }
        .kb-chunk-tile::before {
            content: '';
            position: absolute; top: 0; left: 0; right: 0; height: 3px;
            background: linear-gradient(90deg, #6366f1 0%, #a855f7 60%, transparent 100%);
            opacity: 0.9;
        }
        .kb-chunk-tile:hover {
            border-color: #a5b4fc;
            box-shadow: 0 10px 24px rgba(67, 56, 202, 0.14);
            transform: translateY(-2px);
        }
        .kb-chunk-tile .cti-head {
            display: flex; align-items: center; gap: 6px;
            padding: 10px 12px 6px;
            flex: 0 0 auto;
        }
        .kb-chunk-tile .cti-index {
            font-size: 11px; font-weight: 700;
            color: #4338ca; background: #eef2ff;
            border-radius: 999px; padding: 2px 10px;
            white-space: nowrap;
        }
        .kb-chunk-tile .cti-size {
            font-size: 11px; color: #6b7280;
        }
        .kb-chunk-tile .cti-meta-dot {
            width: 6px; height: 6px; border-radius: 50%;
            background: #c7d2fe;
        }
        .kb-chunk-tile .cti-content {
            flex: 1 1 auto; min-height: 0;
            padding: 0 12px 10px;
            margin: 0;
            font-family: ui-monospace, Menlo, monospace;
            font-size: 12.5px; line-height: 1.6;
            color: #1f2937;
            /* 限制最多显示 8 行后省略 */
            display: -webkit-box;
            -webkit-line-clamp: 8;
            -webkit-box-orient: vertical;
            overflow: hidden;
            white-space: pre-wrap;
            word-break: break-word;
        }
        .kb-chunk-tile .cti-foot {
            flex: 0 0 auto;
            padding: 6px 8px 6px 12px;
            border-top: 1px solid #f1f5f9;
            background: #fafbff;
            display: flex; align-items: center; gap: 6px;
            font-size: 11px; color: #6b7280;
        }
        .kb-chunk-tile .cti-foot-src {
            flex: 1 1 auto; min-width: 0;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
            font-family: ui-monospace, Menlo, monospace;
        }
        /* 底部「详情」按钮：紧凑且显眼，防止 Quasar 默认 min-height 把卡片撑高 */
        .kb-chunk-tile .cti-foot-btn {
            flex: 0 0 auto;
            min-height: 0 !important;
            padding: 2px 8px !important;
        }
        .kb-chunk-tile .cti-foot-btn .q-btn__content {
            font-weight: 600;
        }
        .kb-chunk-tile mark {
            background: #fde68a; color: #111827;
            padding: 0 2px; border-radius: 2px;
        }
        /* 空态 */
        .kb-chunk-empty {
            grid-column: 1 / -1;
            display: flex; flex-direction: column; align-items: center;
            justify-content: center;
            padding: 40px 20px;
            color: #6b7280; gap: 10px;
            background: #ffffff; border: 1px dashed #cbd5e1;
            border-radius: 12px;
        }
        /* 搜索工具条（分片详情 dialog 内部） */
        .kb-chunk-searchbar {
            display: flex; align-items: center; gap: 8px;
            flex-wrap: wrap;
        }
        .kb-chunk-searchbar .q-field {
            min-width: 240px;
        }
        .kb-chunk-info {
            display: flex; align-items: center; gap: 6px;
            padding: 4px 10px;
            background: #eef2ff; color: #4338ca;
            border-radius: 999px;
            font-size: 12px; font-weight: 600;
            white-space: nowrap;
        }
        .kb-meta-grid {
            display: grid;
            grid-template-columns: max-content 1fr;
            gap: 4px 10px;
            font-size: 11px;
            color: #6b7280;
            font-family: ui-monospace, Menlo, monospace;
        }
        .kb-meta-grid .k { color: #4338ca; font-weight: 600; }
        .kb-score-bar {
            height: 4px; border-radius: 2px; background: #e5e7eb;
            overflow: hidden; margin-top: 4px;
        }
        .kb-score-fill {
            height: 100%;
            background: linear-gradient(90deg, #22c55e, #2563eb);
        }

        /* === 宽版弹窗（预览 / 分片详情）=== */
        /* 根基：走 Quasar maximized（.q-dialog__inner 自带 100vw/100vh）， */
        /* 再把 padding / 圆角 / 阴影补回来，让 card 真正填满可视区域。 */
        .kb-wide-dialog .q-dialog__inner,
        .kb-wide-dialog .q-dialog__inner--maximized {
            padding: 20px !important;
            height: 100% !important;
            width: 100% !important;
            max-height: 100vh !important;
            max-width: 100vw !important;
            align-items: stretch !important;
            justify-content: stretch !important;
        }
        .kb-wide-dialog .q-dialog__inner > .q-card,
        .kb-wide-dialog .q-dialog__inner--maximized > .q-card {
            width: 100% !important;
            max-width: none !important;
            height: 100% !important;
            max-height: none !important;
            border-radius: 14px !important;
            display: flex !important;
            flex-direction: column !important;
            overflow: hidden !important;
            box-shadow: 0 20px 50px rgba(15, 23, 42, 0.18) !important;
            margin: 0 !important;
            padding: 0 !important;
        }
        .kb-wide-header {
            flex: 0 0 auto !important;
            padding: 14px 20px !important;
            border-bottom: 1px solid #e5e7eb !important;
            background: linear-gradient(180deg, #f5f7ff 0%, #ffffff 100%) !important;
        }
        .kb-wide-toolbar {
            flex: 0 0 auto !important;
            padding: 10px 20px !important;
            background: #ffffff !important;
            border-bottom: 1px solid #f1f5f9 !important;
        }
        /* body：吃掉 header / toolbar / footer 之外全部剩余高度 */
        .kb-wide-body {
            flex: 1 1 auto !important;
            min-height: 0 !important;
            padding: 16px 20px !important;
            background: #f8fafc !important;
            display: flex !important;
            flex-direction: column !important;
            gap: 10px;
        }
        /* 预览模式：body 不滚，内部单一 fill 子项自己铺满 & 自己滚 */
        .kb-wide-body.kb-fill {
            overflow: hidden !important;
        }
        /* 分片列表模式：body 垂直堆叠，body 自身滚动 */
        .kb-wide-body.kb-scroll {
            overflow: auto !important;
        }
        .kb-wide-footer {
            flex: 0 0 auto !important;
            padding: 10px 20px !important;
            border-top: 1px solid #e5e7eb !important;
            background: #ffffff !important;
        }
        /* 预览填充容器：iframe / pre / img 都套这个，保证铺满 body 剩余高度 */
        .kb-preview-fill {
            flex: 1 1 auto !important;
            min-height: 0 !important;
            width: 100% !important;
            height: 100% !important;
            display: flex !important;
            flex-direction: column;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            background: #ffffff;
            overflow: hidden;
        }
        .kb-preview-fill iframe {
            flex: 1 1 auto !important;
            width: 100% !important;
            height: 100% !important;
            min-height: 0 !important;
            border: 0 !important;
            display: block !important;
        }
        .kb-preview-fill pre.kb-preview-text {
            flex: 1 1 auto !important;
            min-height: 0 !important;
            margin: 0;
            width: 100%;
            padding: 18px 20px;
            background: #0b1020;
            color: #e5e7eb;
            font-family: ui-monospace, Menlo, monospace;
            font-size: 13.5px;
            line-height: 1.7;
            white-space: pre-wrap;
            word-break: break-word;
            overflow: auto;
            border-radius: 10px;
        }
        .kb-preview-img-wrap {
            flex: 1 1 auto !important;
            min-height: 0 !important;
            display: flex;
            align-items: center;
            justify-content: center;
            background: repeating-conic-gradient(
                #f3f4f6 0 25%, #ffffff 0 50%) 50% / 20px 20px;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            overflow: auto;
        }
        .kb-preview-img-wrap img {
            max-width: 100%;
            max-height: 100%;
            object-fit: contain;
            display: block;
        }
        .kb-preview-unsupported {
            flex: 1 1 auto !important;
            min-height: 0 !important;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 12px;
            background: #ffffff;
            border: 1px dashed #cbd5e1;
            border-radius: 10px;
        }
        </style>
        """
    )

    # 全局状态
    kb_defaults = _kb_settings()
    view_state: Dict[str, Any] = {
        "view": "list",        # list / detail
        "current_kb": None,    # dict or None（detail view 的目标）
        "rows": [],
        "search": "",
        "vis_filter": "全部",
    }
    page_root = ui.column().classes("w-full").style("gap: 12px;")

    def _mark_changed() -> None:
        if mark_restart_needed is not None:
            try:
                mark_restart_needed()
            except Exception:  # noqa: BLE001
                pass

    # ======================================================================
    # 列表视图
    # ======================================================================
    def _render_list_view() -> None:
        page_root.clear()
        view_state["view"] = "list"
        with page_root:
            _build_list_toolbar()
            _build_preview_service_card()
            stats_row = ui.row().classes("items-center w-full").style("gap: 8px;")
            grid = ui.grid(columns=2).classes("w-full").style("gap: 12px;")

            def _reload() -> None:
                try:
                    view_state["rows"] = _list_kbs_with_meta()
                except Exception as e:  # noqa: BLE001
                    logger.exception("list kbs failed")
                    ui.notify(
                        f"加载知识库失败：{type(e).__name__}: {e}",
                        type="negative",
                    )
                    view_state["rows"] = []
                _render_stats(stats_row)
                _render_grid(grid)

            # 把 _reload 暴露到状态里，供对话框回调用
            view_state["_reload_list"] = _reload
            _reload()

    def _build_list_toolbar() -> None:
        toolbar = ui.row().classes("items-center w-full no-wrap").style("gap: 8px;")
        with toolbar:
            search = ui.input(
                placeholder="搜索知识库...",
            ).props("dense outlined clearable").classes("col-grow").style(
                "max-width: 280px;"
            )

            vis_toggle = ui.toggle(
                ["全部", "private", "public"], value=view_state["vis_filter"],
            ).props("dense").classes("col-shrink")

            ui.space()

            ui.button(
                "刷新", icon="refresh",
                on_click=lambda: view_state["_reload_list"](),
            ).props("flat dense")

            ui.button(
                "新建知识库", icon="add",
                on_click=lambda: _open_create_dialog(),
            ).props("color=primary unelevated dense")

            def _on_search(e) -> None:
                view_state["search"] = str(e.value or "")
                view_state["_reload_list"]()

            def _on_vis(e) -> None:
                view_state["vis_filter"] = str(e.value or "全部") or "全部"
                view_state["_reload_list"]()

            search.set_value(view_state["search"])
            search.on_value_change(_on_search)
            vis_toggle.on_value_change(_on_vis)

    def _filtered_rows() -> List[Dict[str, Any]]:
        q = (view_state["search"] or "").strip().lower()
        vf = view_state["vis_filter"]
        out: List[Dict[str, Any]] = []
        for r in view_state["rows"]:
            if vf != "全部" and r.get("visibility") != vf:
                continue
            if q:
                hay = " ".join([
                    str(r.get("kb_name", "")),
                    str(r.get("kb_info", "")),
                    str(r.get("vs_type", "")),
                    str(r.get("embed_model", "")),
                    str(r.get("owner_name", "")),
                ]).lower()
                if q not in hay:
                    continue
            out.append(r)
        return out

    def _build_preview_service_card() -> None:
        """文件预览服务(kkFileView)— KB 维度的全局配置块。

        设计:URL 一行 + 验证 + 保存 + 状态指示;清空 URL 即关闭旁车,前端
        自动 fallback 到内置 renderer。
        """
        cur = (kb_defaults.get("kkfileview_url") or "").strip()
        with ui.card().classes("w-full").props("flat bordered").style(
            "padding: 10px 14px;"
        ):
            with ui.row().classes("items-center w-full no-wrap").style("gap: 10px;"):
                ui.icon("preview", size="18px").classes("text-indigo-7")
                ui.label("文件预览服务 (kkFileView 旁车)").classes(
                    "text-subtitle2",
                ).style("font-weight: 600;")
                status_chip = ui.html(
                    '<span class="kb-chip" style="background:#f1f5f9;color:#64748b">未验证</span>'
                )
                ui.space()
                ui.label(
                    "配置后客户端预览覆盖 100+ 格式 (Office / WPS / 3D / CAD / 视频转码 等);"
                    "留空走内置 renderer。",
                ).classes("text-caption text-grey-6").style("max-width: 50%;")

            with ui.row().classes("items-center w-full no-wrap q-mt-xs").style("gap: 8px;"):
                url_in = ui.input(
                    label="kkFileView 地址",
                    value=cur,
                    placeholder="http://127.0.0.1:8012",
                ).props("dense outlined clearable").classes("col-grow")

                async def _on_verify() -> None:
                    val = (url_in.value or "").strip()
                    if not val:
                        status_chip.set_content(
                            '<span class="kb-chip" style="background:#f1f5f9;color:#64748b">空</span>'
                        )
                        ui.notify("请先填写地址再验证", type="warning")
                        return
                    status_chip.set_content(
                        '<span class="kb-chip" style="background:#fef3c7;color:#92400e">验证中…</span>'
                    )
                    # 探活是阻塞 IO,丢线程池避免卡住事件循环
                    import asyncio as _asyncio
                    r = await _asyncio.to_thread(_probe_kkfileview, val)
                    if r["ok"]:
                        latency = r.get("latency_ms", 0)
                        status_chip.set_content(
                            f'<span class="kb-chip" style="background:#dcfce7;color:#166534">'
                            f'✓ 在线 · {latency}ms</span>'
                        )
                        ui.notify(
                            f"kkFileView 连通正常({latency}ms);保存后前端预览全格式接管",
                            type="positive",
                        )
                    else:
                        status_chip.set_content(
                            f'<span class="kb-chip" style="background:#fee2e2;color:#991b1b">'
                            f'✗ 不可达</span>'
                        )
                        ui.notify(
                            f"kkFileView 验证失败:{r['msg']}",
                            type="negative",
                        )

                ui.button(
                    "验证连通性", icon="network_check", on_click=_on_verify,
                ).props("flat dense color=primary").tooltip(
                    "GET <url>/ 看是否返回 kkFileView 主页(2~4s 超时)"
                )

                def _on_save() -> None:
                    val = (url_in.value or "").strip()
                    try:
                        from chayuan.server.config_panel import yaml_store
                        yaml_store.save_updates(
                            "kb_settings.yaml",
                            {"KKFILEVIEW_URL": val},
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("save kkfileview url failed")
                        ui.notify(
                            f"保存失败:{type(exc).__name__}: {exc}",
                            type="negative",
                        )
                        return
                    kb_defaults["kkfileview_url"] = val
                    # 同步进进程内 Settings,前端立即生效(/knowledge_base/preview-config 现读)
                    try:
                        from chayuan.settings import Settings as _S
                        if hasattr(_S.kb_settings, "KKFILEVIEW_URL"):
                            _S.kb_settings.KKFILEVIEW_URL = val
                    except Exception:  # noqa: BLE001
                        pass
                    _mark_changed()
                    ui.notify(
                        "已保存到 kb_settings.yaml;前端下次刷新预览面板即生效"
                        if val
                        else "已清空 kkFileView 地址,前端走内置 renderer",
                        type="positive",
                    )

                ui.button(
                    "保存", icon="save", on_click=_on_save,
                ).props("unelevated dense color=primary")

    def _render_stats(stats_row) -> None:
        stats_row.clear()
        rows = view_state["rows"] or []
        total = len(rows)
        pub = sum(1 for r in rows if r.get("visibility") == "public")
        files = sum(int(r.get("file_count") or 0) for r in rows)
        orph = sum(1 for r in rows if r.get("orphan"))
        with stats_row:
            def _stat(icon: str, label: str, value: Any, color: str) -> None:
                ui.html(
                    f'<div style="display:flex;align-items:center;gap:8px;'
                    f'padding:6px 12px;background:#ffffff;border:1px solid #e5e7eb;'
                    f'border-radius:8px;">'
                    f'<span class="material-icons" style="color:{color};font-size:18px;">'
                    f'{icon}</span>'
                    f'<span style="font-size:12px;color:#6b7280;">{label}</span>'
                    f'<span style="font-weight:600;color:#111827;">{value}</span></div>'
                )
            _stat("library_books", "知识库", total, "#4338ca")
            _stat("public", "public", pub, "#b45309")
            _stat("description", "文件总数", files, "#047857")
            if orph:
                _stat("warning", "磁盘孤儿", orph, "#b91c1c")

    def _render_grid(grid) -> None:
        grid.clear()
        rows = _filtered_rows()
        if not rows:
            with grid:
                ui.label("没有匹配的知识库。").classes(
                    "text-grey-7 q-pa-md"
                ).style("grid-column: span 2;")
            return
        with grid:
            for r in rows:
                _render_kb_card(r)

    def _render_kb_card(r: Dict[str, Any]) -> None:
        kb_name = r["kb_name"]
        with ui.element("div").classes("kb-card"):
            with ui.row().classes("items-center no-wrap w-full").style("gap: 8px;"):
                ui.icon("library_books", size="22px").classes("text-indigo-7")
                title_lbl = ui.label(kb_name).classes("text-subtitle1").style(
                    "font-weight: 600; line-height: 1.2; flex: 1 1 auto; "
                    "overflow: hidden; text-overflow: ellipsis; white-space: nowrap; "
                    "cursor: pointer; color: #1e40af;"
                )
                title_lbl.on(
                    "click", lambda _=None, kb=r: _enter_detail(kb),
                )
                title_lbl.tooltip("点击查看文件清单")
                vis = (r.get("visibility") or "private")
                vis_cls = "pub" if vis == "public" else "priv"
                ui.html(f'<span class="kb-chip {vis_cls}">{vis}</span>')
                if r.get("orphan"):
                    ui.html(
                        '<span class="kb-chip orph" title="磁盘目录存在但未入库">'
                        '磁盘孤儿</span>'
                    )

            info = (r.get("kb_info") or "").strip()
            if info:
                ui.label(info).classes("text-body2 text-grey-8").style(
                    "line-height: 1.4; "
                    "display: -webkit-box; -webkit-line-clamp: 2; "
                    "-webkit-box-orient: vertical; overflow: hidden;"
                )

            with ui.row().classes("items-center w-full").style(
                "gap: 6px; flex-wrap: wrap;"
            ):
                vs_type = (r.get("vs_type") or "").strip() or "-"
                emb = (r.get("embed_model") or "").strip() or "-"
                ui.html(f'<span class="kb-chip vs">VS · {_escape(vs_type)}</span>')
                ui.html(f'<span class="kb-chip emb">Embed · {_escape(emb)}</span>')
                ui.html(
                    f'<span class="kb-stat">文件 {int(r.get("file_count") or 0)}</span>'
                )
                owner = r.get("owner_name") or "-"
                ui.html(
                    f'<div class="kb-meta">'
                    f'<span class="material-icons">person</span>'
                    f'<span>{_escape(str(owner))}</span></div>'
                )

            with ui.row().classes("items-center w-full no-wrap q-mt-xs").style(
                "gap: 4px;"
            ):
                ui.space()
                ui.button(
                    "进入管理", icon="login",
                    on_click=lambda _=None, kb=r: _enter_detail(kb),
                ).props("unelevated dense size=sm color=primary")
                ui.button(
                    "设置", icon="tune",
                    on_click=lambda _=None, kb=r: _open_edit_dialog(kb),
                ).props("flat dense size=sm color=grey-8")
                ui.button(
                    "删除", icon="delete_outline",
                    on_click=lambda _=None, kb=r: _confirm_delete(kb),
                ).props("flat dense size=sm color=negative")

    # ---- 新建对话框（5 类型统一入口）---------------------------------
    #
    # 知识库类型维度是第一级选择：
    #   📄 文档        —— 上传文件 + 默认向量库自动向量化；走内部 _create_kb
    #   🧠 向量库       —— BYO 外部向量库（Milvus/PG/ES/Chroma/Zilliz/Relyt），
    #                      走 /knowledge_source/ API（kind="vs"）
    #   🗄️ 结构化数据库  —— MySQL / Postgres / SQLite / … 走 /knowledge_source/
    #   🍃 半结构化     —— MongoDB / Elasticsearch（文本检索）同上
    #   🖼️ 图像数据     —— CLIP / SigLIP 同上
    #
    # 关键设计：
    # - **doc** 走内部 Python 函数（和以前一致，无 API 依赖）；不需要选向量库类型
    #   （kb_settings.yaml 的 DEFAULT_VS_TYPE 会自动生效），也不暴露"测试连接"。
    # - **vs / sql / nosql / image** 必须走 HTTP API，所以在对话框顶部做 API 探活，
    #   未启动就禁用提交。
    # - **vs** 方言字段复用 config_panel.vs_config.VS_TYPES 的 VsField 描述——
    #   与"基础配置 · 向量库"页共用同一套字段定义，保证一致性。

    def _open_create_dialog() -> None:
        _open_create_dialog_typed()

    def _open_create_dialog_typed() -> None:
        embed_choices = _available_embeddings()
        default_emb = _default_embedding()
        if default_emb and default_emb not in embed_choices:
            embed_choices = [default_emb] + embed_choices
        if not embed_choices:
            embed_choices = [default_emb] if default_emb else [""]
        users = _list_users_lite()

        # API 探活：非 doc 类型需 API 在线才能创建
        from chayuan.server.config_panel.api_gate import (
            api_base_url, probe_api_status, invalidate_cache,
        )

        state: Dict[str, Any] = {
            "kind": "doc",  # doc | vs | sql | nosql | image
            "api_ok": False,
            "api_detail": "",
        }

        with ui.dialog() as dialog, ui.card().style(
            "min-width: 560px; max-width: 680px;"
        ):
            ui.label("新建知识库").classes("text-h6 q-mb-xs")
            ui.label(
                "第一步选择知识库类型；不同类型的字段会随之变化。"
            ).classes("text-caption text-grey-6 q-mb-sm").style(
                "line-height: 1.5;"
            )

            # —— 类型选择（第一级）————————————————————————————————
            kind_opts = {
                "doc":    "📄 文档（上传文件 + 默认向量库自动向量化）",
                "vs":     "🧠 向量库（连接已有 Milvus / PG / ES / Chroma / Zilliz / Relyt）",
                "sql":    "🗄️ 结构化数据库（MySQL / Postgres / SQLite / …）",
                "nosql":  "🍃 半结构化（MongoDB / Elasticsearch）",
                "image":  "🖼️ 图像数据（CLIP / SigLIP 向量化）",
            }
            kind_select = ui.select(
                kind_opts, value="doc", label="知识库类型",
            ).props("dense outlined").classes("w-full")

            # —— API 探活 banner（非 doc 类型依赖 API）——————————
            api_banner = ui.element("div").classes("w-full")

            def _refresh_api_banner() -> None:
                api_banner.clear()
                if state["kind"] == "doc":
                    state["api_ok"] = True
                    return
                status = probe_api_status(use_cache=False)
                state["api_ok"] = status.ok
                state["api_detail"] = status.detail
                with api_banner:
                    if status.ok:
                        with ui.row().classes(
                            "items-center gap-2 q-pa-xs bg-green-1 rounded-borders"
                        ):
                            ui.icon("check_circle").classes("text-green-700")
                            ui.label(
                                f"API 就绪 · {status.url}"
                            ).classes("text-caption")
                    else:
                        with ui.column().classes(
                            "w-full q-pa-sm bg-red-1 rounded-borders gap-1"
                        ):
                            with ui.row().classes("items-center gap-2"):
                                ui.icon("error").classes("text-red-700")
                                ui.label(
                                    "API 服务未就绪，当前类型无法创建"
                                ).classes("text-sm font-bold text-red-800")
                            ui.label(
                                f"{api_base_url()}: {status.detail}"
                            ).classes("text-caption text-red-700")
                            ui.label(
                                "请先执行 `chayuan start -a` 启动 API。"
                            ).classes("text-caption text-gray-700")
                            ui.button(
                                "🔄 重试探活",
                                on_click=lambda: (invalidate_cache(),
                                                    _refresh_api_banner()),
                            ).props("flat dense color=red size=sm")

            # —— 公共字段 ——————————————————————————————————————
            with ui.element("div").classes("w-full"):
                name_input = ui.input(
                    label="名称（kb_name / source_name）",
                    placeholder="建议使用英文/数字/下划线；作唯一标识",
                ).props("dense outlined autofocus").classes("w-full")

                info_input = ui.textarea(
                    label="简介 / 描述（可选，供 Agent 识别）",
                    placeholder="例如：IT 运维手册；或 「订单数据库」",
                ).props("dense outlined autogrow").classes("w-full q-mt-xs")

            # —— 动态字段容器（按类型切换）——————————————————————
            form_box = ui.element("div").classes("w-full q-mt-sm")
            # form_state 用于各分支收集字段
            form_state: Dict[str, Any] = {}

            def _render_fields() -> None:
                form_box.clear()
                form_state.clear()
                kind = state["kind"]
                with form_box:
                    if kind == "doc":
                        _render_doc_fields(form_state, embed_choices, default_emb)
                    elif kind == "vs":
                        _render_vs_fields(form_state, embed_choices, default_emb)
                    elif kind == "sql":
                        _render_sql_fields(form_state)
                    elif kind == "nosql":
                        _render_nosql_fields(form_state)
                    elif kind == "image":
                        _render_image_fields(form_state)
                    # 所有类型共享：owner + visibility
                    with ui.grid(columns=2).classes("w-full q-mt-xs").style("gap:8px;"):
                        owner_opts = {0: "（无 owner）", **users}
                        form_state["owner_sel"] = ui.select(
                            owner_opts, value=0, label="Owner（可选）",
                        ).props("dense outlined")
                        form_state["vis_sel"] = ui.select(
                            {"private": "private（私有）",
                             "public":  "public（公共）"},
                            value="private", label="可见性 visibility",
                        ).props("dense outlined")

            def _on_kind(e) -> None:
                state["kind"] = str(e.value or "doc")
                _refresh_api_banner()
                _render_fields()

            kind_select.on_value_change(_on_kind)

            # 初次渲染
            _refresh_api_banner()
            _render_fields()

            # —— 连通性测试 + 创建 —————————————————————————————
            def _test_connection() -> None:
                kind = state["kind"]
                if kind == "doc":
                    ui.notify(
                        "文档类型使用系统默认向量库，无需单独测试连接；"
                        "请在「知识库配置」页测试默认后端。",
                        type="info", multi_line=True,
                    )
                    return
                if not state["api_ok"]:
                    ui.notify("API 服务未就绪；无法测试", type="warning")
                    return
                payload = _collect_test_payload(kind, form_state)
                if payload is None:
                    return
                try:
                    import httpx
                    with httpx.Client(timeout=10, trust_env=False) as c:
                        r = c.post(
                            f"{api_base_url()}/knowledge_source/test_connection",
                            json=payload,
                        )
                        data = (r.json() or {}).get("data") or {}
                except Exception as e:  # noqa: BLE001
                    ui.notify(f"测试失败：{type(e).__name__}: {e}",
                                type="negative")
                    return
                if data.get("ok"):
                    ui.notify(f"✅ 连通：{data.get('msg') or ''}", type="positive")
                else:
                    ui.notify(f"❌ 失败：{data.get('msg') or '未知错误'}",
                                type="negative", multi_line=True)

            def _do_create() -> None:
                kn = (name_input.value or "").strip()
                info = str(info_input.value or "")
                if not kn:
                    ui.notify("请填写名称", type="warning")
                    return
                kind = state["kind"]
                vis = str(form_state["vis_sel"].value or "private")
                try:
                    if kind == "doc":
                        # 受管 KB：vs_type 直接用 kb_settings 里的默认值
                        msg = _create_kb(
                            kb_name=kn,
                            vs_type=kb_defaults["default_vs_type"],
                            embed_model=str(form_state["emb_select"].value or ""),
                            kb_info=info, visibility=vis,
                            owner_id=(int(form_state["owner_sel"].value)
                                        if form_state["owner_sel"].value else None),
                        )
                    else:
                        if not state["api_ok"]:
                            ui.notify(
                                "API 服务未启动，非文档类型必须依赖 API；"
                                "请先启动 API Server",
                                type="warning", multi_line=True,
                            )
                            return
                        msg = _create_via_api(kind, kn, info, vis, form_state)
                except Exception as e:  # noqa: BLE001
                    logger.exception("create kb failed")
                    ui.notify(f"创建失败：{type(e).__name__}: {e}",
                                type="negative")
                    return
                ui.notify(msg or "创建成功", type="positive")
                _mark_changed()
                dialog.close()
                view_state["_reload_list"]()

            with ui.row().classes("w-full justify-between q-mt-sm").style(
                "gap: 6px;"
            ):
                ui.button(
                    "🔌 测试连接",
                    on_click=_test_connection,
                ).props("outline dense color=primary").tooltip(
                    "文档类型不可测试；向量库 / SQL / NoSQL / 图像类型可用"
                )
                with ui.row().classes("gap-2"):
                    ui.button("取消", on_click=dialog.close).props("flat dense")
                    ui.button(
                        "创建", icon="add", on_click=_do_create,
                    ).props("unelevated dense color=primary")

        dialog.open()

    # —————————————————————————————————————————————————————————————
    # 子表单渲染器（每种类型一个）
    # —————————————————————————————————————————————————————————————

    def _render_doc_fields(fs: Dict[str, Any],
                             embed_choices: List[str],
                             default_emb: str) -> None:
        """📄 文档：只选 embed_model。向量库类型来自 kb_settings.DEFAULT_VS_TYPE。"""
        # 显示默认向量库类型供用户确认（只读 chip，不可在此修改）
        vs_map = _vs_type_choices()
        default_key = kb_defaults["default_vs_type"]
        default_label = vs_map.get(default_key, default_key)
        with ui.row().classes(
            "items-center w-full q-pa-xs bg-blue-1 rounded-borders"
        ).style("gap: 6px;"):
            ui.icon("info").classes("text-blue-800")
            ui.label(
                f"使用系统默认向量库：{default_label}。"
                "如需修改请到「知识库配置」页配置。"
            ).classes("text-caption text-blue-900")
        fs["emb_select"] = ui.select(
            embed_choices,
            value=default_emb or (embed_choices[0] if embed_choices else ""),
            label="嵌入模型 embed_model",
            with_input=True, new_value_mode="add-unique",
        ).props("dense outlined").classes("w-full q-mt-xs")

    # VS_TYPES 在向量库分支里会用到；模块级导入避免每次点下拉都再 import
    from chayuan.server.config_panel import vs_config as _vs_config
    from chayuan.server.knowledge_source.ext_vs.backends import (
        SUPPORTED_DIALECTS as _EXT_VS_DIALECTS,
    )

    def _render_vs_fields(fs: Dict[str, Any],
                            embed_choices: List[str],
                            default_emb: str) -> None:
        """🧠 向量库：选方言 → 渲染 VS_TYPES 字段 + collection/embedder。"""
        # 方言下拉：只暴露外部可接入的 6 种；排除 faiss（本地索引，不支持 BYO）
        dialect_opts = dict(_EXT_VS_DIALECTS)
        # 默认选项：若当前系统默认就是这些里的一种，则优先选它；否则 milvus
        sys_default = kb_defaults["default_vs_type"]
        default_dialect = sys_default if sys_default in dialect_opts else "milvus"

        fs["vs_dialect"] = ui.select(
            dialect_opts, value=default_dialect, label="向量库类型 vs_type",
        ).props("dense outlined").classes("w-full")

        # 字段容器：切换 dialect 时重绘
        fs_box = ui.element("div").classes("w-full q-mt-xs")
        fs["_vs_fields_box"] = fs_box
        fs["_vs_field_refs"] = {}       # name -> ui element
        fs["_vs_current_dialect"] = default_dialect

        # collection / index / embed_model：所有方言都要
        def _redraw(dialect: str) -> None:
            fs_box.clear()
            fs["_vs_field_refs"] = {}
            vt = _vs_config.vs_type_by_key(dialect)
            if vt is None:
                with fs_box:
                    ui.label(f"未知方言 {dialect!r}").classes("text-negative")
                return
            # 复用 vs_config 的字段描述：一个方言对应一组 VsField
            with fs_box:
                # 按 2 列网格渲染
                with ui.grid(columns=2).classes("w-full").style("gap: 8px;"):
                    for f in vt.fields:
                        el = _make_vs_input(ui, f)
                        fs["_vs_field_refs"][f.name] = el
                # collection / index / persist_directory：由本对话框额外补充
                # （vs_config 只管"默认后端连接"，不涉及具体 collection 名）
                ui.separator().classes("q-my-xs")
                with ui.grid(columns=2).classes("w-full").style("gap: 8px;"):
                    if dialect in ("milvus", "zilliz", "pg", "relyt", "chromadb"):
                        fs["_vs_collection"] = ui.input(
                            label="集合名 collection",
                            placeholder="已存在的 collection 名，区分大小写",
                        ).props("dense outlined")
                    if dialect == "es":
                        fs["_vs_collection"] = ui.input(
                            label="索引名 index_name",
                            placeholder="已存在的 ES index 名",
                        ).props("dense outlined")
                    if dialect == "chromadb":
                        fs["_vs_persist"] = ui.input(
                            label="持久化目录 persist_directory",
                            placeholder="绝对路径；指向 Chroma 的 db 目录",
                        ).props("dense outlined")
                    fs["emb_select"] = ui.select(
                        embed_choices,
                        value=default_emb or (embed_choices[0] if embed_choices else ""),
                        label="嵌入模型 embed_model",
                        with_input=True, new_value_mode="add-unique",
                    ).props("dense outlined")

        def _on_vs_dialect(e) -> None:
            d = str(e.value or default_dialect)
            fs["_vs_current_dialect"] = d
            _redraw(d)
        fs["vs_dialect"].on_value_change(_on_vs_dialect)
        _redraw(default_dialect)

    _SQL_DIALECTS = [
        ("mysql",      3306, "MySQL"),
        ("postgresql", 5432, "PostgreSQL"),
        ("sqlite",     0,    "SQLite（文件）"),
        ("mssql",      1433, "SQL Server"),
        ("oracle",     1521, "Oracle"),
        ("clickhouse", 8123, "ClickHouse"),
        ("doris",      9030, "Doris"),
    ]

    def _render_sql_fields(fs: Dict[str, Any]) -> None:
        dialect_opts = {d: label for d, _, label in _SQL_DIALECTS}
        fs["dialect"] = ui.select(
            dialect_opts, value="mysql", label="数据库类型",
        ).props("dense outlined").classes("w-full")

        with ui.grid(columns=2).classes("w-full q-mt-xs").style("gap: 8px;"):
            fs["host"] = ui.input(label="主机", placeholder="127.0.0.1").props(
                "dense outlined"
            )
            fs["port"] = ui.number(label="端口", value=3306, min=0, max=65535,
                                      step=1).props("dense outlined")
        fs["database"] = ui.input(
            label="数据库名 / SQLite 文件路径",
        ).props("dense outlined").classes("w-full q-mt-xs")
        with ui.grid(columns=2).classes("w-full q-mt-xs").style("gap: 8px;"):
            fs["username"] = ui.input(label="用户名").props("dense outlined")
            fs["password"] = ui.input(
                label="密码", password=True, password_toggle_button=True,
            ).props("dense outlined")
        fs["allowed_tables"] = ui.input(
            label="白名单表（可选，逗号分隔）",
            placeholder="Text2SQL 仅能触达这些表；留空则全部可见",
        ).props("dense outlined").classes("w-full q-mt-xs")

        # 端口默认值随 dialect 自动切
        def _on_dialect(e) -> None:
            d = str(e.value or "mysql")
            for k, p, _lbl in _SQL_DIALECTS:
                if k == d:
                    fs["port"].set_value(p)
                    # SQLite：隐藏 host/port/user/pwd
                    is_sqlite = (d == "sqlite")
                    fs["host"].visible = not is_sqlite
                    fs["port"].visible = not is_sqlite
                    fs["username"].visible = not is_sqlite
                    fs["password"].visible = not is_sqlite
                    break
        fs["dialect"].on_value_change(_on_dialect)

    def _render_nosql_fields(fs: Dict[str, Any]) -> None:
        fs["kind_sub"] = ui.select(
            {"mongodb": "MongoDB", "elasticsearch": "Elasticsearch"},
            value="mongodb", label="类型",
        ).props("dense outlined").classes("w-full")
        with ui.grid(columns=2).classes("w-full q-mt-xs").style("gap: 8px;"):
            fs["host"] = ui.input(label="主机", placeholder="127.0.0.1").props(
                "dense outlined"
            )
            fs["port"] = ui.number(label="端口", value=27017, min=0, max=65535,
                                      step=1).props("dense outlined")
        fs["database"] = ui.input(
            label="库名 / 默认 index（可选）",
        ).props("dense outlined").classes("w-full q-mt-xs")
        with ui.grid(columns=2).classes("w-full q-mt-xs").style("gap: 8px;"):
            fs["username"] = ui.input(label="用户名（可选）").props("dense outlined")
            fs["password"] = ui.input(
                label="密码（可选）", password=True, password_toggle_button=True,
            ).props("dense outlined")
        fs["allowed_raw"] = ui.input(
            label="白名单集合 / 索引（逗号分隔，可选）",
        ).props("dense outlined").classes("w-full q-mt-xs")

        def _on_nosql_kind(e) -> None:
            v = str(e.value or "mongodb")
            fs["port"].set_value(27017 if v == "mongodb" else 9200)
        fs["kind_sub"].on_value_change(_on_nosql_kind)

    def _render_image_fields(fs: Dict[str, Any]) -> None:
        """图像 KB 创建表单：只展示"严格就绪"的模型；空列表时引导去模型配置页下载。

        能力过滤策略
        ----------
        默认只列**跨模态**模型（text+image 同语义空间），因为这是日常"文本查图"
        场景——这是最主流的需求。用户可切换到"包含仅视觉模型"看到 DINOv2 /
        ResNet 等只能以图搜图的家族，并会被明确告知"创建后仅支持以图搜图"。

        搜索一致性
        ----------
        - KB 创建时把模型名写入 ``spec.options.embedder_model``
        - 入库时 ``ImageConnector.add_image`` 把 model name + dim 写进 RetrievalChunk
          metadata，store 层对 dim 做硬约束
        - 检索时同样从 ``spec.options`` 读模型名，走同一 ``get_embedder``；
          维度/模型不一致会被 ``_check_consistency`` 拦截并返回清晰错误
        - 所以"搜时模型一致"是**架构保证的硬约束**，不是靠用户自觉

        「严格就绪」= 依赖可用 + 已下载 + smoke 测试通过。由
        :func:`chayuan.server.image_source.model_registry.list_ready_models`
        统一判定；与模型配置页「图像向量化模型」卡片共用同一 SSOT。
        """
        # 第 0 步：先渲染"包含仅视觉模型"开关；默认关闭 → 只列跨模态
        include_iv: Dict[str, Any] = {"value": False}

        container = ui.column().classes("w-full").style("gap: 6px;")

        def _repaint() -> None:
            container.clear()
            ready_items = _list_ready_image_models(
                crossmodal_only=not include_iv["value"]
            )
            ready_names = [m["name"] for m in ready_items]

            with container:
                # 顶部 note + toggle
                with ui.row().classes(
                    "items-center w-full no-wrap q-mb-xs"
                ).style("gap: 8px;"):
                    ui.icon("info", size="14px").classes("text-indigo-6")
                    ui.label(
                        "KB 一旦创建，其图像向量化模型不可更改——这是向量空间一致性要求。"
                    ).classes("text-caption text-indigo-8").style("line-height: 1.4;")
                    ui.space()
                    sw = ui.switch(
                        value=include_iv["value"],
                        text="包含仅视觉模型（DINOv2/ResNet；仅以图搜图）",
                    ).props("dense")
                    sw.on_value_change(lambda e: (
                        include_iv.__setitem__("value", bool(e.value)),
                        _repaint(),
                    )[-1])

                if not ready_names:
                    with ui.row().classes(
                        "items-center w-full no-wrap q-pa-sm"
                    ).style(
                        "gap: 8px; background: #fef3c7; border-radius: 6px; "
                        "border: 1px solid #fcd34d;"
                    ):
                        ui.icon("warning_amber", size="20px").classes("text-amber-8")
                        ui.label(
                            "尚无可用的图像向量化模型。请先前往『模型配置』→「图像向量化模型」"
                            "下载并测试至少一个模型。"
                        ).classes("text-caption").style(
                            "color: #92400e; line-height: 1.4;"
                        )

                        def _goto_model_config() -> None:
                            try:
                                from nicegui import ui as _ui
                                _ui.navigate.to("/?section=file:model_settings.yaml")
                            except Exception:  # noqa: BLE001
                                ui.notify(
                                    "请手动切换到左侧导航「模型配置」页下载图像模型",
                                    type="info", multi_line=True,
                                )

                        ui.button(
                            "前往模型配置", icon="open_in_new",
                            on_click=_goto_model_config,
                        ).props("unelevated dense color=amber-8 size=sm")
                    fs["embedder"] = ui.select(
                        [""], value="", label="图像向量化模型（不可用）",
                    ).props("dense outlined").classes("w-full q-mt-xs")
                    fs["embedder"].disable()
                    return

                # 富文本标签："名字 · 能力 · 中文 · 大小 · 维度"
                labels: Dict[str, str] = {}
                for m in ready_items:
                    caps = m.get("capabilities") or {}
                    cap_label = "跨模态" if caps.get("crossmodal") else "仅视觉"
                    zh = {"strong": "中文强", "medium": "中文中",
                          "weak": "中文弱"}.get(m.get("chinese_level", ""), "")
                    size = m.get("cached_size_mb") or 0
                    size_h = f"{size / 1024:.1f}GB" if size >= 1024 else f"{int(size)}MB"
                    labels[m["name"]] = (
                        f"{m['name']}  ·  {cap_label}  ·  {zh}  ·  {size_h}  ·  {m.get('dim')}维"
                    )

                fs["embedder"] = ui.select(
                    labels, value=ready_names[0],
                    label="图像向量化模型（创建后不可更改）",
                ).props("dense outlined").classes("w-full")
                ui.label(
                    f"已就绪 {len(ready_names)} 个模型"
                    + (
                        "（含仅视觉：此类模型只支持以图搜图，不支持文本查图）"
                        if include_iv["value"] else "（仅跨模态）"
                    )
                    + "。添加/测试其他模型请到『模型配置』页。"
                ).classes("text-caption text-grey-6")

        _repaint()

    def _list_ready_image_models(
        crossmodal_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """优先走同进程调用（快且不受 API Gate 影响）；失败回落 HTTP；再失败空列表。"""
        # 路径 1：同进程直调（Config Panel 与 API Server 同进程时生效）
        try:
            from chayuan.server.image_source.model_registry import list_ready_models
            items = list_ready_models(crossmodal_only=crossmodal_only) or []
            if items:
                return items
        except Exception as e:  # noqa: BLE001
            logger.debug("local list_ready_models 失败，改走 HTTP：%r", e)
        # 路径 2：HTTP，兼容 panel 与 api 分离部署
        try:
            import httpx
            from chayuan.server.config_panel.api_gate import api_base_url
            with httpx.Client(timeout=2, trust_env=False) as c:
                r = c.get(
                    f"{api_base_url()}/image_models/",
                    params={
                        "ready": "true",
                        "crossmodal_only": "true" if crossmodal_only else "false",
                    },
                )
                if r.status_code == 200:
                    data = r.json() or {}
                    return list(data.get("data") or [])
        except Exception as e:  # noqa: BLE001
            logger.debug("remote list_ready_models 失败：%r", e)
        return []

    # ---- 连通性测试 payload 组装 / API 创建调用 ----------------------

    def _collect_vs_values(fs: Dict[str, Any]) -> Dict[str, Any]:
        """把 🧠 向量库分支的表单控件收敛成 vs_config 风格的 values dict。"""
        out: Dict[str, Any] = {}
        for k, el in (fs.get("_vs_field_refs") or {}).items():
            try:
                out[k] = el.value
            except Exception:
                out[k] = ""
        return out

    def _vs_payload_common(fs: Dict[str, Any], kind_hint: str = "vs"
                             ) -> Optional[Dict[str, Any]]:
        """给 /knowledge_source/test_connection 和 /knowledge_source/ POST
        组装 **向量库** 类型的共用 body 片段。失败会自己弹 toast 并返回 None。"""
        dialect = str(fs.get("vs_dialect").value if fs.get("vs_dialect") else "")
        if not dialect:
            ui.notify("请先选择向量库类型", type="warning")
            return None
        values = _collect_vs_values(fs)

        # 按方言把 values 摊开到 ConnectionSpec 字段
        host = ""
        port = 0
        database = ""
        username = ""
        password = ""
        options: Dict[str, Any] = {}

        if dialect in ("milvus", "zilliz"):
            host = str(values.get("host", "") or "")
            try:
                port = int(values.get("port") or 19530)
            except (TypeError, ValueError):
                port = 19530
            username = str(values.get("user", "") or "")
            password = str(values.get("password", "") or "")
            options = {
                "secure": bool(values.get("secure", dialect == "zilliz")),
                "token": str(values.get("token", "") or ""),
                "db_name": str(values.get("db_name", "") or ""),
                "collection": str((fs.get("_vs_collection").value
                                   if fs.get("_vs_collection") else "") or ""),
            }
            if fs.get("emb_select"):
                options["embed_model"] = str(fs["emb_select"].value or "")
        elif dialect in ("pg", "relyt"):
            uri = str(values.get("connection_uri", "") or "").strip()
            if not uri:
                ui.notify("connection_uri 不能为空", type="warning")
                return None
            options = {
                "connection_uri": uri,
                "collection": str((fs.get("_vs_collection").value
                                   if fs.get("_vs_collection") else "") or ""),
            }
            if fs.get("emb_select"):
                options["embed_model"] = str(fs["emb_select"].value or "")
        elif dialect == "es":
            host = str(values.get("host", "") or "")
            try:
                port = int(values.get("port") or 9200)
            except (TypeError, ValueError):
                port = 9200
            username = str(values.get("user", "") or "")
            password = str(values.get("password", "") or "")
            options = {
                "scheme": str(values.get("scheme", "http") or "http"),
                "index_name": str((fs.get("_vs_collection").value
                                   if fs.get("_vs_collection") else "") or ""),
            }
            if fs.get("emb_select"):
                options["embed_model"] = str(fs["emb_select"].value or "")
        elif dialect == "chromadb":
            persist = str((fs.get("_vs_persist").value
                           if fs.get("_vs_persist") else "") or "")
            if not persist:
                ui.notify("Chroma 需要填写 persist_directory", type="warning")
                return None
            database = persist
            options = {
                "persist_directory": persist,
                "collection": str((fs.get("_vs_collection").value
                                   if fs.get("_vs_collection") else "") or ""),
            }
            if fs.get("emb_select"):
                options["embed_model"] = str(fs["emb_select"].value or "")
        else:
            ui.notify(f"未知向量库方言：{dialect}", type="warning")
            return None

        payload: Dict[str, Any] = {
            "dialect": dialect,
            "host": host, "port": port, "database": database,
            "username": username, "password": password,
            "options": options,
            "kind": kind_hint,
        }
        return payload

    def _collect_test_payload(kind: str, fs: Dict[str, Any]
                                 ) -> Optional[Dict[str, Any]]:
        if kind == "vs":
            return _vs_payload_common(fs, kind_hint="vs")
        if kind == "sql":
            dialect = str(fs["dialect"].value or "mysql")
            if dialect == "sqlite":
                return {
                    "dialect": "sqlite", "host": "", "port": 0,
                    "database": str(fs["database"].value or ""),
                    "username": "", "password": "", "options": {},
                }
            return {
                "dialect": dialect,
                "host": str(fs["host"].value or ""),
                "port": int(fs["port"].value or 0),
                "database": str(fs["database"].value or ""),
                "username": str(fs["username"].value or ""),
                "password": str(fs["password"].value or ""),
                "options": {},
            }
        if kind == "nosql":
            return {
                "dialect": str(fs["kind_sub"].value or "mongodb"),
                "host": str(fs["host"].value or ""),
                "port": int(fs["port"].value or 0),
                "database": str(fs["database"].value or ""),
                "username": str(fs["username"].value or ""),
                "password": str(fs["password"].value or ""),
                "options": {},
            }
        return None

    def _create_via_api(kind: str, name: str, description: str,
                           visibility: str, fs: Dict[str, Any]) -> str:
        import httpx
        from chayuan.server.config_panel.api_gate import api_base_url
        body: Dict[str, Any]
        if kind == "vs":
            # 🧠 外部向量库：复用 _vs_payload_common 组装 ConnectionSpec 片段，
            # 再拼上 source 层字段（name / display_name / description / visibility）
            partial = _vs_payload_common(fs, kind_hint="vs")
            if partial is None:
                raise RuntimeError("请检查向量库表单字段")
            # 先做一次 test_connection（API 侧 POST /knowledge_source/ 里也会测，
            # 这里重复一次是因为对话框体验上"创建前给一次可见的 ✅/❌"）——
            # 实际防垃圾落库由服务端负责。
            body = {
                "name": name, "kind": "vs",
                "display_name": name, "description": description,
                "dialect": partial["dialect"],
                "host": partial["host"], "port": partial["port"],
                "database": partial["database"],
                "username": partial["username"], "password": partial["password"],
                "options": partial["options"], "allowed": {},
                "visibility": visibility,
            }
        elif kind == "sql":
            dialect = str(fs["dialect"].value or "mysql")
            allowed = {}
            raw = str(fs.get("allowed_tables").value or "") if fs.get("allowed_tables") else ""
            if raw.strip():
                allowed["tables"] = [t.strip() for t in raw.split(",") if t.strip()]
            if dialect == "sqlite":
                body = {
                    "name": name, "kind": "sql",
                    "display_name": name, "description": description,
                    "dialect": "sqlite", "host": "", "port": 0,
                    "database": str(fs["database"].value or ""),
                    "username": "", "password": "",
                    "options": {}, "allowed": allowed, "visibility": visibility,
                }
            else:
                body = {
                    "name": name, "kind": "sql",
                    "display_name": name, "description": description,
                    "dialect": dialect,
                    "host": str(fs["host"].value or ""),
                    "port": int(fs["port"].value or 0),
                    "database": str(fs["database"].value or ""),
                    "username": str(fs["username"].value or ""),
                    "password": str(fs["password"].value or ""),
                    "options": {}, "allowed": allowed, "visibility": visibility,
                }
        elif kind == "nosql":
            sub = str(fs["kind_sub"].value or "mongodb")
            allowed = {}
            raw = str(fs.get("allowed_raw").value or "") if fs.get("allowed_raw") else ""
            items = [s.strip() for s in raw.split(",") if s.strip()]
            if items:
                allowed["collections" if sub == "mongodb" else "indices"] = items
            body = {
                "name": name, "kind": "mongo" if sub == "mongodb" else "es",
                "display_name": name, "description": description,
                "dialect": sub,
                "host": str(fs["host"].value or ""),
                "port": int(fs["port"].value or 0),
                "database": str(fs["database"].value or ""),
                "username": str(fs["username"].value or ""),
                "password": str(fs["password"].value or ""),
                "options": {}, "allowed": allowed, "visibility": visibility,
            }
        elif kind == "image":
            embedder = str(fs["embedder"].value or "")
            body = {
                "name": name, "kind": "image",
                "display_name": name, "description": description,
                "dialect": "image", "host": "", "port": 0,
                "database": name, "username": "", "password": "",
                "options": {"embedder_model": embedder, "source_name": name},
                "allowed": {}, "visibility": visibility,
            }
        else:
            raise ValueError(f"不支持的类型 kind={kind!r}")
        with httpx.Client(timeout=30, trust_env=False) as c:
            r = c.post(f"{api_base_url()}/knowledge_source/", json=body)
            if r.status_code != 200:
                try:
                    detail = r.json().get("detail") or r.text
                except Exception:
                    detail = r.text
                raise RuntimeError(f"HTTP {r.status_code}: {detail[:300]}")
            data = (r.json() or {}).get("data") or {}
            sid = data.get("id")
            if not sid:
                raise RuntimeError(f"创建成功但未返回 id: {data}")
            return f"已创建 {kind} 数据源 id={sid}"

    # ---- 编辑对话框 -------------------------------------------------
    def _open_edit_dialog(r: Dict[str, Any]) -> None:
        kb_id = r.get("id")
        if not kb_id:
            ui.notify(
                "此知识库仅存在于磁盘（未入库），无法在此页面编辑。"
                "请先在对话界面或本页面重新建库。",
                type="warning", multi_line=True,
            )
            return
        users = _list_users_lite()
        users_with_none = {0: "（无 owner）", **users}

        with ui.dialog() as dialog, ui.card().style(
            "min-width: 480px; max-width: 560px;"
        ):
            ui.label(f"设置：{r['kb_name']}").classes("text-h6 q-mb-xs")

            info_input = ui.textarea(
                label="kb_info（Agent 使用）",
                value=r.get("kb_info") or "",
            ).props("dense outlined autogrow").classes("w-full")

            with ui.grid(columns=2).classes("w-full").style("gap: 8px;"):
                vis_sel = ui.select(
                    {"private": "private（私有）", "public": "public（公共）"},
                    value=r.get("visibility") or "private",
                    label="可见性 visibility",
                ).props("dense outlined").classes("w-full")

                owner_sel = ui.select(
                    users_with_none,
                    value=r.get("owner_id") or 0,
                    label="Owner",
                ).props("dense outlined").classes("w-full")

            def _do_save() -> None:
                try:
                    new_info = str(info_input.value or "")
                    if new_info != (r.get("kb_info") or ""):
                        _update_kb_info(r["kb_name"], new_info)
                    _update_meta(
                        int(kb_id),
                        visibility=str(vis_sel.value or "private"),
                        owner_id=int(owner_sel.value) if owner_sel.value else None,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.exception("save kb meta failed")
                    ui.notify(f"保存失败：{type(e).__name__}: {e}", type="negative")
                    return
                ui.notify("已保存", type="positive")
                dialog.close()
                view_state["_reload_list"]()

            with ui.row().classes("w-full justify-end q-mt-sm").style("gap: 6px;"):
                ui.button("取消", on_click=dialog.close).props("flat dense")
                ui.button(
                    "保存", icon="save", on_click=_do_save,
                ).props("unelevated dense color=primary")

        dialog.open()

    # ---- 删除确认 ---------------------------------------------------
    def _confirm_delete(r: Dict[str, Any]) -> None:
        is_orphan = bool(r.get("orphan"))
        with ui.dialog() as dialog, ui.card().style(
            "min-width: 380px; max-width: 480px;"
        ):
            title = (
                f"清理磁盘孤儿「{r['kb_name']}」？"
                if is_orphan else f"删除知识库「{r['kb_name']}」？"
            )
            ui.label(title).classes("text-h6")
            desc = (
                "该知识库只在磁盘上残留目录、DB 无记录；清理后仅删除磁盘文件，"
                "不影响数据库。生产环境请先备份。"
                if is_orphan else
                "该操作不可撤销：会清空向量库、删除数据库记录及磁盘文件。"
                "生产环境请先备份。"
            )
            ui.label(desc).classes("text-caption text-grey-7 q-mb-sm").style(
                "line-height: 1.5;"
            )

            def _do_del() -> None:
                try:
                    msg = _delete_kb(
                        r["kb_name"], orphan=bool(r.get("orphan")),
                    )
                except Exception as e:  # noqa: BLE001
                    logger.exception("delete kb failed")
                    ui.notify(f"删除失败：{type(e).__name__}: {e}", type="negative")
                    return
                ui.notify(msg, type="info")
                _mark_changed()
                dialog.close()
                view_state["_reload_list"]()

            with ui.row().classes("w-full justify-end q-mt-sm").style("gap: 6px;"):
                ui.button("取消", on_click=dialog.close).props("flat dense")
                ui.button(
                    "确认删除", icon="delete_outline", on_click=_do_del,
                ).props("unelevated dense color=negative")
        dialog.open()

    # ======================================================================
    # 详情视图
    # ======================================================================
    def _enter_detail(kb: Dict[str, Any]) -> None:
        view_state["current_kb"] = kb
        _render_detail_view()

    def _back_to_list() -> None:
        view_state["current_kb"] = None
        _render_list_view()

    def _render_detail_view() -> None:
        page_root.clear()
        view_state["view"] = "detail"
        kb = view_state["current_kb"]
        if not kb:
            _render_list_view()
            return

        with page_root:
            # --- 顶部紧凑面包屑（无大标题）---
            with ui.row().classes("items-center w-full no-wrap").style("gap: 6px;"):
                ui.button(
                    icon="arrow_back", on_click=lambda: _back_to_list(),
                ).props("flat dense").tooltip("返回知识库列表")
                ui.icon("library_books", size="18px").classes("text-indigo-7")
                ui.label(kb["kb_name"]).classes("text-body1").style(
                    "font-weight: 600;"
                )
                vs = (kb.get("vs_type") or "-")
                emb = (kb.get("embed_model") or "-")
                ui.html(f'<span class="kb-chip vs">VS · {_escape(vs)}</span>')
                ui.html(f'<span class="kb-chip emb">Embed · {_escape(emb)}</span>')
                vis = kb.get("visibility") or "private"
                ui.html(
                    f'<span class="kb-chip {"pub" if vis == "public" else "priv"}">'
                    f'{vis}</span>'
                )
                ui.space()
                ui.button(
                    "设置", icon="tune",
                    on_click=lambda: _open_edit_dialog(kb),
                ).props("flat dense")

            # --- 切分参数（常驻顶部，不折叠）+ 文件管理 ---
            chunk_refs = _render_chunk_settings_bar(kb)
            _render_files_panel(kb, chunk_refs)

    # ---- 顶部切分参数条（常驻） -------------------------------------
    def _render_chunk_settings_bar(kb: Dict[str, Any]) -> Dict[str, Any]:
        """渲染顶部『切分参数 + 保存』，返回三个输入控件的引用。"""
        with ui.card().classes("w-full").props("flat bordered").style(
            "padding: 10px 14px;"
        ):
            with ui.row().classes("items-center w-full no-wrap").style("gap: 10px;"):
                ui.icon("settings", size="18px").classes("text-indigo-7")
                ui.label("切分参数").classes("text-subtitle2").style(
                    "font-weight: 600;"
                )

                chunk_size_in = ui.number(
                    label="chunk_size",
                    value=kb_defaults["chunk_size"],
                    min=50, max=4000, step=50,
                ).props("dense outlined").style("max-width: 160px;")
                chunk_overlap_in = ui.number(
                    label="chunk_overlap",
                    value=kb_defaults["chunk_overlap"],
                    min=0, max=2000, step=10,
                ).props("dense outlined").style("max-width: 160px;")
                zh_sw = ui.switch(
                    text="中文标题加强",
                    value=kb_defaults["zh_title_enhance"],
                ).props("dense")

                ui.label(
                    "作为上传 / 重切 / 重建 的默认参数。",
                ).classes("text-caption text-grey-6")

                ui.space()

                def _do_save_chunk_defaults() -> None:
                    try:
                        from chayuan.server.config_panel import yaml_store
                        yaml_store.save_updates(
                            "kb_settings.yaml",
                            {
                                "CHUNK_SIZE": int(chunk_size_in.value or 750),
                                "OVERLAP_SIZE": int(chunk_overlap_in.value or 150),
                                "ZH_TITLE_ENHANCE": bool(zh_sw.value),
                            },
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("save chunk defaults failed")
                        ui.notify(
                            f"保存失败：{type(exc).__name__}: {exc}",
                            type="negative",
                        )
                        return
                    kb_defaults["chunk_size"] = int(chunk_size_in.value or 750)
                    kb_defaults["chunk_overlap"] = int(chunk_overlap_in.value or 150)
                    kb_defaults["zh_title_enhance"] = bool(zh_sw.value)
                    _mark_changed()
                    ui.notify("切分参数已保存到 kb_settings.yaml", type="positive")

                ui.button(
                    "保存", icon="save", on_click=_do_save_chunk_defaults,
                ).props("unelevated dense color=primary").tooltip(
                    "写入 kb_settings.yaml，重启后生效。"
                )

        return {
            "chunk_size": chunk_size_in,
            "chunk_overlap": chunk_overlap_in,
            "zh": zh_sw,
        }

    # ---- 文件清单面板（含工具栏 + 列表） ---------------------------
    def _render_files_panel(
        kb: Dict[str, Any], chunk_refs: Dict[str, Any],
    ) -> None:
        kb_name = kb["kb_name"]
        chunk_size_in = chunk_refs["chunk_size"]
        chunk_overlap_in = chunk_refs["chunk_overlap"]
        zh_sw = chunk_refs["zh"]
        files_state: Dict[str, Any] = {
            "selected": set(),
            "page_size": 25,
            "page": 1,
            "search": "",
        }

        # === 文件列表卡片（含顶部工具栏） ===
        list_card = ui.card().classes("w-full").props("flat bordered")
        with list_card:
            with ui.row().classes(
                "items-center w-full no-wrap q-pa-sm"
            ).style("gap: 8px; flex-wrap: wrap;"):
                ui.icon("description", size="18px").classes("text-grey-7")
                ui.label("文件清单").classes("text-subtitle2").style(
                    "font-weight: 600;"
                )
                file_count_lbl = ui.label("").classes("text-caption text-grey-6")

                ui.space()

                file_search = ui.input(
                    placeholder="搜索文件...",
                ).props("dense outlined clearable").style("max-width: 220px;")

                ui.button(
                    "向量检索调试", icon="search",
                    on_click=lambda: _open_search_dialog(kb),
                ).props("outline dense color=indigo").tooltip(
                    "用任意 query 验证 embedding / 阈值是否符合预期"
                )
                ui.button(
                    "一键同步", icon="sync",
                    on_click=lambda: _do_one_click_sync(
                        kb_name, chunk_size_in, chunk_overlap_in, zh_sw,
                        _refresh_files,
                    ),
                ).props("outline dense color=primary").tooltip(
                    "把 content/ 目录中未入库的文件批量向量化"
                )
                ui.button(
                    "重建向量库", icon="restart_alt",
                    on_click=lambda: _open_recreate_dialog(
                        kb_name, _refresh_files,
                    ),
                ).props("outline dense color=warning").tooltip(
                    "清空当前 KB 的所有向量，按磁盘文件重新切分并入库"
                )
                ui.button(
                    "刷新", icon="refresh",
                    on_click=lambda: _refresh_files(),
                ).props("flat dense")
                ui.button(
                    "批量删除", icon="delete_outline",
                    on_click=lambda: _confirm_batch_delete(),
                ).props("flat dense color=negative")
                ui.button(
                    "添加", icon="add",
                    on_click=lambda: _open_upload_dialog(
                        kb_name, chunk_size_in, chunk_overlap_in, zh_sw,
                        _refresh_files,
                    ),
                ).props("unelevated dense color=primary").tooltip(
                    "上传文件到此知识库"
                )

            table_host = ui.column().classes("w-full").style(
                "padding: 0 8px 8px 8px; gap: 0;"
            )
            pager_row = ui.row().classes(
                "items-center w-full no-wrap"
            ).style(
                "padding: 6px 8px; background: #f9fafb; border-top: 1px solid #e5e7eb;"
            )

        def _on_file_search(e) -> None:
            files_state["search"] = str(e.value or "")
            files_state["page"] = 1
            _refresh_files()
        file_search.on_value_change(_on_file_search)

        def _filtered_files(all_files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            q = (files_state["search"] or "").strip().lower()
            if not q:
                return all_files
            return [f for f in all_files
                    if q in str(f.get("file_name", "")).lower()]

        def _refresh_files() -> None:
            all_files = _list_kb_files(kb_name)
            filtered = _filtered_files(all_files)
            page_size = int(files_state["page_size"])
            total = len(filtered)
            total_pages = max(1, (total + page_size - 1) // page_size)
            if files_state["page"] > total_pages:
                files_state["page"] = total_pages
            start = (files_state["page"] - 1) * page_size
            page_files = filtered[start:start + page_size]

            file_count_lbl.set_text(
                f"共 {total} 个（{start + 1}–{start + len(page_files)} / {total}）"
                if total else "当前没有文件"
            )

            table_host.clear()
            with table_host:
                if not page_files:
                    ui.label("没有匹配的文件。").classes(
                        "text-grey-7 q-pa-md"
                    )
                else:
                    _render_files_header()
                    for i, f in enumerate(page_files):
                        _render_file_row(kb_name, f, i)

            pager_row.clear()
            with pager_row:
                sel_cnt = len(files_state["selected"])
                ui.label(
                    f"已选 {sel_cnt} 项" if sel_cnt else "未选中"
                ).classes("text-caption text-grey-7")
                ui.space()

                ui.button(
                    icon="first_page",
                    on_click=lambda: _goto_page(1),
                ).props("flat dense size=sm").tooltip("首页")
                ui.button(
                    icon="chevron_left",
                    on_click=lambda: _goto_page(files_state["page"] - 1),
                ).props("flat dense size=sm").tooltip("上一页")
                ui.label(
                    f"第 {files_state['page']} / {total_pages} 页",
                ).classes("text-caption").style("padding: 0 6px;")
                ui.button(
                    icon="chevron_right",
                    on_click=lambda: _goto_page(files_state["page"] + 1),
                ).props("flat dense size=sm").tooltip("下一页")
                ui.button(
                    icon="last_page",
                    on_click=lambda: _goto_page(total_pages),
                ).props("flat dense size=sm").tooltip("末页")

                ui.select(
                    [10, 25, 50, 100], value=files_state["page_size"],
                    label="每页",
                ).props("dense outlined").style("max-width: 110px;").on_value_change(
                    lambda e: _set_page_size(int(e.value or 25))
                )

        def _goto_page(p: int) -> None:
            files_state["page"] = max(1, int(p))
            _refresh_files()

        def _set_page_size(n: int) -> None:
            files_state["page_size"] = n
            files_state["page"] = 1
            _refresh_files()

        def _render_files_header() -> None:
            with ui.row().classes("items-center w-full no-wrap").style(
                "gap: 8px; padding: 8px 10px; "
                "background: #f3f4f6; border-radius: 6px 6px 0 0; "
                "font-size: 12px; font-weight: 600; color: #374151; "
                "border-bottom: 1px solid #e5e7eb;"
            ):
                all_on_page = _filtered_files(_list_kb_files(kb_name))[
                    (files_state["page"] - 1) * files_state["page_size"]:
                    files_state["page"] * files_state["page_size"]
                ]

                def _toggle_all(e) -> None:
                    if e.value:
                        for f in all_on_page:
                            files_state["selected"].add(f["file_name"])
                    else:
                        for f in all_on_page:
                            files_state["selected"].discard(f["file_name"])
                    _refresh_files()

                page_all_selected = (
                    bool(all_on_page) and all(
                        f["file_name"] in files_state["selected"]
                        for f in all_on_page
                    )
                )
                ui.checkbox(
                    value=page_all_selected, on_change=_toggle_all,
                ).props("dense size=sm").style("flex: 0 0 22px;")
                ui.html('<div style="flex:1 1 auto;">文件名</div>')
                ui.html('<div style="width:60px;flex:0 0 60px;">类型</div>')
                ui.html('<div style="width:80px;flex:0 0 80px;">分片数</div>')
                ui.html('<div style="width:120px;flex:0 0 120px;">状态</div>')
                ui.html('<div style="width:320px;flex:0 0 320px;">操作</div>')

        def _render_file_row(kb_name: str, f: Dict[str, Any], i: int) -> None:
            fn = str(f.get("file_name") or "")
            ext = str(f.get("file_ext") or "").lstrip(".")
            docs_count = f.get("docs_count") or 0
            in_folder = bool(f.get("in_folder"))
            in_db = bool(f.get("in_db"))
            bg = "#ffffff" if i % 2 == 0 else "#fafbfc"

            with ui.row().classes("items-center w-full no-wrap").style(
                f"gap: 8px; padding: 6px 10px; background: {bg}; "
                f"border-bottom: 1px solid #f1f5f9;"
            ):
                def _on_check(e, fname=fn) -> None:
                    sel = files_state["selected"]
                    if e.value:
                        sel.add(fname)
                    else:
                        sel.discard(fname)

                ui.checkbox(
                    value=fn in files_state["selected"], on_change=_on_check,
                ).props("dense size=sm").style("flex: 0 0 22px;")

                ui.label(fn).classes("text-body2").style(
                    "flex: 1 1 auto; min-width: 0; overflow: hidden; "
                    "text-overflow: ellipsis; white-space: nowrap;"
                ).tooltip(fn)
                ui.label(ext or "-").classes(
                    "text-caption text-grey-7"
                ).style("flex: 0 0 60px;")
                ui.label(str(docs_count)).classes(
                    "text-caption text-grey-8"
                ).style("flex: 0 0 80px;")

                badges = []
                if in_folder:
                    badges.append('<span class="kb-chip emb">磁盘</span>')
                if in_db:
                    badges.append('<span class="kb-chip vs">入库</span>')
                if not in_folder and in_db:
                    badges.append('<span class="kb-chip orph">仅 DB</span>')
                ui.html(
                    f'<div style="flex:0 0 120px;display:flex;'
                    f'gap:4px;flex-wrap:wrap;">{"".join(badges)}</div>'
                )

                with ui.row().classes("no-wrap").style(
                    "flex: 0 0 320px; gap: 2px; justify-content: flex-end;"
                ):
                    ui.button(
                        "预览", icon="visibility",
                        on_click=lambda _=None, name=fn: _open_preview(kb_name, name),
                    ).props("flat dense size=sm color=primary").tooltip(
                        "在浏览器中直接预览文件内容"
                    )
                    ui.button(
                        "下载", icon="download",
                        on_click=lambda _=None, name=fn: _download_file(
                            kb_name, name,
                        ),
                    ).props("flat dense size=sm color=teal").tooltip(
                        "下载原始文件到本地"
                    )
                    ui.button(
                        "分片", icon="layers",
                        on_click=lambda _=None, name=fn: _open_chunks(kb_name, name),
                    ).props("flat dense size=sm color=indigo").tooltip(
                        "查看此文件的所有向量分片"
                    )
                    ui.button(
                        "重切", icon="refresh",
                        on_click=lambda _=None, name=fn: _open_resplit(
                            kb_name, name, chunk_size_in, chunk_overlap_in, zh_sw,
                        ),
                    ).props("flat dense size=sm color=orange").tooltip(
                        "用当前切分参数重新向量化"
                    )
                    ui.button(
                        icon="delete_outline",
                        on_click=lambda _=None, name=fn: _confirm_single_delete(name),
                    ).props("flat round dense size=sm color=negative")

        def _confirm_single_delete(fname: str) -> None:
            with ui.dialog() as d, ui.card().style("min-width: 360px;"):
                ui.label(f"删除文件「{fname}」？").classes(
                    "text-subtitle1 q-mb-xs"
                )
                ui.label(
                    "会同步从磁盘与向量库删除，不可恢复。"
                ).classes("text-caption text-grey-7")
                with ui.row().classes("w-full justify-end q-mt-sm").style(
                    "gap: 6px;"
                ):
                    ui.button("取消", on_click=d.close).props("flat dense")

                    def _ok() -> None:
                        try:
                            _delete_docs(kb_name, [fname])
                        except Exception as e:  # noqa: BLE001
                            logger.exception("delete single doc failed")
                            ui.notify(
                                f"删除失败：{type(e).__name__}: {e}",
                                type="negative",
                            )
                            return
                        files_state["selected"].discard(fname)
                        ui.notify("已删除", type="info")
                        d.close()
                        _refresh_files()

                    ui.button(
                        "确认删除", icon="delete_outline", on_click=_ok,
                    ).props("unelevated dense color=negative")
            d.open()

        def _confirm_batch_delete() -> None:
            names = sorted(files_state["selected"])
            if not names:
                ui.notify("请先勾选需要删除的文件", type="warning")
                return
            with ui.dialog() as d, ui.card().style("min-width: 380px;"):
                ui.label(f"批量删除 {len(names)} 个文件？").classes(
                    "text-subtitle1 q-mb-xs"
                ).style("font-weight: 600;")
                ui.label(
                    "会同步从磁盘与向量库一起清理，不可恢复。"
                ).classes("text-caption text-grey-7")
                with ui.row().classes("w-full justify-end q-mt-sm").style(
                    "gap: 6px;"
                ):
                    ui.button("取消", on_click=d.close).props("flat dense")

                    def _ok() -> None:
                        try:
                            _delete_docs(kb_name, names)
                        except Exception as e:  # noqa: BLE001
                            logger.exception("batch delete failed")
                            ui.notify(
                                f"删除失败：{type(e).__name__}: {e}",
                                type="negative",
                            )
                            return
                        files_state["selected"] = set()
                        ui.notify(f"已删除 {len(names)} 个", type="positive")
                        d.close()
                        _refresh_files()

                    ui.button(
                        "确认删除", icon="delete_outline", on_click=_ok,
                    ).props("unelevated dense color=negative")
            d.open()

        _refresh_files()

    # ---- 向量检索调试（弹窗） ---------------------------------------
    def _open_search_dialog(kb: Dict[str, Any]) -> None:
        kb_name = kb["kb_name"]
        with ui.dialog() as d, ui.card().style(
            "min-width: 780px; max-width: 1100px; width: 85vw; "
            "max-height: 90vh; display: flex; flex-direction: column;"
        ):
            with ui.row().classes("items-center w-full no-wrap").style("gap: 8px;"):
                ui.icon("search", size="22px").classes("text-indigo-7")
                ui.label(f"向量检索调试 · {kb_name}").classes("text-h6").style(
                    "flex: 1 1 auto;"
                )
                ui.button(icon="close", on_click=d.close).props(
                    "flat round dense"
                )
            ui.label(
                "用于验证 embedding / 阈值是否符合预期；不影响任何持久数据。"
            ).classes("text-caption text-grey-6")

            with ui.row().classes("w-full items-end q-mt-xs").style("gap: 8px;"):
                query_input = ui.input(
                    label="查询 query",
                    placeholder="输入要检索的问题...",
                ).props("dense outlined autofocus").classes("col-grow")
                top_k_in = ui.number(
                    label="top_k", value=kb_defaults["top_k"],
                    min=1, max=50, step=1,
                ).props("dense outlined").style("max-width: 110px;")
                score_in = ui.number(
                    label="score_threshold",
                    value=kb_defaults["score_threshold"],
                    min=0.0, max=2.0, step=0.1, format="%.2f",
                ).props("dense outlined").style("max-width: 160px;")
                ui.button(
                    "搜索", icon="search",
                    on_click=lambda: _do_search(),
                ).props("unelevated dense color=primary")

            ui.separator().classes("q-my-xs")
            result_box = ui.column().classes("w-full").style(
                "gap: 8px; flex: 1 1 auto; overflow: auto;"
            )

            def _render_hit(i: int, hit: Dict[str, Any], query: str) -> None:
                meta = hit.get("metadata") or {}
                src = str(meta.get("source", "")) or "—"
                score = hit.get("score")
                try:
                    score_v = float(score) if score is not None else None
                except Exception:  # noqa: BLE001
                    score_v = None
                rel_percent = 0.0
                if score_v is not None:
                    rel_percent = max(
                        0.0, min(1.0, (2.0 - score_v) / 2.0)
                    ) * 100
                content = hit.get("page_content") or ""
                highlighted = _highlight(content, query)
                with ui.element("div").classes("kb-chunk"):
                    with ui.row().classes(
                        "items-center w-full no-wrap",
                    ).style("gap: 8px;"):
                        ui.html(f'<span class="kb-chip vs">#{i + 1}</span>')
                        ui.label(src).classes("text-body2").style(
                            "font-weight: 600; flex: 1 1 auto; min-width: 0; "
                            "overflow: hidden; text-overflow: ellipsis; "
                            "white-space: nowrap;"
                        )
                        if score_v is not None:
                            ui.label(f"score={score_v:.4f}").classes(
                                "text-caption text-grey-7",
                            ).style(
                                "font-family: ui-monospace, Menlo, monospace;"
                            )
                    if score_v is not None:
                        ui.html(
                            f'<div class="kb-score-bar">'
                            f'<div class="kb-score-fill" '
                            f'style="width:{rel_percent:.1f}%"></div>'
                            f'</div>'
                        )
                    ui.html(f'<pre>{highlighted}</pre>')
                    meta_html = _render_meta_grid_html(meta)
                    if meta_html:
                        ui.html(meta_html)

            def _do_search() -> None:
                q = (query_input.value or "").strip()
                if not q:
                    ui.notify("请输入查询内容", type="warning")
                    return
                result_box.clear()
                with result_box:
                    ui.label("搜索中...").classes("text-grey-7")
                docs = _search_docs(
                    kb_name, q,
                    top_k=int(top_k_in.value or 3),
                    score_threshold=float(score_in.value or 2.0),
                )
                result_box.clear()
                with result_box:
                    if not docs:
                        ui.label(
                            "没有命中结果。尝试提高 score_threshold（上限 2.0），"
                            "或检查 embedding 是否已就绪。"
                        ).classes("text-grey-7 q-pa-md")
                        return
                    ui.label(
                        f"命中 {len(docs)} 条（按相关度排序）"
                    ).classes("text-caption text-grey-7")
                    for i, hit in enumerate(docs):
                        _render_hit(i, hit, q)

            query_input.on("keydown.enter", lambda _=None: _do_search())
        d.open()

    # ---- 一键同步（工具栏按钮） --------------------------------------
    def _do_one_click_sync(
        kb_name: str,
        chunk_size_in, chunk_overlap_in, zh_sw,
        refresh_cb: Callable[[], None],
    ) -> None:
        files = _list_kb_files(kb_name)
        missing = [
            str(f.get("file_name") or "")
            for f in files
            if f.get("in_folder") and not f.get("in_db")
        ]
        missing = [m for m in missing if m]
        if not missing:
            ui.notify("没有需要同步的文件", type="info")
            return
        try:
            res = _vectorize_files(
                kb_name, missing,
                chunk_size=int(chunk_size_in.value or kb_defaults["chunk_size"]),
                chunk_overlap=int(
                    chunk_overlap_in.value or kb_defaults["chunk_overlap"]
                ),
                zh_title_enhance=bool(zh_sw.value),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("sync failed")
            ui.notify(f"同步失败：{exc}", type="negative")
            return
        fails = res.get("failed_files") or {}
        ok_cnt = len(missing) - len(fails)
        ui.notify(
            f"已同步 {ok_cnt} / {len(missing)} 个文件；"
            + (f"失败 {len(fails)} 个" if fails else ""),
            type="positive" if not fails else "warning",
            multi_line=True,
        )
        refresh_cb()

    # ---- 重建向量库（对话框） ---------------------------------------
    def _open_recreate_dialog(
        kb_name: str, refresh_cb: Callable[[], None],
    ) -> None:
        with ui.dialog() as d, ui.card().style("min-width: 440px;"):
            ui.label("重建整个向量库？").classes(
                "text-subtitle1 q-mb-xs"
            ).style("font-weight: 600;")
            ui.label(
                "会清空向量库后，按 content/ 目录下现有文件重新切分并向量化。"
                "耗时与文件数量成正比；期间不会接受新请求。"
            ).classes("text-caption text-grey-7").style("line-height: 1.5;")

            with ui.grid(columns=2).classes("w-full q-mt-sm").style("gap: 8px;"):
                cs = ui.number(
                    label="chunk_size", value=kb_defaults["chunk_size"],
                    min=50, max=4000, step=50,
                ).props("dense outlined").classes("w-full")
                co = ui.number(
                    label="chunk_overlap", value=kb_defaults["chunk_overlap"],
                    min=0, max=2000, step=10,
                ).props("dense outlined").classes("w-full")
                zh = ui.switch(
                    text="中文标题加强",
                    value=kb_defaults["zh_title_enhance"],
                ).props("dense")

            with ui.row().classes("w-full justify-end q-mt-sm").style(
                "gap: 6px;"
            ):
                ui.button("取消", on_click=d.close).props("flat dense")

                def _ok() -> None:
                    d.close()
                    try:
                        _do_recreate_sync(
                            kb_name,
                            chunk_size=int(cs.value or 750),
                            chunk_overlap=int(co.value or 150),
                            zh_title_enhance=bool(zh.value),
                        )
                        ui.notify(
                            f"{kb_name} 已重建完成。",
                            type="positive", timeout=6000, multi_line=True,
                        )
                        _mark_changed()
                        refresh_cb()
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("recreate vs failed")
                        ui.notify(
                            f"重建失败：{type(exc).__name__}: {exc}",
                            type="negative",
                        )

                ui.button(
                    "开始重建", icon="refresh", on_click=_ok,
                ).props("unelevated dense color=warning")
        d.open()

    # ---- 上传对话框 -------------------------------------------------
    def _open_upload_dialog(
        kb_name: str,
        chunk_size_in, chunk_overlap_in, zh_sw,
        refresh_cb: Callable[[], None],
    ) -> None:
        """弹窗上传：多选 + 进度；『确定』按钮关闭后刷新文件清单。"""
        pending: Dict[str, Dict[str, Any]] = {}

        with ui.dialog() as d, ui.card().style(
            "min-width: 640px; max-width: 820px; width: 75vw; "
            "max-height: 85vh; display: flex; flex-direction: column;"
        ):
            with ui.row().classes("items-center w-full no-wrap").style("gap: 8px;"):
                ui.icon("cloud_upload", size="22px").classes("text-indigo-7")
                ui.label(f"上传文件 · {kb_name}").classes("text-h6").style(
                    "flex: 1 1 auto;"
                )
                ui.button(icon="close", on_click=d.close).props(
                    "flat round dense"
                )
            ui.label(
                "支持多选；上传后按当前切分参数自动入库。"
            ).classes("text-caption text-grey-6")

            with ui.row().classes("items-center w-full").style("gap: 10px;"):
                override_sw = ui.switch(
                    text="覆盖已存在", value=False,
                ).props("dense")
                override_sw.tooltip(
                    "开启后同名同大小的文件也会被覆盖重写 + 重新向量化",
                )
                auto_vec_sw = ui.switch(
                    text="保存后自动向量化", value=True,
                ).props("dense")

            upload_status = ui.column().classes("w-full q-mt-sm").style(
                "gap: 4px; flex: 1 1 auto; overflow: auto; "
                "max-height: 45vh; padding-right: 4px;"
            )

            def _handle_upload(e) -> None:
                try:
                    content = e.content.read()
                    filename = e.name or "unknown"
                except Exception as exc:  # noqa: BLE001
                    logger.exception("upload read failed")
                    ui.notify(f"读取上传文件失败：{exc}", type="negative")
                    return

                save = _save_upload_to_disk(
                    kb_name, filename, content,
                    override=bool(override_sw.value),
                )

                if save["ok"] and auto_vec_sw.value:
                    try:
                        res = _vectorize_files(
                            kb_name, [filename],
                            chunk_size=int(
                                chunk_size_in.value or kb_defaults["chunk_size"]
                            ),
                            chunk_overlap=int(
                                chunk_overlap_in.value
                                or kb_defaults["chunk_overlap"]
                            ),
                            zh_title_enhance=bool(zh_sw.value),
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("vectorize failed")
                        save["msg"] = f"已保存但向量化失败：{exc}"
                        save["ok"] = False
                    else:
                        fails = res.get("failed_files") or {}
                        if fails:
                            save["msg"] = (
                                f"已保存；向量化失败："
                                f"{list(fails.values())[0]}"
                            )
                            save["ok"] = False
                        else:
                            save["msg"] = "已上传并入库"
                elif save["ok"]:
                    save["msg"] = "已保存到磁盘；未自动向量化"

                pending[filename] = save
                _render_status()

            def _render_status() -> None:
                upload_status.clear()
                with upload_status:
                    if not pending:
                        ui.label("尚无上传记录。").classes(
                            "text-caption text-grey-6",
                        )
                        return
                    for fname, save in pending.items():
                        status_cls = "ok" if save["ok"] else (
                            "warn" if "已存在" in save["msg"] else "err"
                        )
                        ui.html(
                            f'<div style="display:flex;align-items:center;'
                            f'gap:6px;padding:4px 0;">'
                            f'<span class="kb-chip {status_cls}">'
                            f'{_escape(fname)}</span>'
                            f'<span style="font-size:12px;color:#6b7280;">'
                            f'{_escape(save["msg"])}</span></div>'
                        )

            upload = ui.upload(
                on_upload=_handle_upload,
                multiple=True,
                auto_upload=True,
                max_file_size=200 * 1024 * 1024,
            ).props(
                'accept=".txt,.md,.pdf,.docx,.doc,.pptx,.ppt,'
                '.xlsx,.xls,.csv,.tsv,.json,.jsonl,.html,.htm,.mhtml,'
                '.png,.jpg,.jpeg,.bmp,.epub,.rtf,.xml,.odt,.eml,.msg,'
                '.rst,.py,.ipynb"'
            ).classes("w-full q-mt-xs")
            upload.tooltip("支持常见文档格式；单文件上限 200MB")

            _render_status()

            with ui.row().classes(
                "w-full justify-end q-mt-sm",
            ).style(
                "gap: 6px; padding-top: 6px; border-top: 1px solid #e5e7eb;"
            ):
                ui.button("取消", on_click=d.close).props("flat dense")

                def _ok() -> None:
                    d.close()
                    refresh_cb()
                    if pending:
                        ok = sum(1 for v in pending.values() if v["ok"])
                        ui.notify(
                            f"已处理 {len(pending)} 个文件（成功 {ok}）",
                            type="positive" if ok == len(pending) else "warning",
                            multi_line=True,
                        )

                ui.button(
                    "确定", icon="check", on_click=_ok,
                ).props("unelevated dense color=primary")

        d.open()

    # ---- 下载单个文件 -----------------------------------------------
    def _download_file(kb_name: str, file_name: str) -> None:
        from chayuan.server.knowledge_base.utils import get_file_path
        path = get_file_path(knowledge_base_name=kb_name, doc_name=file_name)
        if not path or not os.path.isfile(path):
            ui.notify(
                "文件不在磁盘上，无法下载（可能仅在向量库中存在）。",
                type="warning",
            )
            return
        try:
            ui.download(path, os.path.basename(file_name))
        except Exception as exc:  # noqa: BLE001
            logger.exception("download failed")
            ui.notify(f"下载失败：{exc}", type="negative")

    def _do_recreate_sync(
        kb_name: str, *,
        chunk_size: int, chunk_overlap: int, zh_title_enhance: bool,
    ) -> None:
        """同步版本的 recreate：不走 `recreate_vector_store` 的 SSE 响应流，
        直接重现其内部流程（clear_vs → create_kb → 按文件切分 + add_doc →
        save_vector_store），同进程即可完成，不用起 HTTP。
        """
        from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
        from chayuan.server.knowledge_base.utils import (
            KnowledgeFile, list_files_from_folder, files2docs_in_thread,
        )
        kb = KBServiceFactory.get_service_by_name(kb_name)
        if kb is None:
            raise RuntimeError(f"知识库 {kb_name} 不存在")
        if kb.exists():
            kb.clear_vs()
        kb.create_kb()
        files = list_files_from_folder(kb_name)
        kb_files = [(f, kb_name) for f in files]
        for status, result in files2docs_in_thread(
            kb_files,
            chunk_size=int(chunk_size),
            chunk_overlap=int(chunk_overlap),
            zh_title_enhance=bool(zh_title_enhance),
        ):
            if not status:
                _n, _f, err = result
                logger.warning(f"recreate: skip {_f}: {err}")
                continue
            _n, file_name, docs = result
            kb_file = KnowledgeFile(filename=file_name, knowledge_base_name=_n)
            kb_file.splited_docs = docs
            kb.add_doc(kb_file, not_refresh_vs_cache=True)
        kb.save_vector_store()

    # ---- 预览 -------------------------------------------------------
    def _open_preview(kb_name: str, file_name: str) -> None:
        from chayuan.server.knowledge_base.utils import get_file_path
        path = get_file_path(knowledge_base_name=kb_name, doc_name=file_name)
        if not path or not os.path.isfile(path):
            ui.notify(
                "文件不在磁盘上，无法预览（可能仅在向量库中存在）。",
                type="warning",
            )
            return
        ext = os.path.splitext(file_name)[-1].lower()
        url = _static_url_for(kb_name, file_name)

        with ui.dialog().props("maximized no-backdrop-dismiss").classes(
            "kb-wide-dialog",
        ) as d, ui.card().classes("kb-wide-card"):
            # ---- Header ----
            with ui.row().classes(
                "items-center w-full no-wrap kb-wide-header",
            ).style("gap: 12px;"):
                ui.icon("visibility", size="24px").classes("text-indigo-7")
                ui.label(f"预览 · {file_name}").classes("text-subtitle1").style(
                    "font-weight: 600; flex: 1 1 auto; min-width: 0; "
                    "overflow: hidden; text-overflow: ellipsis; "
                    "white-space: nowrap;"
                ).tooltip(file_name)
                ui.html(
                    f'<span class="kb-chip emb">'
                    f'{_escape((ext or "—").lstrip("."))}</span>'
                )
                ui.label(
                    _fmt_size(os.path.getsize(path)),
                ).classes("text-caption text-grey-7 font-mono")
                ui.button(icon="close", on_click=d.close).props(
                    "flat round dense"
                ).tooltip("关闭 (Esc)")

            # ---- Body（flex:1 铺满；预览模式 body 不滚，内部 fill 子项自己滚）----
            body = ui.column().classes("w-full kb-wide-body kb-fill")
            with body:
                if ext in (
                    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
                ):
                    ui.html(
                        f'<div class="kb-preview-img-wrap">'
                        f'<img src="{_escape(url)}" alt="{_escape(file_name)}"/>'
                        f'</div>'
                    )
                elif ext == ".pdf":
                    ui.html(
                        f'<div class="kb-preview-fill">'
                        f'<iframe src="{_escape(url)}"></iframe>'
                        f'</div>'
                    )
                elif ext in (
                    ".txt", ".md", ".json", ".jsonl", ".csv", ".tsv",
                    ".html", ".htm", ".xml", ".py", ".rst",
                    ".toml", ".yaml", ".yml", ".log", ".srt",
                ):
                    res = _read_text_file(path)
                    if not res["ok"]:
                        ui.label(f"读取失败：{res.get('msg')}").classes(
                            "text-negative q-pa-md"
                        )
                    else:
                        if res.get("truncated"):
                            ui.html(
                                f'<div style="display:flex;align-items:center;'
                                f'gap:6px;padding:6px 10px;background:#fef3c7;'
                                f'color:#92400e;border:1px solid #fde68a;'
                                f'border-radius:8px;font-size:12px;">'
                                f'<span class="material-icons" '
                                f'style="font-size:16px;">info</span>'
                                f'文件较大（{_fmt_size(res["size"])}），'
                                f'仅预览前 512 KB。'
                                f'</div>'
                            )
                        ui.html(
                            f'<div class="kb-preview-fill">'
                            f'<pre class="kb-preview-text">'
                            f'{_escape(res["text"])}'
                            f'</pre></div>'
                        )
                else:
                    with ui.column().classes("kb-preview-unsupported"):
                        ui.icon("description", size="72px").classes("text-grey-5")
                        ui.label(
                            f"『{ext or '未知格式'}』暂不支持在线预览。"
                        ).classes("text-grey-7 text-body1")
                        ui.label(
                            "可在新标签页打开或下载后查看。",
                        ).classes("text-caption text-grey-6")
                        with ui.row().classes("no-wrap q-mt-sm").style(
                            "gap: 8px;"
                        ):
                            ui.button(
                                "在新标签页打开", icon="open_in_new",
                                on_click=lambda u=url: ui.navigate.to(
                                    u, new_tab=True,
                                ),
                            ).props("outline dense color=primary")
                            ui.button(
                                "下载", icon="download",
                                on_click=lambda: _download_file(
                                    kb_name, file_name,
                                ),
                            ).props("unelevated dense color=primary")

            # ---- Footer ----
            with ui.row().classes(
                "items-center w-full no-wrap kb-wide-footer",
            ).style("gap: 10px;"):
                ui.icon("folder", size="16px").classes("text-grey-6")
                ui.label(path).classes(
                    "text-caption text-grey-7 font-mono",
                ).style(
                    "flex: 1 1 auto; min-width: 0; overflow: hidden; "
                    "text-overflow: ellipsis; white-space: nowrap;"
                ).tooltip("磁盘绝对路径")
                ui.button(
                    "下载", icon="download",
                    on_click=lambda: _download_file(kb_name, file_name),
                ).props("outline dense color=teal")
                ui.button(
                    "在新标签页打开", icon="open_in_new",
                    on_click=lambda u=url: ui.navigate.to(u, new_tab=True),
                ).props("outline dense")
                ui.button(
                    "查看分片", icon="layers",
                    on_click=lambda: (
                        d.close(), _open_chunks(kb_name, file_name),
                    ),
                ).props("unelevated dense color=primary")
        d.open()

    # ---- 分片抽屉 ---------------------------------------------------
    def _open_chunks(kb_name: str, file_name: str) -> None:
        state_local: Dict[str, Any] = {
            "mode": "stored",   # stored / preview
            "query": "",
            "chunks_stored": [],
            "chunks_preview": [],
        }

        with ui.dialog().props("maximized no-backdrop-dismiss").classes(
            "kb-wide-dialog",
        ) as d, ui.card().classes("kb-wide-card"):
            # ---- Header ----
            with ui.row().classes(
                "items-center w-full no-wrap kb-wide-header",
            ).style("gap: 12px;"):
                ui.icon("layers", size="24px").classes("text-indigo-7")
                ui.label(f"分片详情 · {file_name}").classes(
                    "text-subtitle1",
                ).style(
                    "font-weight: 600; flex: 1 1 auto; min-width: 0; "
                    "overflow: hidden; text-overflow: ellipsis; "
                    "white-space: nowrap;"
                ).tooltip(file_name)
                status = ui.label("").classes(
                    "text-caption text-grey-7 q-px-sm q-py-xs",
                ).style(
                    "background:#eef2ff;color:#4338ca;border-radius:999px;"
                    "font-weight:600;"
                )
                ui.button(icon="close", on_click=d.close).props(
                    "flat round dense"
                ).tooltip("关闭 (Esc)")

            # ---- Toolbar：模式 + 搜索 + 预览参数 ----
            # 搜索支持正文 / metadata 任意字段 / 分片 ID；顶部展示"X / Y"实时计数。
            with ui.column().classes("w-full kb-wide-toolbar").style("gap: 8px;"):
                with ui.row().classes(
                    "items-center w-full no-wrap kb-chunk-searchbar",
                ).style("gap: 8px; flex-wrap: wrap;"):
                    mode_toggle = ui.toggle(
                        {
                            "stored": "已入库分片",
                            "preview": "切分预览（不落盘）",
                        },
                        value="stored",
                    ).props("dense color=indigo")
                    match_count_chip = ui.html(
                        '<span class="kb-chunk-info">'
                        '<span class="material-icons" style="font-size:14px">'
                        'filter_list</span>共 0 个'
                        '</span>'
                    )
                    ui.space()
                    query_in = ui.input(
                        placeholder="搜索分片内容 / 来源 / metadata...",
                    ).props(
                        'dense outlined clearable rounded '
                        'prepend-icon=search'
                    ).classes("col-grow").style(
                        "min-width: 280px; max-width: 420px;"
                    )

                preview_params = ui.row().classes(
                    "items-center w-full no-wrap",
                ).style("gap: 8px;")
                preview_params.visible = False
                with preview_params:
                    ui.label("预览切分参数：").classes(
                        "text-caption text-grey-7",
                    )
                    cs = ui.number(
                        label="chunk_size",
                        value=kb_defaults["chunk_size"],
                        min=50, max=4000, step=50,
                    ).props("dense outlined").style("max-width: 150px;")
                    co = ui.number(
                        label="chunk_overlap",
                        value=kb_defaults["chunk_overlap"],
                        min=0, max=2000, step=10,
                    ).props("dense outlined").style("max-width: 150px;")
                    zh = ui.switch(
                        text="中文标题加强",
                        value=kb_defaults["zh_title_enhance"],
                    ).props("dense")
                    ui.space()
                    ui.button(
                        "重新切分", icon="content_cut",
                        on_click=lambda: _run_preview_split(),
                    ).props("unelevated dense color=primary")

            # ---- Body（flex:1 铺满；分片列表本身滚动）----
            body = ui.column().classes("w-full kb-wide-body kb-scroll").style(
                "gap: 8px;"
            )

            def _on_mode(e) -> None:
                state_local["mode"] = str(e.value or "stored")
                if state_local["mode"] == "preview":
                    preview_params.visible = True
                    if not state_local["chunks_preview"]:
                        _run_preview_split()
                else:
                    preview_params.visible = False
                _render_chunks()

            def _on_query(e) -> None:
                state_local["query"] = str(e.value or "")
                _render_chunks()

            mode_toggle.on_value_change(_on_mode)
            query_in.on_value_change(_on_query)

            def _run_preview_split() -> None:
                status.set_text("切分中…")
                res = _preview_split(
                    kb_name, file_name,
                    chunk_size=int(cs.value or 750),
                    chunk_overlap=int(co.value or 150),
                    zh_title_enhance=bool(zh.value),
                )
                if not res["ok"]:
                    ui.notify(res["msg"], type="negative", multi_line=True)
                    status.set_text(f"预览失败：{res['msg']}")
                    return
                state_local["chunks_preview"] = res["chunks"]
                status.set_text(res["msg"])
                _render_chunks()

            def _filter_chunks(
                chunks: List[Dict[str, Any]], q: str,
            ) -> List[Dict[str, Any]]:
                """搜索正文 / metadata 任意值 / 分片 ID。空 query 直接返回全部。

                metadata 的 key 也可被匹配（例如输入 "page"），覆盖用户
                按来源页码过滤的常见诉求；value 在字符串化后比较，兼容
                int / list。
                """
                if not q:
                    return chunks
                ql = q.lower()

                def _match(c: Dict[str, Any]) -> bool:
                    if ql in (c.get("page_content") or "").lower():
                        return True
                    if ql in str(c.get("id") or "").lower():
                        return True
                    meta = c.get("metadata") or {}
                    if not isinstance(meta, dict):
                        return False
                    for k, v in meta.items():
                        if k == "vector":
                            continue
                        if ql in str(k).lower() or ql in str(v).lower():
                            return True
                    return False

                return [c for c in chunks if _match(c)]

            def _render_chunks() -> None:
                chunks = (state_local["chunks_preview"]
                          if state_local["mode"] == "preview"
                          else state_local["chunks_stored"])
                q = state_local["query"]
                filtered = _filter_chunks(chunks, q)
                # 顶部 chip 实时反映"当前显示 / 总数"
                if q:
                    _set_html(
                        match_count_chip,
                        '<span class="kb-chunk-info">'
                        '<span class="material-icons" style="font-size:14px">'
                        f'filter_list</span>{len(filtered)} / {len(chunks)} 个</span>',
                    )
                else:
                    _set_html(
                        match_count_chip,
                        '<span class="kb-chunk-info">'
                        '<span class="material-icons" style="font-size:14px">'
                        f'layers</span>共 {len(chunks)} 个</span>',
                    )

                body.clear()
                with body:
                    if not chunks:
                        with ui.column().classes(
                            "w-full items-center justify-center",
                        ).style(
                            "flex:1 1 auto;min-height:0;gap:8px;"
                            "color:#6b7280;"
                        ):
                            ui.icon("inbox", size="56px").classes(
                                "text-grey-4",
                            )
                            ui.label(
                                "尚无分片。"
                                if state_local["mode"] == "stored"
                                else "点击『重新切分』按当前参数生成预览。"
                            ).classes("text-body2")
                        return
                    # 卡片网格：每行最少 3 张（容器宽度 ≥ 940 时），窄屏自动折行
                    with ui.element("div").classes("kb-chunk-grid"):
                        if not filtered:
                            ui.html(
                                '<div class="kb-chunk-empty">'
                                '<span class="material-icons" '
                                'style="font-size:48px;color:#cbd5e1">'
                                'search_off</span>'
                                '<div style="font-size:14px;color:#475569;'
                                'font-weight:600">过滤后无匹配分片</div>'
                                '<div style="font-size:12px;color:#6b7280">'
                                '试试更短的关键字，或清空搜索框查看全部分片。'
                                '</div></div>'
                            )
                            return
                        for c in filtered:
                            _render_chunk_tile(c, q)

            def _load_stored() -> None:
                status.set_text("加载中…")
                try:
                    state_local["chunks_stored"] = _list_file_chunks(
                        kb_name, file_name,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("list chunks failed")
                    ui.notify(f"加载失败：{exc}", type="negative")
                    state_local["chunks_stored"] = []
                status.set_text(
                    f"已入库 {len(state_local['chunks_stored'])} 个分片"
                )
                _render_chunks()

            _load_stored()

        d.open()

    def _render_chunk_tile(c: Dict[str, Any], query: str) -> None:
        """渲染一张紧凑的分片卡（~300×240）—— 底部放一个真正的「详情」按钮。

        结构：
        - 头部：#序号 + 字符数 + metadata 个数指示灯；
        - 正文：高亮 query 后最多 8 行、溢出省略；
        - 底部：文件来源简写（若有 metadata.source）+ ``ui.button('详情')``；
          除了按钮，整卡也挂 ``.on('click', ...)`` 做冗余保险 —— 但点按钮本身
          就是用户最容易命中的目标，不依赖 div click 事件的可靠性。
        """
        content = c.get("page_content") or ""
        meta = c.get("metadata") or {}
        cid = str(c.get("id") or "")
        idx = int(c.get("index", 0)) + 1

        # 底部来源文案：优先 source 文件名 + page（若有）
        src_label = ""
        if isinstance(meta, dict):
            src = str(meta.get("source") or "")
            if src:
                src_label = src.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            page = meta.get("page")
            if page is not None and src_label:
                src_label = f"{src_label} · p{page}"
            elif page is not None:
                src_label = f"page {page}"
        meta_count = 0
        if isinstance(meta, dict):
            meta_count = sum(1 for k in meta.keys() if k != "vector")

        def _open_detail(_=None, chunk=c) -> None:
            _open_chunk_detail_dialog(chunk)

        tile = ui.element("div").classes("kb-chunk-tile")
        tile.tooltip("点击「详情」按钮查看 / 编辑 / 删除该分片")
        # 整卡也挂一个 click（冗余）——用户点在空白处也算；在按钮上的点击
        # 会因为 ``.stop`` / default behavior 被按钮单独吃掉。
        tile.on("click", _open_detail)
        with tile:
            ui.html(
                '<div class="cti-head">'
                f'<span class="cti-index">#{idx}</span>'
                f'<span class="cti-size">{len(content)} 字符</span>'
                + (f'<span class="cti-meta-dot" title="{meta_count} 个 metadata 字段"></span>'
                   f'<span class="cti-size">{meta_count} meta</span>'
                   if meta_count else "")
                + '</div>'
            )
            highlighted = _highlight(content, query)
            ui.html(f'<div class="cti-content">{highlighted}</div>')

            # 底部 —— 左边是来源文案（HTML），右边是真正的 ``ui.button``
            with ui.element("div").classes("cti-foot"):
                if src_label:
                    ui.html(
                        '<span class="material-icons" '
                        'style="font-size:14px;color:#6366f1">description</span>'
                        f'<span class="cti-foot-src" title="{_escape(src_label)}">'
                        f'{_escape(src_label)}</span>'
                    )
                else:
                    ui.html(
                        '<span class="cti-foot-src" style="color:#9ca3af">'
                        f'{_escape(cid) or "—"}</span>'
                    )
                ui.button(
                    "详情", icon="arrow_forward",
                    on_click=_open_detail,
                ).props(
                    "flat dense size=sm color=indigo no-caps"
                ).classes("cti-foot-btn").tooltip("打开完整内容 / 可编辑保存")

    def _open_chunk_detail_dialog(c: Dict[str, Any]) -> None:
        """单个分片的详情弹窗 —— 可编辑 + 保存 + 删除。

        - **正文**：``ui.textarea``，用户可以直接改，保存会触发重新向量化；
        - **metadata**：默认只读网格；顶部切换开关 → 变成 JSON 编辑器，保存前
          做 ``json.loads`` 校验，格式错误会阻断提交；
        - **保存**：调用 ``_update_chunk(kb_name, id, content, meta)``；后者
          走 ``kb.update_doc_by_ids``（del + re-add，id 不变）；
        - **删除**：单独的 negative 按钮 + 二次确认；
        - **切分预览模式**下分片没有 id，保存 / 删除按钮自动禁用并给出 tooltip。
        """
        import json as _json

        content = c.get("page_content") or ""
        original_content = content
        meta = c.get("metadata") or {}
        original_meta = dict(meta)
        cid = str(c.get("id") or "")
        idx = int(c.get("index", 0)) + 1
        is_preview = (state_local["mode"] == "preview") or not cid

        ui_state: Dict[str, Any] = {
            "meta_edit_mode": False,         # False = 只读网格；True = JSON 编辑
            "content_area": None,
            "meta_json_area": None,
            "meta_readonly_host": None,
            "size_label": None,
            "dirty_label": None,
        }

        def _current_content() -> str:
            area = ui_state.get("content_area")
            if area is None:
                return content
            return str(getattr(area, "value", "") or "")

        def _current_meta() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
            """返回 (metadata_dict, error_msg)。只读模式直接用原始 meta。"""
            if not ui_state["meta_edit_mode"]:
                return original_meta, None
            area = ui_state.get("meta_json_area")
            raw = str(getattr(area, "value", "") or "").strip()
            if not raw:
                return {}, None
            try:
                parsed = _json.loads(raw)
            except _json.JSONDecodeError as e:
                return None, f"metadata JSON 解析失败：{e.msg}（行 {e.lineno} 列 {e.colno}）"
            if not isinstance(parsed, dict):
                return None, "metadata 必须是 JSON 对象 (dict)"
            return parsed, None

        def _refresh_dirty() -> None:
            """更新字符计数 + 是否脏的徽章。"""
            cur = _current_content()
            if ui_state.get("size_label") is not None:
                _set_html(
                    ui_state["size_label"],
                    f'<span class="kb-chunk-info">'
                    f'<span class="material-icons" style="font-size:14px">'
                    f'text_fields</span>{len(cur)} 字符</span>',
                )
            if ui_state.get("dirty_label") is None:
                return
            meta_val, _err = _current_meta()
            dirty = (cur != original_content) or (
                meta_val is not None and meta_val != original_meta
            )
            if dirty:
                _set_html(
                    ui_state["dirty_label"],
                    '<span class="kb-chunk-info" '
                    'style="background:#fef3c7;color:#92400e;">'
                    '<span class="material-icons" style="font-size:14px">edit</span>'
                    '有未保存修改</span>',
                )
            else:
                _set_html(ui_state["dirty_label"], '')

        with ui.dialog().props("persistent") as dd, ui.card().style(
            "min-width: min(820px, 94vw); max-width: 94vw; "
            "max-height: 92vh; display: flex; flex-direction: column; gap: 8px;"
        ):
            # === Header ===
            with ui.row().classes("items-center w-full no-wrap").style("gap: 10px;"):
                ui.html(f'<span class="kb-chip vs">#{idx}</span>')
                ui.label(f"分片 #{idx}").classes("text-subtitle1").style(
                    "font-weight: 600; flex: 1 1 auto; min-width: 0; "
                    "overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
                )
                ui_state["size_label"] = ui.html(
                    f'<span class="kb-chunk-info">'
                    f'<span class="material-icons" style="font-size:14px">'
                    f'text_fields</span>{len(content)} 字符</span>'
                )
                ui_state["dirty_label"] = ui.html('')
                ui.button(icon="close", on_click=dd.close).props(
                    "flat round dense",
                ).tooltip("关闭（有未保存改动时请先保存或放弃）")

            if cid:
                ui.label(f"ID：{cid}").classes(
                    "text-caption text-grey-5 font-mono",
                ).style(
                    "overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
                ).tooltip(cid)
            elif is_preview:
                ui.html(
                    '<div style="display:flex;align-items:center;gap:6px;'
                    'padding:6px 10px;background:#fef3c7;color:#92400e;'
                    'border:1px solid #fde68a;border-radius:8px;font-size:12px;">'
                    '<span class="material-icons" style="font-size:16px">info</span>'
                    '切分预览模式下分片尚未落库，保存 / 删除按钮不可用；'
                    '可通过上方「重切」或「一键同步」落库后再编辑。</div>'
                )

            # === 正文：可编辑 textarea ===
            ui.label("正文").classes("text-caption text-grey-7").style(
                "margin-top: 4px; font-weight: 600;"
            )
            content_area = ui.textarea(value=content).props(
                "outlined autogrow"
            ).classes("w-full").style(
                "flex: 0 0 auto; min-height: 180px; max-height: 42vh; overflow: auto; "
                "font-family: ui-monospace, Menlo, monospace; font-size: 13px;"
            )
            # NiceGUI ui.textarea 的 textarea 样式透传：行高 + 粘滞
            content_area.on_value_change(lambda _=None: _refresh_dirty())
            ui_state["content_area"] = content_area

            # === metadata 区域：只读网格 / JSON 编辑 双模式 ===
            with ui.row().classes("items-center w-full no-wrap").style(
                "gap: 8px; margin-top: 8px;"
            ):
                ui.label("metadata").classes("text-caption text-grey-7").style(
                    "font-weight: 600;"
                )
                ui.space()
                edit_meta_sw = ui.switch(text="以 JSON 形式编辑 metadata").props("dense")
            meta_readonly_host = ui.column().classes("w-full").style(
                "flex: 0 0 auto; min-height: 0;"
            )
            meta_readonly_host.set_visibility(True)
            meta_html = _render_meta_grid_html(meta) or (
                '<div style="color:#9ca3af;font-size:12px;padding:6px 0;">'
                '（无 metadata）</div>'
            )
            with meta_readonly_host:
                ui.html(meta_html)
            ui_state["meta_readonly_host"] = meta_readonly_host

            meta_json_area = ui.textarea(
                value=_json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True),
            ).props("outlined autogrow").classes("w-full").style(
                "font-family: ui-monospace, Menlo, monospace; font-size: 12.5px; "
                "min-height: 140px; max-height: 30vh;"
            )
            meta_json_area.set_visibility(False)
            meta_json_area.on_value_change(lambda _=None: _refresh_dirty())
            ui_state["meta_json_area"] = meta_json_area

            def _on_toggle_meta_mode(e) -> None:
                is_json = bool(e.value)
                ui_state["meta_edit_mode"] = is_json
                meta_readonly_host.set_visibility(not is_json)
                meta_json_area.set_visibility(is_json)
                _refresh_dirty()

            edit_meta_sw.on_value_change(_on_toggle_meta_mode)

            # === 底部按钮 ===
            with ui.row().classes("w-full items-center q-mt-xs").style(
                "gap: 6px; padding-top: 8px; border-top: 1px solid #e5e7eb;"
            ):
                def _copy_content() -> None:
                    cur = _current_content()
                    js = f"navigator.clipboard.writeText({_json.dumps(cur)})"
                    try:
                        ui.run_javascript(js)
                        ui.notify("已复制分片正文到剪贴板", type="positive")
                    except Exception:  # noqa: BLE001
                        ui.notify("复制失败：浏览器不支持 clipboard API", type="warning")

                def _reset() -> None:
                    content_area.value = original_content
                    meta_json_area.value = _json.dumps(
                        original_meta, ensure_ascii=False, indent=2, sort_keys=True,
                    )
                    _refresh_dirty()
                    ui.notify("已恢复到原始内容", type="info")

                del_btn = ui.button(
                    "删除此分片", icon="delete_outline",
                    on_click=lambda: _confirm_delete_chunk(),
                ).props("flat dense color=negative no-caps")
                if is_preview:
                    del_btn.props("disable")
                    del_btn.tooltip("预览模式下无 ID，不能删除")
                else:
                    del_btn.tooltip(
                        "按 ID 从向量库中删除该分片；不可恢复。",
                    )

                ui.space()

                ui.button("复制正文", icon="content_copy",
                          on_click=_copy_content).props("flat dense")
                ui.button("还原", icon="undo", on_click=_reset).props(
                    "flat dense",
                ).tooltip("放弃本次修改，回到打开时的内容")
                ui.button("关闭", on_click=dd.close).props("flat dense")

                save_btn = ui.button(
                    "保存并重新向量化", icon="save",
                    on_click=lambda: _do_save(),
                ).props("unelevated dense color=primary no-caps")
                if is_preview:
                    save_btn.props("disable")
                    save_btn.tooltip(
                        "切分预览模式下分片没有 ID，无法原位保存。"
                        "请先通过「重切」或「一键同步」落库。",
                    )
                else:
                    save_btn.tooltip(
                        "写回向量库（删除旧向量 + 按新正文重新计算 embedding）",
                    )

            def _do_save() -> None:
                if is_preview:
                    return
                new_content = _current_content()
                new_meta, meta_err = _current_meta()
                if meta_err:
                    ui.notify(meta_err, type="negative", multi_line=True)
                    return
                if new_content == original_content and new_meta == original_meta:
                    ui.notify("没有可保存的改动", type="info")
                    return
                if not new_content.strip():
                    ui.notify(
                        "正文为空。若要删除请使用『删除此分片』按钮。",
                        type="warning",
                    )
                    return

                # 把 source / page 等关键字段守住：用户若误删，回填原 meta 的值
                if isinstance(new_meta, dict):
                    for key in ("source",):
                        if not new_meta.get(key) and original_meta.get(key):
                            new_meta[key] = original_meta[key]

                ui.notify("正在重新向量化并写回向量库…", type="ongoing", timeout=2000)
                res = _update_chunk(
                    kb_name, cid, new_content, new_meta or {},
                )
                if not res.get("ok"):
                    ui.notify(res.get("msg", "保存失败"), type="negative", multi_line=True)
                    return
                ui.notify(res.get("msg", "已保存"), type="positive", timeout=4000)
                # 更新内存里的记录 + 刷新外层列表
                c["page_content"] = new_content
                c["metadata"] = new_meta or {}
                if state_local["mode"] == "stored":
                    # stored 列表里的这条引用同一个对象（_list_file_chunks 返回的 dict）
                    for st in state_local.get("chunks_stored") or []:
                        if st.get("id") == cid:
                            st["page_content"] = new_content
                            st["metadata"] = new_meta or {}
                            break
                dd.close()
                _render_chunks()

            def _confirm_delete_chunk() -> None:
                if is_preview:
                    return
                with ui.dialog() as confirm_dlg, ui.card().style(
                    "min-width: 360px;"
                ):
                    ui.label(f"删除分片 #{idx}？").classes(
                        "text-subtitle1 q-mb-xs",
                    ).style("font-weight: 600;")
                    ui.label(
                        "会把此分片从向量库中移除；不影响磁盘原文件。"
                        "不可恢复。",
                    ).classes("text-caption text-grey-7").style("line-height: 1.5;")
                    with ui.row().classes("w-full justify-end q-mt-sm").style(
                        "gap: 6px;",
                    ):
                        ui.button(
                            "取消", on_click=confirm_dlg.close,
                        ).props("flat dense")

                        def _ok() -> None:
                            res = _delete_chunk(kb_name, cid)
                            if not res.get("ok"):
                                ui.notify(
                                    res.get("msg", "删除失败"),
                                    type="negative",
                                )
                                return
                            ui.notify(res.get("msg", "已删除"), type="info")
                            # 从内存快照里摘掉
                            if state_local["mode"] == "stored":
                                state_local["chunks_stored"] = [
                                    st for st in (state_local.get("chunks_stored") or [])
                                    if st.get("id") != cid
                                ]
                            confirm_dlg.close()
                            dd.close()
                            _render_chunks()

                        ui.button(
                            "确认删除", icon="delete_outline", on_click=_ok,
                        ).props("unelevated dense color=negative")
                confirm_dlg.open()

            _refresh_dirty()
        dd.open()

    # ---- 单文件重切 -------------------------------------------------
    def _open_resplit(
        kb_name: str, file_name: str,
        chunk_size_ref, chunk_overlap_ref, zh_ref,
    ) -> None:
        with ui.dialog() as d, ui.card().style("min-width: 420px;"):
            ui.label(f"重新切分「{file_name}」？").classes(
                "text-subtitle1 q-mb-xs"
            ).style("font-weight: 600;")
            ui.label(
                "会用以下参数重新执行 loader + splitter，并覆盖已有向量。"
                "不会删除磁盘文件。"
            ).classes("text-caption text-grey-7 q-mb-sm").style(
                "line-height: 1.5;"
            )

            with ui.grid(columns=2).classes("w-full").style("gap: 8px;"):
                cs = ui.number(
                    label="chunk_size",
                    value=int(chunk_size_ref.value or kb_defaults["chunk_size"]),
                    min=50, max=4000, step=50,
                ).props("dense outlined").classes("w-full")
                co = ui.number(
                    label="chunk_overlap",
                    value=int(chunk_overlap_ref.value or kb_defaults["chunk_overlap"]),
                    min=0, max=2000, step=10,
                ).props("dense outlined").classes("w-full")
                zh = ui.switch(
                    text="中文标题加强", value=bool(zh_ref.value),
                ).props("dense")

            with ui.row().classes("w-full justify-end q-mt-sm").style(
                "gap: 6px;"
            ):
                ui.button("取消", on_click=d.close).props("flat dense")

                def _ok() -> None:
                    try:
                        res = _vectorize_files(
                            kb_name, [file_name],
                            chunk_size=int(cs.value or 750),
                            chunk_overlap=int(co.value or 150),
                            zh_title_enhance=bool(zh.value),
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("resplit failed")
                        ui.notify(
                            f"重切失败：{type(exc).__name__}: {exc}",
                            type="negative",
                        )
                        return
                    fails = res.get("failed_files") or {}
                    if fails:
                        ui.notify(
                            f"{file_name} 重切失败：{list(fails.values())[0]}",
                            type="negative",
                        )
                    else:
                        ui.notify(f"{file_name} 已重新向量化", type="positive")
                    d.close()
                    # 刷新文件列表 tab
                    if view_state["view"] == "detail":
                        _render_detail_view()

                ui.button(
                    "开始重切", icon="content_cut", on_click=_ok,
                ).props("unelevated dense color=primary")
        d.open()

    # ---- 启动 -------------------------------------------------------
    _render_list_view()


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _escape(s: Any) -> str:
    return (
        str(s).replace("&", "&amp;")
              .replace("<", "&lt;")
              .replace(">", "&gt;")
              .replace('"', "&quot;")
    )


def _static_url_for(kb_name: str, file_name: str) -> str:
    """拼出 `/static/kb_content/<kb>/content/<file>` 的 URL（已 URL-encode）。"""
    kb_q = urllib.parse.quote(kb_name, safe="")
    # file_name 是 posix 相对路径，像 "a/b.txt" ——每段单独 encode
    parts = [urllib.parse.quote(p, safe="") for p in file_name.split("/")]
    return f"/static/kb_content/{kb_q}/content/" + "/".join(parts)


def _highlight(text: str, query: str) -> str:
    """把 query 里的每个非空白词在 text 中高亮（HTML 转义后做替换）。"""
    safe = _escape(text)
    if not query:
        return safe
    # 取 query 里长度≥2 的 token，避免高亮单字造成全文黄底
    import re
    tokens = [t for t in re.findall(r"\S+", query) if len(t) >= 2]
    if not tokens:
        return safe
    # 按长度降序，避免 "北京" 与 "北" 互相覆盖
    tokens.sort(key=len, reverse=True)
    out = safe
    for t in tokens:
        pattern = re.compile(re.escape(_escape(t)), re.IGNORECASE)
        out = pattern.sub(
            lambda m: (
                f'<mark style="background:#fde68a;color:#111827;'
                f'padding:0 2px;border-radius:2px;">{m.group(0)}</mark>'
            ),
            out,
        )
    return out


def _render_meta_grid_html(meta: Dict[str, Any]) -> str:
    """把 metadata 渲染成紧凑的 key: value 网格（HTML 字符串）。"""
    if not meta:
        return ""
    rows: List[str] = []
    for k in sorted(meta.keys()):
        v = meta[k]
        if k == "vector":
            continue
        if isinstance(v, (list, tuple)) and len(v) > 16:
            display = f"{_escape(str(v[:6]))} … ({len(v)} items)"
        else:
            display = _escape(str(v))
        rows.append(
            f'<div class="k">{_escape(k)}</div><div>{display}</div>'
        )
    if not rows:
        return ""
    return (
        '<div class="kb-meta-grid" style="margin-top:8px;">'
        + "".join(rows)
        + "</div>"
    )


def _set_html(el, html: str) -> None:
    """兼容 NiceGUI 不同版本 ui.html 的内容原地更新方法。"""
    if el is None:
        return
    try:
        el.content = html
        return
    except Exception:
        pass
    try:
        el.set_content(html)
    except Exception:
        pass


def _fmt_size(n: int) -> str:
    try:
        v = float(n)
    except Exception:  # noqa: BLE001
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024:
            return f"{v:.1f} {unit}" if unit != "B" else f"{int(v)} {unit}"
        v /= 1024
    return f"{v:.1f} TB"


__all__ = ["render_kb_manage_page"]
