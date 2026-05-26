"""自适应 query rewriter。

四种策略,可独立调用,也支持 ``auto`` 自动路由:

- **passthrough**:不改写,直接用原 query。免费、快、低风险。
- **rewrite**:LLM 把 query 改写得更适合向量检索(去口语化 / 加同义词 / 解代词)。
- **multi_query**:LLM 生成 N 个不同视角的改写,扩大召回。
- **hyde**:Hypothetical Document Embeddings — LLM 生成"理想答案"作为查询,
  embed 后做相似度检索。对短 query / 关键词稀疏场景特别有效(Gao et al. 2022)。
- **decompose**:把多跳 / 复合 query 分解为子问题(least-to-most prompting)。

auto 路由(基于 Microsoft / Towards Data Science 的工业模式):
    if len(query) < 12 OR contains pronoun → hyde
    elif looks complex / multiple intents  → multi_query
    else                                   → passthrough

设计契约:
- 任何策略失败(LLM 超时 / 配额耗尽 / 解析失败)→ 退回 passthrough,绝不阻塞。
- 输出永远包含 **原 query**(在第一位),改写产物追加。前端 trace 面板可展示。
- 模块本身不调向量库 — 它只产出 query 字符串列表。
"""
from __future__ import annotations

import logging
import hashlib
import json
import re
import time
from typing import List, Literal, Optional, Tuple

logger = logging.getLogger("chayuan.retrieval.rewriter")

RewriteStrategy = Literal["auto", "passthrough", "rewrite", "multi_query", "hyde", "decompose", "off"]

# ---------------------------------------------------------------------------
# 启发式判定:auto 路由用
# ---------------------------------------------------------------------------

# 中文代词 + 指示词 — 出现这些时,query 大概率依赖上下文,需要改写
_PRONOUNS_CN = {"它", "它们", "他", "她", "这", "那", "这个", "那个", "这些", "那些",
                "上述", "上面", "前面", "刚才", "之前", "此", "其"}
# 复杂查询启发:有多个问号 / 连词 / "并且" / "以及" → 可能要 decompose
_COMPLEX_HINTS = re.compile(r"(并且|以及|同时|还要|还有|另外|还包括|什么.*什么|哪些.*哪些)")
# 短查询阈值(中文字符 / 英文 token)
SHORT_QUERY_THRESHOLD = 12


def _has_pronoun(query: str) -> bool:
    return any(p in query for p in _PRONOUNS_CN)


def _looks_complex(query: str) -> bool:
    if query.count("?") + query.count("?") >= 2:
        return True
    return bool(_COMPLEX_HINTS.search(query))


def auto_route(query: str) -> RewriteStrategy:
    """决定一条 query 走哪种改写策略。"""
    q = (query or "").strip()
    if not q:
        return "passthrough"
    if len(q) < SHORT_QUERY_THRESHOLD or _has_pronoun(q):
        return "hyde"
    if _looks_complex(q):
        return "multi_query"
    return "passthrough"


# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

_REWRITE_PROMPT = """你是一个查询改写助手。把下面这条用户问题改写成更适合向量数据库检索的形式:
- 去除口语化、寒暄、限定词
- 显式补全代词指代
- 保留所有关键实体、数字、专有名词
- 只输出改写后的查询本身,不要任何解释或前缀

原始问题:{query}
改写后:"""

_MULTI_QUERY_PROMPT = """生成 {n} 个不同视角、不同表达方式的检索查询,目的是从向量数据库召回最全面的相关文档。
要求:
- 每行一条,共 {n} 行
- 不要编号、不要任何前缀(如"查询1:")
- 表达多样化:换词、变换句式、补充同义词
- 保留原意,不要无中生有

原始问题:{query}
改写列表:"""

_HYDE_PROMPT = """假设你已经知道答案,请用 1~3 句话写出针对下面这个问题的"理想答案"。
要求:
- 直接给答案,不要前缀、解释、提问、总结
- 包含问题里出现的关键实体 / 数字 / 名词
- 不要编造具体数据 — 用占位词"X"或泛指替代

问题:{query}
理想答案:"""

