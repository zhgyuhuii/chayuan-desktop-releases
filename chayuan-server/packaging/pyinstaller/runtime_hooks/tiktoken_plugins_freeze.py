"""PyInstaller runtime hook: 修 tiktoken 在 frozen exe 里找不到 cl100k_base 等编码。

问题
----
tiktoken 的 encoding(cl100k_base / o200k_base / r50k_base / p50k_base / p50k_edit)
都注册在 ``tiktoken_ext`` 命名空间包下的子模块里(主要是 ``tiktoken_ext.openai_public``)。
tiktoken 在 ``tiktoken.registry`` 里这样发现它们::

    @functools.lru_cache()
    def _available_plugin_modules() -> Sequence[str]:
        import tiktoken_ext
        mods = []
        plugin_mods = pkgutil.iter_modules(
            tiktoken_ext.__path__, tiktoken_ext.__name__ + "."
        )
        for _, mod_name, _ in plugin_mods:
            mods.append(mod_name)
        return mods

``tiktoken_ext`` 是 PEP 420 命名空间包(无 ``__init__.py``)。PyInstaller 静态打包后,
``tiktoken_ext.__path__`` 经常是空 list,``pkgutil.iter_modules`` 返空 →
``cl100k_base`` 等 encoding 的 constructor 永远不进 ``ENCODINGS`` 注册表 →
``tiktoken.get_encoding('cl100k_base')`` 抛 ``Unknown encoding cl100k_base.``。

把 ``tiktoken_ext.openai_public`` 显式列进 hiddenimports 让 PyInstaller 打进 bundle
是**必要但不充分**的:它保证模块可 import,但没改 tiktoken 自己的发现逻辑。

修法
----
monkey-patch ``tiktoken.registry._available_plugin_modules`` 直接返回硬编码列表。
encoding constructors 写在 ``tiktoken_ext.openai_public`` 里(我们 hiddenimport
保证它可加载),patch 后 tiktoken 第一次 ``get_encoding`` 就能找到。

只在 frozen 下生效;dev 模式 import 没问题不动它。

⚠ 排在 multiprocessing_freeze 之前
----------------------------------
chayuan-server 用 ``mp.spawn`` 起 API server / config-panel 等子进程
(``--multiprocessing-fork`` argv)。frozen 下 mp child 在
``multiprocessing.spawn.freeze_support()`` 那一步就 ``sys.exit`` 进入
spawn_main,**后续 runtime hook 全不会跑**。

KB 创建 handler 跑在 API server 这个 mp child 里。如果 tiktoken patch
排在 multiprocessing_freeze 之后,mp child 里就**没 patch**,KB 创建
仍然 ``Unknown encoding cl100k_base``。

每个 mp child 是独立 Python 解释器(Windows spawn 语义),各自重新跑
所有 runtime hook 直到 freeze_support 退出 → 把 tiktoken hook 放在
第一个,每个 child 启动时都 apply 一次,patch 在每个 child 里都生效。
"""
from __future__ import annotations

import sys


def _log(msg: str) -> None:
    sys.stderr.write(f"[tiktoken-rthook] {msg}\n")
    sys.stderr.flush()


if not getattr(sys, "frozen", False):
    # dev 模式不需要 patch,tiktoken 自己的命名空间发现机制能 work
    pass
else:
    try:
        import tiktoken.registry as _tk_reg

        # tiktoken 内置 + 我们关心的 openai 编码全在这里
        _KNOWN_PLUGIN_MODULES = (
            "tiktoken_ext.openai_public",
            # tiktoken 现在只有这一个 ext 模块,但留扩展位防上游加新的
        )

        def _frozen_plugin_modules():
            # tiktoken 用 functools.lru_cache,我们返 tuple 跟它原签名一致
            return _KNOWN_PLUGIN_MODULES

        # 替换 — 注意 lru_cache 装饰过的函数,直接覆盖属性即可,不用 cache_clear
        _tk_reg._available_plugin_modules = _frozen_plugin_modules  # noqa: SLF001

        # 顺便触发一次注册,fail-fast:如果 tiktoken_ext.openai_public 这一刻
        # 不能 import,这里就报清晰错误,不要等到第一次 get_encoding 才崩。
        import importlib

        _mod = importlib.import_module("tiktoken_ext.openai_public")
        _ctors = getattr(_mod, "ENCODING_CONSTRUCTORS", None)
        if _ctors:
            _log(
                f"已 patch _available_plugin_modules;"
                f"openai_public 提供 encoding={sorted(_ctors.keys())}"
            )
        else:
            _log("warning: tiktoken_ext.openai_public 没有 ENCODING_CONSTRUCTORS 属性")
    except Exception as _e:  # noqa: BLE001
        _log(f"patch 失败(tiktoken 可能未打进 bundle): {_e!r}")
