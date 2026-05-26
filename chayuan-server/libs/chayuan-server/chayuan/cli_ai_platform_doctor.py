"""``chayuan doctor ai-platform`` —— 与 ``GET /v1/admin/doctor`` 同源的 CLI。

为什么单写一个？
================

* 桌面用户在 ``设置 → AI 平台`` 看 doctor 报告；
* 命令行用户也想 ``chayuan doctor ai-platform`` 同样体检结果，不用先把 HTTP
  服务起来；
* 集群运维想把它丢到 ``cron`` / k8s probe 里 ``--fail-on warn`` 退出码触发告警。

为了避免代码重复，本命令直接调用 ``chayuan_gateway.routers.admin`` 里 doctor
路由背后的 helper（不开 HTTP 服务，纯进程内）；前端面板和命令行看到的 JSON
schema 完全一致。
"""
from __future__ import annotations

import json
import sys
from typing import Optional

import click


def _run_in_process(*, with_adapters: bool, with_runtime: bool) -> dict:
    """与 ``GET /v1/admin/doctor`` 复用同一份实现。"""
    from chayuan_gateway.routers.admin import doctor as _doctor_route
    return _doctor_route(with_adapters=with_adapters, with_runtime=with_runtime)


def _summary_status(report: dict) -> str:
    """从 doctor 报告里挤出一句话状态。"""
    pre = report.get("preflight") or {}
    summary = (pre or {}).get("summary") if isinstance(pre, dict) else None
    fatal = warn = ok = 0
    if isinstance(summary, dict):
        fatal = int(summary.get("fatal", 0) or 0)
        warn = int(summary.get("warn", 0) or 0)
        ok = int(summary.get("ok", 0) or 0)

    adapters = report.get("adapters") or []
    bad = sum(1 for a in adapters if not a.get("ok"))
    return f"preflight: fatal={fatal} warn={warn} ok={ok}; adapters: {len(adapters) - bad}/{len(adapters)} reachable"


def _print_human(report: dict, *, verbose: bool) -> None:
    """文本模式：彩色简洁报告。"""
    host = report.get("host") or {}
    click.secho(
        f"chayuan AI 平台体检 · {host.get('system', '?')}/{host.get('machine', '?')}"
        f" · python {host.get('python', '?')}",
        fg="cyan", bold=True,
    )

    pre = report.get("preflight") or {}
    if isinstance(pre, dict) and "checks" in pre:
        click.echo("\n[Preflight]")
        for c in pre.get("checks", []) or []:
            sev = (c.get("severity") or "").lower()
            color = {"ok": "green", "warn": "yellow", "fatal": "red"}.get(sev, "white")
            mark = {"ok": "✓", "warn": "⚠", "fatal": "✘"}.get(sev, "·")
            click.secho(f"  {mark} {c.get('name', '<no-name>')}", fg=color)
            if verbose and c.get("detail"):
                click.echo(f"      {c.get('detail')}")
            if c.get("fix") and (verbose or sev != "ok"):
                click.echo(f"      → fix: {c.get('fix')}")

    runtime = report.get("runtime") or {}
    if runtime:
        click.echo("\n[Runtime endpoints]")
        for name, ep in runtime.items():
            url = ep.get("url") if isinstance(ep, dict) else None
            host_port = (
                f"{ep.get('host')}:{ep.get('port')}"
                if isinstance(ep, dict) and ep.get("host") and ep.get("port")
                else "<unset>"
            )
            click.echo(f"  · {name:<12} {url or host_port}")

    adapters = report.get("adapters") or []
    if adapters:
        click.echo("\n[Adapters]")
        for a in adapters:
            ok = bool(a.get("ok"))
            fg = "green" if ok else ("yellow" if a.get("mock") else "red")
            mark = "✓" if ok else ("·" if a.get("mock") else "✘")
            tail = f" mock={a.get('mock')}" if a.get("mock") else ""
            base = a.get("base_url") or "<no-url>"
            extra = ""
            if not ok and a.get("detail"):
                extra = f"  ({a['detail']})"
            click.secho(f"  {mark} {a.get('name', '?'):<12} {base}{tail}{extra}", fg=fg)

    click.echo()
    click.secho("→ " + _summary_status(report), bold=True)


def _exit_code(report: dict, fail_on: Optional[str]) -> int:
    """fail_on=warn / error → 出现对应严重度时退码 2，否则 0。"""
    if fail_on is None:
        return 0
    pre = report.get("preflight") or {}
    counts = (pre or {}).get("summary") if isinstance(pre, dict) else None
    if isinstance(counts, dict):
        if fail_on == "fatal" and int(counts.get("fatal", 0) or 0) > 0:
            return 2
        if fail_on == "warn" and (
            int(counts.get("fatal", 0) or 0) > 0
            or int(counts.get("warn", 0) or 0) > 0
        ):
            return 2
    adapters = report.get("adapters") or []
    if fail_on in ("warn", "fatal") and any(not a.get("ok") and not a.get("mock") for a in adapters):
        return 2
    return 0


@click.command("ai-platform", help="对 9 类 AI 平台跑 preflight + adapter ping + runtime 端点检查（与 /v1/admin/doctor 同源）。")
@click.option("--json", "as_json", is_flag=True, default=False, help="输出 JSON（前端 / CI 用）")
@click.option("--no-adapters", "no_adapters", is_flag=True, default=False,
              help="跳过对 11 个 adapter 的 ping，仅看 OS / runtime 端点。")
@click.option("--no-runtime", "no_runtime", is_flag=True, default=False,
              help="跳过 runtime.json 端点摘要。")
@click.option("--verbose/--no-verbose", default=False, help="打印每条检查的详情 + fix 提示。")
@click.option("--fail-on", "fail_on",
              type=click.Choice(["warn", "fatal"], case_sensitive=False),
              default=None,
              help="出现该等级问题时退出码 2，便于 CI / k8s probe。")
def ai_platform_doctor_cmd(as_json: bool, no_adapters: bool, no_runtime: bool,
                           verbose: bool, fail_on: Optional[str]) -> None:
    """``chayuan doctor ai-platform`` 入口。"""
    try:
        report = _run_in_process(
            with_adapters=not no_adapters,
            with_runtime=not no_runtime,
        )
    except Exception as e:  # noqa: BLE001
        click.secho(f"✘ 执行失败：{type(e).__name__}: {e}", fg="red", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report, verbose=verbose)

    sys.exit(_exit_code(report, fail_on.lower() if fail_on else None))


__all__ = ["ai_platform_doctor_cmd"]
