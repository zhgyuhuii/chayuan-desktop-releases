"""DashScope (阿里云百炼) 原生 Embedding 客户端。

为什么不用 OpenAIEmbeddings 直接打:
- 百炼的 OpenAI 兼容端点 `https://dashscope.aliyuncs.com/compatible-mode/v1`
  只代理 `/chat/completions`,不代理 `/embeddings`。
- 实际调用 `OpenAIEmbeddings(api_base=...compat-mode/v1)` → 404
  "Unsupported model `text-embedding-v3` for OpenAI compatibility mode."
- 百炼的 embedding 必须走原生 endpoint:
  `POST https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding`

实现要点:
- 实现 langchain_core.embeddings.Embeddings 接口,可替换 OpenAIEmbeddings 不改 caller。
- 单批最多 10 条(v3) / 25 条(v2);自动按 batch_size 切片。
- 失败有结构化诊断(model / dim / texts_count / http_status / aliyun_code)。
- httpx 已是 chayuan-server 依赖,不引入新包;走 sync HTTP(embed_query 一般在
  worker 线程,不需要 async)。
"""
from __future__ import annotations

import logging
from typing import List, Optional

import httpx
from langchain_core.embeddings import Embeddings

logger = logging.getLogger("chayuan.embeddings.dashscope")

# DashScope 原生 embedding endpoint(2024+,/v3 / v2 通用)。
DASHSCOPE_EMBED_ENDPOINT = (
    "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
)

# 各模型的单批上限(超出会被服务端 400)。表里没有的模型按 v3 取保守值。
_BATCH_LIMITS = {
    "text-embedding-v3": 10,
    "text-embedding-v2": 25,
    "text-embedding-v1": 25,
}

# 默认输出维度(MRL — text-embedding-v3 支持 1024/768/512/256;不传走 1024)。
# KB 一旦建好 collection,后续必须用同样维度,因此这里强制可显式设置。
DEFAULT_DIM = 1024


class DashScopeEmbeddings(Embeddings):
    """DashScope text-embedding 原生客户端。

    与 OpenAIEmbeddings 接口签名兼容(embed_query / embed_documents)。
    """

    def __init__(
        self,
        *,
        model: str = "text-embedding-v3",
        api_key: str,
        base_url: Optional[str] = None,
        dimensions: Optional[int] = None,
        text_type_for_query: str = "query",
        text_type_for_document: str = "document",
        timeout: float = 30.0,
        max_retries: int = 2,
        proxy: Optional[str] = None,
    ) -> None:
        if not api_key:
            raise ValueError("DashScopeEmbeddings 需要 api_key(从 dashscope.console.aliyun.com 申请)")
        self.model = model
        self.api_key = api_key
        # base_url 允许覆盖(如 VPC 内网入口);默认走公网 endpoint。
        self.endpoint = base_url or DASHSCOPE_EMBED_ENDPOINT
        self.dimensions = int(dimensions) if dimensions else None
        self.text_type_for_query = text_type_for_query
        self.text_type_for_document = text_type_for_document
        self.timeout = timeout
        self.max_retries = max_retries
        self.proxy = proxy
        self._batch_limit = _BATCH_LIMITS.get(model, 10)

    # ------------------------------------------------------------------
    # langchain Embeddings 接口
    # ------------------------------------------------------------------

    def embed_query(self, text: str) -> List[float]:
        out = self._call([text], text_type=self.text_type_for_query)
        if not out:
            raise RuntimeError("DashScope 返回空 embedding (query)")
        return out[0]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        result: List[List[float]] = []
        for i in range(0, len(texts), self._batch_limit):
            chunk = texts[i : i + self._batch_limit]
            embs = self._call(chunk, text_type=self.text_type_for_document)
            if len(embs) != len(chunk):
                raise RuntimeError(
                    f"DashScope 批量 embed 返回数与输入数不一致 "
                    f"(in={len(chunk)} out={len(embs)} model={self.model})"
                )
            result.extend(embs)
        return result

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _call(self, texts: List[str], *, text_type: str) -> List[List[float]]:
        # 防御:DashScope 单条 8K token 上限;过长会被 400。这里仅做长度截断保护
        # (字符层级,精确计数交给 server)。8K token ≈ 16K UTF-8 字节,这里给 24K 字符余量。
        safe_texts = [(t or "")[:24000] for t in texts]
        params: dict = {"text_type": text_type}
        if self.dimensions:
            params["dimension"] = self.dimensions

        body = {
            "model": self.model,
            "input": {"texts": safe_texts},
            "parameters": params,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_exc: Optional[BaseException] = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout, proxy=self.proxy) as client:
                    resp = client.post(self.endpoint, json=body, headers=headers)
                if resp.status_code != 200:
                    # 让上层能看到 model + http_status + 阿里 code,避免"莫名其妙 404"
                    detail = ""
                    try:
                        detail = resp.json()
                    except Exception:  # noqa: BLE001
                        detail = resp.text[:500]
                    raise RuntimeError(
                        f"DashScope embed HTTP {resp.status_code} "
                        f"(model={self.model} dim={self.dimensions} n={len(safe_texts)}): {detail}"
                    )
                data = resp.json()
                # 标准响应:{"output": {"embeddings": [{"text_index":0,"embedding":[..]}, ...]}}
                emb_list = (data.get("output") or {}).get("embeddings") or []
                if not emb_list:
                    raise RuntimeError(
                        f"DashScope embed 响应缺 output.embeddings: {str(data)[:300]}"
                    )
                # 服务端不保证返回顺序,按 text_index 排序回原顺序
                emb_list = sorted(emb_list, key=lambda x: int(x.get("text_index", 0)))
                vecs = [list(map(float, e.get("embedding") or [])) for e in emb_list]
                if any(not v for v in vecs):
                    raise RuntimeError("DashScope embed 含空向量")
                return vecs
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exc = e
                if attempt < self.max_retries:
                    logger.info("DashScope embed 第 %d 次重试: %r", attempt + 1, e)
                    continue
                raise
            except Exception as e:  # noqa: BLE001
                last_exc = e
                # 4xx 类错误重试无意义,直接抛
                raise
        # 不会到这里
        raise last_exc or RuntimeError("DashScope embed unknown failure")
