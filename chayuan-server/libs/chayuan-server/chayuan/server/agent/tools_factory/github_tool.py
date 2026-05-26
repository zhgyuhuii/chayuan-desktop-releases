"""GitHub 查询工具。

查询某个仓库的 Issue / PR / Release / 文件内容等；只读能力，不做写操作（避免误删）。
需要 Personal Access Token（最小权限 ``repo:read``）。
"""
from typing import Literal

from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import get_tool_config

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)


@regist_tool(title="GitHub 仓库查询")
def github_tool(
    action: Literal[
        "list_open_issues", "get_issue", "list_prs", "get_pr",
        "get_file", "list_branches",
    ] = Field(description="要执行的只读动作"),
    repo: str = Field(description="owner/repo，如 tiangolo/fastapi"),
    number: int = Field(0, description="issue / PR 的编号（仅 get_issue / get_pr 需要）"),
    path: str = Field("", description="文件路径（仅 get_file 需要）"),
):
    """GitHub 只读查询 — 查 issue / PR / 文件内容 / 分支等公共仓库信息。
调用时机:用户问「某 GitHub 仓库的 issue 状态」「某 PR 的内容」「某文件在仓库里什么样」「某仓库有哪些分支」时。
输入:action(``get_issue`` / ``get_pr`` / ``get_file`` / ``list_branches``);repo(owner/repo,如 ``tiangolo/fastapi``);number(issue/PR 编号);path(文件路径);ref(分支或 tag,默认 main)。
输出:JSON 结构化结果。
不要用于:写入操作(本工具只读)、非公开/私有仓库需先配 token、GitLab(用 gitlab_tool)。"""
    cfg = get_tool_config("github_tool") or {}
    token = (cfg.get("token") or "").strip()
    if not token:
        return BaseToolOutput({
            "error": "未配置 GitHub token，请到 https://github.com/settings/tokens 申请",
        }, format="json")
    try:
        from github import Github, GithubException
    except ImportError:
        return BaseToolOutput({
            "error": "PyGithub 未安装，请 `pip install PyGithub`",
        }, format="json")

    try:
        gh = Github(token)
        r = gh.get_repo(repo)
        if action == "list_open_issues":
            issues = r.get_issues(state="open")[:10]
            return BaseToolOutput({
                "data": [
                    {"number": i.number, "title": i.title, "user": i.user.login,
                     "labels": [l.name for l in i.labels], "url": i.html_url}
                    for i in issues
                ],
            }, format="json")
        if action == "get_issue":
            i = r.get_issue(int(number))
            return BaseToolOutput({
                "number": i.number, "title": i.title, "state": i.state,
                "body": (i.body or "")[:4000], "user": i.user.login,
                "url": i.html_url,
            }, format="json")
        if action == "list_prs":
            prs = r.get_pulls(state="open")[:10]
            return BaseToolOutput({
                "data": [
                    {"number": p.number, "title": p.title, "user": p.user.login,
                     "url": p.html_url}
                    for p in prs
                ],
            }, format="json")
        if action == "get_pr":
            p = r.get_pull(int(number))
            return BaseToolOutput({
                "number": p.number, "title": p.title, "state": p.state,
                "body": (p.body or "")[:4000], "url": p.html_url,
            }, format="json")
        if action == "get_file":
            f = r.get_contents(path)
            content = f.decoded_content.decode("utf-8", "replace")[:20000]
            return BaseToolOutput({
                "repo": repo, "path": path, "size": f.size, "content": content,
            }, format="json")
        if action == "list_branches":
            branches = [b.name for b in r.get_branches()[:30]]
            return BaseToolOutput({"branches": branches}, format="json")
    except GithubException as e:
        return BaseToolOutput({
            "error": f"GitHub API error: {e.status} {e.data}",
        }, format="json")
    except Exception as e:  # noqa: BLE001
        return BaseToolOutput({"error": f"{type(e).__name__}: {e}"}, format="json")

    return BaseToolOutput({"error": f"未知 action: {action}"}, format="json")
