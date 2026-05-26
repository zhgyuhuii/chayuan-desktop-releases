# -*- mode: python ; coding: utf-8 -*-
# 单机版 chayuan-server PyInstaller 配置。
#
# 目标:把 chayuan-server 打成 ``--onedir`` 产物,Tauri 桌面客户端通过
# ``resources`` 把整个目录 bundle 进安装包,运行时 sidecar.rs 用
# ``resource_dir()/chayuan-server/chayuan-server.exe`` 起子进程。启动时以
# ``CHAYUAN_ROOT=<dataDir>`` env 注入用户首启动向导选定的数据目录。
#
# 模式:**onedir**(2026-05-19 从 onefile 切过来)。
#
# 切换动机:
#   1. onefile 解压 726 MB bundle 到 %TEMP%\_MEIxxxxx 要 100-150s,Tauri /healthz
#      60s 探针必超时,主界面进不去。
#   2. 把 torch CPU + torchvision + transformers 打进 bundle 需要 c10.dll /
#      torch_cpu.dll 等 ~30 个 native DLL 按固定布局摆好。onefile 解压时
#      DLL 加载顺序不可控,触发经典 ``c10.dll WinError 1114``。
#   3. onedir 把 _internal/ 跟 exe 放一起,DLL 路径稳定,torch import 链全通。
#
# Tauri 集成:
#   - 不再用 ``externalBin``(只能嵌单 exe,_internal/ 不会跟着进安装包)。
#   - 改用 ``resources: ["chayuan-server/**/*"]``,build.py 把 dist/chayuan-server/
#     整个拷到 ``apps/desktop/src-tauri/resources/chayuan-server/``,Tauri bundle
#     时一并打进 NSIS / MSI / dmg。
#   - sidecar.rs 用 ``app.path().resolve("chayuan-server/chayuan-server.exe",
#     BaseDirectory::Resource)`` 拿可执行文件路径,直接 spawn(不走 plugin-shell
#     的 sidecar() helper)。
#
# 用法:
#     poetry run pyinstaller packaging/pyinstaller/chayuan-server.spec --noconfirm
#     poetry run python packaging/pyinstaller/build.py    # 上述 + 拷到 desktop/binaries
#
# 体积说明(详见 README):v0 单机包预计 1.8-2.2 GB(paddleocr 1.2 GB / faiss 120 MB
# / onnxruntime 70 MB)。Phase 5 把 paddle / faiss / unstructured / nicegui 等
# 异步剥离后,目标 < 600 MB。
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (  # noqa: F401  (spec 由 PyInstaller 注入 SPECPATH)
    collect_all,
    collect_data_files,
    collect_submodules,
)


# ──────────────────────────────────────────────────────────────
# 路径
# ──────────────────────────────────────────────────────────────

# packaging/pyinstaller/ → chayuan-server/(repo root for chayuan-server)
ROOT = Path(SPECPATH).resolve().parent.parent
SERVER_PKG = ROOT / "libs" / "chayuan-server"

# 入口(Click main)。chayuan/__main__.py 不存在时回退到 cli.py。
ENTRY = SERVER_PKG / "chayuan" / "__main__.py"
if not ENTRY.is_file():
    ENTRY = SERVER_PKG / "chayuan" / "cli.py"


# ──────────────────────────────────────────────────────────────
# Hidden imports(动态 import 的模块)
# ──────────────────────────────────────────────────────────────

hidden_modules: list = []
hidden_modules += collect_submodules("chayuan")
# chat.handlers 包内 8 个 mode handler 由 handlers/__init__._load_builtins 用
# importlib.import_module(string) 动态加载,PyInstaller AST 静态扫描偶尔会漏
# (历史 incident 2026-05-24:supervisor.py 在 PYZ 缺失 -> ModuleNotFoundError ->
# 整个 _load_builtins 链虽然 try/except 但 kb handler 也跟着没注册 ->
# chat 选了 KB 但 retrieve 返空 chunks)。显式列出,杜绝静态分析漏抓。
hidden_modules += [
    "chayuan.server.chat.handlers",
    "chayuan.server.chat.handlers.base",
    "chayuan.server.chat.handlers.llm",
    "chayuan.server.chat.handlers.agent",
    "chayuan.server.chat.handlers.kb",
    "chayuan.server.chat.handlers.file",
    "chayuan.server.chat.handlers.search_engine",
    "chayuan.server.chat.handlers.multi_source",
    "chayuan.server.chat.handlers.vision",
    "chayuan.server.chat.handlers.supervisor",
]
hidden_modules += collect_submodules("langchain")
hidden_modules += collect_submodules("langchain_core")
hidden_modules += collect_submodules("langchain_openai")
hidden_modules += collect_submodules("langchain_community")
hidden_modules += collect_submodules("langchain_text_splitters")
hidden_modules += collect_submodules("langchain_classic")
hidden_modules += collect_submodules("langchain_ollama")
hidden_modules += collect_submodules("langchain_experimental")
hidden_modules += collect_submodules("uvicorn")
hidden_modules += collect_submodules("fastapi")
hidden_modules += collect_submodules("starlette")
hidden_modules += collect_submodules("sse_starlette")
hidden_modules += collect_submodules("nicegui")
hidden_modules += collect_submodules("onnxruntime")
hidden_modules += collect_submodules("sqlalchemy")
hidden_modules += collect_submodules("pydantic")
hidden_modules += collect_submodules("openai")
hidden_modules += collect_submodules("tiktoken")
# unstructured: RapidOCRDocLoader / mypdfloader / mydocloader 内部用 unstructured
# 处理 .docx / .pdf / .ppt;它有大量动态 import 和 data file (nlp/english-words.txt
# 等)需要显式收集。漏了用户上传 docx 时直接 FileNotFoundError english-words.txt。
hidden_modules += collect_submodules("unstructured")
hidden_modules += collect_submodules("nltk")
hidden_modules += collect_submodules("sqlite_vec")
# huggingface_hub:install_job 在线下载 bundled_models 时 import,
# 动态用 hf_hub_download / snapshot_download,静态分析挖不到子模块。
hidden_modules += collect_submodules("huggingface_hub")