_DECOMPOSE_PROMPT = """把下面这条复杂问题分解为 2~4 个独立、原子的子问题,每个子问题都能独立检索。
要求:
- 每行一条子问题
- 不要编号、不要前缀
- 子问题加起来要能完整覆盖原问题
- 如果原问题已经足够简单,只输出原问题本身

原始问题:{query}
子问题:"""


# ---------------------------------------------------------------------------
# LLM 调用 + 解析
# ---------------------------------------------------------------------------

# auto / multi_query 默认生成数量
DEFAULT_N_VARIANTS = 3
# 单次 LLM 调用超时(秒);超时直接 fallback,不阻塞主路径
LLM_TIMEOUT = 6.0


def _cache_enabled(query: str) -> bool:
    try:
        from chayuan.settings import Settings
        bs = Settings.basic_settings
        return bool(getattr(bs, "SEMANTIC_CACHE_ENABLED", False)) and len(query or "") >= int(getattr(bs, "SEMANTIC_CACHE_MIN_LEN", 5) or 5)
    except Exception:
        return False


def _cache_key(query: str, strategy: str, model_name: Optional[str], n_variants: int) -> str:
    try:
        from chayuan.settings import Settings
        ns = getattr(Settings.basic_settings, "SEMANTIC_CACHE_NAMESPACE", "chayuan:semcache") or "chayuan:semcache"
    except Exception:
        ns = "chayuan:semcache"
    raw = json.dumps({
        "query": query,
        "strategy": strategy,
        "model": model_name or "",
        "n": n_variants,
    }, ensure_ascii=False, sort_keys=True)
    return f"{ns}:rewrite:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _cache_get(key: str) -> Optional[Tuple[List[str], dict]]:
    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("redis", "redis>=5.0,<6.0")
        import redis  # type: ignore
        from chayuan.settings import Settings
        url = (getattr(Settings.basic_settings, "REDIS_URL", "") or "").strip()
        if not url:
            return None
        raw = redis.Redis.from_url(url, decode_responses=True).get(key)
        if not raw:
            return None
        data = json.loads(raw)
        queries = data.get("queries")
        trace = data.get("trace")
        if isinstance(queries, list) and isinstance(trace, dict):
            trace = {**trace, "cache_hit": True}
            return [str(x) for x in queries], trace
    except Exception as e:  # noqa: BLE001
        logger.debug("rewriter cache get failed: %r", e)
    return None


