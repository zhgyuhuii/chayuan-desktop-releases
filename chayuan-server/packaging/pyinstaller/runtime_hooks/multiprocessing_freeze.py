"""PyInstaller runtime hook: 子进程 spawn 协议拦截 + UTF-8 stdio。

为什么需要
----------
chayuan-server 在 startup.py 里:
- ``mp.set_start_method("spawn")`` 强制 spawn(macOS Python 3.8+ 默认就是 spawn)
- ``mp.Process(run_api_server)`` / ``mp.Process(run_config_panel)`` 起子进程
- ``uvicorn.run(workers=N)`` 多 worker(N>1 时 uvicorn 内部用 subprocess)
- ``ProcessPoolExecutor`` 也走 spawn

frozen bundle 下,所有 spawn 路径都会**重新 invoke 同一个 exe**,argv 形如:
- 标准 mp:``[exe, --multiprocessing-fork, tracker_fd=N, pipe_handle=N, ...]``
- resource_tracker:``[exe, -B, -S, -I, -c, 'from multiprocessing.resource_tracker import main;main(N)']``
- loky / uvicorn reload:``[exe, -B, -S, -I, -O, -c, <prog>, ...]``

如果在 user code(Click) 之前不拦截,Click 会报 ``No such option`` 直接退。

两个微妙的坑
-----------
1. **``multiprocessing.freeze_support()`` 在 macOS / Linux 上是 no-op!**
   只有 Windows 才会 forward 到 ``spawn.freeze_support``。要在 POSIX
   frozen bundle 上抓 ``--multiprocessing-fork``,必须直接调
   ``multiprocessing.spawn.freeze_support()``。

2. **``spawn_main`` 解 prep data 时会 ``_fixup_main_from_path``,
   尝试 ``import __main__.py`` ── frozen bundle 里这文件不存在,直接
   ``ImportError``。**修复:在父进程修补 ``get_preparation_data``,
   把 ``init_main_from_name`` 和 ``init_main_from_path`` 摘掉。
   PyInstaller 自己的 ``pyi_rth_multiprocessing.py`` 应该做这件事,
   但它在用户 hook **之后**才跑,而且实测有时不生效。所以我们在自己
   的 hook 里也打一遍补丁,belt + suspenders。

诊断
----
hook 头会打 argv 和 sys.frozen,出问题时 grep ``[mp-freeze]`` 即可定位。
"""
from __future__ import annotations

import sys


def _log(msg: str) -> None:
    sys.stderr.write(f"[mp-freeze] {msg}\n")
    sys.stderr.flush()


# ──────────────────────────────────────────────────────────────
# 1) UTF-8 stdio
# ──────────────────────────────────────────────────────────────
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None:
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


_log(f"argv={sys.argv!r}  frozen={getattr(sys, 'frozen', False)}  exe={sys.executable!r}")


# ──────────────────────────────────────────────────────────────
# 2) 修补 multiprocessing.spawn.get_preparation_data
#    父进程调它生成给子进程的 prep data;默认会塞 init_main_from_path,
#    子进程 spawn_main → _fixup_main_from_path 加载该路径会 ImportError。
# ──────────────────────────────────────────────────────────────
import multiprocessing.spawn  # noqa: E402

_orig_get_prep = multiprocessing.spawn.get_preparation_data


def _patched_get_prep(name):
    d = _orig_get_prep(name)
    d.pop("init_main_from_name", None)
    d.pop("init_main_from_path", None)
    return d


multiprocessing.spawn.get_preparation_data = _patched_get_prep


# 同时给 _fixup_main_from_path 包一层兜底:就算 PyInstaller / 其它库晚于
# 我们补 init_main_from_path 进 prep data,子进程跑到这一步发现路径不
# 存在直接静默跳过,不要 raise。
_orig_fixup_path = multiprocessing.spawn._fixup_main_from_path  # noqa: SLF001


