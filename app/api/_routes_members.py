"""Member management routes — super_admin only: add/edit-role/edit-branch/remove.

Every mutation re-renders the full members panel (mirrors branches_panel_html's
list+form pattern) so the row-level inline forms have a single render path to test."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.admin._branch import require_super_admin
from app.domain.enums import Role
from app.modules.auth.repository import MembershipRepo, UserRepo

from ._i18n import apply_lang, t
from ._ui_members import members_panel_html

router = APIRouter(dependencies=[Depends(require_super_admin)])

_MEMBERS_Q = (
    "SELECT m.id, u.telegram_id, m.role, u.name, m.branch_id"
    " FROM membership m JOIN app_user u ON u.id = m.user_id"
    " ORDER BY m.branch_id NULLS FIRST, m.role, u.name"
)
_BRANCHES_Q = "SELECT id, name FROM branch ORDER BY name"
_ROLE_VALUES = {r.value for r in Role}


async def _render_panel(session) -> str:
    rows = (await session.execute(text(_MEMBERS_Q))).all()
    branches = (await session.execute(text(_BRANCHES_Q))).all()
    return members_panel_html(list(rows), list(branches))


def _current_user_id(request: Request) -> int | None:
    user = getattr(getattr(request, "state", None), "user", None) or {}
    return user.get("uid")


def _forbidden_self_edit(request: Request, target_user_id: int) -> HTMLResponse | None:
    """Block editing/removing your OWN membership — the simplest way to guarantee a
    super_admin can never accidentally lock themselves out."""
    if target_user_id == _current_user_id(request):
        return HTMLResponse(
            f'<div class="emp">{t("member.self_locked")}</div>', status_code=400)
    return None


@router.get("/members/panel", response_class=HTMLResponse)
async def members_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        html = await _render_panel(session)
    return HTMLResponse(html)


@router.post("/members/create", response_class=HTMLResponse)
async def members_create(
    request: Request, telegram_id: int = Form(...), name: str = Form(default=""),
    role: str = Form(default=Role.BRANCH_VIEWER.value), branch_id: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    if role not in _ROLE_VALUES:
        return HTMLResponse('<div class="emp">Invalid role</div>', status_code=400)
    bid = int(branch_id) if branch_id.strip().isdigit() else None
    async with session_scope() as session:
        users = UserRepo(session)
        user = await users.get_by_telegram_id(telegram_id)
        if user is None:
            user = await users.create(telegram_id, name.strip() or None)
        elif name.strip():
            user.name = name.strip()
            session.add(user)
        # upsert, not create: one role per (user, branch) — a user can hold different
        # roles in different branches, but never two conflicting rows for the same branch.
        await MembershipRepo(session).upsert(user.id, bid, Role(role))
        html = await _render_panel(session)
    return HTMLResponse(html)


@router.post("/members/{membership_id}/role", response_class=HTMLResponse)
async def members_set_role(
    membership_id: int, request: Request, role: str = Form(...),
) -> HTMLResponse:
    apply_lang(request)
    if role not in _ROLE_VALUES:
        return HTMLResponse('<div class="emp">Invalid role</div>', status_code=400)
    async with session_scope() as session:
        repo = MembershipRepo(session)
        m = await repo.get(membership_id)
        if m is None:
            return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
        blocked = _forbidden_self_edit(request, m.user_id)
        if blocked is not None:
            return blocked
        await repo.update_role(membership_id, Role(role))
        html = await _render_panel(session)
    return HTMLResponse(html)


@router.post("/members/{membership_id}/branch", response_class=HTMLResponse)
async def members_set_branch(
    membership_id: int, request: Request, branch_id: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    bid = int(branch_id) if branch_id.strip().isdigit() else None
    async with session_scope() as session:
        repo = MembershipRepo(session)
        m = await repo.get(membership_id)
        if m is None:
            return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
        blocked = _forbidden_self_edit(request, m.user_id)
        if blocked is not None:
            return blocked
        await repo.update_branch(membership_id, bid)
        html = await _render_panel(session)
    return HTMLResponse(html)


@router.post("/members/{membership_id}/delete", response_class=HTMLResponse)
async def members_delete(membership_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        repo = MembershipRepo(session)
        m = await repo.get(membership_id)
        if m is None:
            return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
        blocked = _forbidden_self_edit(request, m.user_id)
        if blocked is not None:
            return blocked
        await repo.delete(membership_id)
        html = await _render_panel(session)
    return HTMLResponse(html)
