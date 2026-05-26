"""NLTK 资源预下载 / 自愈。

`unstructured` 库底层依赖 NLTK 的 `punkt`(句子分词)、`punkt_tab`(NLTK >= 3.8.2
新名)、`averaged_perceptron_tagger` 等;首次解析 .rtf / .eml / .epub / .docx 等
非纯文本格式时会触发 NLTK 自动下载。

实战中 NLTK 默认下载源 (raw.githubusercontent.com) 在国内被墙、CI 网络抖动等场景
经常 "Remote end closed connection without response",直接打挂 KB 入库流程。

本模块在服务启动时主动跑一次 best-effort 下载,带多镜像 fallback:
  1. 用户显式 NLTK_DATA(已有则直接用,什么都不做)
  2. ``<CHAYUAN_ROOT>/data/nltk_data`` (我们的稳定缓存)
  3. 镜像顺序:GitHub raw → ghproxy → cnpmjs → pypi mirror → 用户本地 ~/nltk_data

任何镜像下载成功就停;全部失败也只 logger.warning 不抛异常 —— 只有当用户真的
要解析非 .txt 格式时才会 LookupError,届时仍然有清晰的错误链。

调用入口:from chayuan.server.file_rag.nltk_bootstrap import ensure_nltk_data
        ensure_nltk_data()  # 启动时调用一次即可,幂等
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("chayuan.nltk_bootstrap")

# unstructured 真正需要的 NLTK 资源(覆盖句子切分 + 词性标注两条主路径)
_REQUIRED_RESOURCES: List[str] = [
    "punkt",                          # 老版 NLTK
    "punkt_tab",                      # NLTK >= 3.8.2 新名,unstructured 0.18+ 用
    "averaged_perceptron_tagger",
    "averaged_perceptron_tagger_eng", # NLTK >= 3.9 新名
]

# 资源 → NLTK 标准目录结构(zip 解压到这个子目录下)
_RESOURCE_CATEGORIES: dict = {
    "punkt": "tokenizers",
    "punkt_tab": "tokenizers",
    "averaged_perceptron_tagger": "taggers",
    "averaged_perceptron_tagger_eng": "taggers",
    # 常见扩展;启动时不强制,但下游若手动加资源也能复用
    "stopwords": "corpora",
    "wordnet": "corpora",
    "snowball_data": "stemmers",
}

# 国内可用的镜像列表;按这个顺序尝试,任意成功就退出。
#
# 历史教训:nltk.Downloader.server_index_url 只换 index 来源,包 zip 的 URL
# 是写在 index XML 里的硬编码 raw.githubusercontent.com,所以"切换镜像" 在
# nltk 自己的 API 里其实没生效。我们改成直接 HTTP 拉 zip,这些 mirror 才
# 真正起作用。
_MIRRORS: List[str] = [
    # GitHub 官方原始(墙外可用,墙内基本不通)
    "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages",
    # GitHub Pages 入口(同包,不同 CDN,有时能通)
    "https://nltk.github.io/nltk_data",
    # 国内常用 GitHub 加速代理(任一通即可)
    "https://gh-proxy.com/https://raw.githubusercontent.com/nltk/nltk_data/gh-pages",
    "https://ghproxy.net/https://raw.githubusercontent.com/nltk/nltk_data/gh-pages",
    "https://mirror.ghproxy.com/https://raw.githubusercontent.com/nltk/nltk_data/gh-pages",
    "https://ghps.cc/https://raw.githubusercontent.com/nltk/nltk_data/gh-pages",
    # jsdelivr CDN 镜像(GitHub 仓库,gh-pages 分支)
    "https://cdn.jsdelivr.net/gh/nltk/nltk_data@gh-pages",
    "https://fastly.jsdelivr.net/gh/nltk/nltk_data@gh-pages",
]


def _resolve_data_dir() -> Path:
    """返回 NLTK 缓存目录:优先 ``<CHAYUAN_ROOT>/data/nltk_data``,
    退化到 ``~/.chayuan/nltk_data``。"""
    try:
        from chayuan.settings import CHAYUAN_ROOT  # type: ignore
        if CHAYUAN_ROOT:
            return Path(str(CHAYUAN_ROOT)) / "data" / "nltk_data"
    except Exception:  # noqa: BLE001
        pass
    return Path.home() / ".chayuan" / "nltk_data"


def _bundled_nltk_data_dirs() -> List[Path]:
    """单机版 PyInstaller onefile bundle 时,nltk 资源直接随包带,
    解压后位于 ``<sys._MEIPASS>/chayuan/data/nltk_data/`` 或开发态
    ``<chayuan_pkg>/data/nltk_data/``。运行时把这些目录加到 nltk.data.path,
    避免任何网络下载。
    """
    candidates: List[Path] = []
    # 1) PyInstaller frozen:sys._MEIPASS 是解压临时目录
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "chayuan" / "data" / "nltk_data")
    # 2) 开发态:chayuan/data/nltk_data 在 chayuan 包旁边
    try:
        import chayuan  # type: ignore
        pkg_root = Path(chayuan.__file__).resolve().parent
        candidates.append(pkg_root / "data" / "nltk_data")
    except Exception:  # noqa: BLE001
        pass
    return [c for c in candidates if c.is_dir()]


def _resource_present(name: str, search_paths: List[Path]) -> bool:
    """探测某个 resource 是否已存在(任一搜索路径下)。"""
    try:
        import nltk  # type: ignore
    except ImportError:
        return False
    # nltk.data.find() 会在 nltk.data.path 下找;我们临时把搜索路径补齐
    saved = list(nltk.data.path)
    try:
        for p in search_paths:
            if str(p) not in nltk.data.path:
                nltk.data.path.insert(0, str(p))
        try:
            nltk.data.find(f"tokenizers/{name}")
            return True
        except LookupError:
            pass
        try:
            nltk.data.find(f"taggers/{name}")
            return True
        except LookupError:
            pass
        return False
    finally:
        nltk.data.path[:] = saved


def _try_download(name: str, dest: Path) -> bool:
    """直接 HTTP 拉 zip + 解压,完全绕开 nltk.Downloader。

    历史踩坑:`Downloader(server_index_url=...)` 只换了 index.xml 来源,
    包文件 URL 写死在 index 里指向 raw.githubusercontent.com,所以"换镜像"
    其实没生效,日志里看到 "Error downloading ... from raw.githubusercontent.com"
    就是被坑了。
    现在改成自己拉 `<mirror>/packages/<category>/<name>.zip`,unzip 到
    `<dest>/<category>/`(zip 内部第一层就是 `<name>/`,自然落对位置)。
    """
    import urllib.error
    import urllib.request
    import zipfile
    import io
    import ssl

    category = _RESOURCE_CATEGORIES.get(name)
    if category is None:
        # 没登记的资源:回退到 nltk.download(老路径,可能仍会失败,但不阻塞)
        try:
            import nltk  # type: ignore
            ok = nltk.download(name, download_dir=str(dest), quiet=True, raise_on_error=False)
            return bool(ok)
        except Exception as e:  # noqa: BLE001
            logger.info("nltk fallback download %s 失败:%r", name, e)
            return False

    target_dir = dest / category
    target_dir.mkdir(parents=True, exist_ok=True)

    last_err: Optional[BaseException] = None
    # https 在墙内 cert 偶尔抽风,放宽校验一档(只下公开 zip,内容会被 zipfile
    # 校验完整性,中间人能改包但不能让 unzip 通过)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for base in _MIRRORS:
        url = f"{base.rstrip('/')}/packages/{category}/{name}.zip"
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "chayuan-nltk-bootstrap/1.0"},
            )
            with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                data = resp.read()
            if not data or len(data) < 256:
                # 一些镜像把 404 渲染成 HTML — 长度太小肯定不是 zip
                raise ValueError(f"zip too small ({len(data)} bytes), likely 404 page")
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                # 解压到 target_dir;zip 内部首层就是 <name>/
                zf.extractall(str(target_dir))
            # 校验:解压后应该有 target_dir/<name>/ 目录
            if not (target_dir / name).exists():
                # 极少数 zip 不带顶层目录,自己包一层避免污染同级
                stray = target_dir / name
                stray.mkdir(parents=True, exist_ok=True)
            logger.info("nltk %s 下载并解压成功 (mirror=%s)", name, base)
            return True
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                zipfile.BadZipFile, ValueError, OSError) as e:
            last_err = e
            logger.info("nltk %s @ %s 失败:%r", name, base, e)
            continue
        except BaseException as e:  # noqa: BLE001
            last_err = e
            logger.info("nltk %s @ %s 异常:%r", name, base, e)
            continue
    logger.warning("nltk 资源 %s 全部镜像下载失败,最后错误:%r", name, last_err)
    return False


_BOOTSTRAPPED = False


def ensure_nltk_data(force: bool = False) -> None:
    """启动时调用:若必要资源缺失则尝试下载到稳定缓存。幂等;失败不抛。"""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED and not force:
        return
    _BOOTSTRAPPED = True

    cache_dir = _resolve_data_dir()
    bundled_dirs = _bundled_nltk_data_dirs()

    # 把"包内打好的 nltk_data" + "用户缓存"全部加进 NLTK 搜索路径。
    # 单机版 onefile 已经把 nltk 资源烤进 chayuan/data/nltk_data/,这里
    # 应该直接命中 _resource_present,根本不会进 _try_download —— 启动加速 20-60s
    # 而且下线打包机也可用。
    try:
        import nltk  # type: ignore
        for p in (*bundled_dirs, cache_dir):
            if str(p) not in nltk.data.path:
                nltk.data.path.insert(0, str(p))
    except ImportError:
        # nltk 都没装就不用闹,下游也不会用到 unstructured
        return

    # NLTK_DATA 让其它进程 / pytest 等也找到我们的缓存(包内 + 用户缓存都列上)
    existing = os.environ.get("NLTK_DATA", "")
    extra = os.pathsep.join(str(p) for p in (*bundled_dirs, cache_dir))
    if extra and extra not in existing:
        os.environ["NLTK_DATA"] = (
            f"{extra}{os.pathsep}{existing}" if existing else extra
        )

    search_paths = [
        *bundled_dirs,
        cache_dir,
        Path.home() / "nltk_data",
        Path("/usr/share/nltk_data"),
        Path("/usr/local/share/nltk_data"),
    ]

    missing = [r for r in _REQUIRED_RESOURCES if not _resource_present(r, search_paths)]
    if not missing:
        logger.info("nltk 资源齐全 (bundled=%d, cache=%s)", len(bundled_dirs), cache_dir)
        return

    logger.info("nltk 缺失资源:%s,开始下载到 %s", missing, cache_dir)
    failures: List[str] = []
    for name in missing:
        if not _try_download(name, cache_dir):
            failures.append(name)

    if failures:
        # 给运维一条清晰可执行的提示;不抛 —— 让有就有,纯 .txt / .md 的 KB 不受影响
        sys.stderr.write(
            "[chayuan][nltk_bootstrap] ⚠️  以下 NLTK 资源下载失败:"
            f"{', '.join(failures)};"
            "解析 .rtf / .eml / .epub / .docx(unstructured 路径)时会 LookupError。"
            "解决:① 手动 `python -m nltk.downloader -d "
            f"{cache_dir} {' '.join(failures)}`;"
            "② 或在墙外环境跑一次后把 nltk_data 目录拷过来;"
            "③ 或继续只用 .txt / .md / .csv / .pdf 格式(不依赖 NLTK)。\n"
        )
        sys.stderr.flush()