# tiktoken 在 ``encoding_for_model`` 里 ``importlib.import_module`` 加载 ext;
# PyInstaller 静态分析挖不到,显式列出。
hidden_modules += [
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
    "click",
    "rich",
    "pydantic_settings",
]

# pip —— 单机版运行时要靠 ``chayuan-server.exe -m pip install ...`` 装可选依赖
# (在线 PyTorch、redis/arq 等)。frozen 入口是 Click CLI,业务代码没有 ``import
# pip``,PyInstaller 静态分析挖不到 pip,默认**不会**打进 bundle ── 装机后
# ``-m pip`` 即便被 runtime hook 透传(见 multiprocessing_freeze.py section 5)
# 也会 ``ModuleNotFoundError: No module named 'pip'``。这里显式 collect_submodules
# 把整套 pip 子模块拉进 hiddenimports;pip 的 vendored data(cacert.pem 等)
# 由下面 _collect_safe("pip") 一并收。setuptools / pkg_resources 是 pip 解依赖 /
# 装 wheel 时的运行期依赖,一并带上。
hidden_modules += collect_submodules("pip")
hidden_modules += [
    "pip",
    "setuptools",
    "pkg_resources",
]


# ──────────────────────────────────────────────────────────────
# Data files(yaml 模板 / 静态资源 / 字体 / 证书)
# ──────────────────────────────────────────────────────────────

datas: list = []

# 第三方包带的 data(tiktoken cl100k_base 等编码、rapidocr 模型权重等)
# ⚠ unstructured 必须在,RapidOCRDocLoader / mypdf / mydoc / myppt 内部
# 都依赖 unstructured 库,它要读 nlp/english-words.txt 等 data file。漏了 →
# 用户上传 .docx / .doc / .pdf 时报:
#   FileNotFoundError: [Errno 2] No such file or directory:
#     '...\_internal\unstructured\nlp\english-words.txt'
# (2026-05-23 实测,KB xxx 的 deepseek apikey 内容就是这样丢的)
for pkg in (
    "tiktoken",
    "rapidocr_onnxruntime",
    "unstructured",
    "nltk",
    "openai",
    "fastapi",
    "starlette",
    "uvicorn",
    "nicegui",
    "click",
    "rich",
    "tabulate",
):
    try:
        d = collect_data_files(pkg)
        if d:
            datas.extend(d)
    except Exception:
        # collect_data_files 在包不存在时抛 ModuleNotFoundError;允许跳过
        pass

# chayuan-server 自带的 yaml seed / static / templates。
# 注意:每行都对应业务代码里硬编码的相对路径,缺哪个 FastAPI 起来时就抛
# RuntimeError: Directory '...' does not exist。新增静态目录时这里同步补。
for sub in (
    # FastAPI 离线 swagger-ui / redoc 资源(MakeFastAPIOffline 加载)
    "chayuan/server/api_server/static",
    # 配置面板:Jinja templates + 静态资源
    "chayuan/server/config_panel/static",
    "chayuan/server/config_panel/templates",
    # /img 路由:logo / 模型 logo / tray icon(server_app.py 用 IMG_DIR 挂载)
    "chayuan/img",
    # 包内 seed 数据(只读,与 CHAYUAN_ROOT/data 用户数据目录区分):
    # - tools_catalog.json:工具目录
    # - ai_platform_config/:模型平台默认配置
    # - knowledge_base/info.db + samples/:首次启动时拷贝到 KB_ROOT_PATH
    # cli.py:408 的 PACKAGE_ROOT/data/... 引用全部依赖这里。
    "chayuan/data",
    # YAML seed 文件(prompt_settings 等)
    "chayuan/data_seed",
    # 服务端打包资源(图片 / 字体等)
    "chayuan/server/_assets",
    # 用户手册资源(Markdown 模板;首启时拷贝并生成 docx 到 <CHAYUAN_ROOT>/manuals/)
    "chayuan/server/manuals/resources",
):
    src = SERVER_PKG / sub
    if src.is_dir():
        datas.append((str(src), sub))

# ──────────────────────────────────────────────────────────────
# 项目内固定模型槽:把 <repo>/vendor/bundled_models/ 整树打进 sidecar 资源。
#
# 运行时 ``local_index.bundled_models_dir()`` 优先解析 ``sys._MEIPASS /
# bundled_models``,然后被 ``first_launch.seed_bundled_models()`` 拷贝到
# ``<CHAYUAN_ROOT>/models/bundled/``。约定见 ``vendor/bundled_models/README.md``。
#
# 注意:layout.yaml release(lite / standard / pro)决定打包前**哪些**模型实际
# 落到这个目录;空目录(只有 .gitkeep)不会真的产生用户可见文件,生效的是
# 由 ``chayuan_packaging fetch <release>`` 预拉的权重。
# ──────────────────────────────────────────────────────────────

# 2026-05-15:bundled_models **不再** 注入 PyInstaller datas。
# 原因:嵌入后 sidecar 单 exe ≥ 2 GB,触发 NSIS 32-bit makensis mmap 上限
# (failed creating mmap of chayuan-server-*.exe),Tauri 2 NSIS bundler 不暴露
# SetCompressor 选项,无法绕过。
#
# 新约定:bundled_models 改由 Tauri **resources** 字段承载 ——
#   build.py 把 ``<repo>/vendor/bundled_models/`` 拷到
#   ``chayuan-client/apps/desktop/src-tauri/bundled_models/``;tauri.conf.json
#   把它列在 bundle.resources;NSIS installer 装机后落到 ``<install_dir>/bundled_models/``。
#   sidecar(externalBin)跟 main exe 同目录,argv0.parent / 'bundled_models'
#   直接命中(见 local_index.bundled_models_dir() resolver)。
#
# 环境变量 CHAYUAN_LITE_BUILD 仍然保留兼容性(build script 据此决定要不要
# 拷模型到 src-tauri/bundled_models/),但对 spec 本身已是 no-op。


