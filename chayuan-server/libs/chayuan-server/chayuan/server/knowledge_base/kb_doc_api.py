import asyncio
import hashlib
import json
import os
import urllib
from typing import Dict, List, Optional

from fastapi import Body, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from langchain_core.documents import Document
from sse_starlette import EventSourceResponse

from chayuan.settings import Settings
from chayuan.server.db.repository.knowledge_file_repository import get_file_detail
from chayuan.server.knowledge_base.kb_service.base import (
    KBServiceFactory,
    get_kb_file_details,
)
from chayuan.server.knowledge_base.model.kb_document_model import DocumentWithVSId
from chayuan.server.knowledge_base.utils import (
    KnowledgeFile,
    files2docs_in_thread,
    get_file_path,
    list_files_from_folder,
    validate_kb_name,
)
from chayuan.server.knowledge_base.kb_cache.faiss_cache import memo_faiss_pool
from chayuan.server.utils import (
    BaseResponse,
    ListResponse,
    check_embed_model,
    run_in_thread_pool,
    get_default_embedding,
)
from chayuan.utils import build_logger

logger = build_logger()


def search_temp_docs(knowledge_id: str = Body(..., description="知识库 ID", examples=["example_id"]),
                     query: str = Body("", description="用户输入", examples=["你好"]),
                     top_k: int = Body(..., description="返回的文档数量", examples=[5]),
                     score_threshold: float = Body(..., description="分数阈值", examples=[0.8])) -> List[Dict]:
    '''从临时 FAISS 知识库中检索文档，用于文件对话'''
    with memo_faiss_pool.acquire(knowledge_id) as vs:
        docs = vs.similarity_search_with_score(
            query, k=top_k, score_threshold=score_threshold
        )
        docs = [x[0].dict() for x in docs]
        return docs


def search_docs(
        query: str = Body("", description="用户输入", examples=["你好"]),
        knowledge_base_name: str = Body(
            ..., description="知识库名称", examples=["samples"]
        ),
        top_k: int = Body(Settings.kb_settings.VECTOR_SEARCH_TOP_K, description="匹配向量数"),
        score_threshold: float = Body(
            Settings.kb_settings.SCORE_THRESHOLD,
            description="知识库匹配相关度阈值，取值范围在0-1之间，"
                        "SCORE越小，相关度越高，"
                        "取到2相当于不筛选，建议设置在0.5左右",
            ge=0.0,
            le=2.0,
        ),
        file_name: str = Body("", description="文件名称，支持 sql 通配符"),
        metadata: dict = Body({}, description="根据 metadata 进行过滤，仅支持一级键"),
        use_hybrid: Optional[bool] = Body(None, description="按请求覆盖 hybrid 开关;None=用全局默认"),
        use_rerank: Optional[bool] = Body(None, description="按请求覆盖 rerank 开关;None=用全局默认"),
) -> List[Dict]:
    kb = KBServiceFactory.get_service_by_name(knowledge_base_name)
    data = []
    if kb is not None:
        if query:
            docs = kb.search_docs(
                query, top_k, score_threshold,
                use_hybrid=use_hybrid, use_rerank=use_rerank,
            )
            # data = [DocumentWithVSId(**x[0].dict(), score=x[1], id=x[0].metadata.get("id")) for x in docs]
            data = [DocumentWithVSId(**{"id": x.metadata.get("id"), **x.dict()}) for x in docs]
        elif file_name or metadata:
            data = kb.list_docs(file_name=file_name, metadata=metadata)
            for d in data:
                if "vector" in d.metadata:
                    del d.metadata["vector"]
    return [x.dict() for x in data]


def list_files(knowledge_base_name: str) -> ListResponse:
    if not validate_kb_name(knowledge_base_name):
        return ListResponse(code=403, msg="Don't attack me", data=[])

    knowledge_base_name = urllib.parse.unquote(knowledge_base_name)
    kb = KBServiceFactory.get_service_by_name(knowledge_base_name)
    if kb is None:
        return ListResponse(
            code=404, msg=f"未找到知识库 {knowledge_base_name}", data=[]
        )
    else:
        all_docs = get_kb_file_details(knowledge_base_name)
        return ListResponse(data=all_docs)


