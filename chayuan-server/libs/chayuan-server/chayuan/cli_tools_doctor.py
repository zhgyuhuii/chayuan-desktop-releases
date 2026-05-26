"""``chayuan doctor tools`` —— 工具配置体检。

对照 ``chayuan/data/tools_catalog.json`` 与 ``tool_settings.yaml`` 的运行时状态，
回答三类最常见的「为什么我这个工具用不起来」问题：

1. **启用但缺包**（critical）：``use: true`` 但 ``required_imports`` 有包没装。
   输出里直接给出 ``pip install`` / ``poetry install -E xxx`` 修复命令。
2. **启用但关键配置空着**（critical）：``use: true`` 但 ``required_config`` 列表或
   password 字段为空（例如 api_key / webhook / token 没填）。
3. **配了但没启用**（info）：Key/token 已经填过但 ``use: false``。常见原因是
   在面板里填了密钥忘了拨开关；友好提示「是否忘了启用？」。

全部读本地 yaml + catalog，不联网、不调真实 API；只要能 import ``chayuan`` 就能跑，
适合在 CI / 运维终端做零依赖自检。

用法：

    chayuan doctor tools              # 人类可读输出
    chayuan doctor tools --json       # CI / 管道
    chayuan doctor tools -k github    # 只看 github_tool 一张
    chayuan doctor tools --fail-on critical  # 有 critical 退 2，供 CI gate
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, Dict, List, Optional

import click

from chayuan.server.config_panel import yaml_store


_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3, "skip": 4}
_EMOJI = {"critical": "🔴", "warning": "⚠️ ", "info": "ℹ️ ", "ok": "✅", "skip": "—"}


@dataclass
class ToolCheck:
    key: str
    title: str
    category: str
    severity: str  # critical / warning / info / ok / skip
    status: str    # 一句话
    registered: bool = False
    enabled: bool = False
    missing_imports: List[str] = field(default_factory=list)
    missing_config: List[str] = field(default_factory=list)
    fix_hints: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 加载 catalog + yaml + 注册表
# ---------------------------------------------------------------------------

def _catalog_path() -> Path:
    import chayuan as _c
    return Path(_c.__file__).resolve().parent / "data" / "tools_catalog.json"


def _load_catalog() -> Dict[str, Any]:
    with open(_catalog_path(), "r", encoding="utf-8") as f:
        return json.load(f)


def _load_yaml_doc() -> Dict[str, Any]:
    res = yaml_store.load_yaml("tool_settings.yaml")
    return res.doc if res.exists else {}


def _registry_keys() -> set:
    from chayuan.server.agent.tools_factory import tools_registry  # noqa: F401
    from chayuan.server.agent import tools_factory  # noqa: F401
    return set(tools_registry._TOOLS_REGISTRY.keys())


# ---------------------------------------------------------------------------
# 检查单个工具
# ---------------------------------------------------------------------------

def _get_by_path(doc: Any, dotted: str, default: Any = None) -> Any:
    return yaml_store.get_by_path(doc, dotted, default=default)


def _required_field_paths(tool: Dict[str, Any]) -> List[str]:
    """决定「哪些字段是必填」的规则：

    - 优先看 ``required_config``（目录条目显式声明，最权威）；
    - 否则回退：所有 ``widget == "password"`` 的字段都当作必填。
    """
    explicit = list(tool.get("required_config") or [])
    if explicit:
        return explicit
    return [
        str(f.get("path", ""))
        for f in (tool.get("fields") or [])
        if f.get("widget") == "password" and f.get("path")
    ]


def _check_tool(
    tool: Dict[str, Any],
    doc: Dict[str, Any],
    registry: set,
) -> ToolCheck:
    key = tool["key"]
    title = tool.get("title", key)
    category = tool.get("category", "")

    # 1) 注册状态：直接 key 或 register_as 子工具
    register_children = list(tool.get("register_as") or [])
    if register_children:
        registered = all(c in registry for c in register_children)
    else:
        registered = key in registry

    # 2) yaml 里是否被加入 + 是否启用
    present_in_yaml = isinstance(doc.get(key), dict)
    enabled = bool(_get_by_path(doc, f"{key}.use", default=False))

    # 3) 依赖 import 检查
    missing_imports: List[str] = []
    for mod in tool.get("required_imports") or []:
        try:
            import_module(mod)
        except Exception:  # noqa: BLE001
            missing_imports.append(str(mod))

    # 4) 必填配置检查
    missing_config: List[str] = []
    for path in _required_field_paths(tool):
        val = _get_by_path(doc, f"{key}.{path}", default=None)
        if val in (None, "", []):
            missing_config.append(path)

    fix_hints: List[str] = []
    if missing_imports:
        extras = tool.get("extras")
        if extras:
            fix_hints.append(
                f"运行 `poetry -C libs/chayuan-server install -E {extras}`"
                f"（或 `pip install {' '.join(missing_imports)}`）"
            )
        else:
            fix_hints.append(f"`pip install {' '.join(missing_imports)}`")
    if missing_config:
        fix_hints.append(
            f"在配置面板打开「{title}」的「设置」，填写：{', '.join(missing_config)}"
        )

    # 5) 综合评级
    if not registered and not register_children:
        # 这通常说明 tools_factory 里忘 import 了；极罕见
        return ToolCheck(
            key=key, title=title, category=category,
            severity="critical",
            status="未在 @regist_tool 注册；catalog 和代码不同步",
            registered=False, enabled=enabled,
            fix_hints=["在 chayuan/server/agent/tools_factory/__init__.py 里 import 对应工具"],
        )

    if enabled and missing_imports:
        return ToolCheck(
            key=key, title=title, category=category,
            severity="critical",
            status=f"已启用但依赖未安装：{', '.join(missing_imports)}",
            registered=True, enabled=True,
            missing_imports=missing_imports, missing_config=missing_config,
            fix_hints=fix_hints,
        )
    if enabled and missing_config:
        return ToolCheck(
            key=key, title=title, category=category,
            severity="critical",
            status=f"已启用但关键配置空着：{', '.join(missing_config)}",
            registered=True, enabled=True,
            missing_imports=missing_imports, missing_config=missing_config,
            fix_hints=fix_hints,
        )
    if enabled:
        return ToolCheck(
            key=key, title=title, category=category,
            severity="ok",
            status="已启用，依赖与关键配置都 OK",
            registered=True, enabled=True,
        )
    # 未启用分支：再看是否「配了但忘开」
    if present_in_yaml and not missing_config and tool.get("required_config") \
            or any(
                _get_by_path(doc, f"{key}.{p}") not in (None, "", [])
                for p in _required_field_paths(tool)
            ):
        return ToolCheck(
            key=key, title=title, category=category,
            severity="info",
            status="关键字段已配置，但 use=false（是否忘了启用？）",
            registered=True, enabled=False,
            missing_imports=missing_imports, missing_config=missing_config,
        )

    return ToolCheck(
        key=key, title=title, category=category,
        severity="skip",
        status="未启用",
        registered=True, enabled=False,
        missing_imports=missing_imports,
        missing_config=missing_config,
    )


# ---------------------------------------------------------------------------
# 汇总 + 输出
# ---------------------------------------------------------------------------

def _build_report(only_keys: Optional[List[str]] = None) -> List[ToolCheck]:
    cat = _load_catalog()
    doc = _load_yaml_doc()
    reg = _registry_keys()

    checks: List[ToolCheck] = []
    for tool in cat["tools"]:
        if only_keys and tool["key"] not in only_keys:
            continue
        checks.append(_check_tool(tool, doc, reg))
    checks.sort(key=lambda c: (_SEVERITY_ORDER.get(c.severity, 99), c.key))
    return checks


def _print_human(checks: List[ToolCheck]) -> None:
    click.secho("", nl=True)
    click.secho("察元AI助手 · 工具体检", bold=True)
    counts: Dict[str, int] = {"critical": 0, "warning": 0, "info": 0, "ok": 0, "skip": 0}
    for c in checks:
        counts[c.severity] = counts.get(c.severity, 0) + 1
    click.secho(
        f"🔴 严重 {counts['critical']}   "
        f"⚠️  警告 {counts['warning']}   "
        f"ℹ️  建议 {counts['info']}   "
        f"✅ 达标 {counts['ok']}   "
        f"— 未启用 {counts['skip']}",
        bold=True,
    )
    click.secho("-" * 72)

    # 分类后展示
    order = ["critical", "warning", "info", "ok", "skip"]
    for sev in order:
        bucket = [c for c in checks if c.severity == sev]
        if not bucket:
            continue
        for c in bucket:
            emoji = _EMOJI.get(c.severity, "?")
            fg = {
                "critical": "red", "warning": "yellow",
                "info": "cyan", "ok": "green", "skip": None,
            }.get(c.severity)
            click.secho(
                f"{emoji} {c.key:<28} [{c.category}]  {c.status}",
                fg=fg,
            )
            for hint in c.fix_hints:
                click.secho(f"      → {hint}", fg="white")

    click.secho("")


def _exit_code(checks: List[ToolCheck], fail_on: Optional[str]) -> int:
    if not fail_on:
        return 1 if any(c.severity == "critical" for c in checks) else 0
    threshold = _SEVERITY_ORDER.get(fail_on, 0)
    for c in checks:
        if _SEVERITY_ORDER.get(c.severity, 99) <= threshold:
            return 2
    return 0


# ---------------------------------------------------------------------------
# Click 命令
# ---------------------------------------------------------------------------

@click.command("tools", help="工具配置体检：扫出「启用但缺包」「配了忘启用」等问题")
@click.option("--json", "as_json", is_flag=True, help="JSON 输出，适合 CI / 管道")
@click.option("-k", "--key", "only_key", multiple=True,
              help="只看指定工具（可多次传入，如 -k github_tool -k notion_search）")
@click.option("--fail-on",
              type=click.Choice(["critical", "warning", "info"]),
              default=None,
              help="CI 模式：任一达到指定严重度即 exit 2")
def tools_doctor_cmd(as_json: bool, only_key: tuple[str, ...], fail_on: Optional[str]) -> None:
    only = list(only_key) or None
    try:
        checks = _build_report(only_keys=only)
    except Exception as e:  # noqa: BLE001
        if as_json:
            click.echo(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        else:
            click.secho(f"工具体检失败：{type(e).__name__}: {e}", fg="red")
        sys.exit(3)

    if as_json:
        out = {
            "checks": [asdict(c) for c in checks],
            "counts": {
                sev: sum(1 for c in checks if c.severity == sev)
                for sev in ("critical", "warning", "info", "ok", "skip")
            },
        }
        click.echo(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        _print_human(checks)

    sys.exit(_exit_code(checks, fail_on))