# ──────────────────────────────────────────────────────────────
# 二进制扩展(sqlite-vec)
#
# 把对应平台的 ``vec0.so`` / ``vec0.dylib`` / ``vec0.dll`` 放到
# ``packaging/vendor/sqlite-vec/`` 即可被 spec 自动嵌入。
# 来源:https://github.com/asg017/sqlite-vec/releases
# ──────────────────────────────────────────────────────────────

binaries: list = []
sqlite_vec_dir = ROOT / "packaging" / "vendor" / "sqlite-vec"
if sqlite_vec_dir.is_dir():
    for f in sqlite_vec_dir.iterdir():
        if f.suffix.lower() in (".so", ".dylib", ".dll"):
            binaries.append((str(f), "."))


# ──────────────────────────────────────────────────────────────
# PyTorch + torchvision **离线 wheel** — 2026-05-18 起改走 ISO external seed,
# 不再嵌进 sidecar exe(spec 这一段刻意留空 + 文档说明)。
#
# 历史:这里曾把 vendor/torch_wheels/<sub>/*.whl 一份份 append 到 datas →
# PyInstaller `--onefile` 嵌进 sidecar exe → 装机后 sys._MEIPASS 解压。
# 单 CPU wheel ~250MB 没问题,但 cu124 wheel ~2.5GB 撞 MSI 2GB 单文件限制
# (LGHT0263),sidecar exe 自身已 ~840MB,再嵌任何 CUDA wheel 必爆。
#
# 新链路(对齐 bundled_models 的 external_seed 设计):
#   1. build.py sync_torch_wheels()  把 vendor/torch_wheels/ 拷到
#      dist-<flavor>/torch_wheels_seed/  (跟 models_seed/ 一样进 ISO 不进 MSI)
#   2. ISO 打包阶段把 torch_wheels_seed/ 嵌进 .iso 介质根
#   3. 装机后 first_launch.seed_torch_wheels() 从 ISO 介质拷到
#      <CHAYUAN_ROOT>/torch_wheels/<variant>/
#   4. pytorch_installer 装 wheel 时优先扫 CHAYUAN_ROOT/torch_wheels/
#
# 这样:
#   - sidecar exe 缩小 ~250MB(CPU 版),启动期 _MEIPASS 解压更快
#   - 未来加 CUDA wheel 不再受 MSI 限制(走 ISO 几 GB 都行)
#   - 用户可以手动加 cu* wheel 到 CHAYUAN_ROOT/torch_wheels/,不重新打包
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# macOS:把构建机 Python 实际链接的 OpenSSL(libssl/libcrypto)注入 bundle 根
#
# 必须在 Analysis() **之前**塞 binaries 列表(走 PyInstaller 标准依赖收集 +
# 归一化通道);写在 Analysis() 之后 append a.binaries 在 onefile 模式不会被
# 后续 EXE 阶段拾取(本文件文件头注释也明确警告)。
#
# 为什么要手动加:Python 3.12 的 _ssl.cpython-312-darwin.so 通过
# @rpath/libssl.3.dylib 引用 OpenSSL,但 PyInstaller 静态分析没法从 @rpath 解
# 到具体源,所以**不会自动收集**;cv2 wheel 自带的 .dylibs/libssl.3.dylib 是
# 旧版 OpenSSL 3.0.x(缺 X509_STORE_get1_objects 等 3.2 新增符号),会让
# _ssl 在加载时报符号缺失 / 库找不到。
#
# 解决:让构建机的 Python 自己 ``import ssl`` 触发 dyld 加载真版 libssl/libcrypto,
# 然后通过 ``_dyld_image_count`` / ``_dyld_get_image_name`` 直接读出当前进程
# 加载的真实文件路径。conda / pyenv / homebrew / python.org 全适配,无需配置。
if sys.platform == "darwin":
    import os as _os

    def _dyld_loaded_dylibs():
        """返回当前进程 dyld 已加载的所有动态库路径(原始路径,不 resolve)。"""
        import ctypes
        libsys = ctypes.CDLL(None)
        libsys._dyld_image_count.restype = ctypes.c_uint32
        libsys._dyld_get_image_name.restype = ctypes.c_char_p
        libsys._dyld_get_image_name.argtypes = [ctypes.c_uint32]
        out: list[Path] = []
        for i in range(libsys._dyld_image_count()):
            n = libsys._dyld_get_image_name(i).decode("utf-8", errors="replace")
            out.append(Path(n))
        return out

    print("[chayuan-spec] ===== macOS OpenSSL 注入开始 =====")
    print(f"[chayuan-spec] sys.executable = {sys.executable}")
    print(f"[chayuan-spec] sys.prefix     = {sys.prefix}")
    print(f"[chayuan-spec] sys.platform   = {sys.platform}")
    try:
        import ssl as _spec_ssl_probe
        import _ssl as _spec_ssl_native
        print(f"[chayuan-spec] ssl.OPENSSL_VERSION = {_spec_ssl_probe.OPENSSL_VERSION}")
        print(f"[chayuan-spec] _ssl.__file__       = {_spec_ssl_native.__file__}")
    except Exception as _e:
        print(f"[chayuan-spec] !! 构建机 Python 自身 import ssl 失败:{_e}")
        raise SystemExit(
            f"[chayuan-spec] FATAL: 构建机 Python 不能 import ssl({_e})。"
            f"请检查 Python 安装或 conda 环境,Python 自身 ssl 都坏的情况下"
            f"打出来的 sidecar 100% 跑不起来。"
        )

    # 全量打印 dyld 已加载的 libssl/libcrypto 候选,便于排查多版本同时在场。
    _all_imgs = _dyld_loaded_dylibs()
    _ssl_candidates = [p for p in _all_imgs
                       if p.name.startswith("libssl.") or p.name.startswith("libcrypto.")]
    print(f"[chayuan-spec] dyld 总加载 dylib 数 = {len(_all_imgs)}")
    print(f"[chayuan-spec] 其中 libssl/libcrypto 命中 {len(_ssl_candidates)} 个:")
    for _p in _ssl_candidates:
        print(f"  · {_p}")

    # 关键修复:**不能** Path.resolve()。conda / Homebrew 上
    # ``libssl.3.dylib`` 通常是指向 ``libssl.3.X.dylib`` 真实文件的 symlink。
    # 一旦 resolve(),basename 就变成 ``libssl.3.X.dylib``;PyInstaller 按 src
    # basename 写入 bundle,_ssl.so 的 @rpath/libssl.3.dylib 就找不到了 ── 这正是
    # 反复出现 "Library not loaded @rpath/libssl.3.dylib" 的根因。
    #
    # 正确做法:取 dyld 报的原始路径(通常本身就是 canonical 名),src 直接是 symlink
    # 路径(PyInstaller copyfile 会跟随 symlink 读真实文件,但 dest basename 用 src
    # 路径的 basename),这样落到 bundle 里就是 _ssl 期望的 ``libssl.3.dylib``。
    def _pick_canonical(prefix: str) -> Path | None:
        """从 dyld 候选里挑 basename 最接近 ``<prefix>3.dylib``(canonical SONAME)的那一个。"""
        canonical = f"{prefix}3.dylib"   # libssl.3.dylib / libcrypto.3.dylib
        # 1. 优先 basename 完全等于 canonical 的(symlink 或同名文件)
        for p in _all_imgs:
            if p.name == canonical:
                return p
        # 2. 其次 basename 以 prefix 开头的第一个(real file like libssl.3.5.dylib)
        for p in _all_imgs:
            if p.name.startswith(prefix):
                return p
        return None

    _ossl_entries: list[tuple[str, str]] = []     # (dest_basename, src_path)
    _ossl_lib_dir: Path | None = None
    for _prefix in ("libcrypto.", "libssl."):
        _picked = _pick_canonical(_prefix)
        if _picked is None:
            continue
        _ossl_lib_dir = _picked.parent
        _canonical = f"{_prefix}3.dylib"
        # 取 canonical symlink:同目录下的 libssl.3.dylib / libcrypto.3.dylib
        _canon_path = _picked.parent / _canonical
        if _canon_path.exists():
            _ossl_entries.append((_canonical, str(_canon_path)))
        else:
            # canonical 不存在(罕见),退而求其次用 dyld 原始路径,但显式指定 dest 名为 canonical
            _ossl_entries.append((_canonical, str(_picked)))

    # 路径兜底(conda env / Homebrew / OPENSSL_LIBDIR)── dyld 没命中时用
    if not _ossl_entries:
        _dirs: list[Path] = []
        _override = _os.environ.get("OPENSSL_LIBDIR", "").strip()
        if _override:
            _dirs.append(Path(_override))
        _dirs.extend([
            Path(sys.prefix) / "lib",
            Path("/opt/homebrew/opt/openssl@3/lib"),
            Path("/usr/local/opt/openssl@3/lib"),
            Path("/opt/homebrew/lib"),
            Path("/usr/local/lib"),
        ])
        for _libname in ("libcrypto.3.dylib", "libssl.3.dylib"):
            for _d in _dirs:
                _full = _d / _libname
                if _full.exists():
                    _ossl_entries.append((_libname, str(_full)))
                    _ossl_lib_dir = _d
                    break

    if not _ossl_entries or len({n for n, _ in _ossl_entries}) < 2:
        # 找不到任意一个 ── 直接 fail build,避免再产出一个运行时挂的 sidecar。
        print("[chayuan-spec] !! FATAL: 没找到 libssl.3.dylib / libcrypto.3.dylib 的有效来源。")
        raise SystemExit(
            "[chayuan-spec] FATAL: macOS bundle 必需的 OpenSSL 来源缺失。\n"
            "  期望:dyld 已加载 libssl.3.dylib + libcrypto.3.dylib,或 OPENSSL_LIBDIR 指向有效目录。\n"
            "  现已收集:" + str(_ossl_entries) + "\n"
            "  解决:在构建机 ``poetry shell`` 里跑 ``python -c \"import ssl; print(ssl.OPENSSL_VERSION)\"`` 确认 ssl 可用,"
            "或者 ``export OPENSSL_LIBDIR=/opt/homebrew/opt/openssl@3/lib`` 后重跑构建。"
        )

    # ⚠ 关键修复:_src 通常是 symlink(libssl.3.dylib → libssl.3.5.dylib)。
    # PyInstaller 6.20 处理 symlink 源时会把 symlink 关系存进 bundle,但目标
    # 文件 ``libssl.3.5.dylib`` 没跟着进去 ── runtime os.walk / find 看得到
    # ``libssl.3.dylib``(目录条目),os.stat 跟随后 ENOENT,dyld 同理报
    # "Library not loaded @rpath/libssl.3.dylib"。
    #
    # 解法:在构建期就把 symlink resolve 到真实字节,以 canonical 名拷贝到
    # tmpdir,生成"叫 libssl.3.dylib 的真实 Mach-O 文件"。然后以这个 tmp 路径
    # 作为 src 喂 PyInstaller ── PyInstaller 看到的是普通文件,没 symlink 可玩。
    import shutil as _shutil
    import tempfile as _tempfile
    _ossl_tmpdir = Path(_tempfile.mkdtemp(prefix="chayuan-ossl-"))
    print(f"[chayuan-spec] 临时落盘目录:{_ossl_tmpdir}")
    _ossl_resolved: list[tuple[str, str]] = []
    for _dest_name, _src in _ossl_entries:
        _real = Path(_src).resolve()      # 跟随 symlink 到真实字节
        _dst = _ossl_tmpdir / _dest_name  # canonical 名 + 真实字节 = 普通文件
        _shutil.copy2(str(_real), str(_dst), follow_symlinks=True)
        _ossl_resolved.append((_dest_name, str(_dst)))
        print(f"[chayuan-spec]   {_dest_name}: symlink {_src} → 真实 {_real} → 拷贝到 {_dst}")
    # 把 resolved 之后的 (canonical 名, tmp 普通文件路径) 替换原 entries
    _ossl_entries = _ossl_resolved

    # 把 OpenSSL 通过 (src, dest_dir) 2-元组塞 binaries(走 Analysis 的标准依赖归一化通道)。
    # 此时 src 已是 tmpdir 里的普通文件,basename 就是 canonical 名,不会被 symlink 误导。
    print("[chayuan-spec] 即将注入 OpenSSL(canonical SONAME → tmp 真实文件):")
    for _dest_name, _src in _ossl_entries:
        binaries.append((_src, "."))
        print(f"  + {_dest_name} ← {_src}")

    print("[chayuan-spec] ===== macOS OpenSSL 注入完成 =====")