def _save_files_in_thread(
        files: List[UploadFile], knowledge_base_name: str, override: bool
):
    """
    通过多线程将上传的文件保存到对应知识库目录内。
    生成器返回保存结果：{"code":200, "msg": "xxx", "data": {"knowledge_base_name":"xxx", "file_name": "xxx"}}
    """

    def save_file(file: UploadFile, knowledge_base_name: str, override: bool) -> dict:
        """
        保存单个文件。

        **N 补丁**：同时写入 FileStorage（MinIO 或本地） + 本地缓存副本。
        本地缓存是为了保持 ``KnowledgeFile.filepath`` 相关的解析/向量化代码免改动；
        存储后端是 local 时，两者其实是同一份文件（Local.put 下就是写本地）。
        """
        try:
            filename = file.filename
            file_path = get_file_path(
                knowledge_base_name=knowledge_base_name, doc_name=filename
            )
            data = {"knowledge_base_name": knowledge_base_name, "file_name": filename}

            file_content = file.file.read()  # 读取上传文件的内容
            file_hash = hashlib.sha256(file_content).hexdigest()
            data.update({"file_hash": file_hash, "file_size": len(file_content)})
            if (
                    os.path.isfile(file_path)
                    and not override
                    and os.path.getsize(file_path) == len(file_content)
            ):
                file_status = f"文件 {filename} 已存在。"
                logger.warn(file_status)
                return dict(code=404, msg=file_status, data=data)

            # 1) FileStorage：权威存储（kb_content 命名空间）
            try:
                from chayuan.server.file_storage import NS, get_storage
                storage = get_storage()
                storage.put(
                    NS.KB_CONTENT, f"{knowledge_base_name}/{filename}",
                    file_content,
                )
            except Exception as _e:  # noqa: BLE001
                logger.warning("FileStorage.put 失败（继续只落本地）：%r", _e)

            # 2) 本地副本：保留老代码里 os.path.exists / file2text 的路径依赖
            if not os.path.isdir(os.path.dirname(file_path)):
                os.makedirs(os.path.dirname(file_path))
            with open(file_path, "wb") as f:
                f.write(file_content)
            return dict(code=200, msg=f"成功上传文件 {filename}", data=data)
        except Exception as e:
            msg = f"{filename} 文件上传失败，报错信息为: {e}"
            logger.error(f"{e.__class__.__name__}: {msg}")
            return dict(code=500, msg=msg, data=data)

    params = [
        {"file": file, "knowledge_base_name": knowledge_base_name, "override": override}
        for file in files
    ]
    for result in run_in_thread_pool(save_file, params=params):
        yield result


# def files2docs(files: List[UploadFile] = File(..., description="上传文件，支持多文件"),
#                 knowledge_base_name: str = Form(..., description="知识库名称", examples=["samples"]),
#                 override: bool = Form(False, description="覆盖已有文件"),
#                 save: bool = Form(True, description="是否将文件保存到知识库目录")):
#     def save_files(files, knowledge_base_name, override):
#         for result in _save_files_in_thread(files, knowledge_base_name=knowledge_base_name, override=override):
#             yield json.dumps(result, ensure_ascii=False)

#     def files_to_docs(files):
#         for result in files2docs_in_thread(files):
#             yield json.dumps(result, ensure_ascii=False)


