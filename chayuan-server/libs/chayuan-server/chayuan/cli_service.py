"""``chayuan service ...`` 与 ``chayuan model ...`` 子命令组。

挂在 ``chayuan/cli.py`` 主 entrypoint 之下，提供：

* ``chayuan service info``        打印 runtime.json 中的 endpoint + 凭据表
* ``chayuan service recheck``     重跑 PortAllocator，把端口/凭据冲突自动修正
* ``chayuan service vendor``      列出 vendor/ 子目录扫描结果
* ``chayuan model scan``          强制扫描本地模型目录
* ``chayuan model list``          打印 local_models.json 的内容
* ``chayuan model import <path>`` 把本地任意路径软链接进 ``models/custom/``
* ``chayuan model download <id>`` 走 catalog.download_registry_model 下载

设计上 CLI 直接走"模块函数"，**不**通过 HTTP 调主进程；这样 1) 启动失败时
也能跑、2) 不依赖 API 鉴权，便于在 K8s job / packaging 后启动脚本里使用。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import click


# ---------------------------------------------------------------------------
# service 子命令组
# ---------------------------------------------------------------------------

@click.group("service", help="服务运行时（端口 / 凭据 / vendor 扫描）")
def service_group() -> None:  # noqa: D401
    """命名空间分组。"""


@service_group.command("info", help="打印各服务最终端口、地址、用户名/密码（默认掩码）")
@click.option("--reveal", is_flag=True, default=False, help="显示明文密码（请勿外发输出）")
@click.option("--json", "as_json", is_flag=True, default=False, help="按 JSON 输出，方便脚本消费")
def service_info_cmd(reveal: bool, as_json: bool) -> None:
    from chayuan.server.runtime import get_runtime_info

    ri = get_runtime_info()
    eps = ri.list_endpoints()
    if as_json:
        payload = [dict(e if reveal else e.masked()) for e in eps]
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not eps:
        click.echo("还没有任何服务被记入 runtime.json。先跑一次 `chayuan start -a` 或 `chayuan service recheck`。")
        return

    rows = [("服务", "类型", "地址", "用户", "密码")]
    for ep in eps:
        m = ep if reveal else ep.masked()
        rows.append((
            str(m.get("name") or ""),
            str(m.get("kind") or ""),
            str(m.get("url") or f"{m.get('host')}:{m.get('port')}"),
            str(m.get("user") or "-"),
            str(m.get("password") or "-"),
        ))
    cols = list(zip(*rows))
    widths = [max(len(str(c)) for c in col) for col in cols]
    for r in rows:
        click.echo("  ".join(str(v).ljust(w) for v, w in zip(r, widths)))
    if not reveal:
        click.echo("\n(密码默认 ****；加 --reveal 显示明文)")


@service_group.command("recheck", help="重新走一遍 PortAllocator，端口冲突时自动 bump 并落库")
@click.option("--json", "as_json", is_flag=True, default=False)
def service_recheck_cmd(as_json: bool) -> None:
    from chayuan.server.runtime import allocate_core_ports, render_endpoints_table
    result = allocate_core_ports()
    if as_json:
        click.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return
    click.echo(render_endpoints_table(result))
    if result.warnings:
        sys.exit(0)


@service_group.command("vendor", help="扫描 <CHAYUAN_ROOT>/vendor 与仓库 vendor/ 目录")
@click.option("--json", "as_json", is_flag=True, default=False)
def service_vendor_cmd(as_json: bool) -> None:
    from chayuan.server.runtime import discover_vendor

    layout = discover_vendor()
    if as_json:
        click.echo(json.dumps(layout.to_dict(), ensure_ascii=False, indent=2))
        return
    click.echo(f"vendor 根目录：{layout.root}")
    if not layout.root.is_dir():
        click.echo("（目录不存在；可创建后参考 vendor/README.md 把第三方服务放进来）")
        return
    for group_name, items in (("services", layout.services), ("runtimes", layout.runtimes), ("unknown", layout.unknown)):
        click.echo(f"\n[{group_name}]")
        if not items:
            click.echo("  (空)")
            continue
        for e in items:
            mark = "✓" if e.available else "✗"
            click.echo(f"  {mark} {e.name:<14} {e.label:<22} kind={e.kind} default_port={e.default_port}")
            if e.binary:
                click.echo(f"     binary: {e.binary}")
            if e.docker_compose:
                click.echo(f"     compose: {e.docker_compose}")
            if e.issues:
                for issue in e.issues:
                    click.echo(f"     ! {issue}")


# ---------------------------------------------------------------------------
# model 子命令组
# ---------------------------------------------------------------------------

@click.group("model", help="本地模型管理（扫描 / 列表 / 导入 / 下载）")
def model_group() -> None:  # noqa: D401
    """命名空间分组。"""


@model_group.command("scan", help="强制扫描本地模型目录，打印新增/更新/删除")
@click.option("--json", "as_json", is_flag=True, default=False)
def model_scan_cmd(as_json: bool) -> None:
    from chayuan.server.model_registry.local_index import scan_once

    delta = scan_once()
    if as_json:
        click.echo(json.dumps({
            "added":   [e.to_dict() for e in delta.added],
            "updated": [e.to_dict() for e in delta.updated],
            "removed": list(delta.removed),
        }, ensure_ascii=False, indent=2))
        return
    click.echo(f"+ 新增 {len(delta.added)}，~ 更新 {len(delta.updated)}，- 删除 {len(delta.removed)}")
    for e in delta.added:
        click.echo(f"  + {e.model_id}  ({e.capability}/{e.format}, conf={e.confidence})")
    for e in delta.updated:
        click.echo(f"  ~ {e.model_id}")
    for mid in delta.removed:
        click.echo(f"  - {mid}")


@model_group.command("list", help="列出 local_models.json 中已知的本地模型")
@click.option("--capability", "-c", default="", help="按 capability 过滤（chat / text-embedding / ...）")
@click.option("--json", "as_json", is_flag=True, default=False)
def model_list_cmd(capability: str, as_json: bool) -> None:
    from chayuan.server.model_registry.local_index import get_local_index

    idx = get_local_index()
    items = idx.list_entries()
    if capability:
        items = [e for e in items if e.capability == capability]
    if as_json:
        click.echo(json.dumps([e.to_dict() for e in items], ensure_ascii=False, indent=2))
        return
    if not items:
        click.echo("(空) — 先把模型放进 <CHAYUAN_ROOT>/models/，再 `chayuan model scan`")
        return
    for e in items:
        click.echo(
            f"{e.model_id:<60}  {e.capability:<16}  {e.format:<14}  "
            f"{e.size_bytes/1024/1024:>9.1f} MiB  {e.path}"
        )


@model_group.command("import", help="把本地任意路径软链接进 <CHAYUAN_ROOT>/models/custom/，并立即扫描入库")
@click.argument("path", type=click.Path(exists=True, dir_okay=True, file_okay=True))
@click.option("--name", "-n", default="", help="可选别名；默认取目录/文件名")
def model_import_cmd(path: str, name: str) -> None:
    import os
    import shutil
    from chayuan.settings import CHAYUAN_ROOT
    from chayuan.server.model_registry.local_index import scan_once

    src = Path(path).expanduser().resolve()
    target_root = Path(CHAYUAN_ROOT) / "models" / "custom"
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / (name or src.name)
    if target.exists():
        raise click.ClickException(f"目标已存在：{target}（请改名或先删除）")

    try:
        os.symlink(src, target)
        action = "symlink"
    except (OSError, NotImplementedError):
        if src.is_dir():
            shutil.copytree(src, target)
        else:
            shutil.copy2(src, target)
        action = "copy"

    delta = scan_once()
    click.secho(f"✓ {action}: {src}  →  {target}", fg="green")
    click.echo(f"  扫描结果：+{len(delta.added)}  ~{len(delta.updated)}  -{len(delta.removed)}")


@model_group.command("download", help="按 catalog 中已有 model_id 走 HF/ModelScope 镜像下载到本地")
@click.argument("model_id")
def model_download_cmd(model_id: str) -> None:
    try:
        from chayuan.server.model_registry.catalog import download_registry_model
    except ImportError as e:
        raise click.ClickException(f"无法加载 model_registry.catalog: {e}")

    click.echo(f"→ 开始下载 {model_id} ...")
    try:
        result = download_registry_model(model_id)
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(f"下载失败：{type(e).__name__}: {e}")

    click.secho(f"✓ 下载完成：{result.get('local_path') or result}", fg="green")
    click.echo("  请运行 `chayuan model scan` 让本地模型索引看到新模型。")


@model_group.command(
    "status",
    help="一站式模型链路自检 — 扫盘 + 必需 capability 覆盖 + 推理引擎启动参数预览",
)
@click.option("--json", "as_json", is_flag=True, default=False,
              help="输出 JSON(供脚本消费)")
@click.option("--no-scan", "no_scan", is_flag=True, default=False,
              help="不扫盘,只读现有 local_models.json(性能敏感场景)")
def model_status_cmd(as_json: bool, no_scan: bool) -> None:
    """打印完整链路状态 — 给运维不开 GUI 就能 debug "模型为什么没起来"。

    输出含三段:

    * bootstrap:必需 capability(chat / text-embedding / rerank)的覆盖情况
    * install_hints:bootstrap 缺项时推荐下载哪个 release(lite/standard/pro)
    * process_args:llamacpp / infinity / ollama 重启时会用什么 args/env
    """
    from chayuan.server.model_registry.bootstrap import check_bootstrap
    from chayuan.server.model_registry.install_hints import build_install_hints
    from chayuan.server.model_registry.process_args import resolve_all

    report = check_bootstrap(do_scan=not no_scan)
    hints = build_install_hints(report.missing) if report.missing else []
    snap = resolve_all()

    if as_json:
        payload = {
            "bootstrap": report.to_dict(),
            "install_hints": [h.to_dict() for h in hints],
            "process_args": {name: r.to_dict() for name, r in snap.items()},
        }
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    # 人类可读
    if report.ready:
        click.secho("✓ 模型库自检通过", fg="green", bold=True)
    else:
        click.secho(
            f"✗ 模型库不完整 — 缺 {', '.join(report.missing)}", fg="yellow", bold=True,
        )

    click.echo("\nCapability 覆盖:")
    for s in report.statuses:
        icon = click.style("✓", fg="green") if s.satisfied else click.style("✗", fg="yellow")
        click.echo(f"  {icon}  {s.capability:<18}  候选 {len(s.candidates)}")
        for c in s.candidates[:3]:
            click.echo(
                f"      · {c.model_id}  ({c.format})  {c.size_bytes/1024/1024:>9.1f} MiB"
            )
        if len(s.candidates) > 3:
            click.echo(f"      ... 还有 {len(s.candidates) - 3} 个候选")

    if hints:
        click.echo("\n推荐下载:")
        for h in hints:
            click.echo(
                f"  → {h.release}({h.approx_size_mb} MB)  覆盖 "
                f"{', '.join(h.covered_capabilities)}"
            )
            click.echo(f"     {h.description}")

    click.echo("\n推理引擎启动参数:")
    for name, r in snap.items():
        icon = click.style("✓", fg="green") if r.ok else click.style("○", fg="cyan")
        suffix = (f" missing={','.join(r.missing)}" if r.missing else "")
        click.echo(f"  {icon}  {name}{suffix}")
        if r.args:
            click.echo(f"      args: {' '.join(r.args)}")
        if r.env:
            for k, v in r.env.items():
                click.echo(f"      env:  {k}={v}")
        if not r.args and not r.env:
            click.echo(click.style("      (空 — 上述 capability 未解析出本地模型)",
                                   fg="bright_black"))


__all__ = ["service_group", "model_group"]
