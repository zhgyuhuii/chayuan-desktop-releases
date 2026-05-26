"""`chayuan chat-doctor` 子命令：对 6 种对话能力做一次静态配置体检。

聚焦 "为什么问大模型回答非所问" 这一典型症状，检查如下几个面：
    1. 当前 CHAYUAN_ROOT / prompt_settings.yaml 必需模板
    2. model_settings.yaml 是否至少配了一个 platform 且 action_model 指向可用 LLM
    3. /chat/chat/completions、/chat/kb_chat、/chat/file_chat、KB OpenAI 兼容 3 条路由已注册
    4. MCP 连接表（有几条、enabled 多少）
    5. Vision/Image 模型有没有被正确归类到 image2text_models
    6. langchain-openai / langchain-core / langchain-ollama 等关键包版本

不跑真实请求（相比集成测试零外网依赖）；需要跑真实 chat smoke 可后续再加
``--smoke`` 子选项去触发一次 /chat/chat/completions。
"""
from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Dict, List, Tuple

import click


_REQUIRED_ACTION_TEMPLATES = (
    "default",
    "platform-agent",
    "platform-knowledge-mode",
    "openai-functions",
    "qwen",
    "glm3",
    "structured-chat-agent",
)
_REQUIRED_RAG_TEMPLATES = ("default", "empty")
_REQUIRED_ROUTES = (
    ("POST", "/chat/chat/completions"),
    ("POST", "/chat/kb_chat"),
    ("POST", "/chat/file_chat"),
    ("POST", "/knowledge_base/{mode}/{param}/chat/completions"),
)
_KEY_PACKAGES = (
    "langchain",
    "langchain-core",
    "langchain-classic",
    "langchain-openai",
    "langchain-community",
    "langchain-experimental",
    "langchain-ollama",
    "openai",
    "tiktoken",
)


def _check_prompt_templates() -> List[Tuple[str, str, str]]:
    """返回 [(severity, id, message)]；severity in {ok, warning, critical}."""
    out: List[Tuple[str, str, str]] = []
    try:
        from chayuan.settings import Settings
        ps = Settings.prompt_settings
    except Exception as e:  # noqa: BLE001
        return [("critical", "prompt.load", f"无法加载 Settings.prompt_settings: {e}")]

    action_model = getattr(ps, "action_model", {}) or {}
    missing_action: List[str] = []
    for key in _REQUIRED_ACTION_TEMPLATES:
        tpl = action_model.get(key)
        if not (isinstance(tpl, dict) and tpl.get("SYSTEM_PROMPT")):
            missing_action.append(key)
    if missing_action:
        out.append((
            "warning",
            "prompt.action_model",
            "prompt_settings.yaml 缺少 action_model 子键：" + ", ".join(missing_action)
            + "（缺 platform-knowledge-mode 会导致 agent 模式只复述系统人设）",
        ))
    else:
        out.append(("ok", "prompt.action_model", f"action_model 模板齐全（{len(_REQUIRED_ACTION_TEMPLATES)} 个必需子键）"))

    rag = getattr(ps, "rag", {}) or {}
    missing_rag: List[str] = []
    for key in _REQUIRED_RAG_TEMPLATES:
        val = rag.get(key)
        if not (isinstance(val, str) and val.strip()):
            missing_rag.append(key)
    if missing_rag:
        out.append((
            "critical", "prompt.rag",
            "prompt_settings.yaml 缺少 rag 模板：" + ", ".join(missing_rag)
            + "（缺 rag.default/empty 会让 KB 问答/文件对话/搜索引擎问答整体挂掉）",
        ))
    else:
        out.append(("ok", "prompt.rag", f"rag 模板齐全（{len(_REQUIRED_RAG_TEMPLATES)} 个必需子键）"))

    return out


def _check_models() -> List[Tuple[str, str, str]]:
    out: List[Tuple[str, str, str]] = []
    try:
        from chayuan.settings import Settings
        platforms = Settings.model_settings.MODEL_PLATFORMS or []
    except Exception as e:  # noqa: BLE001
        return [("critical", "model.load", f"无法加载 Settings.model_settings: {e}")]

    if not platforms:
        return [("critical", "model.platforms", "MODEL_PLATFORMS 为空，所有对话模式都无法工作")]

    total_llm = 0
    total_vision = 0
    total_embed = 0
    for p in platforms:
        total_llm += len(getattr(p, "llm_models", None) or [])
        total_vision += len(getattr(p, "image2text_models", None) or [])
        total_embed += len(getattr(p, "embed_models", None) or [])
    out.append(("ok" if total_llm else "critical", "model.llm_count",
                f"LLM 模型合计 {total_llm} 个 / Vision {total_vision} 个 / Embedding {total_embed} 个"))
    if total_vision == 0:
        out.append(("warning", "model.vision_missing",
                    "未配置任何 image2text_models → 图像对话不可用（可在模型设置里给多模态模型打 vision tag）"))
    if total_embed == 0:
        out.append(("warning", "model.embed_missing",
                    "未配置任何 embed_models → 知识库问答/文件对话的向量检索会失败"))
    return out