# ──────────────────────────────────────────────────────────────
# 排除项(单机版用不到)
# ──────────────────────────────────────────────────────────────

# 2026-05-19 起 build.py 在 lite flavor 跑 PyInstaller 时设 CHAYUAN_LITE_BUILD=1。
# 此前注释说"对 spec 已是 no-op",2026-05-19 起重新生效:
# lite 没有 image-embedding cap (LITE_CAPS = embedding/rerank/asr/ocr/tts),
# torch+torchvision+transformers (~480 MB) 在 lite bundle 里是死重量 — 这里
# 据 LITE_BUILD 把它们从 _collect_required 拿掉 + 加进 excludes。
# ⚠ 图像向量化(image-embedding)能力已下线 —— 它是系统里**唯一**硬依赖
# torch / torchvision / transformers 的能力。这里强制 LITE_BUILD=True,让
# 下方 _collect_required 永远跳过这三个,full 版安装包 -480MB。
# 要恢复图像功能:把下行改回 `os.environ.get("CHAYUAN_LITE_BUILD", "0") == "1"`,
# 并同步打开 pyproject 的 torch / transformers / image extras 注销。
LITE_BUILD = True  # 强制 True;原:os.environ.get("CHAYUAN_LITE_BUILD", "0") == "1"

excludes = [
    # Celery / Redis 链路:单机模式走 asyncio.Queue + threadpool
    "celery",
    "kombu",
    "billiard",
    "amqp",
    # arq + redis 异步队列:同上
    "arq",
    "redis",
    "aioredis",
    # 多用户 PostgreSQL 驱动:单机走 sqlite
    "psycopg2",
    "psycopg",
    "asyncpg",
    # 测试 / 类型工具
    "pytest",
    "mypy",
    "black",
    # 2026-05-19 切 --onedir:torch CPU + torchvision + transformers 进 bundle,
    # image-embedding(HF CLIP)直接 in-process import,不再依赖外部 wheel + pip。
    # ▸ torchaudio:语音走 whisper.cpp / piper,torch 系不依赖 audio backend → 排除
    # ▸ sentence_transformers:embedding/rerank 走 llama-server GGUF,跟 ST 不直接
    #   依赖 → 排除节省 ~200MB
    # ▸ rerankers:同上,rerank 走 llama-server cross-encoder GGUF → 排除
    "torchaudio",
    "sentence_transformers",
    "rerankers",
    # PaddleOCR 系:Phase 5 已从 pyproject 砍掉;此处兜底防 transitive 引入
    "paddle",
    "paddleocr",
    "paddlepaddle",
    # ─── bundle 瘦身(排除运行时不需要、但被 langchain hook 拉进来的大头) ───
    # nltk 自带的测试套件 / 示例 app / 演示数据:仅业务用到的 token / corpus 走
    # 运行时按需加载,这些训练演示模块完全打不进 sidecar。
    "nltk.test",
    "nltk.app",
    "nltk.book",
    "nltk.chat",
    # 注意:``nltk.misc`` 不能排除,nltk/__init__.py:203 直接 ``from nltk import misc``
    # 排除会触发运行时 ImportError: cannot import name 'misc' from partially initialized module。
    "nltk.corpus.europarl_raw",
    # onnxruntime 训练 / 模型转换 / 量化工具:推理用不到。前面的注释也明确说
    # 单机版仅做 ONNX 推理。
    "onnxruntime.transformers",
    "onnxruntime.tools",
    "onnxruntime.training",
    "onnxruntime.quantization",
    "onnxruntime.datasets",
    # sklearn 测试和实验性 API
    "sklearn.tests",
    "sklearn.experimental",
    # 其它深度学习框架 / 训练加速:已被 torch 系排除,这里把 transitive 入口堵死
    "tensorflow",
    "tensorflow_datasets",
    "tensorflow_hub",
    "keras",
    "jax",
    "jaxlib",
    "flax",
    "optax",
    "xgboost",
    "lightgbm",
    "catboost",
    # 各 lib 自带测试 / 示例 / 文档资源(matplotlib 一个 tests/ 就 100+ MB)
    "matplotlib.tests",
    "scipy.tests",
    "pandas.tests",
    "numpy.tests",
    "PIL.tests",
    "pyarrow.tests",
    "lxml.tests",
    # langchain 海量可选集成(用不到的第三方平台)── 项目代码没引用的全砍。
    # 业务代码以后真用到时,把对应一行从这里删掉再重跑构建。
    "langchain_classic.tools.azure_cognitive_services",
    "langchain_classic.tools.gmail",
    "langchain_classic.tools.office365",
    "langchain_classic.tools.playwright",
    "langchain_classic.tools.amadeus",
    "langchain_classic.tools.zapier",
    "langchain_classic.tools.eleven_labs",
    "langchain_classic.tools.edenai",
    "langchain_classic.tools.gitlab",
    "langchain_classic.tools.steam",
    "langchain_classic.tools.nasa",
    "langchain_classic.tools.bing_search",
    "langchain_classic.tools.brave_search",
    "langchain_classic.tools.golden_query",
    "langchain_classic.tools.metaphor_search",
    "langchain_classic.tools.searchapi",
    "langchain_classic.tools.searx_search",
    "langchain_classic.tools.tavily_search",
    "langchain_classic.tools.google_finance",
    "langchain_classic.tools.google_jobs",
    "langchain_classic.tools.google_lens",
    "langchain_classic.tools.google_places",
    "langchain_classic.tools.google_scholar",
    "langchain_classic.tools.google_serper",
    "langchain_classic.tools.google_trends",
    "langchain_classic.tools.reddit_search",
    "langchain_classic.tools.slack",
    "langchain_classic.tools.steamship_image_generation",
    "langchain_classic.tools.openweathermap",
    "langchain_classic.tools.scenexplain",
    "langchain_classic.tools.merriam_webster",
    "langchain_classic.tools.dataforseo_api_search",
    "langchain_classic.tools.bearly",
    "langchain_classic.tools.e2b_data_analysis",
    "langchain_classic.tools.multion",
    "langchain_classic.tools.nuclia",
    "langchain_classic.tools.clickup",
    "langchain_classic.chat_loaders.facebook_messenger",
    "langchain_classic.chat_loaders.gmail",
    "langchain_classic.chat_loaders.imessage",
    "langchain_classic.chat_loaders.slack",
    "langchain_classic.chat_loaders.telegram",
    "langchain_classic.chat_loaders.whatsapp",
    "langchain_community.tools.azure_cognitive_services",
    "langchain_community.tools.office365",
    "langchain_community.tools.gmail",
    "langchain_community.tools.playwright",
    # PyMuPDF / faker:tests 资源
    "faker.tests",
    # ipython / jupyter:不用 notebook
    "IPython",
    "ipykernel",
    "ipywidgets",
    "jupyter",
    "notebook",
    "qtconsole",
]


