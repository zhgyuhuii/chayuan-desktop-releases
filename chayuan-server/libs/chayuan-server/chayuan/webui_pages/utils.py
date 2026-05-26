# 该文件封装了对api.py的请求，可以被不同的webui使用
# 通过ApiRequest和AsyncApiRequest支持同步/异步调用

import base64
import contextlib
import json
import logging
import os
from io import BytesIO
from pathlib import Path
from typing import *

import httpx

from chayuan.settings import Settings
from chayuan.server.utils import api_address, get_httpx_client, set_httpx_config, get_default_embedding
from chayuan.utils import build_logger


logger = build_logger()

set_httpx_config()


class ApiRequest:
    """
    api.py调用的封装（同步模式）,简化api调用方式
    """

    def __init__(
        self,
        base_url: str = api_address(),
        timeout: float = Settings.basic_settings.HTTPX_DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url
        self.timeout = timeout
        self._use_async = False
        self._client = None

    @property
    def client(self):
        if self._client is None or self._client.is_closed:
            self._client = get_httpx_client(
                base_url=self.base_url, use_async=self._use_async, timeout=self.timeout
            )
        return self._client

    # ------------------------------------------------------------------
    # P3: 鉴权头自动注入
    # session_state.access_token 由登录页写入；也支持 CHAYUAN_ACCESS_TOKEN 环境变量，
    # 方便 streamlit 之外的脚本直接复用 ApiRequest。
    # ------------------------------------------------------------------
    def _auth_token(self) -> Optional[str]:
        try:
            import streamlit as st  # type: ignore
            t = st.session_state.get("access_token")  # type: ignore[attr-defined]
            if t:
                return str(t)
        except Exception:
            pass
        t = os.environ.get("CHAYUAN_ACCESS_TOKEN") or ""
        return t or None

    def _inject_auth(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        token = self._auth_token()
        if not token:
            return kwargs
        headers = dict(kwargs.get("headers") or {})
        headers.setdefault("Authorization", f"Bearer {token}")
        kwargs["headers"] = headers
        return kwargs

    def get(
        self,
        url: str,
        params: Union[Dict, List[Tuple], bytes] = None,
        retry: int = 3,
        stream: bool = False,
        **kwargs: Any,
    ) -> Union[httpx.Response, Iterator[httpx.Response], None]:
        kwargs = self._inject_auth(kwargs)
        while retry > 0:
            try:
                if stream:
                    return self.client.stream("GET", url, params=params, **kwargs)
                else:
                    return self.client.get(url, params=params, **kwargs)
            except Exception as e:
                msg = f"error when get {url}: {e}"
                logger.error(f"{e.__class__.__name__}: {msg}")
                retry -= 1

    def post(
        self,
        url: str,
        data: Dict = None,
        json: Dict = None,
        retry: int = 3,
        stream: bool = False,
        **kwargs: Any,
    ) -> Union[httpx.Response, Iterator[httpx.Response], None]:
        kwargs = self._inject_auth(kwargs)
        while retry > 0:
            try:
                if stream:
                    return self.client.stream(
                        "POST", url, data=data, json=json, **kwargs
                    )
                else:
                    return self.client.post(url, data=data, json=json, **kwargs)
            except Exception as e:
                msg = f"error when post {url}: {e}"
                logger.error(f"{e.__class__.__name__}: {msg}")
                retry -= 1

    def delete(
        self,
        url: str,
        data: Dict = None,
        json: Dict = None,
        retry: int = 3,
        stream: bool = False,
        **kwargs: Any,
    ) -> Union[httpx.Response, Iterator[httpx.Response], None]:
        kwargs = self._inject_auth(kwargs)
        while retry > 0:
            try:
                if stream:
                    return self.client.stream(
                        "DELETE", url, data=data, json=json, **kwargs
                    )
                else:
                    return self.client.delete(url, data=data, json=json, **kwargs)
            except Exception as e:
                msg = f"error when delete {url}: {e}"
                logger.error(f"{e.__class__.__name__}: {msg}")
                retry -= 1

    def put(
        self,
        url: str,
        data: Dict = None,
        json: Dict = None,
        retry: int = 3,
        stream: bool = False,
        **kwargs: Any,
    ) -> Union[httpx.Response, Iterator[httpx.Response], None]:
        kwargs = self._inject_auth(kwargs)
        while retry > 0:
            try:
                if stream:
                    return self.client.stream(
                        "PUT", url, data=data, json=json, **kwargs
                    )
                else:
                    return self.client.put(url, data=data, json=json, **kwargs)
            except Exception as e:
                msg = f"error when put {url}: {e}"
                logger.error(f"{e.__class__.__name__}: {msg}")
                retry -= 1

    def patch(
        self,
        url: str,
        data: Dict = None,
        json: Dict = None,
        retry: int = 3,
        **kwargs: Any,
    ) -> Union[httpx.Response, None]:
        """httpx 的 Client.patch 与 post 签名一致；方言一致处理 auth 注入。"""
        kwargs = self._inject_auth(kwargs)
        while retry > 0:
            try:
                return self.client.request(
                    "PATCH", url, data=data, json=json, **kwargs
                )
            except Exception as e:  # noqa: BLE001
                msg = f"error when patch {url}: {e}"
                logger.error(f"{e.__class__.__name__}: {msg}")
                retry -= 1

    def _httpx_stream2generator(
        self,
        response: contextlib._GeneratorContextManager,
        as_json: bool = False,
    ):
        """
        将httpx.stream返回的GeneratorContextManager转化为普通生成器
        """

        async def ret_async(response, as_json):
            try:
                async with response as r:
                    chunk_cache = ""
                    async for chunk in r.aiter_text(None):
                        if not chunk:  # fastchat api yield empty bytes on start and end
                            continue
                        if as_json:
                            try:
                                if chunk.startswith("data: "):
                                    data = json.loads(chunk_cache + chunk[6:-2])
                                elif chunk.startswith(":"):  # skip sse comment line
                                    continue
                                else:
                                    data = json.loads(chunk_cache + chunk)

                                chunk_cache = ""
                                yield data
                            except Exception as e:
                                msg = f"接口返回json错误： ‘{chunk}’。错误信息是：{e}。"
                                logger.error(f"{e.__class__.__name__}: {msg}")

                                if chunk.startswith("data: "):
                                    chunk_cache += chunk[6:-2]
                                elif chunk.startswith(":"):  # skip sse comment line
                                    continue
                                else:
                                    chunk_cache += chunk
                                continue
                        else:
                            # print(chunk, end="", flush=True)
                            yield chunk
            except httpx.ConnectError as e:
                msg = f"无法连接API服务器，请确认 ‘api.py’ 已正常启动。({e})"
                logger.error(msg)
                yield {"code": 500, "msg": msg}
            except httpx.ReadTimeout as e:
                msg = f"API通信超时，请确认已启动FastChat与API服务（详见Wiki '5. 启动 API 服务或 Web UI'）。（{e}）"
                logger.error(msg)
                yield {"code": 500, "msg": msg}
            except Exception as e:
                msg = f"API通信遇到错误：{e}"
                logger.error(f"{e.__class__.__name__}: {msg}")
                yield {"code": 500, "msg": msg}

        def ret_sync(response, as_json):
            try:
                with response as r:
                    chunk_cache = ""
                    for chunk in r.iter_text(None):
                        if not chunk:  # fastchat api yield empty bytes on start and end
                            continue
                        if as_json:
                            try:
                                if chunk.startswith("data: "):
                                    data = json.loads(chunk_cache + chunk[6:-2])
                                elif chunk.startswith(":"):  # skip sse comment line
                                    continue
                                else:
                                    data = json.loads(chunk_cache + chunk)

                                chunk_cache = ""
                                yield data
                            except Exception as e:
                                msg = f"接口返回json错误： ‘{chunk}’。错误信息是：{e}。"
                                logger.error(f"{e.__class__.__name__}: {msg}")

                                if chunk.startswith("data: "):
                                    chunk_cache += chunk[6:-2]
                                elif chunk.startswith(":"):  # skip sse comment line
                                    continue
                                else:
                                    chunk_cache += chunk
                                continue
                        else:
                            # print(chunk, end="", flush=True)
                            yield chunk
            except httpx.ConnectError as e:
                msg = f"无法连接API服务器，请确认 ‘api.py’ 已正常启动。({e})"
                logger.error(msg)
                yield {"code": 500, "msg": msg}
            except httpx.ReadTimeout as e:
                msg = f"API通信超时，请确认已启动FastChat与API服务（详见Wiki '5. 启动 API 服务或 Web UI'）。（{e}）"
                logger.error(msg)
                yield {"code": 500, "msg": msg}
            except Exception as e:
                msg = f"API通信遇到错误：{e}"
                logger.error(f"{e.__class__.__name__}: {msg}")
                yield {"code": 500, "msg": msg}

        if self._use_async:
            return ret_async(response, as_json)
        else:
            return ret_sync(response, as_json)

    def _get_response_value(
        self,
        response: httpx.Response,
        as_json: bool = False,
        value_func: Callable = None,
    ):
        """
        转换同步或异步请求返回的响应
        `as_json`: 返回json
        `value_func`: 用户可以自定义返回值，该函数接受response或json
        """

        def to_json(r):
            try:
                return r.json()
            except Exception as e:
                msg = "API未能返回正确的JSON。" + str(e)
                logger.error(f"{e.__class__.__name__}: {msg}")
                return {"code": 500, "msg": msg, "data": None}

        if value_func is None:
            value_func = lambda r: r

        async def ret_async(response):
            if as_json:
                return value_func(to_json(await response))
            else:
                return value_func(await response)

        if self._use_async:
            return ret_async(response)
        else:
            if as_json:
                return value_func(to_json(response))
            else:
                return value_func(response)

    # 服务器信息
    def get_server_configs(self, **kwargs) -> Dict:
        response = self.post("/server/configs", **kwargs)
        return self._get_response_value(response, as_json=True)

    def get_prompt_template(
        self,
        type: str = "llm_chat",
        name: str = "default",
        **kwargs,
    ) -> str:
        data = {
            "type": type,
            "name": name,
        }
        response = self.post("/server/get_prompt_template", json=data, **kwargs)
        return self._get_response_value(response, value_func=lambda r: r.text)

    # 对话相关操作
    def chat_chat(
        self,
        query: str,
        metadata: dict,
        conversation_id: str = None,
        history_len: int = -1,
        history: List[Dict] = [],
        stream: bool = True,
        chat_model_config: Dict = None,
        tool_config: Dict = None,
        **kwargs,
    ):
        """
        对应api.py/chat/chat接口
        """
        data = {
            "query": query,
            "metadata": metadata,
            "conversation_id": conversation_id,
            "history_len": history_len,
            "history": history,
            "stream": stream,
            "chat_model_config": chat_model_config,
            "tool_config": tool_config,
        }

        # print(f"received input message:")
        # pprint(data)

        response = self.post("/chat/chat", json=data, stream=True, **kwargs)
        return self._httpx_stream2generator(response, as_json=True)

    def upload_temp_docs(
        self,
        files: List[Union[str, Path, bytes]],
        knowledge_id: str = None,
        chunk_size=Settings.kb_settings.CHUNK_SIZE,
        chunk_overlap=Settings.kb_settings.OVERLAP_SIZE,
        zh_title_enhance=Settings.kb_settings.ZH_TITLE_ENHANCE,
    ):
        """
        对应api.py/knowledge_base/upload_temp_docs接口
        """

        def convert_file(file, filename=None):
            if isinstance(file, bytes):  # raw bytes
                file = BytesIO(file)
            elif hasattr(file, "read"):  # a file io like object
                filename = filename or file.name
            else:  # a local path
                file = Path(file).absolute().open("rb")
                filename = filename or os.path.split(file.name)[-1]
            return filename, file

        files = [convert_file(file) for file in files]
        data = {
            "knowledge_id": knowledge_id,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "zh_title_enhance": zh_title_enhance,
        }

        response = self.post(
            "/knowledge_base/upload_temp_docs",
            data=data,
            files=[("files", (filename, file)) for filename, file in files],
        )
        return self._get_response_value(response, as_json=True)

    def file_chat(
        self,
        query: str,
        knowledge_id: str,
        top_k: int = Settings.kb_settings.VECTOR_SEARCH_TOP_K,
        score_threshold: float = Settings.kb_settings.SCORE_THRESHOLD,
        history: List[Dict] = [],
        stream: bool = True,
        model: str = None,
        temperature: float = 0.9,
        max_tokens: int = None,
        prompt_name: str = "default",
    ):
        """
        对应api.py/chat/file_chat接口
        """
        data = {
            "query": query,
            "knowledge_id": knowledge_id,
            "top_k": top_k,
            "score_threshold": score_threshold,
            "history": history,
            "stream": stream,
            "model_name": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "prompt_name": prompt_name,
        }

        response = self.post(
            "/chat/file_chat",
            json=data,
            stream=True,
        )
        return self._httpx_stream2generator(response, as_json=True)

    # 知识库相关操作

    def list_knowledge_bases(
        self,
    ):
        """
        对应api.py/knowledge_base/list_knowledge_bases接口
        """
        response = self.get("/knowledge_base/list_knowledge_bases")
        return self._get_response_value(
            response, as_json=True, value_func=lambda r: r.get("data", [])
        )

    def create_knowledge_base(
        self,
        knowledge_base_name: str,
        vector_store_type: str = Settings.kb_settings.DEFAULT_VS_TYPE,
        embed_model: str = get_default_embedding(),
        kb_info: str = "",
        visibility: str = "private",
        storage_backend: str = "",
    ):
        """对应 /knowledge_base/create_knowledge_base。

        ``storage_backend``：``""`` 跟随全局 / ``"local"`` / ``"minio"``
        """
        data = {
            "knowledge_base_name": knowledge_base_name,
            "vector_store_type": vector_store_type,
            "embed_model": embed_model,
            "kb_info": kb_info,
            "visibility": visibility,
            "storage_backend": storage_backend or "",
        }

        response = self.post(
            "/knowledge_base/create_knowledge_base",
            json=data,
        )
        return self._get_response_value(response, as_json=True)

    def get_kb_storage_backend(self, knowledge_base_name: str) -> Dict:
        """查询某 KB 的存储后端详情（override/global/resolved/available）。"""
        resp = self.get(
            "/knowledge_base/storage_backend",
            params={"knowledge_base_name": knowledge_base_name},
        )
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data") or {},
        )

    def update_kb_storage_backend(
        self, knowledge_base_name: str, storage_backend: str,
        migrate: bool = False, dry_run: bool = False,
    ) -> Dict:
        """修改某 KB 的存储后端。``storage_backend`` 留空 = 跟随全局。"""
        resp = self.post(
            "/knowledge_base/update_storage_backend",
            json={
                "knowledge_base_name": knowledge_base_name,
                "storage_backend": storage_backend or "",
                "migrate": bool(migrate),
                "dry_run": bool(dry_run),
            },
        )
        return self._get_response_value(resp, as_json=True)

    def delete_knowledge_base(
        self,
        knowledge_base_name: str,
    ):
        """
        对应api.py/knowledge_base/delete_knowledge_base接口
        """
        response = self.post(
            "/knowledge_base/delete_knowledge_base",
            json=f"{knowledge_base_name}",
        )
        return self._get_response_value(response, as_json=True)

    def list_kb_docs(
        self,
        knowledge_base_name: str,
    ):
        """
        对应api.py/knowledge_base/list_files接口
        """
        response = self.get(
            "/knowledge_base/list_files",
            params={"knowledge_base_name": knowledge_base_name},
        )
        return self._get_response_value(
            response, as_json=True, value_func=lambda r: r.get("data", [])
        )

    def search_kb_docs(
        self,
        knowledge_base_name: str,
        query: str = "",
        top_k: int = Settings.kb_settings.VECTOR_SEARCH_TOP_K,
        score_threshold: int = Settings.kb_settings.SCORE_THRESHOLD,
        file_name: str = "",
        metadata: dict = {},
    ) -> List:
        """
        对应api.py/knowledge_base/search_docs接口
        """
        data = {
            "query": query,
            "knowledge_base_name": knowledge_base_name,
            "top_k": top_k,
            "score_threshold": score_threshold,
            "file_name": file_name,
            "metadata": metadata,
        }

        response = self.post(
            "/knowledge_base/search_docs",
            json=data,
        )
        return self._get_response_value(response, as_json=True)

    def upload_kb_docs(
        self,
        files: List[Union[str, Path, bytes]],
        knowledge_base_name: str,
        override: bool = False,
        to_vector_store: bool = True,
        chunk_size=Settings.kb_settings.CHUNK_SIZE,
        chunk_overlap=Settings.kb_settings.OVERLAP_SIZE,
        zh_title_enhance=Settings.kb_settings.ZH_TITLE_ENHANCE,
        docs: Dict = {},
        not_refresh_vs_cache: bool = False,
    ):
        """
        对应api.py/knowledge_base/upload_docs接口
        """

        def convert_file(file, filename=None):
            if isinstance(file, bytes):  # raw bytes
                file = BytesIO(file)
            elif hasattr(file, "read"):  # a file io like object
                filename = filename or file.name
            else:  # a local path
                file = Path(file).absolute().open("rb")
                filename = filename or os.path.split(file.name)[-1]
            return filename, file

        files = [convert_file(file) for file in files]
        data = {
            "knowledge_base_name": knowledge_base_name,
            "override": override,
            "to_vector_store": to_vector_store,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "zh_title_enhance": zh_title_enhance,
            "docs": docs,
            "not_refresh_vs_cache": not_refresh_vs_cache,
        }

        if isinstance(data["docs"], dict):
            data["docs"] = json.dumps(data["docs"], ensure_ascii=False)
        response = self.post(
            "/knowledge_base/upload_docs",
            data=data,
            files=[("files", (filename, file)) for filename, file in files],
        )
        return self._get_response_value(response, as_json=True)

    def delete_kb_docs(
        self,
        knowledge_base_name: str,
        file_names: List[str],
        delete_content: bool = False,
        not_refresh_vs_cache: bool = False,
    ):
        """
        对应api.py/knowledge_base/delete_docs接口
        """
        data = {
            "knowledge_base_name": knowledge_base_name,
            "file_names": file_names,
            "delete_content": delete_content,
            "not_refresh_vs_cache": not_refresh_vs_cache,
        }

        response = self.post(
            "/knowledge_base/delete_docs",
            json=data,
        )
        return self._get_response_value(response, as_json=True)

    def update_kb_info(self, knowledge_base_name, kb_info):
        """
        对应api.py/knowledge_base/update_info接口
        """
        data = {
            "knowledge_base_name": knowledge_base_name,
            "kb_info": kb_info,
        }

        response = self.post(
            "/knowledge_base/update_info",
            json=data,
        )
        return self._get_response_value(response, as_json=True)

    def update_kb_docs(
        self,
        knowledge_base_name: str,
        file_names: List[str],
        override_custom_docs: bool = False,
        chunk_size=Settings.kb_settings.CHUNK_SIZE,
        chunk_overlap=Settings.kb_settings.OVERLAP_SIZE,
        zh_title_enhance=Settings.kb_settings.ZH_TITLE_ENHANCE,
        docs: Dict = {},
        not_refresh_vs_cache: bool = False,
    ):
        """
        对应api.py/knowledge_base/update_docs接口
        """
        data = {
            "knowledge_base_name": knowledge_base_name,
            "file_names": file_names,
            "override_custom_docs": override_custom_docs,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "zh_title_enhance": zh_title_enhance,
            "docs": docs,
            "not_refresh_vs_cache": not_refresh_vs_cache,
        }

        if isinstance(data["docs"], dict):
            data["docs"] = json.dumps(data["docs"], ensure_ascii=False)

        response = self.post(
            "/knowledge_base/update_docs",
            json=data,
        )
        return self._get_response_value(response, as_json=True)

    def recreate_vector_store(
        self,
        knowledge_base_name: str,
        allow_empty_kb: bool = True,
        vs_type: str = Settings.kb_settings.DEFAULT_VS_TYPE,
        embed_model: str = get_default_embedding(),
        chunk_size=Settings.kb_settings.CHUNK_SIZE,
        chunk_overlap=Settings.kb_settings.OVERLAP_SIZE,
        zh_title_enhance=Settings.kb_settings.ZH_TITLE_ENHANCE,
    ):
        """
        对应api.py/knowledge_base/recreate_vector_store接口
        """
        data = {
            "knowledge_base_name": knowledge_base_name,
            "allow_empty_kb": allow_empty_kb,
            "vs_type": vs_type,
            "embed_model": embed_model,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "zh_title_enhance": zh_title_enhance,
        }

        response = self.post(
            "/knowledge_base/recreate_vector_store",
            json=data,
            stream=True,
            timeout=None,
        )
        return self._httpx_stream2generator(response, as_json=True)

    # ------------------------------------------------------------------
    # 知识源（Knowledge Source）：统一接入 SQL / Mongo / ES / Vector
    # 对应后端 /knowledge_source 前缀路由
    # ------------------------------------------------------------------
    def ks_list_dialects(self) -> Dict[str, str]:
        resp = self.get("/knowledge_source/dialects")
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data", {})
        )

    def ks_list(self, kind: Optional[str] = None) -> List[Dict]:
        params = {"kind": kind} if kind else None
        resp = self.get("/knowledge_source/", params=params)
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data", [])
        )

    def ks_get(self, source_id: int) -> Dict:
        resp = self.get(f"/knowledge_source/{int(source_id)}")
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data", {})
        )

    def ks_test_connection(
        self,
        dialect: str,
        host: str = "",
        port: int = 0,
        database: str = "",
        username: str = "",
        password: str = "",
        options: Optional[Dict] = None,
        connection_id: Optional[int] = None,
    ) -> Dict:
        data = {
            "dialect": dialect, "host": host, "port": port, "database": database,
            "username": username, "password": password,
            "options": options or {}, "connection_id": connection_id,
        }
        resp = self.post("/knowledge_source/test_connection", json=data)
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data", {})
        )

    def ks_create(
        self,
        name: str,
        kind: str,
        display_name: str = "",
        description: str = "",
        dialect: str = "",
        host: str = "",
        port: int = 0,
        database: str = "",
        username: str = "",
        password: str = "",
        options: Optional[Dict] = None,
        allowed: Optional[Dict[str, List[str]]] = None,
        visibility: str = "private",
    ) -> Dict:
        data = {
            "name": name, "kind": kind,
            "display_name": display_name, "description": description,
            "dialect": dialect,
            "host": host, "port": port, "database": database,
            "username": username, "password": password,
            "options": options or {},
            "allowed": allowed or {},
            "visibility": visibility,
        }
        resp = self.post("/knowledge_source/", json=data)
        return self._get_response_value(resp, as_json=True)

    def ks_update(self, source_id: int, **kwargs) -> Dict:
        resp = self.patch(f"/knowledge_source/{int(source_id)}", json=kwargs) \
            if hasattr(self, "patch") else self.post(
                f"/knowledge_source/{int(source_id)}",
                json=kwargs,
                headers={"X-HTTP-Method-Override": "PATCH"},
            )
        return self._get_response_value(resp, as_json=True)

    def ks_delete(self, source_id: int) -> Dict:
        resp = self.delete(f"/knowledge_source/{int(source_id)}") \
            if hasattr(self, "delete") else self.post(
                f"/knowledge_source/{int(source_id)}",
                headers={"X-HTTP-Method-Override": "DELETE"},
            )
        return self._get_response_value(resp, as_json=True)

    def ks_introspect(self, source_id: int, sample_rows: int = 3) -> Dict:
        resp = self.post(
            f"/knowledge_source/{int(source_id)}/introspect",
            json={"sample_rows": int(sample_rows)},
        )
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data", {})
        )

    def ks_schema(self, source_id: int) -> Dict:
        resp = self.get(f"/knowledge_source/{int(source_id)}/schema")
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data", {})
        )

    def ks_catalog_probe(
        self,
        dialect: str,
        host: str = "",
        port: int = 0,
        database: str = "",
        username: str = "",
        password: str = "",
        options: Optional[Dict] = None,
        kind: str = "",
        connection_id: Optional[int] = None,
        refresh: bool = False,
    ) -> Dict:
        """轻量 catalog：只返 name 列表，供创建 UI 在测试连接后拉可选范围。"""
        data = {
            "dialect": dialect, "host": host, "port": port, "database": database,
            "username": username, "password": password,
            "options": options or {}, "kind": kind,
            "connection_id": connection_id, "refresh": bool(refresh),
        }
        resp = self.post("/knowledge_source/catalog", json=data)
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data", {})
        )

    def ks_catalog_for_source(self, source_id: int, refresh: bool = False) -> Dict:
        resp = self.get(
            f"/knowledge_source/{int(source_id)}/catalog",
            params={"refresh": "true" if refresh else "false"},
        )
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data", {})
        )

    def ks_patch_allowed(
        self, source_id: int, allowed: Dict[str, List[str]],
    ) -> Dict:
        """把白名单替换为给定集合；空即取消范围、恢复"默认全部"。"""
        resp = self.patch(
            f"/knowledge_source/{int(source_id)}/allowed",
            json={"allowed": allowed or {}},
        ) if hasattr(self, "patch") else self.post(
            f"/knowledge_source/{int(source_id)}/allowed",
            json={"allowed": allowed or {}},
            headers={"X-HTTP-Method-Override": "PATCH"},
        )
        return self._get_response_value(resp, as_json=True)

    def ks_grant(self, source_id: int, target_user_id: int, role: str = "reader") -> Dict:
        resp = self.post(
            f"/knowledge_source/{int(source_id)}/grants",
            json={"target_user_id": int(target_user_id), "role": role},
        )
        return self._get_response_value(resp, as_json=True)

    def ks_revoke(self, source_id: int, target_user_id: int) -> Dict:
        resp = self.delete(
            f"/knowledge_source/{int(source_id)}/grants/{int(target_user_id)}"
        ) if hasattr(self, "delete") else self.post(
            f"/knowledge_source/{int(source_id)}/grants/{int(target_user_id)}",
            headers={"X-HTTP-Method-Override": "DELETE"},
        )
        return self._get_response_value(resp, as_json=True)

    def ks_grant_batch(
        self, source_ids: List[int], user_ids: List[int], role: str = "reader",
    ) -> Dict:
        resp = self.post(
            "/knowledge_source/grants/batch",
            json={"source_ids": source_ids, "user_ids": user_ids, "role": role},
        )
        return self._get_response_value(resp, as_json=True)

    def ks_multi_search_stream(
        self,
        query: str,
        source_ids: Optional[List[int]] = None,
        select_all: bool = False,
        top_k: int = 5,
        per_source_timeout: float = 30.0,
        llm_model: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
    ):
        """以 generator 形式流式返回 SSE 事件 dict：{"event", "data"(已解 json)}。"""
        data = {
            "query": query,
            "source_ids": source_ids or [],
            "select_all": bool(select_all),
            "top_k": int(top_k),
            "per_source_timeout": float(per_source_timeout),
            "llm_model": llm_model,
            "history": history or [],
        }
        resp = self.post("/knowledge_source/multi_search", json=data, stream=True)
        return self._sse_iter(resp)

    def ks_multi_chat_stream(
        self,
        query: str,
        source_ids: Optional[List[int]] = None,
        select_all: bool = False,
        top_k: int = 5,
        per_source_timeout: float = 30.0,
        history: Optional[List[Dict[str, str]]] = None,
        model: str = "",
        temperature: float = 0.3,
        prompt_name: str = "default",
    ):
        data = {
            "query": query,
            "source_ids": source_ids or [],
            "select_all": bool(select_all),
            "top_k": int(top_k),
            "per_source_timeout": float(per_source_timeout),
            "history": history or [],
            "stream": True,
            "model": model,
            "temperature": float(temperature),
            "prompt_name": prompt_name,
        }
        resp = self.post("/knowledge_source/multi_chat", json=data, stream=True)
        return self._sse_iter(resp)

    # ------------------------------------------------------------------
    # 文件存储：MinIO / 本地 后端管理
    # ------------------------------------------------------------------
    def storage_status(self) -> Dict:
        resp = self.get("/storage/status")
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data") or {},
        )

    def storage_test_connection(
        self, endpoint: str, access_key: str, secret_key: str,
        secure: bool = False, region: str = "us-east-1",
    ) -> Dict:
        resp = self.post(
            "/storage/test_connection",
            json={
                "endpoint": endpoint, "access_key": access_key,
                "secret_key": secret_key, "secure": bool(secure), "region": region,
            },
        )
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data") or {},
        )

    def storage_switch_backend(
        self, backend: str, minio: Optional[Dict] = None,
    ) -> Dict:
        resp = self.post(
            "/storage/switch_backend",
            json={"backend": backend, "minio": minio},
        )
        return self._get_response_value(resp, as_json=True) or {}

    def storage_migrate(
        self, namespace: str, direction: str = "local_to_minio",
        dry_run: bool = True, limit: int = 500,
    ) -> Dict:
        resp = self.post(
            "/storage/migrate",
            json={
                "namespace": namespace, "direction": direction,
                "dry_run": bool(dry_run), "limit": int(limit),
            },
        )
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data") or {},
        )

    def storage_list(self, ns: str, prefix: str = "", limit: int = 200) -> List[Dict]:
        resp = self.get("/storage/list",
                          params={"ns": ns, "prefix": prefix, "limit": int(limit)})
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data") or [],
        )

    # ------------------------------------------------------------------
    # 图像知识源：模型管理 + 上传 + 搜图
    # ------------------------------------------------------------------
    def image_models_list(self) -> List[Dict]:
        resp = self.get("/image_models/")
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data") or [],
        )

    def image_model_download(self, model_name: str, sync: bool = False) -> Dict:
        resp = self.post(
            "/image_models/download",
            json={"model_name": model_name, "sync": bool(sync)},
        )
        return self._get_response_value(resp, as_json=True) or {}

    def image_model_disk_usage(self) -> Dict:
        resp = self.get("/image_models/disk_usage")
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data") or {},
        )

    def image_upload(
        self, source_id: int, files: List, tags: str = "",
    ) -> Dict:
        from io import BytesIO
        parts = []
        for name, data in files:
            if isinstance(data, bytes):
                parts.append(("files", (name, BytesIO(data), "image/*")))
            elif hasattr(data, "read"):
                parts.append(("files", (name, data, "image/*")))
        resp = self.post(
            f"/knowledge_source/{int(source_id)}/image/upload",
            data={"tags": tags}, files=parts,
        )
        return self._get_response_value(resp, as_json=True) or {}

    def image_list(self, source_id: int, limit: int = 50) -> Dict:
        resp = self.get(
            f"/knowledge_source/{int(source_id)}/image/list",
            params={"limit": int(limit)},
        )
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data") or {},
        )

    def image_search_by_image(
        self, source_id: int, image_bytes: bytes, top_k: int = 5,
    ) -> List[Dict]:
        from io import BytesIO
        resp = self.post(
            f"/knowledge_source/{int(source_id)}/image/search_by_image",
            data={"top_k": int(top_k)},
            files=[("file", ("query.jpg", BytesIO(image_bytes), "image/jpeg"))],
        )
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data") or [],
        )

    # ------------------------------------------------------------------
    # B 路线：RAPTOR / GraphRAG 构建入口
    # ------------------------------------------------------------------
    def build_raptor(
        self, knowledge_base_name: str, *,
        target_cluster_size: int = 5, max_levels: int = 3,
        llm_model: Optional[str] = None,
    ) -> Dict:
        resp = self.post(
            f"/knowledge_base/{knowledge_base_name}/build_raptor",
            json={
                "target_cluster_size": int(target_cluster_size),
                "max_levels": int(max_levels),
                "llm_model": llm_model,
            },
        )
        return self._get_response_value(resp, as_json=True)

    def build_graphrag(
        self, knowledge_base_name: str, *,
        max_chunks: int = 10000, community_min_size: int = 2,
        llm_model: Optional[str] = None,
    ) -> Dict:
        resp = self.post(
            f"/knowledge_base/{knowledge_base_name}/build_graphrag",
            json={
                "max_chunks": int(max_chunks),
                "community_min_size": int(community_min_size),
                "llm_model": llm_model,
            },
        )
        return self._get_response_value(resp, as_json=True)

    def graphrag_stats(self, knowledge_base_name: str) -> Dict:
        resp = self.get(f"/knowledge_base/{knowledge_base_name}/graphrag/stats")
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data", {}),
        )

    def ks_training_list(
        self, source_id: int, kind: Optional[str] = None,
        approved_only: bool = True, limit: int = 500,
    ) -> List[Dict]:
        params = {"approved_only": str(approved_only).lower(), "limit": int(limit)}
        if kind:
            params["kind"] = kind
        resp = self.get(f"/knowledge_source/{int(source_id)}/training", params=params)
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data", [])
        )

    def ks_training_add(
        self, source_id: int, *, kind: str,
        question: str = "", sql: str = "", content: str = "", approved: int = 1,
    ) -> Dict:
        resp = self.post(
            f"/knowledge_source/{int(source_id)}/training",
            json={"kind": kind, "question": question, "sql": sql,
                  "content": content, "approved": int(approved)},
        )
        return self._get_response_value(resp, as_json=True)

    def ks_training_delete(self, source_id: int, sample_id: int) -> Dict:
        resp = self.delete(
            f"/knowledge_source/{int(source_id)}/training/{int(sample_id)}"
        ) if hasattr(self, "delete") else self.post(
            f"/knowledge_source/{int(source_id)}/training/{int(sample_id)}",
            headers={"X-HTTP-Method-Override": "DELETE"},
        )
        return self._get_response_value(resp, as_json=True)

    def ks_download_result(
        self,
        source_id: int,
        query: str,
        format: str = "csv",
        top_k: int = 200,
        llm_model: Optional[str] = None,
    ) -> bytes:
        """把 /download_result 的响应同步读成 bytes，前端可直接丢给
        ``st.download_button`` 用。
        """
        resp = self.post(
            f"/knowledge_source/{int(source_id)}/download_result",
            json={
                "query": query, "format": format, "top_k": int(top_k),
                "llm_model": llm_model,
            },
        )
        if resp is None:
            return b""
        # 直接拿原始字节；后端已经带了 BOM / JSON 编码
        try:
            return resp.content or b""
        except Exception:  # noqa: BLE001
            return b""

    def _sse_iter(self, response):
        """把 httpx stream 响应按 SSE 协议切分为 {"event","data"(已 json 解析)} 字典。

        容错：数据行不是 JSON 时按字符串传出。
        """
        import json as _json
        if response is None:
            return
        try:
            with response as r:
                event = None
                buf: List[str] = []
                for raw in r.iter_lines():
                    # httpx 在 stream 模式下 iter_lines 返回 str；保险起见兼容 bytes
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    line = raw.rstrip("\r")
                    if line == "":
                        if buf:
                            payload = "\n".join(buf)
                            try:
                                payload = _json.loads(payload)
                            except Exception:
                                pass
                            yield {"event": event or "message", "data": payload}
                        event = None
                        buf = []
                        continue
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        buf.append(line[5:].lstrip())
                    # 其它前缀忽略
                if buf:
                    payload = "\n".join(buf)
                    try:
                        payload = _json.loads(payload)
                    except Exception:
                        pass
                    yield {"event": event or "message", "data": payload}
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"SSE 解析异常：{_e}")

    def embed_texts(
        self,
        texts: List[str],
        embed_model: str = get_default_embedding(),
        to_query: bool = False,
    ) -> List[List[float]]:
        """
        对文本进行向量化，可选模型包括本地 embed_models 和支持 embeddings 的在线模型
        """
        data = {
            "texts": texts,
            "embed_model": embed_model,
            "to_query": to_query,
        }
        resp = self.post(
            "/other/embed_texts",
            json=data,
        )
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data")
        )

    def chat_feedback(
        self,
        message_id: str,
        score: int,
        reason: str = "",
    ) -> int:
        """
        反馈对话评价
        """
        data = {
            "message_id": message_id,
            "score": score,
            "reason": reason,
        }
        resp = self.post("/chat/feedback", json=data)
        return self._get_response_value(resp)

    def list_tools(self, enabled: bool = True) -> Dict:
        """列出可在对话界面使用的工具。

        ``enabled=True`` 默认只返回 ``tool_settings.yaml`` 里启用（use=true）的工具，
        与「配置面板 → 工具配置」的开关保持一致；传 ``False`` 可获取所有已注册工具。
        """
        params = {"enabled": "true"} if enabled else None
        resp = self.get("/tools", params=params)
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data", {})
        )

    def call_tool(
        self,
        name: str,
        tool_input: Dict = {},
    ):
        """
        调用工具
        """
        data = {
            "name": name,
            "tool_input": tool_input,
        }
        resp = self.post("/tools/call", json=data)
        return self._get_response_value(
            resp, as_json=True, value_func=lambda r: r.get("data")
        )

    # MCP Profile Methods
    def get_mcp_profile(self, **kwargs) -> Dict:
        """
        获取 MCP 通用配置
        """
        resp = self.get("/api/v1/mcp_connections/profile", **kwargs)
        return self._get_response_value(resp, as_json=True)

    def create_mcp_profile(
        self,
        timeout: int = 30,
        working_dir: str = "/tmp",
        env_vars: Dict[str, str] = None,
        **kwargs
    ) -> Dict:
        """
        创建 MCP 通用配置
        """
        if env_vars is None:
            env_vars = {}
        data = {
            "timeout": timeout,
            "working_dir": working_dir,
            "env_vars": env_vars,
        }
        resp = self.post("/api/v1/mcp_connections/profile", json=data, **kwargs)
        return self._get_response_value(resp, as_json=True)

    def update_mcp_profile(
        self,
        timeout: int = 30,
        working_dir: str = "/tmp",
        env_vars: Dict[str, str] = None,
        **kwargs
    ) -> Dict:
        """
        更新 MCP 通用配置
        """
        if env_vars is None:
            env_vars = {}
        data = {
            "timeout": timeout,
            "working_dir": working_dir,
            "env_vars": env_vars,
        }
        resp = self.put("/api/v1/mcp_connections/profile", json=data, **kwargs)
        return self._get_response_value(resp, as_json=True)

    def reset_mcp_profile(self, **kwargs) -> Dict:
        """
        重置 MCP 通用配置为默认值
        """
        resp = self.post("/api/v1/mcp_connections/profile/reset", **kwargs)
        return self._get_response_value(resp, as_json=True)

    def delete_mcp_profile(self, **kwargs) -> Dict:
        """
        删除 MCP 通用配置
        """
        resp = self.delete("/api/v1/mcp_connections/profile", **kwargs)
        return self._get_response_value(resp, as_json=True)

    # MCP Connection Methods
    def add_mcp_connection(
        self,
        server_name: str,
        args: List[str] = None,
        env: Dict[str, str] = None,
        cwd: Optional[str] = None,
        transport: str = "stdio",
        timeout: int = 30,
        enabled: bool = True,
        description: Optional[str] = None,
        config: Dict = None,
        **kwargs
    ) -> Dict:
        """
        添加 MCP 连接
        """
        if args is None:
            args = []
        if env is None:
            env = {}
        if config is None:
            config = {}
        data = {
            "server_name": server_name,
            "args": args,
            "env": env,
            "cwd": cwd,
            "transport": transport,
            "timeout": timeout,
            "enabled": enabled,
            "description": description,
            "config": config,
        }
        resp = self.post("/api/v1/mcp_connections/", json=data, **kwargs)
        return self._get_response_value(resp, as_json=True)

    def get_all_mcp_connections(self, enabled_only: bool = False, **kwargs) -> Dict:
        """
        获取所有 MCP 连接
        """
        params = {"enabled_only": enabled_only} if enabled_only else {}
        resp = self.get("/api/v1/mcp_connections/", params=params, **kwargs)
        return self._get_response_value(resp, as_json=True)

    def get_mcp_connection(self, connection_id: str, **kwargs) -> Dict:
        """
        根据 ID 获取 MCP 连接
        """
        resp = self.get(f"/api/v1/mcp_connections/{connection_id}", **kwargs)
        return self._get_response_value(resp, as_json=True)

    def update_mcp_connection(
        self,
        connection_id: str,
        server_name: Optional[str] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        transport: Optional[str] = None,
        timeout: Optional[int] = None,
        enabled: Optional[bool] = None,
        description: Optional[str] = None,
        config: Optional[Dict] = None,
        **kwargs
    ) -> Dict:
        """
        更新 MCP 连接
        """
        data = {}
        if server_name is not None:
            data["server_name"] = server_name
        if args is not None:
            data["args"] = args
        if env is not None:
            data["env"] = env
        if cwd is not None:
            data["cwd"] = cwd
        if transport is not None:
            data["transport"] = transport
        if timeout is not None:
            data["timeout"] = timeout
        if enabled is not None:
            data["enabled"] = enabled
        if description is not None:
            data["description"] = description
        if config is not None:
            data["config"] = config
        
        resp = self.put(f"/api/v1/mcp_connections/{connection_id}", json=data, **kwargs)
        return self._get_response_value(resp, as_json=True)

    def delete_mcp_connection(self, connection_id: str, **kwargs) -> Dict:
        """
        删除 MCP 连接
        """
        resp = self.delete(f"/api/v1/mcp_connections/{connection_id}", **kwargs)
        return self._get_response_value(resp, as_json=True)

    def enable_mcp_connection(self, connection_id: str, **kwargs) -> Dict:
        """
        启用 MCP 连接
        """
        resp = self.post(f"/api/v1/mcp_connections/{connection_id}/enable", **kwargs)
        return self._get_response_value(resp, as_json=True)

    def disable_mcp_connection(self, connection_id: str, **kwargs) -> Dict:
        """
        禁用 MCP 连接
        """
        resp = self.post(f"/api/v1/mcp_connections/{connection_id}/disable", **kwargs)
        return self._get_response_value(resp, as_json=True)

    
    def search_mcp_connections(
        self,
        keyword: Optional[str] = None,
        server_type: Optional[str] = None,
        enabled: Optional[bool] = None,
        limit: int = 50,
        **kwargs
    ) -> Dict:
        """
        搜索 MCP 连接
        """
        data = {
            "keyword": keyword,
            "server_type": server_type,
            "enabled": enabled,
            "limit": limit,
        }
        resp = self.post("/api/v1/mcp_connections/search", json=data, **kwargs)
        return self._get_response_value(resp, as_json=True)

    def get_mcp_connections_by_server_name(self, server_name: str, **kwargs) -> Dict:
        """
        根据服务器名称获取 MCP 连接
        """
        resp = self.get(f"/api/v1/mcp_connections/server/{server_name}", **kwargs)
        return self._get_response_value(resp, as_json=True)

    def get_enabled_mcp_connections(self, **kwargs) -> Dict:
        """
        获取启用的 MCP 连接
        """
        resp = self.get("/api/v1/mcp_connections/enabled/list", **kwargs)
        return self._get_response_value(resp, as_json=True)

    

