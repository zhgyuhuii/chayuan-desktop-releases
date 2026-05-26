# SPDX-License-Identifier: MIT
# Copyright (c) 北京智灵鸟科技中心. All rights reserved.
"""察元AI助手（Chayuan）服务端主包。

版本号单一来源（SSOT）策略
============================
- **唯一来源**：``libs/chayuan-server/pyproject.toml`` 里的 ``[tool.poetry].version``。
- 本文件通过 ``importlib.metadata`` 读取已安装包的版本元信息；当且仅当
  从源码直接运行（未 ``pip install -e .``）且元信息不可得时，才回退到下方
  ``_FALLBACK_VERSION``。

这样做的好处：
  * 所有 ``from chayuan import __version__`` 的地方都会拿到同一个值；
  * 发版本只需要改 pyproject.toml 一处，不必扫代码里的魔法字符串；
  * CI/打包拿到的 Poetry 版本与运行时自报版本永远一致。

如果你在源码树里确实需要一个兜底值（比如裸跑 ``python cli.py`` 用于本地冒烟），
请只改 ``_FALLBACK_VERSION``；生产发版请改 pyproject.toml。
"""
from __future__ import annotations

__product_name__ = "察元AI助手"
__copyright__ = "Copyright (c) 北京智灵鸟科技中心"

#: 当无法从安装元数据读取版本时使用的兜底值。
#: 保持与 ``libs/chayuan-server/pyproject.toml`` 保持一致，避免源码裸跑时拿到旧号。
_FALLBACK_VERSION = "1.0.0.0"


def _detect_version() -> str:
    """优先从已安装包的元数据读取，失败回退到常量。不抛异常。"""
    try:
        from importlib.metadata import PackageNotFoundError, version as _pkg_version

        try:
            return _pkg_version("chayuan-server")
        except PackageNotFoundError:
            # 未 pip install 的源码运行场景；常见于开发机 `python cli.py`。
            return _FALLBACK_VERSION
    except Exception:
        return _FALLBACK_VERSION


__version__: str = _detect_version()


# ---------------------------------------------------------------------------
# 噪声治理:在 transformers / langchain / torch 这些大库被 import 之前就把
# warnings filter / env var 立起来。
#
# 否则等到 setup_observability() 才设置时,冷启动那一波"Accessing __path__"
# 和"Examining the path of ..."(每加载一个 image_processor 都打)已经把屏幕
# 刷满,完全盖住关键日志。Python 的 warnings 模块对每条独立 warning 默认
# 只打一次,filter 早一秒立起来就少几百行噪声。
#
# 这里**完全不**读 Settings(避免 settings 还没初始化时的循环 import);
# 直接读环境变量 CHAYUAN_VERBOSE_LOGS — 默认关。配置面板里勾上"详细日志"后
# Settings 写文件 + 设置该环境变量,下次启动生效。运行期切换走
# observability.logging.reapply_noise_filter()。
# ---------------------------------------------------------------------------
def _apply_early_log_filters() -> None:
    import os
    import warnings

    if os.environ.get("CHAYUAN_VERBOSE_LOGS", "").lower() in ("1", "true", "yes"):
        return  # 用户主动要详细日志,什么都不动

    # transformers 自己的 verbosity 也是看这个 env 决定的
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    # 屏蔽 transformers deprecation:`Accessing __path__ from .models.xxx`
    warnings.filterwarnings("ignore", message=r"Accessing `__path__` from .*")
    # 屏蔽 image_processor 探测时的 `Examining the path of ... raised: ...`
    # (这条其实是 logger.warning,但部分版本会 fall through 到 warnings)
    warnings.filterwarnings("ignore", message=r"Examining the path of .*")
    # langchain 系常见的 LangChainDeprecationWarning
    warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"langchain.*")


_apply_early_log_filters()
