import asyncio
import multiprocessing as mp
import os
import socket
import sys
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Generator,
    List,
    Literal,
    Optional,
    Tuple,
    Union,
)

import httpx
import openai
from fastapi import FastAPI
from langchain_core.tools import BaseTool
from langchain_core.embeddings import Embeddings
from langchain_openai.chat_models import ChatOpenAI
from langchain_openai.llms import OpenAI
from memoization import cached, CachingAlgorithmFlag

# 触发 langchain / openai 等三方库的兼容补丁(reasoning_content 回传 等),
# 必须在 ChatOpenAI import 之后但任何调用之前;放最早的 utils 文件即可。
from chayuan.server import compat as _compat  # noqa: F401

from chayuan.settings import CHAYUAN_ROOT, Settings, XF_MODELS_TYPES
from chayuan.server.pydantic_v2 import BaseModel, Field
from chayuan.utils import build_logger
import requests

logger = build_logger()

# auto_detect 非 xinference 时每个平台只打一次 WARNING，避免高频接口刷屏
_autodetect_unsupported_logged: set[tuple[str, str]] = set()


async def wrap_done(fn: Awaitable, event: asyncio.Event):
    """Wrap an awaitable with a event to signal when it's done or an exception is raised."""
    try:
        await fn
    except Exception as e:
        msg = f"Caught exception: {e}"
        logger.error(f"{e.__class__.__name__}: {msg}")
    finally:
        # Signal the aiter to stop.
        event.set()


def get_base_url(url):
    parsed_url = urlparse(url)  # 解析url
    base_url = '{uri.scheme}://{uri.netloc}/'.format(uri=parsed_url)  # 格式化基础url
    return base_url.rstrip('/')


# ---------------------------------------------------------------------------
# Runtime overrides:admin 在线开关 / 黑名单
# 落到 <CHAYUAN_ROOT>/platform_overrides.json,启动时 lazy load,改动 PATCH 即写盘。
# 形如 {"<platform_name>": {"enabled": false, "disabled_models": ["xxx"]}}
# ---------------------------------------------------------------------------

import json as _json
_PLATFORM_OVERRIDES_PATH = CHAYUAN_ROOT / "platform_overrides.json"
_platform_overrides: Optional[Dict[str, Dict]] = None


