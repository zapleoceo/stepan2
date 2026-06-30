"""Manager UI — 3-column layout (sidebar + thread list + panel).

Full-page routes only; all HTMX partials live in _routes_*.py sub-modules.

Routes registered here:
  GET  /ui/inbox     — full shell (inbox active)
  GET  /ui/coach     — full shell (coach active)
  GET  /ui/threads   — HTMX: thread list
  GET  /ui/lang/{c}  — language cookie + redirect
"""
from __future__ import annotations

import html as _h

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.admin._branch import branch_ids_from_request

from ._i18n import LANG_COOKIE, LANGS, apply_lang, t
from ._query import _branch_where, fetch_coach_data
from ._routes_admin import _agent_toggle_html  # noqa: F401 (re-exported for tests)
from ._routes_admin import router as _admin_router
from ._routes_branches import router as _branches_router
from ._routes_chat import router as _chat_router
from ._routes_coach import router as _coach_router
from ._routes_knowledge import router as _knowledge_router
from ._routes_products import router as _products_router
from ._ui_html import app_shell, thread_list_html
from ._ui_panels import coach_chat_html, knowledge_list_html

router = APIRouter(prefix="/ui")
router.include_router(_chat_router)
router.include_router(_coach_router)
router.include_router(_knowledge_router)
router.include_router(_products_router)
router.include_router(_admin_router)
router.include_router(_branches_router)

_THREAD_TMPL = (
    "SELECT ct.id, l.display_name, l.stage, ct.last_in_at,"
    " (SELECT m.text FROM message m WHERE m.thread_id = ct.id"
    "  ORDER BY m.occurred_at DESC, m.id DESC LIMIT 1) last_msg,"
    " (SELECT m.direction FROM message m WHERE m.thread_id = ct.id"
    "  ORDER BY m.occurred_at DESC, m.id DESC LIMIT 1) last_dir"
    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
    " {where}"
    " ORDER BY COALESCE(ct.last_in_at, ct.created_at) DESC LIMIT 100"
)

_FULL_PAGE_PATHS = {"/ui/inbox", "/ui/coach", "/ui/knowledge"}


# ─── full pages ───────────────────────────────────────────────────────────────

@router.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request) -> HTMLResponse:
    lang = apply_lang(request)
    empty = f'<div class="emp">{_h.escape(t("inbox.select"))}</div>'
    return HTMLResponse(app_shell(lang, empty, active_nav="inbox"))


@router.get("/knowledge", response_class=HTMLResponse)
async def knowledge_page(request: Request) -> HTMLResponse:
    lang = apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    where, params = _branch_where(branch_ids)
    async with session_scope() as session:
        q = (
            "SELECT id, slug, title, content"  # noqa: S608
            f" FROM knowledge_doc {where} ORDER BY id"
        )
        docs = (await session.execute(text(q), params)).all()
    thr = knowledge_list_html(list(docs))
    empty = f'<div class="emp">{_h.escape(t("know.select"))}</div>'
    return HTMLResponse(app_shell(lang, empty, active_nav="know", thr_html=thr))


@router.get("/coach", response_class=HTMLResponse)
async def coach_page(request: Request) -> HTMLResponse:
    lang = apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    branch_id = branch_ids[0] if branch_ids else 1
    async with session_scope() as session:
        edits, notes = await fetch_coach_data(session, branch_id)
    panel = coach_chat_html(branch_id, edits, notes)
    return HTMLResponse(app_shell(lang, panel, active_nav="coach"))


# ─── thread list ──────────────────────────────────────────────────────────────

@router.get("/threads", response_class=HTMLResponse)
async def threads_partial(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    where_clause = "WHERE l.branch_id = ANY(:bids)" if branch_ids else ""
    params = {"bids": branch_ids} if branch_ids else {}
    async with session_scope() as session:
        rows = (
            await session.execute(
                text(_THREAD_TMPL.format(where=where_clause)), params,
            )
        ).all()
    return HTMLResponse(thread_list_html(rows))


# ─── language switcher ────────────────────────────────────────────────────────

@router.get("/lang/{code}")
async def set_lang(code: str, request: Request) -> RedirectResponse:
    from urllib.parse import urlparse  # noqa: PLC0415

    lang = code if code in LANGS else "en"
    # HTMX pushes partial URLs (/ui/*/panel) to the address bar; redirect
    # to a known full-page path to avoid rendering raw HTML without CSS.
    raw_ref = request.headers.get("referer", "")
    path = urlparse(raw_ref).path if raw_ref else ""
    target = raw_ref if path in _FULL_PAGE_PATHS else "/ui/inbox"
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(
        LANG_COOKIE, lang, max_age=60 * 60 * 24 * 365,
        httponly=False, samesite="lax",
    )
    return resp
