"""`chayuan doctor` 子命令：CLI 版一键体检 + 修复。

对齐 OpenClaw / `brew doctor` 风格：

- **默认**：跑全部分类，彩色打印；有 critical 返回码 1，warning 返回码 0。
- **`--json`**：纯 JSON 输出，**永不打印彩色**；适合 CI / k8s liveness 探针。
- **`--category`**：只跑指定分类（逗号分隔）；可选值见
  :data:`chayuan.server.config_panel.health.CATEGORIES`。
- **`--fix`**：跑完体检后自动执行所有 `fixable` 条目的 fixer；跑完再做一次
  体检并在输出底部展示修复前后差异。
- **`--fix-id`**：显式指定要执行的 fixer（逗号分隔，优先级高于 ``--fix``）。
- **`--fail-on critical|warning`**：CI 模式，任何达到指定严重度即 exit 2。
- **`--timeout`**：单次 HTTP / TCP 探测超时（秒）；默认沿用 health 模块内默认。

输出对齐 ``chayuan status``：严重度走 emoji + 带色行头。

使用示例::

    chayuan doctor
    chayuan doctor --category config,runtime --fix
    chayuan doctor --json | jq '.counts'
    chayuan doctor --fail-on critical  # k8s exec liveness
"""
from __future__ import annotations

import json
import sys
from typing import Iterable, List, Optional, Set

import click


_VALID_CATEGORIES = ("config", "resource", "runtime", "connectivity", "api", "scale")
_VALID_FAIL_ON = ("critical", "warning", "info")


def _echo(msg: str, *, color: Optional[str] = None, bold: bool = False, nl: bool = True) -> None:
    """click.secho 的薄包装；测试 / pipe 时方便兜底。"""
    click.secho(msg, fg=color, bold=bold, nl=nl)


def _severity_style(sev: str) -> str:
    return {
        "critical": "red",
        "warning":  "yellow",
        "info":     "cyan",
        "ok":       "green",
    }.get(sev, "white")


def _severity_label(sev: str) -> str:
    from chayuan.server.config_panel.health import SEVERITY_META
    return SEVERITY_META.get(sev, {}).get("cli", sev.upper()[:4].ljust(4))