def upload_docs(
        files: List[UploadFile] = File(..., description="上传文件，支持多文件"),
        knowledge_base_name: str = Form(
            ..., description="知识库名称", examples=["samples"]
        ),
        override: bool = Form(False, description="覆盖已有文件"),
        to_vector_store: bool = Form(True, description="上传文件后是否进行向量化"),
        chunk_size: int = Form(Settings.kb_settings.CHUNK_SIZE, description="知识库中单段文本最大长度"),
        chunk_overlap: int = Form(Settings.kb_settings.OVERLAP_SIZE, description="知识库中相邻文本重合长度"),
        zh_title_enhance: bool = Form(Settings.kb_settings.ZH_TITLE_ENHANCE, description="是否开启中文标题加强"),
        docs: str = Form("", description="自定义的docs，需要转为json字符串"),
        not_refresh_vs_cache: bool = Form(False, description="暂不保存向量库（用于FAISS）"),
        uploader_id: int = None,
) -> BaseResponse:
    """
    API接口：上传文件，并/或向量化
    """
    if not validate_kb_name(knowledge_base_name):
        return BaseResponse(code=403, msg="Don't attack me")

    kb = KBServiceFactory.get_service_by_name(knowledge_base_name)
    if kb is None:
        return BaseResponse(code=404, msg=f"未找到知识库 {knowledge_base_name}")

    docs = json.loads(docs) if docs else {}
    failed_files = {}
    file_names = list(docs.keys())
    file_hashes = {}

    # 先将上传的文件保存到磁盘
    for result in _save_files_in_thread(
            files, knowledge_base_name=knowledge_base_name, override=override
    ):
        filename = result["data"]["file_name"]
        if result["code"] != 200:
            failed_files[filename] = result["msg"]
        else:
            file_hashes[filename] = result["data"].get("file_hash") or ""

        if filename not in file_names:
            file_names.append(filename)

    # 对保存的文件进行向量化
    if to_vector_store:
        result = update_docs(
            knowledge_base_name=knowledge_base_name,
            file_names=file_names,
            override_custom_docs=True,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            zh_title_enhance=zh_title_enhance,
            docs=docs,
            file_hashes=file_hashes,
            uploader_id=uploader_id,
            not_refresh_vs_cache=True,
        )
        failed_files.update(result.data["failed_files"])
        if not not_refresh_vs_cache:
            kb.save_vector_store()

    return BaseResponse(
        code=200, msg="文件上传与向量化完成", data={"failed_files": failed_files}
    )


def delete_docs(
        knowledge_base_name: str = Body(..., examples=["samples"]),
        file_names: List[str] = Body(..., examples=[["file_name.md", "test.txt"]]),
        delete_content: bool = Body(False),
        not_refresh_vs_cache: bool = Body(False, description="暂不保存向量库（用于FAISS）"),
) -> BaseResponse:
    if not validate_kb_name(knowledge_base_name):
        return BaseResponse(code=403, msg="Don't attack me")

    knowledge_base_name = urllib.parse.unquote(knowledge_base_name)
    kb = KBServiceFactory.get_service_by_name(knowledge_base_name)
    if kb is None:
        return BaseResponse(code=404, msg=f"未找到知识库 {knowledge_base_name}")

    failed_files = {}
    for file_name in file_names:
        if not kb.exist_doc(file_name):
            failed_files[file_name] = f"未找到文件 {file_name}"

        try:
            kb_file = KnowledgeFile(
                filename=file_name, knowledge_base_name=knowledge_base_name
            )
            kb.delete_doc(kb_file, delete_content, not_refresh_vs_cache=True)
        except Exception as e:
            msg = f"{file_name} 文件删除失败，错误信息：{e}"
            logger.error(f"{e.__class__.__name__}: {msg}")
            failed_files[file_name] = msg

    if not not_refresh_vs_cache:
        kb.save_vector_store()

    return BaseResponse(
        code=200, msg=f"文件删除完成", data={"failed_files": failed_files}
    )


def update_info(
        knowledge_base_name: str = Body(
            ..., description="知识库名称", examples=["samples"]
        ),
        kb_info: str = Body(..., description="知识库介绍", examples=["这是一个知识库"]),
):
    if not validate_kb_name(knowledge_base_name):
        return BaseResponse(code=403, msg="Don't attack me")

    kb = KBServiceFactory.get_service_by_name(knowledge_base_name)
    if kb is None:
        return BaseResponse(code=404, msg=f"未找到知识库 {knowledge_base_name}")
    kb.update_info(kb_info)

    return BaseResponse(code=200, msg=f"知识库介绍修改完成", data={"kb_info": kb_info})


