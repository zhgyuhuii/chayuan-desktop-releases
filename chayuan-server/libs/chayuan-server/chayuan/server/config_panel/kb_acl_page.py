"""Config Panel · 知识库访问控制页。

直接走 ``chayuan.server.auth.service`` 的 ACL 函数 + ORM 查 KB 清单。
面板以 admin 身份操作，不再走 /knowledge_base/{name}/grants 的 JWT 路径。

页面布局：
    - 顶部：KB 列表（含 owner / visibility / 授权人数）
    - 选中一个 KB 后：
        · 可设置可见性 private / public
        · 可修改 owner
        · 列出授权记录，可新增 reader / editor / 删除
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _list_kbs() -> List[Dict[str, Any]]:
    from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
    from chayuan.server.db.session import session_scope

    with session_scope() as s:
        rows = s.query(KnowledgeBaseModel).order_by(KnowledgeBaseModel.id.asc()).all()
        out = []
        for kb in rows:
            out.append({
                "id": kb.id,
                "kb_name": kb.kb_name,
                "owner_id": kb.owner_id,
                "visibility": kb.visibility or "private",
            })
        return out


def _list_users_lite() -> Dict[int, str]:
    from chayuan.server.auth.service import list_users
    return {u.id: f"#{u.id} {u.username}" for u in list_users(limit=500)}


def _list_grants(kb_id: int) -> List[Dict[str, Any]]:
    from chayuan.server.auth.service import list_kb_grants
    users = _list_users_lite()
    out = []
    for g in list_kb_grants(kb_id):
        out.append({
            "user_id": g.user_id,
            "user": users.get(g.user_id, f"#{g.user_id}"),
            "role": g.role,
            "granted_by": g.granted_by,
        })
    return out


def _set_visibility(kb_id: int, visibility: str) -> None:
    from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
    from chayuan.server.db.session import session_scope

    if visibility not in ("private", "public"):
        raise ValueError("visibility must be private/public")
    with session_scope() as s:
        kb = s.get(KnowledgeBaseModel, int(kb_id))
        if kb is None:
            raise ValueError("kb not found")
        kb.visibility = visibility


def _set_owner(kb_id: int, owner_id: Optional[int]) -> None:
    from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
    from chayuan.server.db.session import session_scope

    with session_scope() as s:
        kb = s.get(KnowledgeBaseModel, int(kb_id))
        if kb is None:
            raise ValueError("kb not found")
        kb.owner_id = int(owner_id) if owner_id else None


def render_kb_acl_page(ui) -> None:
    ui.label("知识库访问控制").classes("text-2xl font-semibold q-mb-sm")
    ui.label(
        "可见性 public 的 KB 所有登录用户都能读；private KB 只有 owner 和被授权者可访问。"
        " admin 对所有 KB 一律放行。"
    ).classes("text-sm text-grey-7 q-mb-md")

    state: Dict[str, Any] = {"selected_kb": None}
    status_label = ui.label("").classes("text-sm text-grey-7")

    def _toast(msg: str, color: str = "positive") -> None:
        status_label.set_text(msg)
        ui.notify(msg, color=color)

    table_area = ui.column().classes("w-full gap-2")
    detail_area = ui.column().classes("w-full gap-2 q-mt-md")

    def _render_detail() -> None:
        detail_area.clear()
        kb = state.get("selected_kb")
        if not kb:
            return
        with detail_area:
            ui.separator().classes("q-my-sm")
            ui.label(f"KB：{kb['kb_name']} (id={kb['id']})").classes("text-lg font-semibold")

            users = _list_users_lite()
            users_with_none = {0: "（无 owner）", **users}

            with ui.row().classes("w-full items-end gap-2"):
                vis = ui.select(
                    {"private": "private（私有）", "public": "public（公共）"},
                    value=kb["visibility"] or "private",
                    label="可见性",
                ).classes("w-56")

                def _go_vis():
                    try:
                        _set_visibility(kb["id"], vis.value or "private")
                        _toast("已更新可见性")
                        _refresh()
                    except Exception as e:  # noqa: BLE001
                        _toast(f"更新失败：{e!r}", color="negative")

                ui.button("保存可见性", on_click=_go_vis).props("color=primary")

                owner = ui.select(
                    users_with_none,
                    value=kb["owner_id"] or 0,
                    label="Owner",
                ).classes("w-56")

                def _go_owner():
                    try:
                        oid = owner.value or 0
                        _set_owner(kb["id"], None if oid == 0 else int(oid))
                        _toast("已更新 owner")
                        _refresh()
                    except Exception as e:  # noqa: BLE001
                        _toast(f"更新失败：{e!r}", color="negative")

                ui.button("保存 Owner", on_click=_go_owner).props("flat")

            ui.separator().classes("q-my-sm")
            ui.label("授权清单").classes("text-base font-semibold")

            grants = _list_grants(kb["id"])
            if grants:
                columns = [
                    {"name": "user", "label": "用户", "field": "user", "align": "left"},
                    {"name": "role", "label": "角色", "field": "role", "align": "left"},
                    {"name": "granted_by", "label": "授权人", "field": "granted_by", "align": "left"},
                ]
                ui.table(columns=columns, rows=grants, row_key="user_id").classes("w-full")
            else:
                ui.label("尚无显式授权。").classes("text-grey-6")

            with ui.row().classes("w-full items-end gap-2 q-mt-sm"):
                grant_user = ui.select(users, label="选择用户").classes("w-64")
                grant_role = ui.select(
                    {"reader": "reader（只读）", "editor": "editor（可写）"},
                    value="reader", label="角色",
                ).classes("w-48")

                def _go_grant():
                    if not grant_user.value:
                        _toast("请选择用户", color="warning")
                        return
                    try:
                        from chayuan.server.auth.service import grant_kb_access
                        grant_kb_access(
                            kb_id=int(kb["id"]),
                            user_id=int(grant_user.value),
                            role=grant_role.value or "reader",
                            granted_by=None,  # 面板操作
                        )
                        _toast("授权成功")
                        _render_detail()
                    except Exception as e:  # noqa: BLE001
                        _toast(f"授权失败：{e!r}", color="negative")

                def _go_revoke():
                    if not grant_user.value:
                        _toast("请选择用户", color="warning")
                        return
                    try:
                        from chayuan.server.auth.service import revoke_kb_access
                        revoke_kb_access(
                            kb_id=int(kb["id"]),
                            user_id=int(grant_user.value),
                        )
                        _toast("已撤销")
                        _render_detail()
                    except Exception as e:  # noqa: BLE001
                        _toast(f"撤销失败：{e!r}", color="negative")

                ui.button("授权 / 更新", on_click=_go_grant).props("color=primary")
                ui.button("撤销", on_click=_go_revoke).props("color=negative flat")

    def _refresh() -> None:
        table_area.clear()
        with table_area:
            kbs = _list_kbs()
            if not kbs:
                ui.label("当前没有知识库。").classes("text-grey-6")
                detail_area.clear()
                state["selected_kb"] = None
                return

            users_map = _list_users_lite()
            rows = []
            for kb in kbs:
                rows.append({
                    **kb,
                    "owner": users_map.get(kb["owner_id"], "-") if kb["owner_id"] else "-",
                })

            columns = [
                {"name": "id", "label": "ID", "field": "id", "align": "left"},
                {"name": "kb_name", "label": "KB", "field": "kb_name", "align": "left"},
                {"name": "owner", "label": "Owner", "field": "owner", "align": "left"},
                {"name": "visibility", "label": "可见性", "field": "visibility", "align": "left"},
            ]
            ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")

            with ui.row().classes("w-full items-end gap-2 q-mt-sm"):
                sel = ui.select(
                    {kb["id"]: f"#{kb['id']} {kb['kb_name']}" for kb in kbs},
                    label="选择 KB 查看 / 修改",
                ).classes("w-96")

                def _go_open():
                    if not sel.value:
                        _toast("请选择 KB", color="warning")
                        return
                    for kb in kbs:
                        if kb["id"] == int(sel.value):
                            state["selected_kb"] = kb
                            _render_detail()
                            return

                ui.button("打开", on_click=_go_open).props("color=primary")

    _refresh()