def _check_routes() -> List[Tuple[str, str, str]]:
    out: List[Tuple[str, str, str]] = []
    try:
        from chayuan.server.api_server.server_app import create_app
        app = create_app(run_mode=None)
    except Exception as e:  # noqa: BLE001
        return [("critical", "route.load_app", f"无法加载 create_app: {e}")]

    registered = set()
    for r in getattr(app, "routes", []):
        methods = getattr(r, "methods", None) or set()
        path = getattr(r, "path", "")
        for m in methods:
            registered.add((m.upper(), path))
    missing: List[str] = []
    for method, path in _REQUIRED_ROUTES:
        if (method, path) not in registered:
            missing.append(f"{method} {path}")
    if missing:
        out.append(("critical", "route.missing", "关键聊天路由未注册：" + ", ".join(missing)))
    else:
        out.append(("ok", "route.missing", f"核心聊天路由已注册（{len(_REQUIRED_ROUTES)} 条）"))
    return out


def _check_mcp() -> List[Tuple[str, str, str]]:
    try:
        from chayuan.server.db.repository.mcp_connection_repository import (
            get_enabled_mcp_connections,
            list_mcp_connections,
        )
    except Exception:
        return [("info", "mcp.repo", "mcp_connection_repository 不可用，跳过 MCP 体检")]
    try:
        all_conns = list_mcp_connections() or []
        enabled = get_enabled_mcp_connections() or []
    except Exception as e:  # noqa: BLE001
        return [("warning", "mcp.query", f"查询 MCP 连接失败: {e}")]
    return [("info", "mcp.summary",
             f"MCP 连接共 {len(all_conns)} 条，启用 {len(enabled)} 条（未启用时 agent 自动降级为 default 模式）")]


def _check_packages() -> List[Tuple[str, str, str]]:
    out: List[Tuple[str, str, str]] = []
    lines: List[str] = []
    for pkg in _KEY_PACKAGES:
        try:
            lines.append(f"{pkg}={version(pkg)}")
        except PackageNotFoundError:
            lines.append(f"{pkg}=MISSING")
    out.append(("info", "deps.versions", "; ".join(lines)))

    try:
        openai_version = version("langchain-openai")
        if openai_version.startswith("0."):
            out.append((
                "critical", "deps.openai_legacy",
                f"langchain-openai {openai_version} 仍是 0.x —— 与 langchain-core 1.x 矩阵错配，会出现行为漂移。"
                "请 pip install -U 'langchain-openai>=1.1.14,<2.0.0'",
            ))
    except PackageNotFoundError:
        out.append(("critical", "deps.openai_missing", "langchain-openai 未安装"))
    return out


SEV_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
SEV_COLOR = {"critical": "red", "warning": "yellow", "info": "cyan", "ok": "green"}
SEV_TAG = {"critical": "CRIT", "warning": "WARN", "info": "INFO", "ok": " OK "}


def run_chat_doctor() -> Tuple[int, Dict[str, Any]]:
    """执行所有子体检，返回 (critical_count, 结构化结果)。"""
    sections = [
        ("prompt", _check_prompt_templates),
        ("model", _check_models),
        ("route", _check_routes),
        ("mcp", _check_mcp),
        ("deps", _check_packages),
    ]
    result: Dict[str, Any] = {"sections": {}, "counts": {"critical": 0, "warning": 0, "info": 0, "ok": 0}}
    for name, fn in sections:
        try:
            items = fn()
        except Exception as e:  # noqa: BLE001
            items = [("critical", f"{name}.crash", f"体检项崩溃：{type(e).__name__}: {e}")]
        result["sections"][name] = [{"severity": s, "id": i, "message": m} for s, i, m in items]
        for s, _, _ in items:
            result["counts"][s] = result["counts"].get(s, 0) + 1
    return result["counts"]["critical"], result


@click.command("chat-doctor", help="体检六种对话能力（普通/KB/图像/文件/搜索/MCP）的配置与依赖")
@click.option("--json", "as_json", is_flag=True, help="纯 JSON 输出（适合 CI）")
def chat_doctor_cmd(as_json: bool) -> None:  # noqa: D401
    critical, report = run_chat_doctor()
    if as_json:
        click.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        click.secho("\n察元AI助手 · 对话能力体检", bold=True)
        click.secho("-" * 60)
        for section_name, items in report["sections"].items():
            click.secho(f"\n[{section_name}]", bold=True)
            for it in items:
                sev = it["severity"]
                click.secho(
                    f"  {SEV_TAG[sev]}  {it['id']:<28}  {it['message']}",
                    fg=SEV_COLOR[sev],
                )
        c = report["counts"]
        click.secho("\n汇总：", bold=True)
        click.secho(
            f"  严重 {c.get('critical', 0)}  警告 {c.get('warning', 0)}  "
            f"建议 {c.get('info', 0)}  达标 {c.get('ok', 0)}",
            fg=SEV_COLOR["critical" if c.get("critical") else "warning" if c.get("warning") else "ok"],
            bold=True,
        )
    raise SystemExit(1 if critical else 0)


if __name__ == "__main__":  # pragma: no cover
    chat_doctor_cmd.main(standalone_mode=True)
