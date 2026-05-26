"""日志：request_id contextvar + 可选 JSON formatter。

使用方式：
- 在 ``middleware.RequestIDMiddleware`` 里拿到 rid 后调用 ``set_request_id(rid)``；
- 任何模块的 logger 都会自动带上该 rid（通过 `RequestIdFilter`）。
- JSON 输出由 ``basic_settings.JSON_LOGS`` 控制，默认关闭，保持兼容。
- 第三方库噪声由 ``basic_settings.VERBOSE_LOGS`` 控制,默认关闭(干净启动日志);
  打开后(配置面板"详细日志"开关) transformers / urllib3 / langchain / 引擎 SQL
  等才会全量输出。

注意：这里只影响根 logger；`loguru` 走它自己一套，不在此模块范围内。
"""
from __future__ import annotations

import json
import logging
import os
import warnings
from contextvars import ContextVar
from typing import Any, Dict, Optional

_REQUEST_ID: ContextVar[str] = ContextVar("chayuan_request_id", default="-")
_INITIALIZED: bool = False


def current_request_id() -> str:
    """获取当前请求的 id；未设置时返回 `-`。"""
    return _REQUEST_ID.get()


def set_request_id(rid: str) -> None:
    """由中间件在请求开始时调用。"""
    _REQUEST_ID.set(rid or "-")


# ---------------------------------------------------------------------------
# Filter：把 rid 注入每条 LogRecord
# ---------------------------------------------------------------------------

class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id()
        return True


# ---------------------------------------------------------------------------
# JSON formatter（无第三方依赖）
# ---------------------------------------------------------------------------

_STANDARD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime",
}


class JsonFormatter(logging.Formatter):
    """生产友好的 JSON 日志；每行 1 条，可直接喂给 Loki/ELK。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # 透传自定义字段
        for k, v in record.__dict__.items():
            if k in _STANDARD_ATTRS or k in payload:
                continue
            if k.startswith("_"):
                continue
            try:
                json.dumps(v)
            except TypeError:
                v = repr(v)
            payload[k] = v
        return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------

def init_logging() -> None:
    """幂等地为根 logger 挂上 RequestIdFilter；JSON_LOGS=true 时切 JsonFormatter。

    不新增 handler：只改造已有 handler（多数情况下 uvicorn 会自带一个 StreamHandler）。
    如果根 logger 目前还没有任何 handler，则加一个 StreamHandler，避免静默。
    """
    global _INITIALIZED
    if _INITIALIZED:
        return
    _INITIALIZED = True

    try:
        from chayuan.settings import Settings

        json_logs = bool(getattr(Settings.basic_settings, "JSON_LOGS", False))
    except Exception:
        json_logs = False

    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())

    rid_filter = RequestIdFilter()
    if json_logs:
        fmt: logging.Formatter = JsonFormatter()
    else:
        fmt = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [rid=%(request_id)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # 给所有已注册的 handler 装上 filter + 新 formatter
    for h in root.handlers:
        h.addFilter(rid_filter)
        h.setFormatter(fmt)

    # uvicorn 自带的 logger：把 filter 加上（formatter 留给 uvicorn 自己也行，
    # 这里统一换成我们的，保证 json/纯文本一致）。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        lg = logging.getLogger(name)
        for h in lg.handlers:
            h.addFilter(rid_filter)
            h.setFormatter(fmt)
        # 未挂 handler 的子 logger：通过 propagate=True 享用 root 的配置
        if not lg.handlers:
            lg.addFilter(rid_filter)

    # 三方库噪声治理(VERBOSE_LOGS=False 时启用)
    try:
        from chayuan.settings import Settings
        verbose = bool(getattr(Settings.basic_settings, "VERBOSE_LOGS", False))
    except Exception:  # noqa: BLE001
        verbose = False
    if not verbose:
        _quiet_third_party_loggers()


# ---------------------------------------------------------------------------
# 三方库噪声治理
# ---------------------------------------------------------------------------

# 各 logger 的目标 level — 只压噪声大户,不影响应用代码 / 业务报错
_NOISY_LOGGERS_LEVELS: Dict[str, int] = {
    # transformers 冷启动加载几百个 image_processor,每个吐"Examining the
    # path of ... raised: No module named 'torchvision'",对运维零信息量
    "transformers": logging.ERROR,
    "transformers.modeling_utils": logging.ERROR,
    "transformers.utils.import_utils": logging.ERROR,
    # HTTP 客户端的连接/重试日志
    "urllib3": logging.WARNING,
    "urllib3.connectionpool": logging.WARNING,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    # langchain 系统:大量 INFO 级 chain trace,生产环境噪声
    "langchain": logging.WARNING,
    "langchain.chains": logging.WARNING,
    "langchain_community": logging.WARNING,
    "langchain_core": logging.WARNING,
    # SQLAlchemy:把每条 SQL 都打印出来太吵
    "sqlalchemy.engine.Engine": logging.WARNING,
    "sqlalchemy.pool": logging.WARNING,
    # pymilvus:连接握手过程几条 info 级
    "pymilvus": logging.WARNING,
    # nltk / huggingface_hub
    "huggingface_hub": logging.WARNING,
    "nltk": logging.WARNING,
    # FilelockFilter 之类
    "filelock": logging.WARNING,
}

# transformers 用 warnings.warn 喷的 deprecation 文案 — Python warnings 不走
# logging,需要单独 filter
_WARNING_PATTERNS = (
    r"Accessing `__path__` from .*",
    r"Examining the path of .*",
)


def _quiet_third_party_loggers() -> None:
    """把噪声大户压到 WARNING/ERROR;同时过滤 transformers 的 deprecation warnings。"""
    for name, level in _NOISY_LOGGERS_LEVELS.items():
        logging.getLogger(name).setLevel(level)

    # transformers 自带的 verbosity API(env var 优先级最高,但已 import
    # 后改 env 不一定生效;库自身也提供 set_verbosity_*,够用了)
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    try:
        from transformers.utils import logging as hf_logging  # type: ignore

        hf_logging.set_verbosity_error()
        hf_logging.disable_progress_bar()
    except Exception:  # noqa: BLE001
        # 没装 transformers 也无所谓
        pass

    # warnings.warn 的 deprecation 噪声(不经过 logging)
    for pat in _WARNING_PATTERNS:
        warnings.filterwarnings("ignore", message=pat)


def reapply_noise_filter() -> None:
    """配置面板切换 VERBOSE_LOGS 后调用,把过滤策略热应用。

    True  → 还原 NOTSET 让各库回到默认级别
    False → 重新压噪声
    """
    try:
        from chayuan.settings import Settings
        verbose = bool(getattr(Settings.basic_settings, "VERBOSE_LOGS", False))
    except Exception:  # noqa: BLE001
        verbose = False

    if verbose:
        for name in _NOISY_LOGGERS_LEVELS:
            logging.getLogger(name).setLevel(logging.NOTSET)
        # warnings 已注册的过滤器移除
        warnings.resetwarnings()
        try:
            from transformers.utils import logging as hf_logging  # type: ignore
            hf_logging.set_verbosity_warning()
            hf_logging.enable_progress_bar()
        except Exception:  # noqa: BLE001
            pass
    else:
        _quiet_third_party_loggers()