# ──────────────────────────────────────────────────────────────
# Analysis
# ──────────────────────────────────────────────────────────────

block_cipher = None

# 大依赖一锅端:用 ``collect_all`` 收集 datas + binaries + hiddens,
# 减少手填 entry。注意 collect_all 返回顺序是 ``(datas, binaries, hiddenimports)``,
# 别按字母顺序解包成 (bins, dat, hids) —— 之前那样写会把 datas 塞进 binaries TOC,
# 后续 EXE() 归一化阶段抛 ``ValueError: not enough values to unpack (expected 3, got 2)``。
#
# 必须在 Analysis() **之前**累加进 binaries/datas/hidden_modules 这三个普通 list,
# 让 Analysis 自己把 2-元组规范化成 3-元组;不能等 Analysis 跑完再 append 到
# ``a.binaries`` / ``a.datas`` —— 那是已规范化的 TOC,塞 2-元组就坏。
def _collect_safe(pkg: str):
    """安全调 ``collect_all``:包不在(瘦构建)时跳过,不影响其它依赖。返回 (datas, binaries, hiddens)。"""
    try:
        return collect_all(pkg)
    except Exception:
        return [], [], []


def _collect_required(pkg: str):
    """``collect_all`` 但包不在就 fail-fast。

    给 torch / torchvision / transformers 等"必须打进 bundle"的依赖用 ──
    poetry 环境漏装这些,_collect_safe 会静默返空 → 装机后 ``import torch``
    报 ModuleNotFoundError,用户/QA 才发现 bundle 漏了大件,反复重打浪费时间。
    fail-fast 让构建在 spec 阶段就炸,把"依赖没装"的问题怼到第一时间。
    """
    try:
        return collect_all(pkg)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(
            f"[chayuan-spec] FATAL: collect_all({pkg!r}) 失败 — 构建环境缺 "
            f"{pkg!r}。先 ``poetry add {pkg}``(注意 torch 选 +cpu 变体)再重打。"
            f"\n  原始错误:{type(e).__name__}: {e}"
        )