def _patched_fixup_path(main_path):
    import os as _os

    if not _os.path.exists(main_path):
        _log(f"_fixup_main_from_path: 跳过不存在的路径 {main_path!r}")
        return
    return _orig_fixup_path(main_path)


multiprocessing.spawn._fixup_main_from_path = _patched_fixup_path  # noqa: SLF001


# ──────────────────────────────────────────────────────────────
# 3) 调 spawn 模块的 freeze_support(macOS / Linux 上必须直接调这个,
#    multiprocessing.freeze_support 是 no-op)。
#    检测 ``[exe, --multiprocessing-fork, kwds...]`` argv,跑 worker 后 exit。
# ──────────────────────────────────────────────────────────────
try:
    multiprocessing.spawn.freeze_support()
    _log("spawn.freeze_support 返回(主进程或非 mp 子进程)")
except SystemExit:
    raise
except BaseException as _e:  # noqa: BLE001
    _log(f"spawn.freeze_support 抛异常:{_e!r}")
    import traceback as _tb

    _tb.print_exc()


# ──────────────────────────────────────────────────────────────
# 4) 兜底:Python 解释器 flag + ``-c <prog>`` 模式
#    resource_tracker / loky / uvicorn --reload 用 ``[exe, -B, -S, -I, -O, -c, <prog>, ...]``
#    把 frozen exe 当 python 用。我们解析 -c 后的源码 exec 之。
# ──────────────────────────────────────────────────────────────
_PY_FLAGS = {"-B", "-S", "-I", "-O", "-OO", "-E", "-u", "-v", "-q", "-d", "-bb"}
_argv = sys.argv
# 接受两种 -c 入口:
#  (a) loky / uvicorn --reload / resource_tracker 风格,带 python flag 前缀:
#        [exe, -B, -S, -I, -O, -c, prog, ...]
#  (b) bare 风格(install_task_manager._bg_cmd_python_inline 用这条):
#        [exe, -c, prog]
# 早期实现只覆盖 (a),(b) 会落到 Click 报 "Usage:..." 退出 →
# OCR / ollama / funasr / paddleocr 等所有 daemon 启动路全部死在这。
if len(_argv) >= 2 and (
    _argv[1] == "-c"
    or _argv[1] in _PY_FLAGS
    or _argv[1].startswith("-X")
    or _argv[1].startswith("-W")
):
    _i = 1
    while _i < len(_argv):
        if _argv[_i] == "-c":
            break
        if not _argv[_i].startswith("-"):
            break
        _i += 1
    if _i < len(_argv) - 1 and _argv[_i] == "-c":
        _prog = _argv[_i + 1]
        _rest = _argv[_i + 2 :]
        _log(f"intercept -c prog (源码 {len(_prog)} 字符)")
        sys.argv = ["-c"] + _rest
        try:
            exec(_prog, {"__name__": "__main__"})  # noqa: S102
        except SystemExit:
            raise
        except BaseException as _e:  # noqa: BLE001
            _log(f"-c 路径 exec 失败:{_e!r}")
            import traceback as _tb

            _tb.print_exc()
            sys.exit(1)
        sys.exit(0)
    else:
        _log(
            f"argv[1]={_argv[1]!r} 像 python flag 但没 -c。argv={_argv!r}"
        )


