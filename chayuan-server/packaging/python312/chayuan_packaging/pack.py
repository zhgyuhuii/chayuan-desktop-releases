"""``chayuan-pack`` 主 CLI（Click 群组）。

子命令
======

* ``fetch``  — 按 release + 平台拉取 vendor + 模型到 ``packaging/.cache``
* ``stage``  — 把 cache 解压到 vendor/ + models/，组装 staging 目录
* ``build``  — staging → AppImage / dmg / NSIS（依赖平台 finalizer）
* ``audit``  — 检查当前 vendor / models 是否已经满足某个 release 的清单
* ``clean``  — 清空 packaging/.cache + packaging/build
* ``sbom``   — 输出当前打包的 SBOM（Software Bill of Materials）

一次完整的发布通常是：
``fetch → stage → build`` 三步；CI 流水线里每步都有 cache。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

import click

from chayuan_packaging import __version__
from chayuan_packaging.fetchers import fetch_resource
from chayuan_packaging.manifest import Layout, ReleaseSpec, Resource, load_layout
from chayuan_packaging.platform_info import (
    ALL_PLATFORMS,
    PlatformKey,
    current_platform,
    parse_platform,
)
from chayuan_packaging.targets import get_finalizer
from chayuan_packaging.unpack import unpack_to


# 仓库根 = packaging/python312/chayuan_packaging/__file__ 上 4 级
REPO_ROOT = Path(__file__).resolve().parents[3]
PKG_ROOT = Path(__file__).resolve().parents[1]   # packaging/python312
DEFAULT_LAYOUT = PKG_ROOT / "layout.yaml"
CACHE_DIR = PKG_ROOT / ".cache"
BUILD_DIR = PKG_ROOT / "build"


def _setup_logging(verbose: bool) -> None:
    fmt = "[%(asctime)s] %(levelname)-7s %(name)s : %(message)s"
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=fmt)


# ────────────────────────────────────────────────────────────────────
# 共享选项
# ────────────────────────────────────────────────────────────────────


def _common_opts(f):
    # 接受 "win" 与 "windows" 两种写法（macOS / GitHub Actions 习惯写 windows）
    f = click.option("--target", type=click.Choice(["linux", "mac", "win", "windows", "darwin", "auto"]),
                     default="auto", help="目标 OS（默认 = 当前主机）")(f)
    f = click.option("--arch", type=click.Choice(["x86_64", "amd64", "arm64", "aarch64", "auto"]),
                     default="auto", help="目标架构（默认 = 当前主机）")(f)
    f = click.option("--release", type=str, default="lite",
                     help="发布矩阵名（lite/standard/pro 或 layout.yaml 自定义）")(f)
    f = click.option("--layout", "layout_path", type=click.Path(),
                     default=str(DEFAULT_LAYOUT),
                     help=f"layout.yaml 路径（默认 {DEFAULT_LAYOUT}）")(f)
    f = click.option("-v", "--verbose", is_flag=True, default=False)(f)
    f = click.option("--offline", is_flag=True, default=False,
                     help="离线：不发任何网络请求；仅读 cache")(f)
    # 给 fetcher 用：覆盖 HF_MIRROR / GITHUB_API_BASE 等公共镜像
    f = click.option("--hf-mirror", "hf_mirror", type=str, default="",
                     help="HuggingFace 镜像（默认读 layout.yaml 或 hf-mirror.com）")(f)
    return f


def _normalize_target(target: str) -> str:
    """把 ``windows``/``darwin`` 等别名映射回 platform_info 接受的 win/mac。"""
    return {"windows": "win", "darwin": "mac"}.get(target, target)


def _apply_hf_mirror(hf_mirror: str) -> None:
    """把 ``--hf-mirror`` 反映到环境变量，让 fetcher 在 import 时拿得到。"""
    if hf_mirror:
        os.environ["HF_MIRROR_URL"] = hf_mirror
        os.environ["HF_ENDPOINT"] = hf_mirror


def _resolve(target: str, arch: str) -> PlatformKey:
    target = _normalize_target(target)
    if target == "auto" and arch == "auto":
        return current_platform()
    return parse_platform(
        None if target == "auto" else target,
        None if arch == "auto" else arch,
    )


# ────────────────────────────────────────────────────────────────────
# CLI 群组
# ────────────────────────────────────────────────────────────────────


@click.group(help="察元跨平台打包器（Python 3.12）。")
@click.version_option(__version__, prog_name="chayuan-pack")
def cli() -> None:  # pragma: no cover
    pass


# ---- fetch ---------------------------------------------------------------


@cli.command(help="按 (release, platform) 拉取所有 vendor / 模型到 .cache/")
@_common_opts
def fetch(target: str, arch: str, release: str, layout_path: str,
          verbose: bool, offline: bool, hf_mirror: str) -> None:
    _setup_logging(verbose)
    _apply_hf_mirror(hf_mirror)
    layout = load_layout(layout_path)
    plat = _resolve(target, arch)
    spec = layout.release(release)
    click.secho(f"→ fetch  release={release}  platform={plat}", fg="cyan", bold=True)

    summary = []
    for r in layout.resources_for_release(release):
        click.echo(f"  · {r.kind}/{r.name} → {r.dest}")
        try:
            res = fetch_resource(r, platform=plat, cache_dir=CACHE_DIR, offline=offline)
            tag = "✓"
            if res.skipped:
                tag = "⚠"
            click.echo(f"    {tag} {res.local_path} ({res.url or 'cache'})")
            summary.append({"name": r.name, "skipped": res.skipped,
                            "path": str(res.local_path), "sha256": res.sha256})
        except Exception as e:  # noqa: BLE001
            level = "warning" if r.optional else "error"
            click.secho(f"    ✘ {level}: {e}", fg="red" if level == "error" else "yellow")
            summary.append({"name": r.name, "error": str(e)})
            if not r.optional and not offline:
                raise click.ClickException(str(e))

    out = CACHE_DIR / f"_fetch-{release}-{plat}.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    click.secho(f"→ summary: {out}", fg="green")


# ---- stage ---------------------------------------------------------------


@cli.command(help="把 .cache 解压到 vendor/ + models/，准备 staging 目录")
@_common_opts
def stage(target: str, arch: str, release: str, layout_path: str,
          verbose: bool, offline: bool, hf_mirror: str) -> None:
    _setup_logging(verbose)
    _apply_hf_mirror(hf_mirror)
    layout = load_layout(layout_path)
    plat = _resolve(target, arch)
    spec = layout.release(release)

    staging = BUILD_DIR / f"staging-{release}-{plat}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    click.secho(f"→ stage  release={release}  platform={plat}  staging={staging}",
                fg="cyan", bold=True)

    # 1) 拷贝 chayuan-server 业务源 + 11 个 ai-platform 包
    _copy_business(staging)

    # 2) 把 cache → 解压到 staging/{vendor,models}/...
    for r in layout.resources_for_release(release):
        click.echo(f"  · {r.kind}/{r.name} → {r.dest}")
        try:
            res = fetch_resource(r, platform=plat, cache_dir=CACHE_DIR, offline=offline)
            if res.skipped:
                click.secho(f"    ⚠ 跳过（cache 缺失）", fg="yellow")
                continue
            dest_abs = staging / r.dest
            unpack_to(res.local_path, dest_abs, kind=r.unpack, repo_root=staging)
            click.echo(f"    ✓ unpacked → {dest_abs}")
        except Exception as e:  # noqa: BLE001
            level = "warning" if r.optional else "error"
            click.secho(f"    ✘ {level}: {e}",
                        fg="red" if level == "error" else "yellow")
            if not r.optional:
                raise click.ClickException(str(e))

    # 3) 复制配置 seed
    seed_src = REPO_ROOT / "libs" / "chayuan-server" / "chayuan" / "data" / "ai_platform_config"
    if seed_src.is_dir():
        seed_dst = staging / "config" / "ai_platform"
        seed_dst.mkdir(parents=True, exist_ok=True)
        for p in seed_src.rglob("*"):
            if p.is_file():
                rel = p.relative_to(seed_src)
                d = seed_dst / rel
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, d)
        click.secho(f"  · config seed → {seed_dst}", fg="green")

    click.secho(f"→ done.  staging at {staging}", fg="green")


def _copy_business(staging: Path) -> None:
    """拷贝 chayuan-server / langchain_chayuan + 11 个 ai-platform 包到 staging。"""
    libs = REPO_ROOT / "libs"
    if not libs.is_dir():
        raise click.ClickException(f"libs/ 不存在：{libs}")
    pkgs = [
        "chayuan-server", "chayuan-core", "chayuan-identify", "chayuan-registry",
        "chayuan-discovery", "chayuan-modelmgr", "chayuan-runtime",
        "chayuan-supervisor", "chayuan-gateway", "chayuan-cli",
        "chayuan-packager", "chayuan-preflight",
    ]
    out_dir = staging / "libs"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in pkgs:
        src = libs / name
        if not src.is_dir():
            click.secho(f"  ! {src} 不存在，跳过", fg="yellow")
            continue
        dst = out_dir / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(
            src, dst,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".pytest_cache", "tests",
                "*.egg-info", "node_modules", ".venv",
            ),
        )
    click.echo(f"  · libs/* → {out_dir}")


# ---- build ---------------------------------------------------------------


@cli.command(help="staging → 平台特定的最终包（AppImage / dmg / NSIS）")
@_common_opts
@click.option("--version", "version_str", type=str, default="0.0.0",
              help="版本号（写入安装包文件名 + Info.plist）")
@click.option("--no-stage", is_flag=True, default=False,
              help="不重新 stage（直接用现有 staging-* 目录）")
def build(target: str, arch: str, release: str, layout_path: str,
          verbose: bool, offline: bool, hf_mirror: str, version_str: str,
          no_stage: bool) -> None:
    _setup_logging(verbose)
    _apply_hf_mirror(hf_mirror)
    plat = _resolve(target, arch)
    staging = BUILD_DIR / f"staging-{release}-{plat}"
    if not no_stage:
        ctx = click.get_current_context()
        ctx.invoke(stage, target=target, arch=arch, release=release,
                   layout_path=layout_path, verbose=verbose, offline=offline,
                   hf_mirror=hf_mirror)

    if not staging.is_dir():
        raise click.ClickException(f"staging not found: {staging}; 先跑 stage")

    finalizer = get_finalizer(plat)
    out_dir = BUILD_DIR / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    click.secho(f"→ build  finalizer={finalizer.name}", fg="cyan", bold=True)
    artifact = finalizer.finalize(
        staging=staging, out_dir=out_dir, platform=plat,
        release=release, version=version_str,
    )
    size_mb = artifact.stat().st_size / 1024 / 1024 if artifact.is_file() else 0
    click.secho(f"→ artifact: {artifact} ({size_mb:.1f} MB)", fg="green", bold=True)


# ---- audit ---------------------------------------------------------------


@cli.command(help="检查仓库 vendor/ + models/ 当前是否满足某个 release 清单")
@_common_opts
def audit(target: str, arch: str, release: str, layout_path: str,
          verbose: bool, offline: bool, hf_mirror: str) -> None:
    _setup_logging(verbose)
    _apply_hf_mirror(hf_mirror)
    layout = load_layout(layout_path)
    plat = _resolve(target, arch)
    spec = layout.release(release)

    click.secho(f"→ audit  release={release}  platform={plat}", fg="cyan", bold=True)
    ok = 0
    miss = 0
    rows = []
    for r in layout.resources_for_release(release):
        target_path = REPO_ROOT / r.dest
        present = target_path.exists() and any(target_path.rglob("*"))
        rows.append((r.kind, r.name, r.dest, present, r.optional))
        if present:
            ok += 1
        else:
            miss += 1

    for kind, name, dest, present, optional in rows:
        mark = "✓" if present else ("·" if optional else "✘")
        color = "green" if present else ("yellow" if optional else "red")
        click.secho(f"  {mark} [{kind}] {name:<28} → {dest}", fg=color)

    click.secho(f"→ ok={ok}  miss={miss}", fg="green" if miss == 0 else "yellow")
    if miss and not all(opt for _, _, _, present, opt in rows if not present):
        sys.exit(1)


# ---- sbom ----------------------------------------------------------------


@cli.command(help="导出当前仓库 vendor/ + models/ 的 SBOM（JSON）")
@click.option("--out", "out_path", type=click.Path(),
              default=str(PKG_ROOT / "sbom.json"))
def sbom(out_path: str) -> None:
    """非常轻量的 SBOM：仓库下 vendor/ + models/ 第一层的目录树。"""
    items = []
    for grp in ("vendor/runtimes", "vendor/services", "models"):
        d = REPO_ROOT / grp
        if not d.is_dir():
            continue
        for child in sorted(d.iterdir()):
            if child.is_dir():
                size = sum(p.stat().st_size for p in child.rglob("*") if p.is_file())
                items.append({
                    "group": grp, "name": child.name,
                    "path": str(child.relative_to(REPO_ROOT)),
                    "bytes": size,
                })
    out = Path(out_path)
    out.write_text(json.dumps({"items": items}, indent=2, ensure_ascii=False), encoding="utf-8")
    total_gb = sum(i["bytes"] for i in items) / 1024 / 1024 / 1024
    click.secho(f"→ {out} ({len(items)} items, {total_gb:.2f} GB)", fg="green")


# ---- clean ---------------------------------------------------------------


@cli.command(help="清空 .cache 和 build/ 产物")
@click.option("--cache-only", is_flag=True, default=False)
@click.option("--build-only", is_flag=True, default=False)
def clean(cache_only: bool, build_only: bool) -> None:
    if cache_only or not build_only:
        if CACHE_DIR.is_dir():
            shutil.rmtree(CACHE_DIR)
            click.echo(f"× rm -rf {CACHE_DIR}")
    if build_only or not cache_only:
        if BUILD_DIR.is_dir():
            shutil.rmtree(BUILD_DIR)
            click.echo(f"× rm -rf {BUILD_DIR}")
    click.secho("→ clean done.", fg="green")


# ────────────────────────────────────────────────────────────────────
# entry
# ────────────────────────────────────────────────────────────────────


def main() -> None:
    """``chayuan-pack`` 入口点（在 pyproject 的 ``[project.scripts]``）。"""
    try:
        cli(standalone_mode=True)
    except click.ClickException:
        raise
    except Exception as e:  # noqa: BLE001
        click.secho(f"✘ {type(e).__name__}: {e}", fg="red", err=True)
        sys.exit(2)


if __name__ == "__main__":  # pragma: no cover
    main()
