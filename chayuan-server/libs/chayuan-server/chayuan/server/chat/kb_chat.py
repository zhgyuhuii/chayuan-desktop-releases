from __future__ import annotations

import asyncio, json
import uuid
from typing import AsyncIterable, List, Optional, Literal

from fastapi import Body, Request
from fastapi.concurrency import run_in_threadpool
from sse_starlette.sse import EventSourceResponse
from langchain_classic.callbacks.streaming_aiter import AsyncIteratorCallbackHandler
from langchain_core.prompts import ChatPromptTemplate


from chayuan.settings import Settings
from chayuan.server.agent.tools_factory.search_internet import search_engine
from chayuan.server.api_server.api_schemas import OpenAIChatOutput
from chayuan.server.chat.utils import History
from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
from chayuan.server.knowledge_base.kb_doc_api import search_docs, search_temp_docs
from chayuan.server.knowledge_base.utils import format_reference
from chayuan.server.utils import (wrap_done, get_ChatOpenAI, get_default_llm,
                                   BaseResponse, get_prompt_template, build_logger,
                                   check_embed_model, api_address
                                )


logger = build_logger()


async def kb_chat(query: str = Body(..., description="用户输入", examples=["你好"]),
                mode: Literal["local_kb", "temp_kb", "search_engine"] = Body("local_kb", description="知识来源"),
                kb_name: str = Body("", description="mode=local_kb时为知识库名称；temp_kb时为临时知识库ID，search_engine时为搜索引擎名称", examples=["samples"]),
                top_k: int = Body(Settings.kb_settings.VECTOR_SEARCH_TOP_K, description="匹配向量数"),
                score_threshold: float = Body(
                    Settings.kb_settings.SCORE_THRESHOLD,
                    description="知识库匹配相关度阈值，取值范围在0-1之间，SCORE越小，相关度越高，取到1相当于不筛选，建议设置在0.5左右",
                    ge=0,
                    le=2,
                ),
                history: List[History] = Body(
                    [],
                    description="历史对话",
                    examples=[[
                        {"role": "user",
                        "content": "我们来玩成语接龙，我先来，生龙活虎"},
                        {"role": "assistant",
                        "content": "虎头虎脑"}]]
                ),
                stream: bool = Body(True, description="流式输出"),
                model: str = Body(get_default_llm(), description="LLM 模型名称。"),
                temperature: float = Body(Settings.model_settings.TEMPERATURE, description="LLM 采样温度", ge=0.0, le=2.0),
                max_tokens: Optional[int] = Body(
                    Settings.model_settings.MAX_TOKENS,
                    description="限制LLM生成Token数量，默认None代表模型最大值"
                ),
                prompt_name: str = Body(
                    "default",
                    description="使用的prompt模板名称(在prompt_settings.yaml中配置)"
                ),
                return_direct: bool = Body(False, description="直接返回检索结果，不送入 LLM"),
                request: Request = None,
                ):
    # P1-6 项 1：灰度切到 ChatGraph。任何异常 / feature flag 关 → 回退老逻辑。
    try:
        from chayuan.server.chat.graph.shims import kb_chat_via_graph, use_chat_graph
        if use_chat_graph():
            user_obj = None
            try:
                u = getattr(request.state, "user", None) if request is not None else None
                if isinstance(u, dict):
                    user_obj = u
            except Exception:  # noqa: BLE001
                user_obj = None
            return await kb_chat_via_graph(
                query=query, mode=mode, kb_name=kb_name,
                top_k=top_k, score_threshold=score_threshold,
                history=[h.dict() if hasattr(h, "dict") else h for h in (history or [])],
                stream=stream, model=model, temperature=temperature,
                max_tokens=max_tokens, prompt_name=prompt_name,
                return_direct=return_direct, request=request, user=user_obj,
            )
    except Exception as _e:  # noqa: BLE001
        logger.warning("ChatGraph 灰度失败，回退老实现：%r", _e)

    if mode == "local_kb":
        kb = KBServiceFactory.get_service_by_name(kb_name)
        if kb is None:
            return BaseResponse(code=404, msg=f"未找到知识库 {kb_name}")
    
    # 把调用方的 access_token 透传进下载链接（浏览器点击 <a href> 时 browser 自己没法
    # 带 Authorization 头，AuthMiddleware 针对 download_doc 允许 ?token=…）。
    caller_token = ""
    try:
        if request is not None:
            auth_hdr = (request.headers.get("Authorization") or "")
            if auth_hdr.lower().startswith("bearer "):
                caller_token = auth_hdr[7:].strip()
            # 如果没带 header（例如 webui 走代理），退一步用当前 state.user 现签一枚
            if not caller_token:
                u = getattr(request.state, "user", None)
                if isinstance(u, dict) and u.get("id"):
                    from chayuan.server.auth.tokens import create_access_token
                    caller_token = create_access_token(
                        user_id=int(u["id"]),
                        username=str(u.get("username") or ""),
                        role=str(u.get("role") or "user"),
                    )
    except Exception:  # noqa: BLE001
        caller_token = ""

    async def knowledge_base_chat_iterator() -> AsyncIterable[str]:
        try:
            nonlocal history, prompt_name, max_tokens

            history = [History.from_data(h) for h in history]

            if mode == "local_kb":
                kb = KBServiceFactory.get_service_by_name(kb_name)
                ok, msg = kb.check_embed_model()
                if not ok:
                    raise ValueError(msg)
                docs = await run_in_threadpool(search_docs,
                                                query=query,
                                                knowledge_base_name=kb_name,
                                                top_k=top_k,
                                                score_threshold=score_threshold,
                                                file_name="",
                                                metadata={})
                source_documents = format_reference(
                    kb_name, docs, api_address(is_public=True), access_token=caller_token,
                )
            elif mode == "temp_kb":
                ok, msg = check_embed_model()
                if not ok:
                    raise ValueError(msg)
                docs = await run_in_threadpool(search_temp_docs,
                                                kb_name,
                                                query=query,
                                                top_k=top_k,
                                                score_threshold=score_threshold)
                source_documents = format_reference(
                    kb_name, docs, api_address(is_public=True), access_token=caller_token,
                )
            elif mode == "search_engine":
                result = await run_in_threadpool(search_engine, query, top_k, kb_name)
                docs = [x.dict() for x in result.get("docs", [])]
                source_documents = [f"""出处 [{i + 1}] [{d['metadata']['filename']}]({d['metadata']['source']}) \n\n{d['page_content']}\n\n""" for i,d in enumerate(docs)]
            else:
                docs = []
                source_documents = []
            # import rich
            # rich.print(dict(
            #     mode=mode,
            #     query=query,
            #     knowledge_base_name=kb_name,
            #     top_k=top_k,
            #     score_threshold=score_threshold,
            # ))
            # rich.print(docs)
            if return_direct:
                yield OpenAIChatOutput(
                    id=f"chat{uuid.uuid4()}",
                    model=None,
                    object="chat.completion",
                    content="",
                    role="assistant",
                    finish_reason="stop",
                    docs=source_documents,
                ) .model_dump_json()
                return

            callback = AsyncIteratorCallbackHandler()
            callbacks = [callback]

            # Langfuse：统一入口，离线 / 未装 / 显式关停 场景全部 no-op。
            try:
                from chayuan.server.observability.langfuse_integration import (
                    inject_into_callbacks,
                )
                callbacks = inject_into_callbacks(callbacks)
            except Exception:  # noqa: BLE001
                pass

            if max_tokens in [None, 0]:
                max_tokens = Settings.model_settings.MAX_TOKENS

            llm = get_ChatOpenAI(
                model_name=model,
                temperature=temperature,
                max_tokens=max_tokens,
                callbacks=callbacks,
            )

            # ===== Rerank (启用) =====
            # 通过 capability_router 拿用户在 UI 配的默认 rerank 模型;
            # 未配 → fallback Settings.kb_settings.RERANKER_MODEL
            # USE_RERANKER 关时跳过 — 让用户能临时关
            try:
                if Settings.kb_settings.USE_RERANKER and docs:
                    from chayuan.server.capability_router import resolve_model
                    rerank_model_id = (
                        resolve_model("rerank")  # 优先 UI 默认配置
                        or Settings.kb_settings.RERANKER_MODEL  # 回退 settings
                    )
                    if rerank_model_id:
                        # ── Step 1: 优先 ONNX 本地 rerank (in-process,纯 onnxruntime
                        # + tokenizers) — 跟 OnnxEmbeddings 同思路,绕开
                        # sentence_transformers (lite spec excludes 它,~200 MB)。
                        # 用户 <bundled>/rerank/<repo>/ 下若有 onnx + tokenizer.json
                        # 就自动命中。命中 None 时再走 LangchainReranker (full/dev)。
                        reranker = None
                        try:
                            from chayuan.server.reranker.onnx_local import (
                                try_get_local_onnx_reranker,
                            )
                            reranker = try_get_local_onnx_reranker(
                                top_n=top_k,
                                max_length=Settings.kb_settings.RERANKER_MAX_LENGTH,
                            )
                        except Exception as _e:  # noqa: BLE001
                            import logging as _logging
                            _logging.getLogger("chayuan.kb_chat").debug(
                                "[rerank] ONNX short-circuit 失败,继续 fallback: %r", _e,
                            )

                        # ── Step 2: ONNX 不可用 → sentence_transformers CrossEncoder
                        # (lite 版会 ImportError 被外层 try/except 接住,用原 docs)
                        if reranker is None:
                            from chayuan.server.reranker.reranker import LangchainReranker
                            from chayuan.server.utils import (
                                get_model_path, embedding_device,
                            )
                            # 本地模型路径解析;若是云端 API 类型,get_model_path 返原 id
                            model_path = (
                                get_model_path(rerank_model_id) or rerank_model_id
                            )
                            reranker = LangchainReranker(
                                top_n=top_k,
                                device=embedding_device(),
                                max_length=Settings.kb_settings.RERANKER_MAX_LENGTH,
                                model_name_or_path=model_path,
                            )
                        # docs 是 dict 列表(page_content/metadata),包成 Document
                        # 后再调 compress;rerank 完拆回 dict 形式
                        from langchain_core.documents import Document as _Doc
                        lc_docs = [
                            _Doc(
                                page_content=str(d.get("page_content", "")),
                                metadata=d.get("metadata", {}),
                            )
                            for d in docs
                        ]
                        reranked = reranker.compress_documents(
                            documents=lc_docs, query=query,
                        )
                        docs = [
                            {"page_content": d.page_content, "metadata": d.metadata}
                            for d in reranked
                        ]
            except Exception as _e:  # noqa: BLE001
                # rerank 失败不影响正常召回 → log 后继续用原 docs
                import logging as _logging
                _logging.getLogger("chayuan.kb_chat").warning(
                    "[rerank] failed, fallback to raw docs: %r", _e,
                )

            context = "\n\n".join([doc["page_content"] for doc in docs])

            if len(docs) == 0:  # 如果没有找到相关文档，使用empty模板
                prompt_name = "empty"
            prompt_template = get_prompt_template("rag", prompt_name)
            input_msg = History(role="user", content=prompt_template).to_msg_template(False)
            chat_prompt = ChatPromptTemplate.from_messages(
                [i.to_msg_template() for i in history] + [input_msg])

            chain = chat_prompt | llm

            # Begin a task that runs in the background.
            task = asyncio.create_task(wrap_done(
                chain.ainvoke({"context": context, "question": query}),
                callback.done),
            )

            if len(source_documents) == 0:  # 没有找到相关文档
                source_documents.append(f"<span style='color:red'>未找到相关文档,该回答为大模型自身能力解答！</span>")

            if stream:
                # yield documents first
                ret = OpenAIChatOutput(
                    id=f"chat{uuid.uuid4()}",
                    object="chat.completion.chunk",
                    content="",
                    role="assistant",
                    model=model,
                    docs=source_documents,
                )
                yield ret.model_dump_json()

                async for token in callback.aiter():
                    ret = OpenAIChatOutput(
                        id=f"chat{uuid.uuid4()}",
                        object="chat.completion.chunk",
                        content=token,
                        role="assistant",
                        model=model,
                    )
                    yield ret.model_dump_json()
            else:
                answer = ""
                async for token in callback.aiter():
                    answer += token
                ret = OpenAIChatOutput(
                    id=f"chat{uuid.uuid4()}",
                    object="chat.completion",
                    content=answer,
                    role="assistant",
                    model=model,
                )
                yield ret.model_dump_json()
            await task
        except asyncio.exceptions.CancelledError:
            logger.warning("streaming progress has been interrupted by user.")
            return
        except Exception as e:
            logger.error(f"error in knowledge chat: {e}")
            yield {"data": json.dumps({"error": str(e)})}
            return

    if stream:
        return EventSourceResponse(knowledge_base_chat_iterator())
    else:
        return await knowledge_base_chat_iterator().__anext__()