def _cache_set(key: str, queries: List[str], trace: dict) -> None:
    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("redis", "redis>=5.0,<6.0")
        import redis  # type: ignore
        from chayuan.settings import Settings
        bs = Settings.basic_settings
        url = (getattr(bs, "REDIS_URL", "") or "").strip()
        if not url:
            return
        ttl = int(getattr(bs, "SEMANTIC_CACHE_TTL_SECONDS", 3600) or 3600)
        redis.Redis.from_url(url, decode_responses=True).set(
            key,
            json.dumps({"queries": queries, "trace": trace}, ensure_ascii=False),
            ex=ttl,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("rewriter cache set failed: %r", e)


def _call_llm(prompt: str, *, model_name: Optional[str], timeout: float = LLM_TIMEOUT) -> Optional[str]:
    """调一次 LLM,返回纯文本输出;任何异常返 None。"""
    if not model_name:
        # 让 get_ChatOpenAI 走默认级联;空字符串它自己处理
        model_name = ""
    try:
        from chayuan.server.utils import get_ChatOpenAI

        llm = get_ChatOpenAI(
            model_name=model_name or None,
            temperature=0.3,
            streaming=False,
            local_wrap=True,
            verbose=False,
        )
    except Exception as e:  # noqa: BLE001
        logger.info("rewriter get_ChatOpenAI failed: %r", e)
        return None
    # langchain BaseChatModel 不直接支持 timeout 参数透传;这里用阻塞调用,
    # 上层(retrieval pipeline)再用 asyncio.wait_for 套整体超时。
    try:
        out = llm.invoke(prompt)
        text = getattr(out, "content", None) or str(out)
        return (text or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.info("rewriter llm.invoke failed: %r", e)
        return None


def _parse_lines(text: str, *, max_lines: int) -> List[str]:
    """把 LLM 多行输出解析为 query 列表;过滤掉前缀编号 / 空行。"""
    if not text:
        return []
    lines: List[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        # 去除常见前缀:"1.", "1)", "(1)", "查询1:", "Q1:"
        s = re.sub(r"^[\s\-\*\(]*\d+[\.\)、:.]\s*", "", s)
        s = re.sub(r"^[查询子问题Q]+\d*[:.\-]\s*", "", s)
        s = s.strip("·•-—— \t\"'`")
        if s:
            lines.append(s)
        if len(lines) >= max_lines:
            break
    return lines


# ---------------------------------------------------------------------------
# 各策略实现
# ---------------------------------------------------------------------------

def rewrite(query: str, *, model_name: Optional[str] = None) -> List[str]:
    """单条改写。返 [original, rewritten] 两条;改写失败只返 [original]。"""
    out = _call_llm(_REWRITE_PROMPT.format(query=query), model_name=model_name)
    if not out:
        return [query]
    out = out.splitlines()[0].strip().strip("\"'`")
    if not out or out == query:
        return [query]
    return [query, out]


def multi_query(query: str, *, model_name: Optional[str] = None, n: int = DEFAULT_N_VARIANTS) -> List[str]:
    """生成 N 条多样化改写。返 [original, q1, q2, ...]。"""
    raw = _call_llm(_MULTI_QUERY_PROMPT.format(query=query, n=n), model_name=model_name)
    variants = _parse_lines(raw or "", max_lines=n)
    # 去重 + 过滤掉与原 query 完全相同的
    out: List[str] = [query]
    seen = {query}
    for v in variants:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def hyde(query: str, *, model_name: Optional[str] = None) -> List[str]:
    """生成假设答案,把它作为额外的检索 query。返 [original, hypothesis]。"""
    raw = _call_llm(_HYDE_PROMPT.format(query=query), model_name=model_name)
    hyp = (raw or "").strip()
    # HyDE 答案可能比较长,截断到 240 字以内(embed 模型 8K token 也只够这个量级,
    # 太长反而会把核心语义稀释)
    hyp = hyp.replace("\n", " ").strip()[:240]
    if not hyp or hyp == query:
        return [query]
    return [query, hyp]


def decompose(query: str, *, model_name: Optional[str] = None) -> List[str]:
    """把复杂 query 分解为子问题。返 [original, sub1, sub2, ...]。"""
    raw = _call_llm(_DECOMPOSE_PROMPT.format(query=query), model_name=model_name)
    subs = _parse_lines(raw or "", max_lines=4)
    out = [query]
    for s in subs:
        if s != query and s not in out:
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# 顶层入口
# ---------------------------------------------------------------------------

def run(
    query: str,
    *,
    strategy: RewriteStrategy = "auto",
    model_name: Optional[str] = None,
    n_variants: int = DEFAULT_N_VARIANTS,
) -> Tuple[List[str], dict]:
    """对一条 query 跑指定改写策略。

    返回 (queries, trace):
      - queries:[original, ...] 至少包含原 query
      - trace:{"strategy_used":..., "elapsed_ms":..., "n":...} 给前端展示
    """
    q = (query or "").strip()
    if not q:
        return [], {"strategy_used": "off", "elapsed_ms": 0, "n": 0}

    chosen = strategy
    if strategy == "auto":
        chosen = auto_route(q)
    if strategy == "off":
        return [q], {"strategy_used": "off", "elapsed_ms": 0, "n": 1}

    cache_key = _cache_key(q, str(chosen), model_name, n_variants)
    if _cache_enabled(q):
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    t0 = time.time()
    try:
        if chosen == "passthrough":
            out = [q]
        elif chosen == "rewrite":
            out = rewrite(q, model_name=model_name)
        elif chosen == "multi_query":
            out = multi_query(q, model_name=model_name, n=n_variants)
        elif chosen == "hyde":
            out = hyde(q, model_name=model_name)
        elif chosen == "decompose":
            out = decompose(q, model_name=model_name)
        else:
            out = [q]
    except Exception as e:  # noqa: BLE001
        logger.warning("rewriter %s failed, fallback passthrough: %r", chosen, e)
        out = [q]
        chosen = "passthrough"

    trace = {
        "strategy_used": chosen,
        "elapsed_ms": int((time.time() - t0) * 1000),
        "n": len(out),
    }
    if _cache_enabled(q):
        _cache_set(cache_key, out, trace)
    return out, trace