def update_docs(
        knowledge_base_name: str = Body(
            ..., description="知识库名称", examples=["samples"]
        ),
        file_names: List[str] = Body(
            ..., description="文件名称，支持多文件", examples=[["file_name1", "text.txt"]]
        ),
        chunk_size: int = Body(Settings.kb_settings.CHUNK_SIZE, description="知识库中单段文本最大长度"),
        chunk_overlap: int = Body(Settings.kb_settings.OVERLAP_SIZE, description="知识库中相邻文本重合长度"),
        zh_title_enhance: bool = Body(Settings.kb_settings.ZH_TITLE_ENHANCE, description="是否开启中文标题加强"),
        override_custom_docs: bool = Body(False, description="是否覆盖之前自定义的docs"),
        docs: str = Body("", description="自定义的docs，需要转为json字符串"),
        file_hashes: Dict[str, str] = None,
        uploader_id: int = None,
        not_refresh_vs_cache: bool = Body(False, description="暂不保存向量库（用于FAISS）"),
) -> BaseResponse:
    """
    更新知识库文档
    """
    if not validate_kb_name(knowledge_base_name):
        return BaseResponse(code=403, msg="Don't attack me")

    kb = KBServiceFactory.get_service_by_name(knowledge_base_name)
    if kb is None:
        return BaseResponse(code=404, msg=f"未找到知识库 {knowledge_base_name}")

    failed_files = {}
    kb_files = []
    docs = json.loads(docs) if docs else {}
    file_hashes = file_hashes or {}

    # 生成需要加载docs的文件列表
    for file_name in file_names:
        file_detail = get_file_detail(kb_name=knowledge_base_name, filename=file_name)
        # 如果该文件之前使用了自定义docs，则根据参数决定略过或覆盖
        if file_detail.get("custom_docs") and not override_custom_docs:
            continue
        if file_name not in docs:
            try:
                kb_files.append(
                    KnowledgeFile(
                        filename=file_name, knowledge_base_name=knowledge_base_name
                    )
                )
            except Exception as e:
                msg = f"加载文档 {file_name} 时出错：{e}"
                logger.error(f"{e.__class__.__name__}: {msg}")
                failed_files[file_name] = msg

    # 从文件生成docs，并进行向量化。
    # 这里利用了KnowledgeFile的缓存功能，在多线程中加载Document，然后传给KnowledgeFile
    for status, result in files2docs_in_thread(
            kb_files,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            zh_title_enhance=zh_title_enhance,
    ):
        if status:
            kb_name, file_name, new_docs = result
            # 诊断:加载成功但切分出 0 个 chunk —— 之前这种情况 update_doc 返回
            # False 被静默丢弃,接口照样 code=200,用户看不到任何反馈。
            # 典型场景:无文字的图片(.png/.jpg)走 RapidOCRLoader,OCR 抽不到
            # 文字 → 0 chunk → 入库 0 条。文档知识库只做 OCR 文本向量化,**不**
            # 对纯图像做 CLIP 视觉向量;无字图应改用"图像知识库"(走
            # /knowledge_source/{id}/image/upload → CLIP 图像嵌入)。
            # 这里不再静默,把它如实报进 failed_files + 打日志。
            if not new_docs:
                ext = os.path.splitext(file_name)[-1].lower()
                if ext in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
                    msg = (
                        f"图片 {file_name} OCR 未提取到任何文字,跳过向量化 —— "
                        "文档库对图片走 OCR → 文本嵌入,只有图中含文字才能被检索到。"
                        "(纯视觉的以图搜图本版本未启用;图像类知识库已下线。)"
                    )
                else:
                    msg = (
                        f"文件 {file_name} 解析后未得到任何可向量化内容(0 个切片),"
                        "已跳过向量化。请检查文件是否为空或格式是否受支持。"
                    )
                logger.warning(msg)
                failed_files[file_name] = msg
                continue
            kb_file = KnowledgeFile(
                filename=file_name, knowledge_base_name=knowledge_base_name
            )
            kb_file.splited_docs = new_docs
            ok = kb.update_doc(
                kb_file,
                not_refresh_vs_cache=True,
                file_hash=file_hashes.get(file_name),
                uploader_id=uploader_id,
            )
            # update_doc 返回 False(如 embedding 模型不可用)同样不再静默
            if ok is False:
                msg = (
                    f"文件 {file_name} 向量化失败:嵌入模型不可用或写入向量库失败,"
                    "请检查「设置 → AI 平台 → 文本嵌入」模型是否就绪。"
                )
                logger.warning(msg)
                failed_files[file_name] = msg
        else:
            kb_name, file_name, error = result
            failed_files[file_name] = error

    # 将自定义的docs进行向量化
    for file_name, v in docs.items():
        try:
            v = [x if isinstance(x, Document) else Document(**x) for x in v]
            kb_file = KnowledgeFile(
                filename=file_name, knowledge_base_name=knowledge_base_name
            )
            kb.update_doc(
                kb_file,
                docs=v,
                not_refresh_vs_cache=True,
                file_hash=file_hashes.get(file_name),
                uploader_id=uploader_id,
            )
        except Exception as e:
            msg = f"为 {file_name} 添加自定义docs时出错：{e}"
            logger.error(f"{e.__class__.__name__}: {msg}")
            failed_files[file_name] = msg

    if not not_refresh_vs_cache:
        kb.save_vector_store()

    return BaseResponse(
        code=200, msg=f"更新文档完成", data={"failed_files": failed_files}
    )