# ──────────────────────────────────────────────────────────────
# 5) ``chayuan-server.exe -m <module> ...`` —— 把 frozen exe 当 python 解释器
#    用来跑模块(等价 ``python -m <module> ...``)。
#
#    为什么需要:frozen bundle 的入口是 Click CLI(chayuan/cli.py),它**不**
#    像真 python 那样支持 ``-m``。但 chayuan 内部多处用
#    ``[sys.executable, "-m", "pip", "install", ...]`` 装运行时依赖:
#      · runtime/pytorch_installer.py 的 build_install_recipe(在线/离线装 torch)
#      · shared/deps.py 的 _run_pip_install(ensure_pkg / ensure_runtime_deps)
#      · cli.py 的 _auto_repair_torch
#    在 frozen 环境里 sys.executable 就是 chayuan-server.exe,这些命令变成
#    ``chayuan-server.exe -m pip install ...`` → Click 把 ``-m`` 当未知 option
#    报 ``No such option: -m`` 直接退(rc=2)。
#
#    这里在 Click 接管 argv **之前**拦截 ``-m``,用 runpy.run_module 跑目标模块,
#    并把 sys.argv 调整成模块自己看到的样子(``[<module-as-script-path>, ...rest]``),
#    行为对齐 ``python -m <module>``。
#
#    注意:必须放在 section 3/4 之后 —— mp spawn 协议偶尔也带 ``-m``(罕见),
#    但那些已被 freeze_support 在前面消费;走到这里的 ``-m`` 就是用户/业务代码
#    显式发起的 ``-m <module>`` 调用。
# ──────────────────────────────────────────────────────────────
if len(_argv) >= 2 and _argv[1] == "-m":
    if len(_argv) < 3 or not _argv[2]:
        _log("argv[1]=='-m' 但缺模块名;按 python 行为退出")
        sys.stderr.write("Error: -m requires a module name\n")
        sys.exit(2)
    _mod = _argv[2]
    _mod_args = _argv[3:]
    _log(f"intercept -m {_mod!r} args={_mod_args!r}")
    import runpy as _runpy

    # python -m 的语义:sys.argv[0] 被替换成模块的 __file__(脚本路径),
    # 其余参数原样。run_module(run_name="__main__") 内部会再设一遍 argv[0],
    # 这里先把 rest 摆好,模块解析 sys.argv[1:] 时就能拿到正确参数。
    sys.argv = [_mod] + list(_mod_args)
    try:
        _runpy.run_module(_mod, run_name="__main__", alter_sys=True)
    except SystemExit:
        raise
    except BaseException as _e:  # noqa: BLE001
        _log(f"-m 路径 run_module 失败:{_e!r}")
        import traceback as _tb

        _tb.print_exc()
        sys.exit(1)
    sys.exit(0)


# ──────────────────────────────────────────────────────────────
# 6) 把运行时安装 torch 的可写目录(<CHAYUAN_ROOT>/py_packages/)加进 sys.path。
#
#    单机版用户点「在线安装 PyTorch」走 ``-m pip install --target
#    <CHAYUAN_ROOT>/py_packages torch ...``(见 pytorch_installer.build_install_recipe)。
#    torch 不落进只读的 _internal/,而是这个可写目录;装完重启 server 后,这里要
#    保证它在 sys.path 上,``import torch`` 才能命中。
#
#    放在 user code(Click)之前 + 在所有 frozen 子进程(API server / config
#    panel / worker)都跑,覆盖所有 ``import torch`` 路径。拿不到 CHAYUAN_ROOT /
#    目录不存在都无害(只是多一个空目录在 path 上)。
#
#    注意:section 5 的 ``-m`` 透传会 ``sys.exit()`` 不会走到这里 —— 那是 pip
#    安装子进程,本来也不该把 py_packages 抢在 pip 前面(避免半装的 torch 干扰
#    pip 自己的依赖解析)。这里只服务真正的 server 启动进程。
# ──────────────────────────────────────────────────────────────
try:
    from chayuan.server.runtime.pytorch_installer import (
        ensure_torch_target_on_syspath as _ensure_torch_path,
    )

    _added = _ensure_torch_path()
    if _added:
        _log(f"py_packages 已加入 sys.path:{_added}")
except BaseException as _e:  # noqa: BLE001
    # chayuan 包还没在 path 上 / settings 异常 —— 不阻断启动,server 启动后
    # first_launch 链路仍会触发安装;最坏情况 import torch 时再报清晰错误。
    _log(f"py_packages sys.path 注入跳过:{_e!r}")


_log("没拦截任何 spawn 协议,继续 user code")
