"""PyInstaller runtime hook: 给 pip 自带的 vendored ``distlib`` 注册 PyInstaller
loader → ResourceFinder 的映射,修复 ``-m pip install`` 在 frozen bundle 里崩。

为什么需要
----------
单机版用 ``chayuan-server.exe -m pip install --target <...> torch ...`` 在线
装可选依赖(见 multiprocessing_freeze.py section 5 的 ``-m`` 透传)。pip 跑到
import ``pip._internal.operations.install.wheel`` 时会连带 import
``pip._vendor.distlib.scripts``,后者在 **Windows** 上(``os.name == 'nt'``)
模块加载阶段就执行::

    WRAPPERS = {
        r.name: r.bytes
        for r in finder(DISTLIB_PACKAGE).iterator("")    # scripts.py:66
        if r.name.endswith(".exe")
    }

它要枚举 ``pip._vendor.distlib`` 这个包自带的可执行 wrapper stub
(``t32.exe / t64.exe / w32.exe / w64.exe / t64-arm.exe / w64-arm.exe``)。

``distlib.resources.finder(package)`` 的实现(resources.py:313)::

    loader = getattr(module, '__loader__', None)
    finder_maker = _finder_registry.get(type(loader))
    if finder_maker is None:
        raise DistlibException('Unable to locate finder for %r' % package)

``_finder_registry``(resources.py:286)只登记了:

    type(None)                    -> ResourceFinder
    zipimport.zipimporter         -> ZipResourceFinder
    _frozen_importlib_external.SourceFileLoader / FileFinder / SourcelessFileLoader
                                  -> ResourceFinder

但 frozen bundle 里 ``pip._vendor.distlib`` 的 ``__loader__`` 是 PyInstaller
的 ``PyiFrozenLoader`` 实例(pyimod02_importers.py:388),它不在这张表里 →
``DistlibException: Unable to locate finder for 'pip._vendor.distlib'`` →
pip 整个挂掉(rc=1)。

为什么用普通文件型 ResourceFinder 就够
--------------------------------------
本项目是 PyInstaller ``--onedir`` 模式:``pip/_vendor/distlib/`` 是
``_internal/`` 下的**真实目录**,里面 ``*.exe`` wrapper 是真实文件
(下面 spec 的 ``collect_data_files('pip')`` 保证它们被打进来)。

distlib 自带的 ``ResourceFinder``(resources.py:118)只靠
``module.__file__`` 的 ``os.path.dirname`` 定位资源目录,再走标准
``os.listdir`` / ``open``。``PyiFrozenLoader`` 为被 collect 的模块正确设置
了指向 ``_internal/.../__init__.py`` 的 ``__file__``,所以直接复用 distlib
自己的文件型 ``ResourceFinder`` 即可 —— 我们只需要把
``PyiFrozenLoader`` 这个 loader 类型补进 ``_finder_registry``。

(注:``--onefile`` 模式下文件在 ``_MEIPASS`` 临时解压区,也仍是真实文件,
所以这个映射对 onefile 同样有效。)

容错
----
非 frozen / pip 没打进来 / PyInstaller 版本换了类名 —— 任意一步拿不到
都安静跳过,绝不让这个 hook 自己把启动搞崩。出问题 grep ``[distlib-finder]``。
"""
from __future__ import annotations

import sys


def _log(msg: str) -> None:
    sys.stderr.write(f"[distlib-finder] {msg}\n")
    sys.stderr.flush()


def _register_pyi_distlib_finder() -> None:
    # 仅 frozen bundle 需要;源码运行时 distlib 的 loader 本来就在 _finder_registry 里。
    if not getattr(sys, "frozen", False):
        _log("非 frozen,跳过")
        return

    # 1) 拿 PyInstaller 的 frozen loader 类。类名/路径随 PyInstaller 版本可能变,
    #    所以多探几个候选;全拿不到就放弃(安静跳过)。
    loader_cls = None
    for _modname, _clsname in (
        ("PyInstaller.loader.pyimod02_importers", "PyiFrozenLoader"),
        ("pyimod02_importers", "PyiFrozenLoader"),
        # 旧版 PyInstaller(<6)用 FrozenImporter,也兜一手。
        ("PyInstaller.loader.pyimod03_importers", "FrozenImporter"),
        ("pyimod03_importers", "FrozenImporter"),
    ):
        try:
            _mod = __import__(_modname, fromlist=[_clsname])
            loader_cls = getattr(_mod, _clsname, None)
            if loader_cls is not None:
                _log(f"PyInstaller loader 类 = {_modname}.{_clsname}")
                break
        except Exception:  # noqa: BLE001
            continue

    # 兜底:从已加载模块的 __loader__ 反推 loader 类(任何 frozen 模块的 loader 都行)。
    if loader_cls is None:
        try:
            _self_loader = getattr(sys.modules.get(__name__), "__loader__", None)
            # runtime hook 自身不一定是 PyiFrozenLoader 加载的,换个稳的:用 sys 之外
            # 任意一个明显来自 PYZ 的模块。这里直接遍历找第一个 PyiFrozen* loader。
            for _m in list(sys.modules.values()):
                _ld = getattr(_m, "__loader__", None)
                if _ld is not None and type(_ld).__name__ in (
                    "PyiFrozenLoader",
                    "FrozenImporter",
                ):
                    loader_cls = type(_ld)
                    _log(f"PyInstaller loader 类(反推)= {loader_cls!r}")
                    break
            del _self_loader
        except Exception:  # noqa: BLE001
            loader_cls = None

    if loader_cls is None:
        _log("拿不到 PyInstaller loader 类,跳过(pip-in-frozen 可能仍会报 distlib 错)")
        return

    # 2) 拿 pip vendored 的 distlib.resources。pip 没打进 bundle 时安静跳过。
    try:
        from pip._vendor.distlib import resources as _distlib_resources
    except Exception as _e:  # noqa: BLE001
        _log(f"import pip._vendor.distlib.resources 失败,跳过:{_e!r}")
        return

    _registry = getattr(_distlib_resources, "_finder_registry", None)
    _resource_finder = getattr(_distlib_resources, "ResourceFinder", None)
    if _registry is None or _resource_finder is None:
        _log("distlib.resources 结构异常(无 _finder_registry / ResourceFinder),跳过")
        return

    # 3) 把 PyInstaller loader 类型 → 文件型 ResourceFinder 注册进去。
    #    onedir/onefile 下 pip/_vendor/distlib/*.exe 都是真实文件,
    #    标准 ResourceFinder(靠 __file__ 定位目录)直接可用。
    if loader_cls in _registry:
        _log(f"{loader_cls.__name__} 已在 _finder_registry,无需重复注册")
        return
    _registry[loader_cls] = _resource_finder
    _log(f"已注册 {loader_cls.__name__} → ResourceFinder")


try:
    _register_pyi_distlib_finder()
except Exception as _e:  # noqa: BLE001
    # 任何意外都不能让这个 hook 把 server 启动带崩。
    _log(f"hook 自身异常,已忽略:{_e!r}")
