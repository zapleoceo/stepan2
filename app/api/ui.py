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
from ._query import (
    _branch_where,
    fetch_coach_data,
    fetch_report_data,
    fetch_stage_counts,
)
from ._routes_admin import _agent_toggles_html  # noqa: F401 (re-exported for tests)
from ._routes_admin import router as _admin_router
from ._routes_branches import router as _branches_router
from ._routes_channels import router as _channels_router
from ._routes_chat import router as _chat_router
from ._routes_coach import router as _coach_router
from ._routes_knowledge import router as _knowledge_router
from ._routes_products import router as _products_router
from ._ui_html import app_shell, funnel_html, thread_list_html
from ._ui_kb import kb_tree_html
from ._ui_panels import coach_chat_html, reports_panel_html

router = APIRouter(prefix="/ui")
router.include_router(_channels_router)
router.include_router(_chat_router)
router.include_router(_coach_router)
router.include_router(_knowledge_router)
router.include_router(_products_router)
router.include_router(_admin_router)
router.include_router(_branches_router)

_THREAD_TMPL = (
    "SELECT ct.id, l.display_name, l.stage,"
    " COALESCE(GREATEST(ct.last_in_at, ct.last_out_at), ct.created_at) AS last_act,"
    " l.phone_e164, ct.product_slug, l.ig_username, l.avatar_url,"
    " l.follower_count, l.following_count, l.agent_enabled,"
    " lm.text AS last_msg, lm.direction AS last_dir,"
    " mc.cnt_in, mc.cnt_out,"
    " b.name AS branch_name"
    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
    " JOIN branch b ON b.id = l.branch_id"
    " LEFT JOIN LATERAL ("
    "  SELECT m.text, m.direction FROM message m WHERE m.thread_id = ct.id"
    "  ORDER BY m.occurred_at DESC, m.id DESC LIMIT 1) lm ON TRUE"
    " LEFT JOIN LATERAL ("
    "  SELECT COUNT(*) FILTER (WHERE m.direction = 'in') AS cnt_in,"
    "         COUNT(*) FILTER (WHERE m.direction = 'out') AS cnt_out"
    "  FROM message m WHERE m.thread_id = ct.id) mc ON TRUE"
    " {where}"
    " ORDER BY COALESCE(GREATEST(ct.last_in_at, ct.last_out_at), ct.created_at)"
    " DESC NULLS LAST LIMIT 100"
)

# ─── full pages ───────────────────────────────────────────────────────────────

@router.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request, stage: str = "") -> HTMLResponse:
    lang = apply_lang(request)
    empty = f'<div class="emp">{_h.escape(t("inbox.select"))}</div>'
    return HTMLResponse(app_shell(lang, empty, active_nav="inbox", stage=stage.strip()))


@router.get("/knowledge", response_class=HTMLResponse)
async def knowledge_page(request: Request) -> HTMLResponse:
    lang = apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    where, params = _branch_where(branch_ids)
    async with session_scope() as session:
        q = (
            "SELECT id, slug, title, content, category, sort_order, updated_by"  # noqa: S608
            f" FROM knowledge_doc {where} ORDER BY sort_order, id"
        )
        docs = (await session.execute(text(q), params)).all()
    thr = kb_tree_html(list(docs))
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

@router.get("/funnel", response_class=HTMLResponse)
async def funnel_partial(request: Request, stage: str = "") -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        counts = await fetch_stage_counts(session, branch_ids)
    return HTMLResponse(funnel_html(counts, active_stage=stage.strip()))


@router.get("/threads", response_class=HTMLResponse)
async def threads_partial(request: Request, stage: str = "") -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    conditions, params = [], {}
    if branch_ids:
        conditions.append("l.branch_id = ANY(:bids)")
        params["bids"] = branch_ids
    if stage.strip():
        conditions.append("l.stage = :stage")
        params["stage"] = stage.strip()
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    async with session_scope() as session:
        rows = (
            await session.execute(
                text(_THREAD_TMPL.format(where=where_clause)), params,
            )
        ).all()
    raw_open = request.cookies.get("stepan2_open_thread", "")
    active_tid = int(raw_open) if raw_open.isdigit() else None
    # Show the branch on each card only when the view spans more than one branch,
    # so cross-branch inboxes stay visually distinct (single-branch view stays clean).
    show_branch = not branch_ids or len(branch_ids) > 1
    return HTMLResponse(thread_list_html(rows, active_tid, show_branch=show_branch))


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request) -> HTMLResponse:
    lang = apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        stage_counts, hour_in, hour_out, ad_funnel, discovery = (
            await fetch_report_data(session, branch_ids)
        )
    panel = reports_panel_html(stage_counts, hour_in, hour_out, ad_funnel, discovery)
    return HTMLResponse(app_shell(lang, panel, active_nav="reports"))


# ─── language switcher ────────────────────────────────────────────────────────

@router.get("/lang/{code}")
async def set_lang(code: str, request: Request) -> RedirectResponse:
    from urllib.parse import urlparse  # noqa: PLC0415

    lang = code if code in LANGS else "en"
    # Return to the exact view the manager was on — switching language must not eject
    # them from an open chat. Any /ui/** path is safe: _PartialShellMiddleware wraps
    # partial URLs (/ui/chat/123, /ui/settings/panel …) in the full shell on direct
    # load. Path-only (never the raw referer) so this can't become an open redirect.
    parsed = urlparse(request.headers.get("referer", ""))
    path = parsed.path or ""
    if path.startswith("/ui/") and not path.startswith("/ui/lang/"):
        target = path + (f"?{parsed.query}" if parsed.query else "")
    else:
        target = "/ui/inbox"
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(
        LANG_COOKIE, lang, max_age=60 * 60 * 24 * 365,
        httponly=False, samesite="lax",
    )
    return resp