def download_doc(
        knowledge_base_name: str = Query(
            ..., description="知识库名称", examples=["samples"]
        ),
        file_name: str = Query(..., description="文件名称", examples=["test.txt"]),
        preview: bool = Query(False, description="是：浏览器内预览；否：下载"),
):
    """
    下载知识库文档
    """
    if not validate_kb_name(knowledge_base_name):
        return BaseResponse(code=403, msg="Don't attack me")

    kb = KBServiceFactory.get_service_by_name(knowledge_base_name)
    if kb is None:
        return BaseResponse(code=404, msg=f"未找到知识库 {knowledge_base_name}")

    if preview:
        content_disposition_type = "inline"
    else:
        content_disposition_type = None

    # 注意:在 try 块外初始化 kb_file=None,以防 KnowledgeFile(...) 在不支持的扩展名等
    # 路径上提前 raise —— 之前的写法在 except 里直接拿 kb_file.filename 会
    # UnboundLocalError 二次崩,把真正错因(ValueError: 暂未支持的文件格式)吞掉。
    kb_file = None
    try:
        kb_file = KnowledgeFile(
            filename=file_name, knowledge_base_name=knowledge_base_name
        )

        # FileStorage 适配:minio 后端 + 浏览器预览 = 同源代理(永远 stream 出去)
        #
        # 历史踩坑:之前 minio 一律 302 到 presigned_url(MinIO 直链),理论上
        # 浏览器跟着 redirect 取文件;实战上有两类问题:
        #   ① MINIO_ENDPOINT 是 docker 网内域名(如 'minio:9000'),浏览器解析不
        #      到 → 持续 307 + 取不到内容(看似 5 次 redirect 死循环);
        #   ② 即使域名能解析,跨域(server origin vs MinIO origin)导致 iframe /
        #      fetch 触发 CORS 预检,MinIO 默认不开 CORS → 永远 fail。
        # 修法:对 preview=true 路径**总是同源 stream**(server 从 MinIO 拉,再
        # 透传给浏览器);仅对 preview=false 的"显式下载"才用 presigned redirect
        # —— 那时是浏览器另开下载,跨域 / 直链都没问题。
        try:
            from chayuan.server.file_storage import NS, get_storage
            from fastapi.responses import RedirectResponse, StreamingResponse
            storage = get_storage()
            key = f"{knowledge_base_name}/{file_name}"
            in_storage = storage.exists(NS.KB_CONTENT, key)
            is_minio = storage.name == "minio"

            if is_minio and in_storage and not preview:
                # 显式下载:走 presigned URL,让浏览器另起连接拉,省服务器带宽
                url = storage.presigned_url(NS.KB_CONTENT, key, expires_sec=3600)
                return RedirectResponse(url)

            if is_minio and in_storage and preview:
                # 预览:同源 stream;先把 bytes 拉回来再回送(服务端中转,
                # 对浏览器来说就是"我们自己服务的文件",CORS / 网络可达性都不再是问题)
                import mimetypes
                data = storage.get(NS.KB_CONTENT, key)
                guessed = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
                disposition = (
                    f'inline; filename*=UTF-8\'\'{urllib.parse.quote(file_name)}'
                )
                return StreamingResponse(
                    iter([data]),
                    media_type=guessed,
                    headers={"Content-Disposition": disposition},
                )

            # local 或 minio 但本地缓存缺失时，确保本地存一份(供下方 FileResponse 用)
            if not os.path.exists(kb_file.filepath) and in_storage:
                storage.copy_to_local(NS.KB_CONTENT, key, kb_file.filepath)
        except Exception as _e:  # noqa: BLE001
            logger.debug("FileStorage 下载兜底失败（继续走本地）：%r", _e)

        if os.path.exists(kb_file.filepath):
            return FileResponse(
                path=kb_file.filepath,
                filename=kb_file.filename,
                media_type="multipart/form-data",
                content_disposition_type=content_disposition_type,
            )
    except Exception as e:
        # 用 file_name(函数入参,永远存在);kb_file 可能因 KnowledgeFile 构造失败而为 None
        msg = f"{file_name} 读取文件失败，错误信息是：{e}"
        logger.error(f"{e.__class__.__name__}: {msg}")
        return BaseResponse(code=500, msg=msg)

    return BaseResponse(code=500, msg=f"{file_name} 读取文件失败")