for big in (
    "faiss",
    "PyMuPDF",
    "fitz",
    "rapidocr_onnxruntime",
    "onnxruntime",
    # pip:运行时 ``-m pip install`` 需要 pip 自带的 vendored 资源
    # (_vendor/certifi/cacert.pem 等)。collect_submodules 已加进 hiddenimports,
    # 这里 collect_all 把 datas/binaries 一并带上。setuptools 同理(装 wheel 时用)。
    # ⚠ collect_all("pip") 已包含 pip._vendor.distlib 的 6 个 wrapper
    # *.exe(t32/t64/w32/w64/t64-arm/w64-arm),装 wheel 时 distlib.scripts
    # 在 import 阶段就要枚举它们;下面还会再显式 collect 一次兜底。
    "pip",
    "setuptools",
    # 注意:
    # - ``nltk`` 已被 _pyinstaller_hooks_contrib 的 hook-nltk.py 接管,
    #   再 collect_all 会把整个 nltk.test / nltk.app(几百 MB)拉进来。
    # - ``sqlite_vec`` 不是真正意义上的 Python 包(__init__ 是 stub),
    #   collect_data_files / collect_dynamic_libs 会报 warning,实际二进制
    #   走前面的 ``packaging/vendor/sqlite-vec/`` 兜底。
):
    _dat, _bin, _hid = _collect_safe(big)
    datas.extend(_dat)
    binaries.extend(_bin)
    hidden_modules.extend(_hid)


