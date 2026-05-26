"""把项目内 ``vendor/bundled_models/`` 种到 ``<CHAYUAN_ROOT>/models/bundled/``。

设计目标
========
* **首启自动**: ``first_launch.run_first_launch_hooks`` 会调本模块在
  ``scan_once`` 之前跑一次,确保自带模型出现在用户数据目录里;
* **幂等 + 用户优先**: 目标文件已存在且 ``size`` 一致(或 ``mtime`` 更新)
  时跳过,保留用户在数据目录里手动替换 / 覆盖的版本;
* **不污染源目录**: bundled 是只读约定(打包时来自 ``_MEIPASS``,无法写回),
  本模块只从 bundled 读,只往 ``<CHAYUAN_ROOT>/models/bundled/`` 写;
* **目录结构对齐**: 与 ``vendor/bundled_models/<cap>/...`` 一致,
  ``local_index`` 扫描时会拿到 ``source_tag = "bundled"``。

返回 :class:`SeedReport`,首启钩子据此填 ``FirstLaunchReport.seeded*``;
所有失败被吞,写入 ``report.errors``。
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger("chayuan.model_registry.bundled_seed")

# 跟 build.py EXTERNAL_SEED_MANIFEST_NAME 对齐;不要换名字,装机后这个文件
# 就在 <install>/bundled_models/.external_seed_manifest.json 这里。
EXTERNAL_SEED_MANIFEST_NAME = ".external_seed_manifest.json"


@dataclass
class SeedReport:
    """``seed_bundled_models`` 的返回快照。"""

    source: Optional[str] = None       # bundled_models_dir() 解析到的源根
    target: Optional[str] = None       # <CHAYUAN_ROOT>/models/bundled
    copied: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)   # 目标已存在且未变
    errors: List[str] = field(default_factory=list)
    # 外部介质种子(ISO 旁挂大文件):manifest 列出来的、装机后仍缺的文件路径。
    # 空列表 = 全齐(或 manifest 不存在 = 不需要 external seed)。
    external_pending: List[str] = field(default_factory=list)
    # 这次成功扫描到并使用的外部介质路径(光驱根/models_seed 这种)。
    # 不是 None = 这次至少试过从外部介质拷过文件;是 None = 没找到匹配介质 / 不需要。
    external_source: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "copied": list(self.copied),
            "skipped": list(self.skipped),
            "errors": list(self.errors),
            "external_pending": list(self.external_pending),
            "external_source": self.external_source,
        }


def _should_replace(src: Path, dst: Path) -> bool:
    """目标存在时,是否需要覆盖。

    用户手动替换过的文件(``size`` 与 bundled 不一致 / ``mtime`` 比 bundled
    新)一律保留,不动。仅在目标确实是 bundled 的旧拷贝(``size`` 一致且
    ``mtime`` 未被改动)时,且 bundled 内容真的变了(``size`` 不一致),才考虑替换。
    保守策略:**只在 ``size`` 不一致 且 用户没改过 ``mtime``** 时才覆盖。
    """
    try:
        ss = src.stat()
        ds = dst.stat()
    except OSError:
        return False
    if ss.st_size != ds.st_size and ds.st_mtime <= ss.st_mtime:
        return True
    return False


def _copy_tree(src_root: Path, dst_root: Path, tag: str, report: SeedReport) -> None:
    """把 ``src_root`` 整树拷贝到 ``dst_root``,逐项判 skip / replace。"""
    if not src_root.is_dir():
        return
    for src in src_root.rglob("*"):
        try:
            rel = src.relative_to(src_root)
        except ValueError:
            continue
        dst = dst_root / rel
        try:
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
                continue
            if not src.is_file():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                if not _should_replace(src, dst):
                    report.skipped.append(f"{tag}/{rel}")
                    continue
            shutil.copy2(src, dst)
            report.copied.append(f"{tag}/{rel}")
        except OSError as e:
            report.errors.append(f"{tag}/{rel}: {type(e).__name__}: {e}")


# ──────────────────────────── 外部介质种子 ────────────────────────────


def _load_external_manifest(bundled_root: Path) -> Optional[dict]:
    """读 <install>/bundled_models/.external_seed_manifest.json。

    没文件 / 解析失败 = 这个装机包没拆出大文件,正常路径走完即可。
    """
    p = bundled_root / EXTERNAL_SEED_MANIFEST_NAME
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[bundled-seed] manifest 解析失败 %s: %r", p, e)
        return None


def _missing_external_files(manifest: dict, target_root: Path) -> List[dict]:
    """对比 manifest 跟 target,返回缺/尺寸不对的条目列表。"""
    missing: List[dict] = []
    for entry in manifest.get("files", []) or []:
        rel = entry.get("path") or ""
        size = int(entry.get("size") or 0)
        if not rel:
            continue
        dst = target_root / rel
        try:
            st = dst.stat() if dst.is_file() else None
        except OSError:
            st = None
        if st is None or (size > 0 and st.st_size != size):
            missing.append(entry)
    return missing


def _candidate_external_roots() -> Iterable[Path]:
    """穷举可能挂载了 ISO 的位置,逐个返回 ``<mount>/models_seed/`` 候选。

    Windows:扫 A-Z 全字母,任何 ``isdir`` 通过的根都看一眼有没 models_seed/。
    Linux/Mac:扫 /mnt /media /Volumes 及其一级子目录(/media/<user>/<vol>)。
    不验内容,只列候选 — 验证留给 _find_external_seed_root 做。
    """
    seen: set[Path] = set()
    if platform.system() == "Windows":
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:/")
            try:
                if not drive.is_dir():
                    continue
            except OSError:
                continue
            candidate = drive / "models_seed"
            if candidate not in seen:
                seen.add(candidate)
                yield candidate
    else:
        roots = [Path(p) for p in ("/mnt", "/media", "/Volumes")]
        for root in roots:
            if not root.is_dir():
                continue
            try:
                level1 = list(root.iterdir())
            except OSError:
                continue
            for sub in level1:
                if not sub.is_dir():
                    continue
                c1 = sub / "models_seed"
                if c1 not in seen:
                    seen.add(c1)
                    yield c1
                # /media/<user>/<volume>/ 这一层(Linux 桌面挂载默认布局)
                try:
                    level2 = list(sub.iterdir())
                except OSError:
                    continue
                for sub2 in level2:
                    if not sub2.is_dir():
                        continue
                    c2 = sub2 / "models_seed"
                    if c2 not in seen:
                        seen.add(c2)
                        yield c2


def _find_external_seed_root(manifest: dict) -> Optional[Path]:
    """在挂载介质里找匹配的 models_seed/ 根。

    验证策略:取 manifest 第一个条目(路径 + size),如果候选根下能找到这个
    文件且 size 一致,就认这是对的介质。一项命中即可,不需要全检。
    """
    files = manifest.get("files", []) or []
    if not files:
        # 没具体文件清单也行 — 任何能找到 models_seed/ 的就用,稳健性优先
        for cand in _candidate_external_roots():
            if cand.is_dir():
                return cand
        return None
    first = files[0]
    expected_rel = first.get("path") or ""
    expected_size = int(first.get("size") or 0)
    if not expected_rel:
        return None
    for cand in _candidate_external_roots():
        try:
            if not cand.is_dir():
                continue
            probe = cand / expected_rel
            if not probe.is_file():
                continue
            if expected_size > 0 and probe.stat().st_size != expected_size:
                logger.warning(
                    "[bundled-seed] 候选 %s 的 %s size 不一致 (%d vs %d),跳过",
                    cand, expected_rel, probe.stat().st_size, expected_size,
                )
                continue
            return cand
        except OSError:
            continue
    return None


def _copy_external_files(
    seed_root: Path,
    entries: List[dict],
    target_root: Path,
    report: SeedReport,
) -> None:
    """从外部介质把 entries 列出的文件拷到 target_root,失败写 report.errors。"""
    for entry in entries:
        rel = entry.get("path") or ""
        if not rel:
            continue
        src = seed_root / rel
        dst = target_root / rel
        try:
            if not src.is_file():
                report.errors.append(f"external/{rel}: 源文件不在介质上")
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            report.copied.append(f"external/{rel}")
        except OSError as e:
            report.errors.append(f"external/{rel}: {type(e).__name__}: {e}")


def _seed_external(src_root: Path, dst_root: Path, report: SeedReport) -> None:
    """如果 src_root 里有 external seed manifest,处理外部介质拷贝路径。

    幂等:每次启动都跑,但 manifest 文件都齐就直接 short-circuit。
    """
    manifest = _load_external_manifest(src_root)
    if not manifest:
        return
    missing = _missing_external_files(manifest, dst_root)
    if not missing:
        return  # 全齐了,不用扫介质
    logger.info(
        "[bundled-seed] external manifest:%d 个大文件待复制(volume=%s)",
        len(missing), manifest.get("iso_volume_hint") or "?",
    )
    ext_root = _find_external_seed_root(manifest)
    if ext_root is None:
        msg = (
            f"未找到匹配的外部介质(期望 {manifest.get('iso_volume_hint') or 'CHAYUAN_FULL'} "
            f"卷标的光驱下有 models_seed/),{len(missing)} 个大文件 "
            f"({sum(int(e.get('size') or 0) for e in missing) // 1024 // 1024} MB) 还没就位。"
            "请挂载 chayuan-desktop ISO 后重启 chayuan-server。"
        )
        report.errors.append(msg)
        report.external_pending = [str(e.get("path") or "") for e in missing]
        logger.warning("[bundled-seed] %s", msg)
        return
    report.external_source = str(ext_root)
    logger.info("[bundled-seed] external seed root → %s", ext_root)
    _copy_external_files(ext_root, missing, dst_root, report)
    # 拷完再核查一次,记录残留(磁盘满 / 文件锁 / 中途介质弹出都可能让这里非空)
    still_missing = _missing_external_files(manifest, dst_root)
    report.external_pending = [str(e.get("path") or "") for e in still_missing]


def seed_bundled_models(*, target_dir: Optional[Path] = None) -> SeedReport:
    """把 ``vendor/bundled_models/`` 种到 ``<CHAYUAN_ROOT>/models/bundled/``。

    Args:
        target_dir: 显式指定目标根(测试用)。默认
            ``<CHAYUAN_ROOT>/models/bundled``。

    Returns:
        :class:`SeedReport` 描述本次拷贝结果;源不存在时返回空 report,
        ``source / target`` 仍记录用于诊断。
    """
    from chayuan.server.model_registry.local_index import (
        BUNDLED_CAPABILITY_DIRS,
        bundled_models_dir,
        models_dir,
    )

    report = SeedReport()
    src = bundled_models_dir()
    if src is None:
        logger.debug("[bundled-seed] bundled_models_dir 未找到,跳过")
        return report
    report.source = str(src)

    dst_root = Path(target_dir) if target_dir is not None else (models_dir() / "bundled")
    report.target = str(dst_root)

    try:
        dst_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        msg = f"create target dir {dst_root}: {type(e).__name__}: {e}"
        report.errors.append(msg)
        logger.warning("[bundled-seed] %s", msg)
        return report

    # 显式建好所有 cap 子目录(即便 src 没内容):
    # UI"扫描挂载"提示的路径 (.../bundled/embedding/、.../bundled/image/ 等)
    # 物理存在,用户能直接打开资源管理器丢模型进去,不用先手建一层。
    # mkdir 是 idempotent,已有的不动;失败只 log 不中断 seed 流程。
    for cap in BUNDLED_CAPABILITY_DIRS:
        try:
            (dst_root / cap).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.debug("[bundled-seed] mkdir %s/%s 失败(忽略): %r", dst_root, cap, e)

    for cap in BUNDLED_CAPABILITY_DIRS:
        sub_src = src / cap
        if not sub_src.is_dir():
            continue
        # 空目录(只有 .gitkeep)直接跳过,不在用户数据目录里复制空壳
        try:
            real_children = [
                p for p in sub_src.iterdir()
                if not p.name.startswith(".") and p.name not in ("__pycache__",)
            ]
        except OSError:
            real_children = []
        if not real_children:
            continue
        _copy_tree(sub_src, dst_root / cap, f"bundled/{cap}", report)

    # 处理 external seed(ISO 旁挂的 ≥ 2 GB 大文件):
    # build.py 在分流时会把大文件清单写到 src/.external_seed_manifest.json,
    # 装机后这里读 manifest → 比对 dst_root 缺啥 → 扫挂载光驱拷过去。
    # manifest 不存在 = 本装机包没有 external seed,short-circuit。
    _seed_external(src, dst_root, report)

    if report.copied or report.errors or report.external_pending:
        logger.info(
            "[bundled-seed] src=%s -> dst=%s copied=%d skipped=%d errors=%d "
            "external_pending=%d external_source=%s",
            report.source, report.target,
            len(report.copied), len(report.skipped), len(report.errors),
            len(report.external_pending), report.external_source,
        )
    return report


__all__ = ["SeedReport", "seed_bundled_models"]
