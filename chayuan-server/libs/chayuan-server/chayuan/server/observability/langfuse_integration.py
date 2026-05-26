"""Langfuse 一键集成（P0-3）—— 离线 / 断网 / 未装 全路径容错。

**设计原则（离线与生产都友好）**：

1. **显式 kill switch**：环境变量 ``CHAYUAN_LANGFUSE_DISABLE=1`` 一刀切关停，
   即使 LANGFUSE_* 凭据齐备也跳过。运维应急必备。
2. **三重降级**：``_env_configured → 包可 import → SDK 实例化成功`` 任一失败自动 no-op。
3. **进程级单例**：Handler / Client 都只建一次，失败缓存 None，避免每请求重试。
4. **非阻塞失败容忍**：把 Handler 包一层 SafeCallbackHandler，内部吃掉所有异常，
   即便断网也不会向上冒泡（LangChain 的 callback 异常会污染主流）。
5. **离线自托管友好**：``LANGFUSE_HOST`` 可以是 ``http://internal-langfuse:3000``；
   没做特殊 URL 校验，用户能指向任何可达端点。
"""
from __future__ import annotations

import logging
import os
import threading
from functools import wraps
from typing import Any, Callable, Optional

logger = logging.getLogger("chayuan.observability.langfuse")

_LOCK = threading.Lock()
_ENABLED: Optional[bool] = None
_HANDLER: Optional[Any] = None
_HANDLER_BUILT: bool = False


def _explicit_disabled() -> bool:
    """运维一键关停：

    - 环境变量 ``CHAYUAN_LANGFUSE_DISABLE`` 取 ``1/true/yes/on`` 任一值
    - 或 ``basic_settings.CHAYUAN_LANGFUSE_DISABLE=True``

    两者**任一生效**即禁用；环境变量优先（部署时不改 yaml 就能应急）。
    """
    v = str(os.environ.get("CHAYUAN_LANGFUSE_DISABLE", "")).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    try:
        from chayuan.settings import Settings
        return bool(getattr(Settings.basic_settings, "CHAYUAN_LANGFUSE_DISABLE", False))
    except Exception:  # noqa: BLE001
        return False