# ──────────────────────────────────────────────────────────────
# pip._vendor.distlib 的 wrapper *.exe / *.cfg —— 显式兜底收集。
#
# 背景:``chayuan-server.exe -m pip install ... torch`` 装 wheel 时,
# pip import ``pip._vendor.distlib.scripts``,该模块在 Windows 上(os.name
# == 'nt')于 *import 阶段* 就执行
#     WRAPPERS = {r.name: r.bytes
#                 for r in finder('pip._vendor.distlib').iterator("")
#                 if r.name.endswith('.exe')}
# 枚举 distlib 自带的可执行 wrapper stub(t32/t64/w32/w64/t64-arm/w64-arm.exe)。
# 这些是**非 .py 资源**,必须真实落进 ``_internal/pip/_vendor/distlib/``,
# 否则 iterator 枚举为空,后续装 console_scripts 入口时拿不到 launcher。
#
# 上面 collect_all("pip") 当前已经把这 6 个 exe 收进 datas;这里再用
# collect_data_files 显式收一次(幂等,PyInstaller COLLECT 阶段对重复
# 目标去重),把"distlib 数据文件必须在场"这个约束写死、不依赖 collect_all
# 的内部行为。注意:对应的 finder 注册见 runtime_hooks/distlib_finder_freeze.py
# —— 数据文件在场 + finder 注册到位,缺一不可。
try:
    _distlib_data = collect_data_files("pip._vendor.distlib")
    if _distlib_data:
        datas.extend(_distlib_data)
        print(f"[chayuan-spec] 已显式收集 pip._vendor.distlib 数据文件 {len(_distlib_data)} 个")
except Exception as _e:  # noqa: BLE001
    # pip 没装(瘦构建)/ distlib 结构变化 —— 不致命,collect_all("pip") 仍兜着。
    print(f"[chayuan-spec] collect_data_files('pip._vendor.distlib') 跳过:{_e!r}")


# torch / torchvision / transformers — full 版必装,缺则 fail-fast(见 _collect_required 注释)。
# torch CPU 250MB + torchvision 30MB + transformers ~200MB,加进 bundle 后 image-embedding
# 的 hf_clip / hf_dinov2 / timm_vision loader 可以直接 in-process import,不再走外部
# pip install + torch_wheels seed 那套(从未真正工作过的)弯路。
#
# lite 版没有 image-embedding cap(LITE_CAPS = embedding/rerank/asr/ocr/tts),这 480 MB
# 就是死重量 → 直接跳过 _collect_required 让 PyInstaller 不打它们,bundle 体积 1.6 GB → 1.1 GB
# 左右。业务代码全是 try/except 里的 lazy import torch / transformers,bundle 里没有
# 不会触发 ImportError 在静态分析阶段;运行时如果业务真碰到 image-embedding 链路,
# 会优雅 fail 并落到 fallback。
#
# ⚠ full 构建环境要求:torch 必须是 ``+cpu`` 变体(``pip install torch --index-url
#   https://download.pytorch.org/whl/cpu``),否则会拉 CUDA stub DLL(+500MB)。
if LITE_BUILD:
    print("[chayuan-spec] CHAYUAN_LITE_BUILD=1 → 跳过 torch/torchvision/transformers,把它们加进 excludes")
    excludes.extend(["torch", "torchvision", "transformers"])
else:
    for required in ("torch", "torchvision", "transformers"):
        _dat, _bin, _hid = _collect_required(required)
        datas.extend(_dat)
        binaries.extend(_bin)
        hidden_modules.extend(_hid)