class AsyncApiRequest(ApiRequest):
    def __init__(
        self, base_url: str = api_address(), timeout: float = Settings.basic_settings.HTTPX_DEFAULT_TIMEOUT
    ):
        super().__init__(base_url, timeout)
        self._use_async = True


def check_error_msg(data: Union[str, dict, list], key: str = "errorMsg") -> str:
    """
    return error message if error occured when requests API
    """
    if isinstance(data, dict):
        if key in data:
            return data[key]
        if "code" in data and data["code"] != 200:
            return data["msg"]
    return ""


def check_success_msg(data: Union[str, dict, list], key: str = "msg") -> str:
    """
    return error message if error occured when requests API
    """
    if (
        isinstance(data, dict)
        and key in data
        and "code" in data
        and data["code"] == 200
    ):
        return data[key]
    return ""


def get_img_base64(file_name: str) -> str:
    """
    get_img_base64 used in streamlit.
    absolute local path not working on windows.
    """
    image = f"{Settings.basic_settings.IMG_DIR}/{file_name}"
    # 读取图片
    with open(image, "rb") as f:
        buffer = BytesIO(f.read())
        base_str = base64.b64encode(buffer.getvalue()).decode()
    return f"data:image/png;base64,{base_str}"


if __name__ == "__main__":
    api = ApiRequest()
    aapi = AsyncApiRequest()

    # with api.chat_chat("你好") as r:
    #     for t in r.iter_text(None):
    #         print(t)

    # r = api.chat_chat("你好", no_remote_api=True)
    # for t in r:
    #     print(t)

    # r = api.duckduckgo_search_chat("室温超导最新研究进展", no_remote_api=True)
    # for t in r:
    #     print(t)

    # print(api.list_knowledge_bases())