def _load_platform_overrides() -> Dict[str, Dict]:
    global _platform_overrides
    if _platform_overrides is not None:
        return _platform_overrides
    if _PLATFORM_OVERRIDES_PATH.is_file():
        try:
            data = _json.loads(_PLATFORM_OVERRIDES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _platform_overrides = {k: v for k, v in data.items() if isinstance(v, dict)}
                return _platform_overrides
        except Exception as e:  # noqa: BLE001
            logger.warning(f"读 {_PLATFORM_OVERRIDES_PATH} 失败,忽略 overrides:{e!r}")
    _platform_overrides = {}
    return _platform_overrides


def _save_platform_overrides() -> None:
    if _platform_overrides is None:
        return
    try:
        _PLATFORM_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PLATFORM_OVERRIDES_PATH.write_text(
            _json.dumps(_platform_overrides, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"写 {_PLATFORM_OVERRIDES_PATH} 失败:{e!r}")


def set_platform_override(name: str, **fields) -> Dict:
    """覆盖单个平台的字段(目前支持 enabled / disabled_models),立即生效 + 持久化。
    返回该平台合并 override 后的最新 dict(供 admin 端点回显)。"""
    overrides = _load_platform_overrides()
    cur = dict(overrides.get(name) or {})
    for k, v in fields.items():
        if v is None:
            cur.pop(k, None)
        else:
            cur[k] = v
    if cur:
        overrides[name] = cur
    else:
        overrides.pop(name, None)
    _save_platform_overrides()
    # 配置改动后,前面 detect 的 60s 缓存可能已陈旧 — 主动失效 ollama 那边
    try:
        detect_ollama_models.cache_clear()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    return get_config_platforms().get(name) or {}


def clear_platform_override(name: str) -> None:
    overrides = _load_platform_overrides()
    if name in overrides:
        del overrides[name]
        _save_platform_overrides()


def _load_env_files_once() -> None:
    """启动期把多路径 .env 注入环境变量（仅首次有副作用）。"""
    try:
        from dotenv import load_dotenv
        # CHAYUAN_ROOT 常为 .../chayuan_data，而用户习惯把 .env 放在仓库根目录；多路径加载避免 Key 未注入（401）
        _env_candidates = [
            CHAYUAN_ROOT / ".env",
            CHAYUAN_ROOT.parent / ".env",
            Path.cwd() / ".env",
        ]
        _loaded: set[Path] = set()
        for env_path in _env_candidates:
            try:
                resolved = env_path.resolve()
            except OSError:
                continue
            if resolved.is_file() and resolved not in _loaded:
                _loaded.add(resolved)
                load_dotenv(resolved, override=False)
    except ImportError:
        pass


def _safe_db_platforms() -> List[Dict]:
    """从 model_platform 表读全部平台 dict；DB 不可用 / 表不存在时返回 []。"""
    try:
        from chayuan.server.db.repository.model_platform_repository import (
            list_platforms,
        )
        return list_platforms()
    except Exception as e:  # noqa: BLE001
        # 表还没建（首次启动 / migration 未跑）—不影响 yaml 路径，安静返回 []
        logger.debug("DB model_platform 不可用，回退 yaml only：%r", e)
        return []


def _get_platform_version_safe() -> int:
    try:
        from chayuan.server.db.repository.model_platform_repository import (
            get_platform_version,
        )
        return get_platform_version()
    except Exception:  # noqa: BLE001
        return 0


def _merge_platforms(
    yaml_seed: List[Dict], db_rows: List[Dict], json_overrides: Dict[str, Dict],
) -> List[Dict]:
    """三层合并：yaml seed → JSON overrides → DB（最高优先）。

    DB 字段全量覆盖（如果 DB 有 row，整条记录就以 DB 为准），yaml 仅当 DB 没有同名时
    才作为基线生效；JSON 旧路径仍兼容（只覆盖 enabled / disabled_models 这种简单开关）。
    """
    by_name: Dict[str, Dict] = {}
    for d in yaml_seed:
        by_name[d["platform_name"]] = dict(d)

    # JSON overrides 叠加在 yaml 上（向后兼容老路径）
    for name, ov in json_overrides.items():
        if name in by_name:
            by_name[name].update(ov)

    # DB 行整体覆盖同名 yaml；yaml 没有的则追加
    for row in db_rows:
        name = row.get("platform_name")
        if not name:
            continue
        # extra / 时间戳不参与 chat 路径，过滤掉
        clean = {k: v for k, v in row.items() if k not in ("extra", "create_time", "update_time")}
        by_name[name] = clean

    return list(by_name.values())


# ---- 5s TTL 缓存：以 platform_version 为 key，写后立即失效 -----------------
_platform_cache_lock = threading.Lock() if False else None  # placeholder; cached() 自带锁


@cached(max_size=4, ttl=5, algorithm=CachingAlgorithmFlag.LRU)
def _resolved_platforms(version: int) -> List[Dict]:  # noqa: ARG001
    """实际合并 + .env 注入；缓存 5s，version 变了立即穿透。"""
    _load_env_files_once()
    yaml_seed = [m.model_dump() for m in Settings.model_settings.MODEL_PLATFORMS]
    db_rows = _safe_db_platforms()
    json_overrides = _load_platform_overrides()
    merged = _merge_platforms(yaml_seed, db_rows, json_overrides)
    for m in merged:
        # 百炼：DASHSCOPE_API_KEY 写在 .env 中，或 export 到环境变量
        if m.get("platform_name") == "bailian":
            env_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
            if env_key:
                m["api_key"] = env_key
    return merged


def get_config_platforms() -> Dict[str, Dict]:
    """获取已启用的模型平台。

    数据来源（按优先级从高到低）：
    1. ``model_platform`` DB 表（admin UI 在线编辑落库）
    2. ``platform_overrides.json``（旧版兼容：仅 enabled / disabled_models）
    3. ``model_settings.yaml`` 里的 ``MODEL_PLATFORMS`` seed

    缓存：5s TTL；写操作触发 ``bump_platform_version()`` 即时穿透。
    enabled=False 的平台会被从结果里过滤——chat / /v1/models 都看不到。
    """
    merged = _resolved_platforms(_get_platform_version_safe())
    return {m["platform_name"]: m for m in merged if m.get("enabled", True)}


def get_all_platforms_with_state() -> List[Dict]:
    """admin 视图：把全部平台（包括 enabled=False 被隐藏的）返回。

    新增 ``builtin_models`` 字段:从 ``PROVIDER_CATALOG`` 的 ``default_models``
    拿该厂商内置主流模型清单,前端据此判断哪些 model id 是"内置"
    (yaml seed / catalog 默认带的) — 不允许在 UI 里删除,删除按钮不渲染。
    用户自加的 model id 不在 ``builtin_models`` 里 → UI 渲染删除按钮。
    """
    # PROVIDER_CATALOG 在 import 时构造,这里 lazy import 避免 utils.py
    # 启动期循环依赖(config_panel.model_config 反过来会用 utils.py 的 helper)。
    try:
        from chayuan.server.config_panel.model_config import _PROVIDER_BY_ID
        _builtin_index: Dict[str, Dict[str, List[str]]] = {
            pid: dict(meta.default_models or {})
            for pid, meta in _PROVIDER_BY_ID.items()
        }
    except Exception:  # noqa: BLE001
        _builtin_index = {}

    merged = _resolved_platforms(_get_platform_version_safe())
    out: List[Dict] = []
    for d in merged:
        pname = d.get("platform_name") or ""
        out.append({
            "platform_name": pname,
            "platform_type": d.get("platform_type"),
            "api_base_url": d.get("api_base_url"),
            "api_key": d.get("api_key"),  # 让 admin 表单可回显
            "api_proxy": d.get("api_proxy"),
            "api_concurrencies": d.get("api_concurrencies"),
            "enabled": bool(d.get("enabled", True)),
            "auto_detect_model": bool(d.get("auto_detect_model", False)),
            "llm_models": list(d.get("llm_models") or []),
            "embed_models": list(d.get("embed_models") or []),
            "text2image_models": list(d.get("text2image_models") or []),
            "image2text_models": list(d.get("image2text_models") or []),
            "rerank_models": list(d.get("rerank_models") or []),
            "speech2text_models": list(d.get("speech2text_models") or []),
            "text2speech_models": list(d.get("text2speech_models") or []),
            "disabled_models": list(d.get("disabled_models") or []),
            "description": d.get("description") or "",
            "builtin_models": _builtin_index.get(pname, {}),
        })
    return out


@cached(max_size=10, ttl=60, algorithm=CachingAlgorithmFlag.LRU)
def detect_ollama_models(base_url: str) -> Dict[str, List[str]]:
    """Ollama auto-detect:GET <base>/api/tags 拿真实安装的模型清单。

    base_url 接受 ``http://host:port`` 或 ``http://host:port/v1`` 两种形式;
    后者剥掉 ``/v1`` 再拼 ``/api/tags``。

    Ollama 的 /api/tags 只返 ``name`` + ``size`` + ``modified_at``,**没有**模型类型字段;
    这里按名字做启发式分类。优先级依次:
      1. ``rerank`` / ``reranker`` / ``cross-encoder`` → rerank_models
         (必须**优先**于 embed,因为 ``bge-reranker-v2-m3`` / ``dengcao/bge-reranker-...``
          这种命名同时含 bge,如果 embed 优先匹配会被错分到嵌入模型)
      2. ``bge`` / ``embed`` / ``embedding`` / ``m3e`` / ``nomic-embed`` / ``mxbai-embed``
         / ``gte-`` / ``e5-`` / ``jina-embed`` → embed_models
      3. 其余 → llm_models

    分类错了不致命:用户在前端可以用「移到其他类型」按钮手动改正(2026-05 加)。
    本函数尽力分对,但毕竟是启发式,不要指望 100% 准。

    返回 dict 含三个 key(rerank_models 字段对齐 PlatformConfig);失败返回空。
    60s TTL 缓存,避免热路径反复打 Ollama。
    """
    parsed = urlparse(base_url)
    if not parsed.scheme:
        # 用户写 "127.0.0.1:11434" 之类不带 scheme:补 http
        base = "http://" + base_url
    else:
        base = f"{parsed.scheme}://{parsed.netloc}"
    url = f"{base.rstrip('/')}/api/tags"

    out: Dict[str, List[str]] = {
        "llm_models": [],
        "embed_models": [],
        "rerank_models": [],
    }
    try:
        resp = requests.get(url, timeout=3.0)
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"detect_ollama_models({url}) 失败:{type(e).__name__}: {e}")
        return out

    rerank_kws = ("rerank", "reranker", "re-rank", "cross-encoder")
    embed_kws = (
        "bge", "embed", "embedding", "m3e", "nomic-embed", "mxbai-embed",
        "gte-", "e5-", "jina-embed",
    )

    for item in data.get("models", []) or []:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        low = name.lower()
        # 顺序敏感:rerank 关键词必须先匹配,避免被 bge 误吞
        if any(kw in low for kw in rerank_kws):
            out["rerank_models"].append(name)
        elif any(kw in low for kw in embed_kws):
            out["embed_models"].append(name)
        else:
            out["llm_models"].append(name)
    return out


@cached(max_size=10, ttl=60, algorithm=CachingAlgorithmFlag.LRU)
def detect_xf_models(xf_url: str) -> Dict[str, List[str]]:
    '''
    use cache for xinference model detecting to avoid:
    - too many requests in short intervals
    - multiple requests to one platform for every model
    the cache will be invalidated after one minute
    '''
    xf_model_type_maps = {
        "llm_models": lambda xf_models: [k for k, v in xf_models.items()
                                         if "LLM" == v["model_type"]
                                         and "vision" not in v["model_ability"]],
        "embed_models": lambda xf_models: [k for k, v in xf_models.items()
                                           if "embedding" == v["model_type"]],
        "text2image_models": lambda xf_models: [k for k, v in xf_models.items()
                                                if "image" == v["model_type"]],
        "image2image_models": lambda xf_models: [k for k, v in xf_models.items()
                                                 if "image" == v["model_type"]],
        "image2text_models": lambda xf_models: [k for k, v in xf_models.items()
                                                if "LLM" == v["model_type"]
                                                and "vision" in v["model_ability"]],
        "rerank_models": lambda xf_models: [k for k, v in xf_models.items()
                                            if "rerank" == v["model_type"]],
        "speech2text_models": lambda xf_models: [k for k, v in xf_models.items()
                                                 if v.get(list(XF_MODELS_TYPES["speech2text"].keys())[0])
                                                 in XF_MODELS_TYPES["speech2text"].values()],
        "text2speech_models": lambda xf_models: [k for k, v in xf_models.items()
                                                 if v.get(list(XF_MODELS_TYPES["text2speech"].keys())[0])
                                                 in XF_MODELS_TYPES["text2speech"].values()],
    }
    models = {}
    try:
        from xinference_client import RESTfulClient as Client
        xf_client = Client(xf_url)
        xf_models = xf_client.list_models()
        for m_type, filter in xf_model_type_maps.items():
            models[m_type] = filter(xf_models)
    except ImportError:
        logger.warning('auto_detect_model needs xinference-client installed. '
                       'Please try "pip install xinference-client". ')
    except requests.exceptions.ConnectionError:
        logger.warning(f"cannot connect to xinference host: {xf_url}, please check your configuration.")
    except Exception as e:
        logger.warning(f"error when connect to xinference server({xf_url}): {e}")
    return models


def get_config_models(
        model_name: str = None,
        model_type: Optional[Literal[
            "llm", "embed", "text2image", "image2image", "image2text", "rerank", "speech2text", "text2speech"
        ]] = None,
        platform_name: str = None,
) -> Dict[str, Dict]:
    """
    获取配置的模型列表，返回值为:
    {model_name: {
        "platform_name": xx,
        "platform_type": xx,
        "model_type": xx,
        "model_name": xx,
        "api_base_url": xx,
        "api_key": xx,
        "api_proxy": xx,
    }}
    """
    result = {}
    if model_type is None:
        model_types = [
            "llm_models",
            "embed_models",
            "text2image_models",
            "image2image_models",
            "image2text_models",
            "rerank_models",
            "speech2text_models",
            "text2speech_models",
        ]
    else:
        model_types = [f"{model_type}_models"]

    for m in list(get_config_platforms().values()):
        if platform_name is not None:
            _pn = str(m.get("platform_name") or "")
            # 遗留的裸 ``local::`` 命名空间(老前端 / 旧持久化状态留下的)实际
            # 对应 local-chat / local-embedding 等 sidecar 平台 —— 容忍匹配,
            # 否则 ``local::<model>`` 永远命中不到 → ModelNotConfigured。
            if platform_name == "local":
                if not (_pn == "local" or _pn.startswith("local-")):
                    continue
            elif platform_name != _pn:
                continue

        if m.get("auto_detect_model"):
            ptype = m.get("platform_type")
            if ptype == "xinference":
                xf_url = get_base_url(m.get("api_base_url"))
                xf_models = detect_xf_models(xf_url)
                for m_type in model_types:
                    m[m_type] = xf_models.get(m_type, [])
            elif ptype == "ollama":
                # Ollama:GET /api/tags;失败时不覆盖配置里的 llm_models / embed_models
                ol_models = detect_ollama_models(m.get("api_base_url") or "")
                for m_type in ("llm_models", "embed_models"):
                    if m_type not in model_types:
                        continue
                    detected = ol_models.get(m_type, [])
                    if detected:
                        # 与配置里手填的合并去重,detected 优先(后续仍可能被用户覆盖)
                        existing = m.get(m_type) or []
                        merged = list(dict.fromkeys(list(existing) + list(detected)))
                        m[m_type] = merged
            else:
                _key = (str(m.get("platform_name")), str(ptype))
                if _key not in _autodetect_unsupported_logged:
                    _autodetect_unsupported_logged.add(_key)
                    logger.warning(
                        f"auto_detect_model not supported for {ptype} yet; "
                        f"using llm_models / embed_models from config for platform {m.get('platform_name')}."
                    )

        # 模型级黑名单:即使 auto_detect 检测到,也从结果里剔除
        blacklist = set(m.get("disabled_models") or [])

        for m_type in model_types:
            models = m.get(m_type, [])
            if models == "auto":
                logger.warning("you should not set `auto` without auto_detect_model=True")
                continue
            elif not models:
                continue
            for m_name in models:
                if m_name in blacklist:
                    continue
                if model_name is None or model_name == m_name:
                    result[m_name] = {
                        "platform_name": m.get("platform_name"),
                        "platform_type": m.get("platform_type"),
                        "model_type": m_type.split("_")[0],
                        "model_name": m_name,
                        "api_base_url": m.get("api_base_url"),
                        "api_key": m.get("api_key"),
                        "api_proxy": m.get("api_proxy"),
                    }
    return result


def get_model_info(
        model_name: str = None, platform_name: str = None, multiple: bool = False
) -> Dict:
    """
    获取配置的模型信息，主要是 api_base_url, api_key
    如果指定 multiple=True，则返回所有重名模型；否则仅返回第一个

    79 题:统一解析 ``model_name`` 中的 ``::`` 平台命名空间。
    -----------------------------------------------------------------
    chayuan-client 在跨平台同名时会传 ``deepseek::deepseek-v4-flash``,
    避免被 ``get_config_models`` 的 dict 覆盖路由到错误平台(如 baidu-qianfan)。
    在 ``get_model_info`` 这一层做解析 → ``get_ChatOpenAI`` /
    ``get_model_client`` 等所有调用方自动受益,不必各自再做一次 split。

    向后兼容:不含 ``::`` 的旧 model_name 行为不变。
    """
    if isinstance(model_name, str) and "::" in model_name:
        ns_platform, ns_model = model_name.split("::", 1)
        ns_platform = ns_platform.strip()
        ns_model = ns_model.strip()
        if ns_platform and ns_model:
            model_name = ns_model
            # 显式传入的 platform_name 优先;否则用命名空间里的
            platform_name = platform_name or ns_platform
    result = get_config_models(model_name=model_name, platform_name=platform_name)
    if len(result) > 0:
        if multiple:
            return result
        else:
            return list(result.values())[0]
    else:
        return {}


def get_default_llm():
    available_llms = list(get_config_models(model_type="llm").keys())
    if Settings.model_settings.DEFAULT_LLM_MODEL in available_llms:
        return Settings.model_settings.DEFAULT_LLM_MODEL
    if available_llms:
        logger.warning(
            f"default llm model {Settings.model_settings.DEFAULT_LLM_MODEL} is not found in available llms, "
            f"using {available_llms[0]} instead"
        )
        return available_llms[0]
    logger.error(
        "No LLM in model_settings: set MODEL_PLATFORMS.*.llm_models or fix auto_detect_model / platform config."
    )
    return Settings.model_settings.DEFAULT_LLM_MODEL


def get_default_embedding():
    """决定该用哪个 embedding 模型。

    解析顺序:
        1. ``capability_router.resolve_model("embedding")``
           — UI 上"默认模型选择 → 文本嵌入"配置(全局唯一真源)
        2. ``Settings.model_settings.DEFAULT_EMBEDDING_MODEL``
           — settings yaml 老配置(向后兼容)
        3. 已加载的可用 embed 模型第一个(兜底)
    """
    available_embeddings = list(get_config_models(model_type="embed").keys())

    # 1. capability_router 优先(UI 最新配置)— 用户在"默认模型选择"配什么用什么
    try:
        from chayuan.server.capability_router import resolve_model
        router_pick = resolve_model("embedding")
        if router_pick and router_pick in available_embeddings:
            return router_pick
        if router_pick:
            logger.debug(
                "[get_default_embedding] router 配的 %s 不在可用列表 %s",
                router_pick, available_embeddings,
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("[get_default_embedding] router lookup failed: %r", e)

    # 2. Settings 老配置
    if Settings.model_settings.DEFAULT_EMBEDDING_MODEL in available_embeddings:
        return Settings.model_settings.DEFAULT_EMBEDDING_MODEL

    # 3. 兜底:已加载的第一个可用
    if available_embeddings:
        logger.warning(
            f"default embedding model "
            f"{Settings.model_settings.DEFAULT_EMBEDDING_MODEL} 不在可用列表,"
            f"使用 {available_embeddings[0]}"
        )
        return available_embeddings[0]
    logger.error(
        "No embedding model in model_settings: set MODEL_PLATFORMS.*.embed_models or fix platform config."
    )
    return Settings.model_settings.DEFAULT_EMBEDDING_MODEL


def get_history_len() -> int:
    return (Settings.model_settings.HISTORY_LEN or
            Settings.model_settings.LLM_MODEL_CONFIG["action_model"]["history_len"])


class ModelNotConfigured(RuntimeError):
    """模型不在 MODEL_PLATFORMS.*.llm_models 中(也没 auto_detect 出来)。
    Chat 链路应捕这个抛人话给前端,而不是让 NoneType.invoke 透传。"""

    def __init__(self, model_name: str):
        self.model_name = model_name
        super().__init__(
            f"模型 '{model_name}' 未在 model_settings.yaml 的 MODEL_PLATFORMS.*.llm_models 中配置;"
            f"或对应平台未启用 / 未连接成功。请把模型加到平台配置,或对 ollama/xinference 开启 auto_detect_model。"
        )


class ModelLoadFailed(RuntimeError):
    """模型已配置但 ChatOpenAI 构造失败(凭证 / 网络 / SDK 校验等)。
    用 cause 透传原异常,前端可拿到根因(如 Pydantic ValidationError 详情)。"""

    def __init__(self, model_name: str, cause: BaseException):
        self.model_name = model_name
        self.cause = cause
        super().__init__(
            f"模型 '{model_name}' 加载失败:{type(cause).__name__}: {cause}"
        )


def get_ChatOpenAI(
        model_name: str | None = None,
        temperature: float = Settings.model_settings.TEMPERATURE,
        max_tokens: int = Settings.model_settings.MAX_TOKENS,
        streaming: bool = True,
        callbacks: List[Callable] = [],
        verbose: bool = True,
        local_wrap: bool = False,  # use local wrapped api
        **kwargs: Any,
) -> ChatOpenAI:
    """构造 ChatOpenAI 实例。

    错误语义:
      - 模型不在配置(或对应平台被禁用) → 抛 ``ModelNotConfigured``
      - 模型在配置但 ChatOpenAI 构造抛(凭证/网络/SDK 校验)→ 抛 ``ModelLoadFailed`` 携 cause
    历史调用方习惯 None 返回值的,显式接 try/except 即可;新代码请按异常处理。
    """
    resolved_model_name = model_name or get_default_llm()
    model_info = get_model_info(resolved_model_name)
    if not model_info:
        logger.error(
            f"model '{resolved_model_name}' not in MODEL_PLATFORMS.*.llm_models. "
            f"在 model_settings.yaml 里把它加到对应平台的 llm_models 列表,或开启该平台的 auto_detect_model。"
        )
        raise ModelNotConfigured(resolved_model_name)
    # 79 题:剥 ``platform::model`` 命名空间,实际发给 OpenAI API 的必须是裸 model_id
    if "::" in resolved_model_name:
        resolved_model_name = resolved_model_name.split("::", 1)[1].strip()

    # P2：把 MetricsCallbackHandler 追加到 callbacks，统计 QPS / latency / 错误率。
    # 不覆盖调用方传入的 callbacks；指标关闭或 LangChain 缺失时 handler 为 None。
    effective_callbacks = list(callbacks or [])
    try:
        from chayuan.server.observability.llm_callback import build_metrics_handler
        _metrics_handler = build_metrics_handler()
        if _metrics_handler is not None:
            effective_callbacks.append(_metrics_handler)
    except Exception:  # noqa: BLE001
        pass

    params = dict(
        streaming=streaming,
        verbose=verbose,
        callbacks=effective_callbacks,
        model_name=resolved_model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )
    # remove paramters with None value to avoid openai validation error
    for k in list(params):
        if params[k] is None:
            params.pop(k)

    try:
        if local_wrap:
            params.update(
                openai_api_base=f"{api_address()}/v1",
                openai_api_key="EMPTY",
            )
        else:
            params.update(
                openai_api_base=model_info.get("api_base_url"),
                openai_api_key=model_info.get("api_key"),
            )
            _proxy = (model_info.get("api_proxy") or "").strip()
            if _proxy:
                params["openai_proxy"] = _proxy
        model = ChatOpenAI(**params)
    except Exception as e:
        logger.exception(
            f"failed to create ChatOpenAI for model: {resolved_model_name}. "
            f"{type(e).__name__}: {e}. "
            f"api_base={params.get('openai_api_base')!r} key_set={bool(params.get('openai_api_key'))}"
        )
        # 不再 return None — 把根因透传出去,避免上层把"加载失败"误报成"未配置"
        raise ModelLoadFailed(resolved_model_name, e) from e
    return model


def get_ChatPlatformAIParams(
        model_name: str | None = None,
        temperature: float = Settings.model_settings.TEMPERATURE,
        max_tokens: int = Settings.model_settings.MAX_TOKENS,
        streaming: bool = True,
        callbacks: List[Callable] = [],
        verbose: bool = True,
        local_wrap: bool = False,  # use local wrapped api
        **kwargs: Any,
) -> Dict:
    resolved_model_name = model_name or get_default_llm()
    model_info = get_model_info(resolved_model_name)
    if not model_info:
        raise ValueError(f"cannot find model info for model: {resolved_model_name}")
    # 79 题:同 get_ChatOpenAI,剥 ``platform::model`` 命名空间
    if "::" in resolved_model_name:
        resolved_model_name = resolved_model_name.split("::", 1)[1].strip()

    params = dict(
        streaming=streaming,
        verbose=verbose,
        callbacks=callbacks,
        model=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )
    # remove paramters with None value to avoid openai validation error
    for k in list(params):
        if params[k] is None:
            params.pop(k)

    try:
        params.update(
            api_base=model_info.get("api_base_url"),
            api_key=model_info.get("api_key"),
        )
        _proxy = (model_info.get("api_proxy") or "").strip()
        if _proxy:
            params["proxy"] = _proxy
        return params
    except Exception as e:
        logger.exception(f"failed to create for model: {model_name}.")
        return {}


def get_OpenAI(
        model_name: str,
        temperature: float,
        max_tokens: int = Settings.model_settings.MAX_TOKENS,
        streaming: bool = True,
        echo: bool = True,
        callbacks: List[Callable] = [],
        verbose: bool = True,
        local_wrap: bool = False,  # use local wrapped api
        **kwargs: Any,
) -> OpenAI:
    # TODO: 从API获取模型信息
    model_info = get_model_info(model_name)
    params = dict(
        streaming=streaming,
        verbose=verbose,
        callbacks=callbacks,
        model_name=resolved_model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        echo=echo,
        **kwargs,
    )
    try:
        if local_wrap:
            params.update(
                openai_api_base=f"{api_address()}/v1",
                openai_api_key="EMPTY",
            )
        else:
            params.update(
                openai_api_base=model_info.get("api_base_url"),
                openai_api_key=model_info.get("api_key"),
            )
            _proxy = (model_info.get("api_proxy") or "").strip()
            if _proxy:
                params["openai_proxy"] = _proxy
        model = OpenAI(**params)
    except Exception as e:
        logger.exception(f"failed to create OpenAI for model: {model_name}.")
        model = None
    return model


def _is_dashscope_platform(model_info: Dict) -> bool:
    """判断是不是阿里百炼 / DashScope。

    platform_type 在 catalog 里被登记为 'openai'(因为 chat 走 OpenAI 兼容),
    所以不能只看 platform_type;看 platform_name 或 api_base_url 域名最稳。
    """
    name = (model_info.get("platform_name") or "").lower()
    if name in ("bailian", "dashscope", "aliyun-dashscope"):
        return True
    base = (model_info.get("api_base_url") or "").lower()
    return "dashscope.aliyuncs.com" in base


def _normalize_ollama_base(api_base_url: Optional[str]) -> str:
    """把任意形态的 Ollama api_base_url 收成 OllamaEmbeddings 要的 root URL。

    catalog 里登记的是 ``http://127.0.0.1:11434/v1``(给 chat 走 OpenAI 兼容用),
    但 OllamaEmbeddings 走原生 ``/api/embed``,要的是 ``http://127.0.0.1:11434``。
    .replace('/v1', '') 会误伤 ``/v1xx`` 这种,且不处理尾斜杠;这里规范化一遍。
    """
    base = (api_base_url or "http://127.0.0.1:11434").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base


def get_Embeddings(
        embed_model: str = None,
        local_wrap: bool = False,  # use local wrapped api
) -> Embeddings:
    """构造一个 langchain Embeddings 实例。

    路由顺序(短路):
    1. local_wrap=True → 走 chayuan-server 自己代理出来的 /v1 endpoint(LocalAI 形态)。
    2. 阿里百炼 / DashScope → DashScopeEmbeddings 原生客户端
       (compatible-mode/v1 不代理 /embeddings,直接用就 404)。
    3. platform_type == 'ollama' → 原生 /api/embed,base_url 规范化掉 /v1 后缀。
    4. platform_type == 'zhipuai' → ZhipuAI 原生包装。
    5. platform_type == 'openai' → OpenAIEmbeddings(deepseek/openrouter/jina/cohere/...
       这些 OpenAI 兼容平台都默认走这条;真实兼容性由各平台自己保证)。
    6. 其它(custom/xinference/lm-studio/vllm/...) → LocalAIEmbeddings 兜底,
       依赖标准 /v1/embeddings 路径。
    """
    try:
        from langchain_ollama import OllamaEmbeddings
    except ImportError:
        from langchain_community.embeddings import OllamaEmbeddings
    from langchain_openai import OpenAIEmbeddings

    from chayuan.server.localai_embeddings import (
        LocalAIEmbeddings,
    )

    # 单机版 short-circuit:CHAYUAN_ONNX_EMBED_DIR 配置就走本地 ONNX,
    # 完全无视 platform 路由 — 因为本地 ONNX 不属于任何 HTTP platform,且单机
    # 用户没配 ollama/openai 时这是唯一可用 embedding。
    # 只有 local_wrap=False(普通业务调用)才走;local_wrap=True 时上层意图明确
    # 要走 chayuan-server 自己的 /v1 代理,绕开。
    if not local_wrap:
        try:
            from chayuan.server.embeddings.onnx_local import try_get_local_onnx_embeddings
            _onnx = try_get_local_onnx_embeddings()
            if _onnx is not None:
                # ! 改 INFO 级:之前 DEBUG 默认不打,导致 frozen 装机版 ONNX
                # short-circuit 命中(自动扫到 bundled bge-m3/onnx/ 子目录)
                # 但里面缺 model.onnx 时,_embed_one 静默返空 list, KB
                # vector_store 写 0 向量,排查时根本看不到 "走的是 ONNX 路径"
                # 这条线索。INFO 级别让默认日志就能看到选了哪条 embed 实现。
                logger.info(
                    "[get_Embeddings] 短路:使用本地 ONNX (model=%r). "
                    "如果后续 embed_documents 返空 list, 大概率是这条路径下 "
                    "model.onnx 缺失。",
                    embed_model,
                )
                return _onnx
        except Exception as e:  # noqa: BLE001
            logger.debug("[get_Embeddings] ONNX short-circuit 探测失败,继续走 HTTP 路由: %r", e)

    embed_model = embed_model or get_default_embedding()
    model_info = get_model_info(model_name=embed_model) or {}
    params = dict(model=embed_model)
    try:
        if local_wrap:
            params.update(
                openai_api_base=f"{api_address()}/v1",
                openai_api_key="EMPTY",
            )
            return LocalAIEmbeddings(**params)

        params.update(
            openai_api_base=model_info.get("api_base_url"),
            openai_api_key=model_info.get("api_key"),
        )
        _proxy = (model_info.get("api_proxy") or "").strip()
        if _proxy:
            params["openai_proxy"] = _proxy

        if _is_dashscope_platform(model_info):
            from chayuan.server.dashscope_embeddings import DashScopeEmbeddings
            return DashScopeEmbeddings(
                model=embed_model,
                api_key=model_info.get("api_key") or "",
                # base_url 留空 → 走默认公网 endpoint;catalog 里登记的是
                # compatible-mode/v1,这条对 embedding 不能用,故不透传。
                proxy=_proxy or None,
            )
        if model_info.get("platform_type") == "ollama":
            return OllamaEmbeddings(
                base_url=_normalize_ollama_base(model_info.get("api_base_url")),
                model=embed_model,
            )
        if model_info.get("platform_type") == "zhipuai":
            from langchain_chayuan.embeddings.zhipuai import ZhipuAIEmbeddings
            return ZhipuAIEmbeddings(
                base_url=model_info.get("api_base_url"),
                api_key=model_info.get("api_key"),
                zhipuai_proxy=model_info.get("api_proxy"),
                model=embed_model,
            )
        if model_info.get("platform_type") == "openai":
            return OpenAIEmbeddings(**params)
        return LocalAIEmbeddings(**params)
    except Exception as e:
        logger.exception(f"failed to create Embeddings for model: {embed_model}.")
        # 错误翻译:LangChain/OpenAI SDK 在 api_key=None 时抛 "OPENAI_API_KEY not set"
        # 这条信息严重误导用户去配 OpenAI 云端 key,但 90% 实际场景是:
        #   - 本地 embedding sidecar 起不来(常见:装的是 *.safetensors,llama-server 只吃 GGUF)
        #   - 或 MODEL_PLATFORMS.*.embed_models 没把这个 model 注册进来
        # 翻译成可执行的指引,让用户看到真根因 + 三条排查路径。
        msg = str(e)
        if "OPENAI_API_KEY" in msg or "openai_api_key" in msg or "api_key" in msg.lower():
            raise RuntimeError(
                f"embedding 模型 '{embed_model}' 不可用:本地 embedding 服务未就绪,"
                f"且 MODEL_PLATFORMS 也没配置可用云端 embedding。\n"
                f"常见原因 + 排查:\n"
                f"  1) 本地模型服务面板里 embedding 行状态是不是 ready?"
                f"如果是 failed,看 last_error。\n"
                f"  2) 模型格式是不是 GGUF 量化版?llama-server 只吃 *.gguf,"
                f"装 *.safetensors 整仓 sidecar 起不来。"
                f"推荐:gpustack/bge-m3-GGUF 里的 .gguf 单文件。\n"
                f"  3) 不想本地跑,可以在设置里配一个云端 embedding"
                f"(OpenAI / 阿里百炼 / 智谱),MODEL_PLATFORMS 里 embed_models 注册。\n"
                f"(原始错误已 traceback 进 server log:{type(e).__name__}: {e})"
            ) from e
        raise e


def check_embed_model(embed_model: str = None) -> Tuple[bool, str]:
    '''
    check weather embed_model accessable, use default embed model if None
    '''
    embed_model = embed_model or get_default_embedding()
    embeddings = get_Embeddings(embed_model=embed_model)
    try:
        embeddings.embed_query("this is a test")
        return True, ""
    except Exception as e:
        msg = f"failed to access embed model '{embed_model}': {e}"
        logger.error(msg)
        return False, msg


def get_OpenAIClient(
        platform_name: str = None,
        model_name: str = None,
        is_async: bool = True,
) -> Union[openai.Client, openai.AsyncClient]:
    """
    construct an openai Client for specified platform or model
    """
    if platform_name is None:
        platform_info = get_model_info(
            model_name=model_name, platform_name=platform_name
        )
        if platform_info is None:
            raise RuntimeError(
                f"cannot find configured platform for model: {model_name}"
            )
        platform_name = platform_info.get("platform_name")
    platform_info = get_config_platforms().get(platform_name)
    assert platform_info, f"cannot find configured platform: {platform_name}"
    params = {
        "base_url": platform_info.get("api_base_url"),
        "api_key": platform_info.get("api_key"),
    }
    httpx_params = {}
    if api_proxy := platform_info.get("api_proxy"):
        httpx_params = {
            "proxies": api_proxy,
            "transport": httpx.HTTPTransport(local_address="0.0.0.0"),
        }

    if is_async:
        if httpx_params:
            params["http_client"] = httpx.AsyncClient(**httpx_params)
        return openai.AsyncClient(**params)
    else:
        if httpx_params:
            params["http_client"] = httpx.Client(**httpx_params)
        return openai.Client(**params)


class MsgType:
    TEXT = 1
    IMAGE = 2
    AUDIO = 3
    VIDEO = 4


class BaseResponse(BaseModel):
    code: int = Field(200, description="API status code")
    msg: str = Field("success", description="API status message")
    data: Any = Field(None, description="API data")

    class Config:
        json_schema_extra = {
            "example": {
                "code": 200,
                "msg": "success",
            }
        }


class ListResponse(BaseResponse):
    data: List[Any] = Field(..., description="List of data")

    class Config:
        json_schema_extra = {
            "example": {
                "code": 200,
                "msg": "success",
                "data": ["doc1.docx", "doc2.pdf", "doc3.txt"],
            }
        }


class ChatMessage(BaseModel):
    question: str = Field(..., description="Question text")
    response: str = Field(..., description="Response text")
    history: List[List[str]] = Field(..., description="History text")
    source_documents: List[str] = Field(
        ..., description="List of source documents and their scores"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "question": "工伤保险如何办理？",
                "response": "根据已知信息，可以总结如下：\n\n1. 参保单位为员工缴纳工伤保险费，以保障员工在发生工伤时能够获得相应的待遇。\n"
                            "2. 不同地区的工伤保险缴费规定可能有所不同，需要向当地社保部门咨询以了解具体的缴费标准和规定。\n"
                            "3. 工伤从业人员及其近亲属需要申请工伤认定，确认享受的待遇资格，并按时缴纳工伤保险费。\n"
                            "4. 工伤保险待遇包括工伤医疗、康复、辅助器具配置费用、伤残待遇、工亡待遇、一次性工亡补助金等。\n"
                            "5. 工伤保险待遇领取资格认证包括长期待遇领取人员认证和一次性待遇领取人员认证。\n"
                            "6. 工伤保险基金支付的待遇项目包括工伤医疗待遇、康复待遇、辅助器具配置费用、一次性工亡补助金、丧葬补助金等。",
                "history": [
                    [
                        "工伤保险是什么？",
                        "工伤保险是指用人单位按照国家规定，为本单位的职工和用人单位的其他人员，缴纳工伤保险费，"
                        "由保险机构按照国家规定的标准，给予工伤保险待遇的社会保险制度。",
                    ]
                ],
                "source_documents": [
                    "出处 [1] 广州市单位从业的特定人员参加工伤保险办事指引.docx：\n\n\t"
                    "( 一)  从业单位  (组织)  按“自愿参保”原则，  为未建 立劳动关系的特定从业人员单项参加工伤保险 、缴纳工伤保 险费。",
                    "出处 [2] ...",
                    "出处 [3] ...",
                ],
            }
        }


def run_async(cor):
    """
    在同步环境中运行异步代码.
    """
    try:
        loop = asyncio.get_event_loop()
    except:
        loop = asyncio.new_event_loop()
    return loop.run_until_complete(cor)


def iter_over_async(ait, loop=None):
    """
    将异步生成器封装成同步生成器.
    """
    ait = ait.__aiter__()

    async def get_next():
        try:
            obj = await ait.__anext__()
            return False, obj
        except StopAsyncIteration:
            return True, None

    if loop is None:
        try:
            loop = asyncio.get_event_loop()
        except:
            loop = asyncio.new_event_loop()

    while True:
        done, obj = loop.run_until_complete(get_next())
        if done:
            break
        yield obj


def MakeFastAPIOffline(
        app: FastAPI,
        static_dir=Path(__file__).parent / "api_server" / "static",
        static_url="/static-offline-docs",
        docs_url: Optional[str] = "/docs",
        redoc_url: Optional[str] = "/redoc",
) -> None:
    """patch the FastAPI obj that doesn't rely on CDN for the documentation page"""
    from fastapi import Request
    from fastapi.openapi.docs import (
        get_redoc_html,
        get_swagger_ui_html,
        get_swagger_ui_oauth2_redirect_html,
    )
    from fastapi.staticfiles import StaticFiles
    from starlette.responses import HTMLResponse

    openapi_url = app.openapi_url
    swagger_ui_oauth2_redirect_url = app.swagger_ui_oauth2_redirect_url

    def remove_route(url: str) -> None:
        """
        remove original route from app
        """
        index = None
        for i, r in enumerate(app.routes):
            if r.path.lower() == url.lower():
                index = i
                break
        if isinstance(index, int):
            app.routes.pop(index)

    # Set up static file mount
    app.mount(
        static_url,
        StaticFiles(directory=Path(static_dir).as_posix()),
        name="static-offline-docs",
    )

    if docs_url is not None:
        remove_route(docs_url)
        remove_route(swagger_ui_oauth2_redirect_url)

        # Define the doc and redoc pages, pointing at the right files
        @app.get(docs_url, include_in_schema=False)
        async def custom_swagger_ui_html(request: Request) -> HTMLResponse:
            root = request.scope.get("root_path")
            favicon = f"{root}{static_url}/favicon.png"
            return get_swagger_ui_html(
                openapi_url=f"{root}{openapi_url}",
                title=app.title + " - Swagger UI",
                oauth2_redirect_url=swagger_ui_oauth2_redirect_url,
                swagger_js_url=f"{root}{static_url}/swagger-ui-bundle.js",
                swagger_css_url=f"{root}{static_url}/swagger-ui.css",
                swagger_favicon_url=favicon,
            )

        @app.get(swagger_ui_oauth2_redirect_url, include_in_schema=False)
        async def swagger_ui_redirect() -> HTMLResponse:
            return get_swagger_ui_oauth2_redirect_html()

    if redoc_url is not None:
        remove_route(redoc_url)

        @app.get(redoc_url, include_in_schema=False)
        async def redoc_html(request: Request) -> HTMLResponse:
            root = request.scope.get("root_path")
            favicon = f"{root}{static_url}/favicon.png"

            return get_redoc_html(
                openapi_url=f"{root}{openapi_url}",
                title=app.title + " - ReDoc",
                redoc_js_url=f"{root}{static_url}/redoc.standalone.js",
                with_google_fonts=False,
                redoc_favicon_url=favicon,
            )


# 从model_config中获取模型信息
# TODO: 移出模型加载后，这些功能需要删除或改变实现

# def list_embed_models() -> List[str]:
#     '''
#     get names of configured embedding models
#     '''
#     return list(MODEL_PATH["embed_model"])


# def get_model_path(model_name: str, type: str = None) -> Optional[str]:
#     if type in MODEL_PATH:
#         paths = MODEL_PATH[type]
#     else:
#         paths = {}
#         for v in MODEL_PATH.values():
#             paths.update(v)

#     if path_str := paths.get(model_name):  # 以 "chatglm-6b": "THUDM/chatglm-6b-new" 为例，以下都是支持的路径
#         path = Path(path_str)
#         if path.is_dir():  # 任意绝对路径
#             return str(path)

#         root_path = Path(MODEL_ROOT_PATH)
#         if root_path.is_dir():
#             path = root_path / model_name
#             if path.is_dir():  # use key, {MODEL_ROOT_PATH}/chatglm-6b
#                 return str(path)
#             path = root_path / path_str
#             if path.is_dir():  # use value, {MODEL_ROOT_PATH}/THUDM/chatglm-6b-new
#                 return str(path)
#             path = root_path / path_str.split("/")[-1]
#             if path.is_dir():  # use value split by "/", {MODEL_ROOT_PATH}/chatglm-6b-new
#                 return str(path)
#         return path_str  # THUDM/chatglm06b


def api_address(is_public: bool = False) -> str:
    '''
    允许用户在 basic_settings.API_SERVER 中配置 public_host, public_port
    以便使用云服务器或反向代理时生成正确的公网 API 地址（如知识库文档下载链接）
    '''
    from chayuan.settings import Settings

    server = Settings.basic_settings.API_SERVER
    if is_public:
        host = server.get("public_host", "127.0.0.1")
        port = server.get("public_port", "62581")
    else:
        host = server.get("host", "127.0.0.1")
        port = server.get("port", "62581")
        if host == "0.0.0.0":
            host = "127.0.0.1"
    return f"http://{host}:{port}"


def config_panel_address() -> str:
    """察元AI助手配置面板（NiceGUI）地址（不含登录路径）。"""
    from chayuan.settings import Settings

    server = Settings.basic_settings.CONFIG_SERVER or {}
    host = server.get("host", "127.0.0.1")
    port = server.get("port", 8502)
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def config_panel_login_url() -> str:
    """察元AI助手配置面板登录 URL（含随机登录路径段）。

    若 `PANEL_LOGIN_PATH` 为空，则返回根地址并附 `/(未生成)` 的提示占位，
    以便 `user-info` 等诊断命令能向用户提示"需要启动一次让其自动生成"。
    """
    from chayuan.settings import Settings

    base = config_panel_address().rstrip("/")
    path_segment = (
        (getattr(Settings.basic_settings, "PANEL_LOGIN_PATH", "") or "").strip().strip("/")
    )
    if not path_segment:
        return f"{base}/(未生成 - 运行 `chayuan start -c` 会自动生成，或 `chayuan update path`)"
    return f"{base}/{path_segment}"


def get_prompt_template(type: str, name: str) -> Optional[str]:
    """
    从prompt_config中加载模板内容
    type: 对应于 model_settings.llm_model_config 模型类别其中的一种，以及 "rag"，如果有新功能，应该进行加入。
    """

    from chayuan.settings import Settings

    return Settings.prompt_settings.model_dump().get(type, {}).get(name)


def get_prompt_template_dict(type: str, name: str) -> Optional[Dict]:
    """
    从prompt_config中加载模板内容
    type: 对应于 model_settings.llm_model_config 模型类别其中的一种，以及 "rag"，如果有新功能，应该进行加入。
    返回：定义的对象特点字典“SYSTEM_PROMPT”，“HUMAN_MESSAGE”
    """

    from chayuan.settings import Settings

    return Settings.prompt_settings.model_dump().get(type, {}).get(name)


def get_model_dump_dict(type: str) -> Optional[Dict]:
    """
    从prompt_config中加载模板内容
    """

    from chayuan.settings import Settings

    return Settings.prompt_settings.model_dump().get(type, {})


def set_httpx_config(
        timeout: float = Settings.basic_settings.HTTPX_DEFAULT_TIMEOUT,
        proxy: Union[str, Dict] = None,
        unused_proxies: List[str] = [],
):
    """
    设置httpx默认timeout。httpx默认timeout是5秒，在请求LLM回答时不够用。
    将本项目相关服务加入无代理列表，避免fastchat的服务器请求错误。(windows下无效)
    对于chatgpt等在线API，如要使用代理需要手动配置。搜索引擎的代理如何处置还需考虑。
    """

    import os

    import httpx

    httpx._config.DEFAULT_TIMEOUT_CONFIG.connect = timeout
    httpx._config.DEFAULT_TIMEOUT_CONFIG.read = timeout
    httpx._config.DEFAULT_TIMEOUT_CONFIG.write = timeout

    # 在进程范围内设置系统级代理
    proxies = {}
    if isinstance(proxy, str):
        for n in ["http", "https", "all"]:
            proxies[n + "_proxy"] = proxy
    elif isinstance(proxy, dict):
        for n in ["http", "https", "all"]:
            if p := proxy.get(n):
                proxies[n + "_proxy"] = p
            elif p := proxy.get(n + "_proxy"):
                proxies[n + "_proxy"] = p

    for k, v in proxies.items():
        os.environ[k] = v

    # set host to bypass proxy
    no_proxy = [
        x.strip() for x in os.environ.get("no_proxy", "").split(",") if x.strip()
    ]
    no_proxy += [
        # do not use proxy for locahost
        "http://127.0.0.1",
        "http://localhost",
    ]
    # do not use proxy for user deployed fastchat servers
    for x in unused_proxies:
        host = ":".join(x.split(":")[:2])
        if host not in no_proxy:
            no_proxy.append(host)
    os.environ["NO_PROXY"] = ",".join(no_proxy)

    def _get_proxies():
        return proxies

    import urllib.request

    urllib.request.getproxies = _get_proxies


def run_in_thread_pool(
        func: Callable,
        params: List[Dict] = [],
) -> Generator:
    """
    在线程池中批量运行任务，并将运行结果以生成器的形式返回。
    请确保任务中的所有操作是线程安全的，任务函数请全部使用关键字参数。
    """
    tasks = []
    with ThreadPoolExecutor() as pool:
        for kwargs in params:
            tasks.append(pool.submit(func, **kwargs))

        for obj in as_completed(tasks):
            try:
                yield obj.result()
            except Exception as e:
                logger.exception(f"error in sub thread: {e}")


def run_in_process_pool(
        func: Callable,
        params: List[Dict] = [],
) -> Generator:
    """
    在线程池中批量运行任务，并将运行结果以生成器的形式返回。
    请确保任务中的所有操作是线程安全的，任务函数请全部使用关键字参数。
    """
    tasks = []
    max_workers = None
    if sys.platform.startswith("win"):
        max_workers = min(
            mp.cpu_count(), 60
        )  # max_workers should not exceed 60 on windows
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        for kwargs in params:
            tasks.append(pool.submit(func, **kwargs))

        for obj in as_completed(tasks):
            try:
                yield obj.result()
            except Exception as e:
                logger.exception(f"error in sub process: {e}")


def get_httpx_client(
        use_async: bool = False,
        proxies: Union[str, Dict] = None,
        timeout: float = Settings.basic_settings.HTTPX_DEFAULT_TIMEOUT,
        unused_proxies: List[str] = [],
        **kwargs,
) -> Union[httpx.Client, httpx.AsyncClient]:
    """
    helper to get httpx client with default proxies that bypass local addesses.
    """
    default_proxies = {
        # do not use proxy for locahost
        "all://127.0.0.1": None,
        "all://localhost": None,
    }
    # do not use proxy for user deployed fastchat servers
    for x in unused_proxies:
        host = ":".join(x.split(":")[:2])
        default_proxies.update({host: None})

    # get proxies from system envionrent
    # proxy not str empty string, None, False, 0, [] or {}
    default_proxies.update(
        {
            "http://": (
                os.environ.get("http_proxy")
                if os.environ.get("http_proxy")
                   and len(os.environ.get("http_proxy").strip())
                else None
            ),
            "https://": (
                os.environ.get("https_proxy")
                if os.environ.get("https_proxy")
                   and len(os.environ.get("https_proxy").strip())
                else None
            ),
            "all://": (
                os.environ.get("all_proxy")
                if os.environ.get("all_proxy")
                   and len(os.environ.get("all_proxy").strip())
                else None
            ),
        }
    )
    for host in os.environ.get("no_proxy", "").split(","):
        if host := host.strip():
            # default_proxies.update({host: None}) # Origin code
            default_proxies.update(
                {"all://" + host: None}
            )  # PR 1838 fix, if not add 'all://', httpx will raise error

    # merge default proxies with user provided proxies
    if isinstance(proxies, str):
        proxies = {"all://": proxies}

    if isinstance(proxies, dict):
        default_proxies.update(proxies)

    # construct Client
    kwargs.update(timeout=timeout, proxies=default_proxies)

    if use_async:
        return httpx.AsyncClient(**kwargs)
    else:
        return httpx.Client(**kwargs)


def get_server_configs() -> Dict:
    """
    获取configs中的原始配置项，供前端使用
    """
    _custom = {
        "api_address": api_address(),
    }

    return {**{k: v for k, v in locals().items() if k[0] != "_"}, **_custom}


def get_temp_dir(id: str = None) -> Tuple[str, str]:
    """
    创建一个临时目录，返回（路径，文件夹名称）
    """
    import uuid

    from chayuan.settings import Settings

    if id is not None:  # 如果指定的临时目录已存在，直接返回
        path = os.path.join(Settings.basic_settings.BASE_TEMP_DIR, id)
        if os.path.isdir(path):
            return path, id

    id = uuid.uuid4().hex
    path = os.path.join(Settings.basic_settings.BASE_TEMP_DIR, id)
    os.mkdir(path)
    return path, id


# 动态更新知识库信息
def update_search_local_knowledgebase_tool():
    import re

    from chayuan.server.agent.tools_factory import tools_registry
    from chayuan.server.db.repository.knowledge_base_repository import list_kbs_from_db

    kbs = list_kbs_from_db()
    template = "Use local knowledgebase from one or more of these:\n{KB_info}\n to get information，Only local data on this knowledge use this tool. The 'database' should be one of the above [{key}]."
    KB_info_str = "\n".join([f"{kb.kb_name}: {kb.kb_info}" for kb in kbs])
    KB_name_info_str = "\n".join([f"{kb.kb_name}" for kb in kbs])
    template_knowledge = template.format(KB_info=KB_info_str, key=KB_name_info_str)

    search_local_knowledgebase_tool = tools_registry._TOOLS_REGISTRY.get(
        "search_local_knowledgebase"
    )
    if search_local_knowledgebase_tool:
        search_local_knowledgebase_tool.description = " ".join(
            re.split(r"\n+\s*", template_knowledge)
        )
        search_local_knowledgebase_tool.args["database"]["choices"] = [
            kb.kb_name for kb in kbs
        ]


def get_tool(name: str = None) -> Union[BaseTool, Dict[str, BaseTool]]:
    import importlib

    from chayuan.server.agent import tools_factory

    importlib.reload(tools_factory)

    from chayuan.server.agent.tools_factory import tools_registry

    update_search_local_knowledgebase_tool()
    if name is None:
        return tools_registry._TOOLS_REGISTRY
    else:
        return tools_registry._TOOLS_REGISTRY.get(name)


def get_tool_config(name: str = None) -> Dict:
    from chayuan.settings import Settings

    if name is None:
        return Settings.tool_settings.model_dump()
    else:
        return Settings.tool_settings.model_dump().get(name, {})


def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("localhost", port)) == 0


if __name__ == "__main__":
    # for debug
    print(get_default_llm())
    print(get_default_embedding())
    platforms = get_config_platforms()
    models = get_config_models()
    model_info = get_model_info(platform_name="xinference-auto")
    print(1)