a = Analysis(
    [str(ENTRY)],
    pathex=[str(SERVER_PKG)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_modules,
    hookspath=[str(ROOT / "packaging" / "pyinstaller" / "hooks")],
    runtime_hooks=[
        # ⚠ tiktoken 必须排在 multiprocessing_freeze **之前** —— mp child 在
        # freeze_support() 那一步就 sys.exit 进入 spawn_main,后续 runtime hook
        # 全不跑。API server 跑在 mp child 里,KB 创建链路要用 tiktoken
        # cl100k_base,patch 必须在每个 child 进程都 apply 才行(每个 child 是
        # 独立 Python 解释器,各自重新跑 hook)。详见 tiktoken_plugins_freeze.py 注释。
        str(ROOT / "packaging" / "pyinstaller" / "runtime_hooks" / "tiktoken_plugins_freeze.py"),
        # 必须在 user code / Click 解析 argv 之前调 multiprocessing.freeze_support(),
        # 否则 Windows 下子进程 ``--multiprocessing-fork`` 参数会被 Click 当未知 option 拒绝。
        str(ROOT / "packaging" / "pyinstaller" / "runtime_hooks" / "multiprocessing_freeze.py"),
        # 给 pip 自带的 vendored distlib 注册 PyInstaller loader → ResourceFinder 映射。
        # 不加的话 ``-m pip install`` 在 Windows frozen bundle 里 import
        # pip._vendor.distlib.scripts 时崩:DistlibException: Unable to locate
        # finder for 'pip._vendor.distlib'。详见该 hook 文件头注释。
        str(ROOT / "packaging" / "pyinstaller" / "runtime_hooks" / "distlib_finder_freeze.py"),
        # macOS:在 _ssl 被 import 前用 ctypes 预加载 libssl/libcrypto,
        # 绕开 PyInstaller onefile 把 dylib 放在子目录、@rpath 又只解析到 bundle 根
        # 导致 ``Library not loaded: @rpath/libssl.3.dylib`` 的死局。
        # 不论 PyInstaller 把 libssl 放在哪个子路径,这个 hook 都能找到并预加载。
        str(ROOT / "packaging" / "pyinstaller" / "runtime_hooks" / "openssl_preload.py"),
    ],
    excludes=excludes,
    cipher=block_cipher,
    noarchive=False,
)


# ──────────────────────────────────────────────────────────────
# macOS:剔除 opencv-python wheel 自带的旧版 OpenSSL(在 cv2/.dylibs/ 里)。
#
# 上面 Analysis 之前已经把构建机 Python 链接的新版 libssl/libcrypto 加到 bundle 根;
# 这里把 cv2 自带的旧版(3.0.x,缺 X509_STORE_get1_objects)从 TOC 里删掉,避免
# dyld 走 cv2 的 .dylibs/ 路径把旧版当成 @rpath/libcrypto.3.dylib 命中。
# 两份 install_name 一致,删掉旧版后 dyld 自动 fallback 到 bundle 根的新版;
# cv2 native 模块 ABI 兼容新版(OpenSSL 3.x 只追加符号)。
#
# 仅 darwin 触发;Windows / Linux 不存在 cv2/.dylibs/ 这个目录。
# ──────────────────────────────────────────────────────────────
if sys.platform == "darwin":
    def _is_cv2_bundled_openssl(entry):
        # PyInstaller TOC entry: (dest_in_bundle, src_path, typecode)
        dest = entry[0].replace("\\", "/")
        if not dest.startswith("cv2/.dylibs/"):
            return False
        name = dest.rsplit("/", 1)[-1]
        return name.startswith("libcrypto.") or name.startswith("libssl.")

    before = len(a.binaries)
    a.binaries = [b for b in a.binaries if not _is_cv2_bundled_openssl(b)]
    after = len(a.binaries)
    if before != after:
        print(
            f"[chayuan-spec] 已剔除 cv2/.dylibs/ 下的旧版 OpenSSL "
            f"{before - after} 个文件,避免与 bundle 根的新版冲突。"
        )

    # ⚠ 关键根因(已通过 runtime lstat / readlink 定罪):
    # PyInstaller 6.20 的 macOS PKG 阶段对每个 Mach-O(包括 _ssl.so)做
    # @rpath / LC_LOAD_DYLIB fixup,会**自动**给 bundle 根添加一条 symlink
    # ``libssl.3.dylib → cv2/.dylibs/libssl.3.dylib``(因为 PyInstaller 扫
    # cv2 wheel 时记下这个路径有同名文件)。这条 symlink 不在 a.binaries /
    # a.datas 的 TOC 里 ── 是 EXE/PKG 后处理加的,spec 改不到。
    #
    # 我们前面又把 cv2/.dylibs/ 下的 OpenSSL 过滤掉(避免旧版冲突)→
    # symlink target 不存在 → runtime os.stat / dyld 全 ENOENT。
    # 不管 src 喂普通文件还是 symlink、BINARY 还是 DATA、Analysis 之前
    # 还是之后塞,根上的 libssl.3.dylib 都会被 PyInstaller 重写成 symlink。
    #
    # 解法:**把 OpenSSL 藏到 PyInstaller 不认识的子路径** ``_chayuan_openssl/``,
    # 不让它有机会做 fixup。runtime hook(openssl_preload.py)在 _ssl
    # import 之前把字节拷到 bundle 根 + cv2/.dylibs/,直接覆盖 symlink。
    print("[chayuan-spec] 注入 OpenSSL 到 _chayuan_openssl/ 子路径(避开 PyInstaller PKG 阶段 fixup):")
    for _name, _src in _ossl_entries:
        _hidden_dest = "_chayuan_openssl/" + _name
        a.datas.append((_hidden_dest, _src, "DATA"))
        print(f"  + {_hidden_dest} ← {_src}")

    # 兜底校验:藏区路径必须存在。
    _hidden_present = {
        str(_e[0]).replace("\\", "/")
        for _e in a.datas
        if str(_e[0]).replace("\\", "/").startswith("_chayuan_openssl/")
    }
    _hidden_expect = {"_chayuan_openssl/libcrypto.3.dylib", "_chayuan_openssl/libssl.3.dylib"}
    if _hidden_expect - _hidden_present:
        raise SystemExit(
            "[chayuan-spec] FATAL: 藏区注入后 a.datas 仍缺:\n"
            "  期望:" + str(sorted(_hidden_expect)) + "\n"
            "  实际:" + str(sorted(_hidden_present)) + "\n"
        )
    print(f"[chayuan-spec] _chayuan_openssl/ 藏区 = {sorted(_hidden_present)}")


pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)


# ──────────────────────────────────────────────────────────────
# EXE / COLLECT
# ──────────────────────────────────────────────────────────────

# Onedir 模式(2026-05-19 起):
#   EXE 只装 bootloader + Python 内嵌 zip(a.scripts + a.zipfiles),
#   ``exclude_binaries=True`` 让 a.binaries / a.datas 走 COLLECT 落到
#   ``dist/chayuan-server/_internal/`` 里,跟 .exe 同级。
# 产物:
#   dist/chayuan-server/
#       chayuan-server.exe          ← bootloader,几 MB
#       _internal/
#           python312.dll
#           torch/ torchvision/ transformers/  ← 这次新加的大件
#           ...其它依赖...
# 这一目录会被 build.py 整个拷到 chayuan-client 的 Tauri resources。
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="chayuan-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,            # paddle 与 strip 偶发不兼容
    upx=False,              # paddle / onnxruntime 与 UPX 不兼容,默认关
    console=True,           # sidecar 需要读 stdout(日志 / READY 行)
    disable_windowed_traceback=False,
    argv_emulation=False,   # CLI 行为依赖 argv,关闭 macOS 的 emu
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)


coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="chayuan-server",   # 产物根目录名(对齐前面注释里的 dist/chayuan-server/)
)