def _yaml_value(name: str) -> str:
    """从 basic_settings.yaml 读取字段（LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY）。

    Settings 读取失败时返回空串——让上层判定为"未配置"而不是崩。
    """
    try:
        from chayuan.settings import Settings
        return str(getattr(Settings.basic_settings, name, "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def effective_config() -> dict:
    """解析出 Langfuse **最终生效**的三个字段。

    优先级（每个字段独立判定）：
      1. 环境变量 ``LANGFUSE_*``（非空 + strip 后非空）
      2. ``basic_settings.yaml`` 同名字段

    让用户既能在面板里填 yaml（开箱即用），又能 env 应急覆盖（运维友好）。
    返回 dict：{public_key, secret_key, host, public_key_source, ...}；
    source 字段告诉调用方每个值是来自 env 还是 yaml，便于调试 / UI 展示。
    """
    out: dict = {}
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        env_val = str(os.environ.get(name, "") or "").strip()
        if env_val:
            out[name] = env_val
            out[name + "_SOURCE"] = "env"
        else:
            yaml_val = _yaml_value(name)
            out[name] = yaml_val
            out[name + "_SOURCE"] = "yaml" if yaml_val else "unset"
    return out


def _env_configured() -> bool:
    """三件套是否齐全——不再只看 env，yaml 里配好也算。

    之所以保留函数名 / 行为：被 langfuse SDK 读取时它仍然走 ``os.environ``——
    所以若值来自 yaml，必须在进程启动早期同步到 env 里（见 ``apply_to_env``）。
    """
    cfg = effective_config()
    return bool(
        cfg.get("LANGFUSE_PUBLIC_KEY")
        and cfg.get("LANGFUSE_SECRET_KEY")
        and cfg.get("LANGFUSE_HOST")
    )


def apply_to_env() -> None:
    """把 yaml 里的 Langfuse 凭据回写到 ``os.environ``（仅当 env 为空时）。

    Langfuse SDK 自身只从环境变量读凭据；为了让 yaml 配置也能被 SDK 认到，
    要在 ``is_enabled()`` 首次判定前把三个值同步到 env。幂等；已有 env 不覆盖。
    """
    cfg = effective_config()
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        if os.environ.get(name):
            continue
        val = cfg.get(name) or ""
        if val:
            os.environ[name] = val


def is_enabled() -> bool:
    """是否启用 Langfuse。结果缓存，避免每请求重算。"""
    global _ENABLED
    if _ENABLED is not None:
        return _ENABLED
    with _LOCK:
        if _ENABLED is not None:
            return _ENABLED
        if _explicit_disabled():
            _ENABLED = False
            logger.info("Langfuse 已被 CHAYUAN_LANGFUSE_DISABLE 显式禁用")
            return False
        # 先把 yaml 里的凭据同步到 env（env 已有则保留），让 SDK 能读到
        apply_to_env()
        if not _env_configured():
            _ENABLED = False
            return False
        try:
            import langfuse  # noqa: F401
            _ENABLED = True
            logger.info("Langfuse 就绪；host=%s",
                        os.environ.get("LANGFUSE_HOST"))
        except Exception as e:  # noqa: BLE001
            _ENABLED = False
            logger.info("Langfuse env 已配置但未安装 langfuse 包；跳过（%r）", e)
    return bool(_ENABLED)


# LangChain BaseCallbackHandler 上所有可能被运行时调用的回调方法名。
# 显式枚举:必须真继承 BaseCallbackHandler 并 override 这些方法,
# 才能通过 langchain-openai 在 Pydantic v2 下的 is_instance 校验
# (传入 _SafeCallbackHandler 实例否则会被拒)。
# 不在这里列的回调走 BaseCallbackHandler 默认 no-op。
_CALLBACK_METHODS = (
    "on_llm_start",
    "on_llm_new_token",
    "on_llm_end",
    "on_llm_error",
    "on_chain_start",
    "on_chain_end",
    "on_chain_error",
    "on_tool_start",
    "on_tool_end",
    "on_tool_error",
    "on_chat_model_start",
    "on_text",
    "on_agent_action",
    "on_agent_finish",
    "on_retriever_start",
    "on_retriever_end",
    "on_retriever_error",
    "on_retry",
)


def _base_callback_handler_cls():
    """晚加载 BaseCallbackHandler:langchain-core 不一定装,失败回退 object。
    回退后 _SafeCallbackHandler 仍可包装 inner,但传给 ChatOpenAI 时
    Pydantic 仍会拒 — 此时调用方应自行判断 callbacks 是否传入。
    """
    try:
        from langchain_core.callbacks import BaseCallbackHandler  # type: ignore
        return BaseCallbackHandler
    except Exception:  # noqa: BLE001
        return object


def _make_safe_handler_cls():
    base = _base_callback_handler_cls()

    class _SafeCallbackHandler(base):  # type: ignore[misc, valid-type]
        """把真 Handler 包一层,吃掉所有回调异常,避免 chain 中断。

        断网 / 证书错 / 4xx 都可能让 SDK 内部 raise;放任冒泡会让 LangChain
        中断业务侧 "突然不能说话了"。这里 override 所有 on_* 方法,
        delegate 到 inner 同时 try/except fail-silent。

        必须真继承 BaseCallbackHandler:langchain-openai 用 Pydantic v2 isinstance
        校验 callbacks,duck typing(只 __getattr__)会被拒成 ValidationError。
        """

        # LangChain 看这个 flag 决定要不要把 on_*_error 异常上抛
        raise_error = False

        def __init__(self, inner):
            try:
                super().__init__()
            except Exception:  # noqa: BLE001
                pass
            self._inner = inner
            self._warned_once = False

        # 形参用双下划线前缀:Python 实参绑定时不会和用户传的 name=... 关键字冲突
        # (LangChain 的 on_chat_model_start 等会以 keyword 形式塞 name='...';
        # 旧实现写成 _safe_call(self, name, ...) 直接 TypeError)。
        def _safe_call(self, __method_name, *args, **kwargs):
            target = getattr(self._inner, __method_name, None)
            if target is None or not callable(target):
                return None
            try:
                return target(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                if not self._warned_once:
                    self._warned_once = True
                    logger.warning(
                        "Langfuse CallbackHandler.%s 异常(后续静默):%r",
                        __method_name, e,
                    )
                return None

    # 给类装上每个 on_* 方法,委托到 _safe_call(只透传 *args/**kwargs,
    # method_name 通过闭包绑定,不进 LangChain 调用方的 kwargs)
    for _name in _CALLBACK_METHODS:
        def _make(method_name):
            def _wrapper(self, *args, **kwargs):
                return self._safe_call(method_name, *args, **kwargs)
            _wrapper.__name__ = method_name
            return _wrapper
        setattr(_SafeCallbackHandler, _name, _make(_name))

    return _SafeCallbackHandler


# 单例 class,模块加载即生成
_SafeCallbackHandler = _make_safe_handler_cls()


def _build_handler() -> Optional[Any]:
    global _HANDLER, _HANDLER_BUILT
    if _HANDLER_BUILT:
        return _HANDLER
    with _LOCK:
        if _HANDLER_BUILT:
            return _HANDLER
        _HANDLER_BUILT = True
        if not is_enabled():
            return None
        try:
            # langfuse 3.x 把 LangChain handler 挪到了 langfuse.langchain;
            # 兼容 2.x 老路径 langfuse.callback。
            try:
                from langfuse.langchain import CallbackHandler  # type: ignore
            except ImportError:
                from langfuse.callback import CallbackHandler  # type: ignore
            raw = CallbackHandler()
            _HANDLER = _SafeCallbackHandler(raw)
        except Exception as e:  # noqa: BLE001
            logger.warning("Langfuse CallbackHandler 构建失败：%r", e)
            _HANDLER = None
    return _HANDLER


def langfuse_callback_handler():
    """返回 LangChain 可消费的 CallbackHandler；未启用 / 构建失败返回 None。"""
    if not is_enabled():
        return None
    return _build_handler()


def observed(name: str, *, kind: str = "span"):
    """把同步函数包成一个 Langfuse 自定义 span；失败 fail-open。"""
    def deco(fn: Callable):
        @wraps(fn)
        def inner(*args, **kwargs):
            if not is_enabled():
                return fn(*args, **kwargs)
            try:
                from langfuse import Langfuse  # type: ignore
                client = Langfuse()
                trace = client.trace(name=name)
                span = trace.span(name=name, metadata={"kind": kind})
            except Exception:  # noqa: BLE001
                return fn(*args, **kwargs)
            try:
                result = fn(*args, **kwargs)
                try:
                    span.end()
                except Exception:  # noqa: BLE001
                    pass
                return result
            except Exception as e:
                try:
                    span.end(level="ERROR", status_message=str(e))
                except Exception:  # noqa: BLE001
                    pass
                raise
        return inner
    return deco


def inject_into_callbacks(callbacks: list) -> list:
    """把 Handler 追加到 LangChain callbacks 列表；未启用时原样返回。

    **返回永远是一个 list** —— 下游可安全传给 get_ChatOpenAI(callbacks=...)
    或做 ``or None`` 转换；不会返回 None。
    """
    base = list(callbacks or [])
    h = langfuse_callback_handler()
    if h is None:
        return base
    return base + [h]


def reset_for_tests() -> None:
    """测试辅助：清掉缓存单例，让环境变量变动后能重新判定。"""
    global _ENABLED, _HANDLER, _HANDLER_BUILT
    with _LOCK:
        _ENABLED = None
        _HANDLER = None
        _HANDLER_BUILT = False


def health() -> dict:
    """供 /healthz 扩展或管理面板展示。"""
    cfg = effective_config()
    return {
        "enabled": is_enabled(),
        "explicit_disabled": _explicit_disabled(),
        "env_configured": _env_configured(),
        "host": cfg.get("LANGFUSE_HOST") or "",
        "host_source": cfg.get("LANGFUSE_HOST_SOURCE") or "unset",
        "public_key_source": cfg.get("LANGFUSE_PUBLIC_KEY_SOURCE") or "unset",
        "secret_key_source": cfg.get("LANGFUSE_SECRET_KEY_SOURCE") or "unset",
    }
