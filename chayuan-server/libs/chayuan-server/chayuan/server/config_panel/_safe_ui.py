"""NiceGUI 安全包装 — 防 ``Client has been deleted`` 警告。

NiceGUI 文档:
    https://github.com/zauberzeug/nicegui/issues/3028

问题:用户关闭 / 切走页面后,客户端被销毁,但 ``ui.timer`` 仍可能触发一次
回调;回调内的 ``set_text`` / ``set_content`` / ``run_javascript`` 等
访问已销毁的 client,触发警告:

    Client has been deleted but is still being used.

不影响功能,但日志噪声大;PR review 时也容易误以为是真 bug。

通用对策:把所有 *后台触发* 的 UI 操作都裹一层 try/except + debug log。
本模块给三个常用包装:

* :func:`safe_timer_cb`     —— 装饰器,把 ``ui.timer`` 的回调包成 fail-soft
* :func:`safe_run_javascript` —— ``ui.run_javascript`` 的 fail-soft 版
* :func:`safe_call`          —— 通用 try/except + debug log

使用建议:
    所有 ``ui.timer(...)`` 的回调都应该用 ``safe_timer_cb`` 包(尤其
    长周期 timer);单次用户交互触发的 click 回调不需要(用户没离场前
    client 一定还在)。
"""
from __future__ import annotations

import functools
import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger("chayuan.config_panel.safe_ui")

F = TypeVar("F", bound=Callable[..., Any])


def patch_nicegui_disconnect_race() -> bool:
    """53 题:抑制 NiceGUI ``Client.delete`` 双重调用产生的 ``KeyError``。

    问题:
      ``Client.delete`` 在 ``handle_disconnect`` 时被调,内部 ``del Client.instances[id]``
      但 instances 可能已被其他路径 pop 过 → ``KeyError``。这条异常通过
      ``background_tasks._handle_task_result`` 的 ``task.result()`` 上抛,被
      asyncio 默认 handler 打成 traceback,server 日志就出现:::

         KeyError: 'c88becb6-...'
         (在 nicegui/client.py:328 ``del Client.instances[self.id]``)

    修复:monkey-patch ``_handle_task_result``,对 KeyError 静默(只记 debug),
    其他异常仍按 NiceGUI 原行为 log。功能不受影响 — KeyError 只是清理已清理的字典。

    幂等:多次调用本函数只 patch 一次。

    返回:True = patched / False = patch 失败(NiceGUI 不在 / 已 patched)
    """
    try:
        from nicegui import background_tasks as _bt
    except Exception:  # noqa: BLE001
        return False

    orig = getattr(_bt, "_handle_task_result", None)
    if orig is None:
        return False
    if getattr(orig, "_chayuan_patched", False):
        return False

    @functools.wraps(orig)
    def _safe_handle(task: Any) -> Any:
        try:
            return orig(task)
        except KeyError as e:
            # disconnect race — instances 已被 pop,删第二次报错。无害
            logger.debug(
                "[nicegui-patch] suppressed KeyError in _handle_task_result: %s", e,
            )
        except Exception:  # noqa: BLE001
            # 其他异常仍交给 NiceGUI 原 handler 处理
            raise
        return None

    _safe_handle._chayuan_patched = True  # type: ignore[attr-defined]
    _bt._handle_task_result = _safe_handle
    logger.info("[nicegui-patch] _handle_task_result KeyError 抑制已生效")
    return True


def safe_timer_cb(fn: F) -> F:
    """装饰一个 ``ui.timer`` 的回调,使它在 client deleted 时静默退出。

    用法::

        @safe_timer_cb
        def _refresh():
            label.set_text("...")

        ui.timer(5.0, _refresh)
    """
    @functools.wraps(fn)
    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            # NiceGUI 的 client-deleted 警告本身只是 warn 级,不抛;
            # 但 set_text 等访问可能抛 AttributeError。两种都吞。
            logger.debug("safe_timer_cb: callback %s skipped: %s",
                         getattr(fn, "__name__", "?"), e)
            return None
    return _wrapped  # type: ignore[return-value]


def safe_run_javascript(ui: Any, code: str) -> None:
    """``ui.run_javascript`` 的 fail-soft 版。client 被销毁时静默。"""
    try:
        ui.run_javascript(code)
    except Exception as e:  # noqa: BLE001
        logger.debug("safe_run_javascript skipped: %s", e)


def safe_call(fn: Callable[[], Any], *, what: str = "ui-callback") -> Any:
    """通用 try/except + debug log 的一次性包装。

    给 click 回调里需要做敏感 UI 操作 (notify / set_text) 用,如果
    NiceGUI client 此刻不可达就静默。
    """
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        logger.debug("safe_call(%s) skipped: %s", what, e)
        return None


def is_client_alive(element: Any) -> bool:
    """判断 ``element`` 所属 NiceGUI Client 是否仍存活(未被 disconnect 删除)。

    使用场景:后台 ``threading.Thread`` 在 fetch 完成后再调 ``container.clear()``
    或 ``label.set_text``,但用户已经离开页面 → ``Client`` 被 NiceGUI 从
    ``Client.instances`` 字典中删除 → 后台线程的 DOM 操作会触发 NiceGUI 的::

        Client has been deleted but is still being used.

    警告(``warn_once``,无 raise — 普通 try/except 抓不住)。

    解决:线程在调用任何 DOM 操作之前,先用本函数检查 client 是否还活,
    如果已死直接 return。

    用法::

        def _bg():
            ...  # fetch
            if not is_client_alive(container):
                return  # 用户已离开页面,跳过 UI 操作
            try:
                _render()
            except Exception:
                pass

    返回:
        ``True``  = client 存活(或 NiceGUI 不可用,保守认为存活)
        ``False`` = client 已被 NiceGUI 删除,DOM 操作会触发警告
    """
    try:
        from nicegui import Client  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        return True  # NiceGUI 不可用 → 不知道,默认存活

    c = getattr(element, "client", None)
    if c is None:
        return True  # element 没绑 client,无法判断,保守认为存活

    cid = getattr(c, "id", None)
    if cid is None:
        return True

    try:
        return cid in Client.instances
    except Exception:  # noqa: BLE001
        return True


__all__ = [
    "is_client_alive",
    "patch_nicegui_disconnect_race",
    "safe_call",
    "safe_run_javascript",
    "safe_timer_cb",
]
