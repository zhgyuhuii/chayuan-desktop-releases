"""GitLab 查询工具（私有部署优先）。

国内企业内部多用 GitLab；这个工具默认只读：Issue / MR / 文件 / 分支。
支持 ``url`` 指向自建 GitLab 实例。
"""
from typing import Literal

from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import get_tool_config

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)


@regist_tool(title="GitLab 仓库查询")
def gitlab_tool(
    action: Literal[
        "list_open_issues", "get_issue", "list_mrs", "get_mr",
        "get_file", "list_branches",
    ] = Field(description="要执行的只读动作"),
    project: str = Field(description="项目 ID 或 namespace/project（如 chayuan/server）"),
    iid: int = Field(0, description="issue / MR 的 iid（get_issue / get_mr 需要）"),
    path: str = Field("", description="文件路径（get_file 需要）"),
    ref: str = Field("main", description="分支或 tag（get_file 使用）"),
):
    """GitLab 只读查询(支持私有部署实例)。功能与 github_tool 镜像。
调用时机:用户问「某 GitLab 项目的 issue / MR / 文件」且仓库在 GitLab(非 GitHub)、或在企业私有 GitLab 上时。
输入:action;project(项目 ID 或 namespace/project,如 ``chayuan/server``);iid(issue/MR 的 iid,与 number 不同);path(文件路径);ref(分支)。
输出:JSON 结构化结果。
不要用于:写入、GitHub(用 github_tool)。"""
    cfg = get_tool_config("gitlab_tool") or {}
    token = (cfg.get("token") or "").strip()
    url = (cfg.get("url") or "https://gitlab.com").strip()
    if not token:
        return BaseToolOutput({
            "error": "未配置 GitLab token（个人访问令牌 / Personal Access Token）",
        }, format="json")
    try:
        import gitlab as _gl
    except ImportError:
        return BaseToolOutput({
            "error": "python-gitlab 未安装，请 `pip install python-gitlab`",
        }, format="json")

    try:
        gl = _gl.Gitlab(url=url, private_token=token)
        p = gl.projects.get(project)
        if action == "list_open_issues":
            issues = p.issues.list(state="opened", per_page=10)
            return BaseToolOutput({
                "data": [
                    {"iid": i.iid, "title": i.title, "author": i.author["username"],
                     "labels": i.labels, "web_url": i.web_url}
                    for i in issues
                ],
            }, format="json")
        if action == "get_issue":
            i = p.issues.get(int(iid))
            return BaseToolOutput({
                "iid": i.iid, "title": i.title, "state": i.state,
                "description": (i.description or "")[:4000],
                "web_url": i.web_url,
            }, format="json")
        if action == "list_mrs":
            mrs = p.mergerequests.list(state="opened", per_page=10)
            return BaseToolOutput({
                "data": [
                    {"iid": m.iid, "title": m.title, "author": m.author["username"],
                     "source": m.source_branch, "target": m.target_branch,
                     "web_url": m.web_url}
                    for m in mrs
                ],
            }, format="json")
        if action == "get_mr":
            m = p.mergerequests.get(int(iid))
            return BaseToolOutput({
                "iid": m.iid, "title": m.title, "state": m.state,
                "description": (m.description or "")[:4000],
                "web_url": m.web_url,
            }, format="json")
        if action == "get_file":
            f = p.files.get(file_path=path, ref=ref)
            import base64
            content = base64.b64decode(f.content).decode("utf-8", "replace")[:20000]
            return BaseToolOutput({
                "project": project, "path": path, "ref": ref, "size": f.size,
                "content": content,
            }, format="json")
        if action == "list_branches":
            branches = [b.name for b in p.branches.list(per_page=30)]
            return BaseToolOutput({"branches": branches}, format="json")
    except Exception as e:  # noqa: BLE001
        return BaseToolOutput({"error": f"{type(e).__name__}: {e}"}, format="json")

    return BaseToolOutput({"error": f"未知 action: {action}"}, format="json")
