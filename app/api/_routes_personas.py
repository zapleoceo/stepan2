"""Persona-library routes: browse the library, pick a persona for the branch, favorite it,
and edit the branch's per-section addendum. Read/browse is open to any authenticated member;
selecting / favoriting / addendum require WRITE on a single branch. Persona content itself is
read-only here (only the author updates the core, in a later phase)."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app.adapters.db.session import session_scope
from app.admin._branch import branch_ids_from_request, writable_branch_ids
from app.api._i18n import apply_lang, t
from app.api._ui_personas import persona_detail_html, personas_panel_html
from app.modules.persona import service as P

router = APIRouter()


def _acting_branch(request: Request) -> int | None:
    """The single branch a write acts on: the caller's writable branch, or the one selected
    in the branch filter for a super_admin. None when the scope isn't a single branch."""
    w = writable_branch_ids(request)
    if w:
        return w[0]
    b = branch_ids_from_request(request)  # None = super_admin; may still have a single filter
    return b[0] if b and len(b) == 1 else None


async def _render_library(request: Request) -> str:
    bid = _acting_branch(request)
    async with session_scope() as session:
        await P.ensure_seeded(session)
        personas = await P.list_personas(session)
        adopt = await P.adoption(session)
        active_id: int | None = None
        fav_ids: set[int] = set()
        if bid is not None:
            active_id, _add, fav_ids = await P.branch_state(session, bid)
        active_name = next((p.name for p in personas if p.id == active_id), "")
    return personas_panel_html(
        personas, adopt, active_id, fav_ids, can_write=bid is not None,
        active_name=active_name)


@router.get("/personas", response_class=HTMLResponse)
async def personas_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    return HTMLResponse(await _render_library(request))


@router.get("/personas/{pid}", response_class=HTMLResponse)
async def persona_detail(pid: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    bid = _acting_branch(request)
    async with session_scope() as session:
        await P.ensure_seeded(session)
        persona = await P.get_persona(session, pid)
        if persona is None:
            return HTMLResponse(f'<div class="emp">{t("pl.gone")}</div>', status_code=404)
        active_id, addendum, fav_ids = (
            await P.branch_state(session, bid) if bid is not None else (None, {}, set()))
    return HTMLResponse(persona_detail_html(
        persona, addendum, active=persona.id == active_id, fav=persona.id in fav_ids,
        can_write=bid is not None))


@router.post("/personas/{pid}/use", response_class=HTMLResponse)
async def persona_use(pid: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    bid = _acting_branch(request)
    if bid is None:
        return HTMLResponse(f'<div class="emp">{t("pl.pick_branch")}</div>', status_code=400)
    async with session_scope() as session:
        if await P.get_persona(session, pid) is None:
            return HTMLResponse(f'<div class="emp">{t("pl.gone")}</div>', status_code=404)
        await P.set_active(session, bid, pid)
    return HTMLResponse(await _render_library(request))


@router.post("/personas/{pid}/favorite", response_class=HTMLResponse)
async def persona_favorite(pid: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    bid = _acting_branch(request)
    if bid is None:
        return HTMLResponse(f'<div class="emp">{t("pl.pick_branch")}</div>', status_code=400)
    async with session_scope() as session:
        if await P.get_persona(session, pid) is None:
            return HTMLResponse(f'<div class="emp">{t("pl.gone")}</div>', status_code=404)
        await P.toggle_favorite(session, bid, pid)
    return HTMLResponse(await _render_library(request))


@router.post("/personas/{pid}/addendum", response_class=HTMLResponse)
async def persona_addendum(
    pid: int, request: Request, section: str = Form(...), text: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    bid = _acting_branch(request)
    if bid is None:
        return HTMLResponse(f'<div class="emp">{t("pl.pick_branch")}</div>', status_code=400)
    async with session_scope() as session:
        persona = await P.get_persona(session, pid)
        if persona is None:
            return HTMLResponse(f'<div class="emp">{t("pl.gone")}</div>', status_code=404)
        await P.save_addendum(session, bid, section.strip(), text)
        active_id, addendum, fav_ids = await P.branch_state(session, bid)
    return HTMLResponse(persona_detail_html(
        persona, addendum, active=persona.id == active_id, fav=persona.id in fav_ids,
        can_write=True))