def recreate_vector_store(
        knowledge_base_name: str = Body(..., examples=["samples"]),
        allow_empty_kb: bool = Body(True),
        vs_type: str = Body(Settings.kb_settings.DEFAULT_VS_TYPE, description="为空知识库指定向量库类型。已有知识库默认使用原向量库类型。"),
        embed_model: str = Body(get_default_embedding(), description="为空知识库指定Embedding模型。已有知识库默认使用原Embedding模型。"),
        chunk_size: int = Body(Settings.kb_settings.CHUNK_SIZE, description="知识库中单段文本最大长度"),
        chunk_overlap: int = Body(Settings.kb_settings.OVERLAP_SIZE, description="知识库中相邻文本重合长度"),
        zh_title_enhance: bool = Body(Settings.kb_settings.ZH_TITLE_ENHANCE, description="是否开启中文标题加强"),
        not_refresh_vs_cache: bool = Body(False, description="暂不保存向量库（用于FAISS）"),
        atomic_swap: bool = Body(False, description="T8：开启影子索引 + 原子切换，重建期间查询不中断（仅 FAISS / Milvus 可原子）"),
):
    """
    recreate vector store from the content.
    this is usefull when user can copy files to content folder directly instead of upload through network.
    by default, get_service_by_name only return knowledge base in the info.db and having document files in it.
    set allow_empty_kb to True make it applied on empty knowledge base which it not in the info.db or having no documents.
    """

    def output():
        try:
            kb = KBServiceFactory.get_service_by_name(knowledge_base_name)
            if kb is None:
                kb = KBServiceFactory.get_service(knowledge_base_name, vs_type, embed_model)
            if not kb.exists() and not allow_empty_kb:
                yield {"code": 404, "msg": f"未找到知识库 ‘{knowledge_base_name}’"}
            else:
                ok, msg = kb.check_embed_model()
                if not ok:
                    yield {"code": 404, "msg": msg}
                elif atomic_swap:
                    # T8：影子索引 + 原子切换；期间查询流量打到旧索引，零宕机
                    from chayuan.server.knowledge_base.kb_service.atomic_rebuild import (
                        rebuild_atomic,
                    )
                    result = rebuild_atomic(
                        knowledge_base_name,
                        vs_type=vs_type, embed_model=embed_model,
                        chunk_size=chunk_size, chunk_overlap=chunk_overlap,
                        zh_title_enhance=zh_title_enhance,
                    )
                    yield json.dumps({
                        "code": 200 if result.get("ok") else 500,
                        "msg": "atomic rebuild done" if result.get("ok") else result.get("error", ""),
                        "data": result,
                    }, ensure_ascii=False)
                    return
                else:
                    if kb.exists():
                        kb.clear_vs()
                    kb.create_kb()
                    files = list_files_from_folder(knowledge_base_name)
                    kb_files = [(file, knowledge_base_name) for file in files]
                    i = 0
                    for status, result in files2docs_in_thread(
                            kb_files,
                            chunk_size=chunk_size,
                            chunk_overlap=chunk_overlap,
                            zh_title_enhance=zh_title_enhance,
                    ):
                        if status:
                            kb_name, file_name, docs = result
                            kb_file = KnowledgeFile(
                                filename=file_name, knowledge_base_name=kb_name
                            )
                            kb_file.splited_docs = docs
                            yield json.dumps(
                                {
                                    "code": 200,
                                    "msg": f"({i + 1} / {len(files)}): {file_name}",
                                    "total": len(files),
                                    "finished": i + 1,
                                    "doc": file_name,
                                },
                                ensure_ascii=False,
                            )
                            kb.add_doc(kb_file, not_refresh_vs_cache=True)
                        else:
                            kb_name, file_name, error = result
                            msg = f"添加文件‘{file_name}’到知识库‘{knowledge_base_name}’时出错：{error}。已跳过。"
                            logger.error(msg)
                            yield json.dumps(
                                {
                                    "code": 500,
                                    "msg": msg,
                                }
                            )
                        i += 1
                    if not not_refresh_vs_cache:
                        kb.save_vector_store()
        except asyncio.exceptions.CancelledError:
            logger.warning("streaming progress has been interrupted by user.")
            return

    return EventSourceResponse(output())
