"""macOS:在 _ssl 被 import 前把藏区 OpenSSL 物化到 bundle 根 + cv2/.dylibs/。

背景(三轮 lstat/readlink 定罪后才搞清):

PyInstaller 6.20 macOS 的 PKG/EXE 后处理对 _ssl.cpython-312-darwin.so 做
@rpath / LC_LOAD_DYLIB fixup,会**自动**在 bundle 根加一条 symlink
``libssl.3.dylib → cv2/.dylibs/libssl.3.dylib``(因为它扫 cv2 wheel 时
记下同名路径)。这条 symlink 不在 a.binaries / a.datas 的 TOC 里,spec
怎么塞都改不到。

我们又过滤掉了 cv2/.dylibs/ 下的旧版 OpenSSL(避免旧版缺
X509_STORE_get1_objects 等 3.2 新增符号)→ symlink target 消失 → runtime
``Library not loaded @rpath/libssl.3.dylib``。

解法分两层:
1. spec 把 OpenSSL 藏到 ``_chayuan_openssl/`` 子目录(PyInstaller 不会对
   这个路径做 @rpath fixup,所以字节会原样落盘)。
2. 这个 hook(在 _ssl import 之前跑)把藏区字节复制到 bundle 根 +
   cv2/.dylibs/,覆盖掉 PyInstaller 留下的悬空 symlink。
"""
from __future__ import annotations

import ctypes
import os
import shutil
import sys


def _log(msg: str) -> None:
    sys.stderr.write(f"[chayuan-rthook-openssl] {msg}\n")
    sys.stderr.flush()


_log("=== runtime hook entered ===")
_log(f"sys.platform = {sys.platform}")
_log(f"sys._MEIPASS = {getattr(sys, '_MEIPASS', '<not set>')}")

if sys.platform == "darwin":
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        _log("WARN: 没有 sys._MEIPASS,非 PyInstaller bundle?跳过。")
    else:
        hidden_dir = os.path.join(meipass, "_chayuan_openssl")
        # 物化目标:bundle 根(@rpath 兜底解析点)+ cv2/.dylibs/(PyInstaller
        # 自动 symlink 的 target)。两处都覆盖,确保不论 dyld 走哪条路径都命
        # 中我们的新版 OpenSSL。
        targets = [meipass]
        cv2_dylibs = os.path.join(meipass, "cv2", ".dylibs")
        if os.path.isdir(cv2_dylibs):
            targets.append(cv2_dylibs)

        _log(f"藏区源 = {hidden_dir}")
        _log(f"物化目标 = {targets}")

        for name in ("libcrypto.3.dylib", "libssl.3.dylib"):
            src = os.path.join(hidden_dir, name)
            if not os.path.isfile(src):
                _log(f"ERROR: 藏区源 {src} 不存在(spec 注入失败?)")
                continue
            for tdir in targets:
                dest = os.path.join(tdir, name)
                try:
                    # os.path.lexists 同时识别普通文件、symlink、悬空 symlink。
                    # os.remove 对 symlink 只删 link 自身,不动 target ── 正合我意。
                    if os.path.lexists(dest):
                        os.remove(dest)
                    shutil.copy2(src, dest)
                    os.chmod(dest, 0o755)
                    _log(f"OK 物化 {dest}")
                except Exception as e:
                    _log(f"FAIL 物化 {dest}: {e}")

        # DYLD_FALLBACK_LIBRARY_PATH 兜底(对子进程有效;主进程已通过 @rpath 解析)
        old_fallback = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
            meipass + (":" + old_fallback if old_fallback else "")
        )

        # 预加载到全局名空间。即便 _ssl import 时 dyld 还是按 @rpath 重新解析,
        # 同名 dylib 已 RTLD_GLOBAL 在内存里 ── 后续解析直接复用,不再读盘。
        for canonical in ("libcrypto.3.dylib", "libssl.3.dylib"):
            path = os.path.join(meipass, canonical)
            try:
                ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
                _log(f"OK CDLL 预加载 {canonical} ← {path}")
            except OSError as e:
                _log(f"FAIL CDLL 预加载 {canonical}: {e}")
elif sys.platform != "darwin":
    _log("非 macOS,跳过预加载")

_log("=== runtime hook done ===")