def _parse_csv(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def _print_human(report, verbose: bool) -> None:
    from chayuan.server.config_panel.health import CATEGORIES

    _echo("", nl=True)
    _echo(f"察元AI助手 · 健康体检   mode={report.mode}   "
          f"耗时 {report.elapsed_ms} ms   @ {report.generated_at}",
          bold=True)
    counts = report.counts
    summary = (
        f"严重 {counts['critical']}  "
        f"警告 {counts['warning']}  "
        f"建议 {counts['info']}  "
        f"达标 {counts['ok']}"
    )
    _echo(summary, color=_severity_style(report.worst_severity), bold=True)
    _echo(f"预计可承载同时在线：{report.est_concurrent_users}")
    _echo("-" * 72)

    by_cat = report.by_category()
    for cat in sorted(by_cat.keys(), key=lambda c: CATEGORIES.get(c, {}).get("order", 99)):
        entries = by_cat[cat]
        if not entries:
            continue
        meta = CATEGORIES.get(cat, {})
        _echo(f"\n[{cat}] {meta.get('label', cat)} — {meta.get('desc', '')}", bold=True)
        for c in entries:
            tag = _severity_label(c.severity)
            fix_mark = "  (可修复)" if c.fixable else ""
            _echo(
                f"  {tag}  {c.id:<32} {c.title}{fix_mark}",
                color=_severity_style(c.severity),
            )
            _echo(f"         {c.summary}")
            if verbose:
                if c.impact:
                    _echo(f"         影响：{c.impact}", color="white")
                if c.fix_hint:
                    _echo(f"         建议：{c.fix_hint}", color="white")
                if c.snippet:
                    _echo("         --- snippet ---")
                    for line in c.snippet.splitlines():
                        _echo(f"         {line}")
    _echo("")


def _select_fix_ids(report, explicit_ids: List[str], include_scale: bool = False) -> List[str]:
    """把 ``report.fixable_checks()`` 映射到 fixer_id 列表；或用用户显式指定。"""
    if explicit_ids:
        # 用户直接给了 fixer_id（不是 check.id），我们去找能匹配的检查；
        # 同时允许传 check.id，用 map 兜底。
        available = {c.id: c.fixer_id for c in report.checks if c.fixer_id}
        mapped: List[str] = []
        for x in explicit_ids:
            if x in available:
                mapped.append(available[x])
            else:
                mapped.append(x)  # 认为是原生 fixer_id
        return mapped

    # 自动挑选：默认不碰 scale 类别（那是配置建议性质，不适合全局自动改）。
    return [
        c.fixer_id for c in report.fixable_checks()
        if include_scale or c.category != "scale"
    ]


def _run_fixers(fixer_ids: Iterable[str]) -> List[dict]:
    from chayuan.server.config_panel.health import run_fixers
    return run_fixers(fixer_ids)


def _print_fixer_results(results: List[dict]) -> None:
    if not results:
        _echo("没有需要修复的项。", color="green")
        return
    _echo("\n一键修复结果：", bold=True)
    for r in results:
        ok = bool(r.get("ok"))
        color = "green" if ok else "red"
        _echo(
            f"  {'OK  ' if ok else 'FAIL'}  {r.get('fixer_id', '?'):<40}  "
            f"{r.get('message', '')}",
            color=color,
        )


def _exit_code(report, fail_on: Optional[str]) -> int:
    """根据 fail-on 计算退出码。默认策略：critical→1；否则 0。"""
    from chayuan.server.config_panel.health import SEVERITY_ORDER
    counts = report.counts
    if fail_on:
        threshold = SEVERITY_ORDER.get(fail_on, 0)
        for sev, n in counts.items():
            if n and SEVERITY_ORDER.get(sev, 99) <= threshold:
                return 2
        return 0
    return 1 if counts.get("critical", 0) else 0


@click.group(
    "doctor",
    invoke_without_command=True,
    help=(
        "体检：配置 / 资源 / 运行时 / 外部连通性 / 服务健康 / 性能扩展。"
        " 加 --fix 一键修复；加 --json 产机器可读结果。"
        " 还支持子命令：``chayuan doctor tools`` 单独跑工具配置体检。"
    ),
)
@click.option(
    "--category", "categories",
    default=None,
    help=f"只跑指定分类（逗号分隔），可选：{', '.join(_VALID_CATEGORIES)}。",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="输出 JSON（禁用颜色）。")
@click.option("--verbose/--no-verbose", default=False, help="文本模式下打印影响 / 建议 / snippet。")
@click.option("--fix", "auto_fix", is_flag=True, default=False, help="自动执行所有 fixable 条目对应的修复。")
@click.option(
    "--fix-id", "fix_ids",
    default=None,
    help="显式指定要修复的条目，逗号分隔；可以传 check.id（如 `config.panel_credentials`）或原生 fixer_id。",
)
@click.option(
    "--fail-on", "fail_on",
    type=click.Choice(_VALID_FAIL_ON, case_sensitive=False),
    default=None,
    help="严重度达到该等级时退出码为 2，便于 CI / k8s probe。",
)
@click.option(
    "--skip-scale/--with-scale",
    default=False,
    help="是否跳过 scale 分类（性能 / 可扩展性的静态建议）。",
)
@click.pass_context
def doctor_cmd(
    ctx: click.Context,
    categories: Optional[str],
    as_json: bool,
    verbose: bool,
    auto_fix: bool,
    fix_ids: Optional[str],
    fail_on: Optional[str],
    skip_scale: bool,
):
    """``chayuan doctor``——多维体检；不带子命令时跑默认「全量体检」路径。

    历史兼容：`chayuan doctor [--json ...]` 完全沿用旧行为。
    新增：`chayuan doctor tools [...]` 走 ``cli_tools_doctor.tools_doctor_cmd``。
    """
    # 被子命令调用时，click 已经处理掉参数，这里不执行默认体检逻辑
    if ctx.invoked_subcommand is not None:
        return

    from chayuan.settings import Settings
    from chayuan.server.config_panel.health import build_report

    Settings.set_auto_reload(False)

    cats = _parse_csv(categories) or None
    if skip_scale and cats:
        cats = [c for c in cats if c != "scale"]
    elif skip_scale and not cats:
        cats = [c for c in _VALID_CATEGORIES if c != "scale"]

    report = build_report(categories=cats)

    explicit_fix_ids = _parse_csv(fix_ids)
    should_fix = auto_fix or bool(explicit_fix_ids)

    fix_results: List[dict] = []
    post_report = None

    if should_fix:
        fids = _select_fix_ids(report, explicit_fix_ids, include_scale=(not skip_scale))
        fix_results = _run_fixers(fids)
        post_report = build_report(categories=cats)

    if as_json:
        payload = {
            "report": report.to_dict(),
            "fix_results": fix_results,
            "post_report": post_report.to_dict() if post_report else None,
        }
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_human(report, verbose=verbose)
        if should_fix:
            _print_fixer_results(fix_results)
            if post_report is not None:
                _echo("\n修复后复检：", bold=True)
                _print_human(post_report, verbose=False)

    final_report = post_report or report
    code = _exit_code(final_report, fail_on)
    Settings.set_auto_reload(True)
    sys.exit(code)


# ---------------------------------------------------------------------------
# 子命令挂载：chayuan doctor tools
# ---------------------------------------------------------------------------

from chayuan.cli_tools_doctor import tools_doctor_cmd as _tools_doctor_cmd  # noqa: E402

doctor_cmd.add_command(_tools_doctor_cmd)

# `chayuan doctor ai-platform` —— 与 GET /v1/admin/doctor 同源的 9 类自检。
# 单独成命令文件，避免给 cli_doctor.py 增加导入开销（chayuan_runtime 11 个
# adapter 起来要几十毫秒）。
try:
    from chayuan.cli_ai_platform_doctor import ai_platform_doctor_cmd as _ai_platform_doctor_cmd
    doctor_cmd.add_command(_ai_platform_doctor_cmd)
except Exception:  # noqa: BLE001
    # ai-platform 模块未装齐时静默降级（standalone chayuan-server 仍可用）
    pass


__all__ = ["doctor_cmd"]
