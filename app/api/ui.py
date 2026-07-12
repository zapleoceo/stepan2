"""Manager UI — 3-column layout (sidebar + thread list + panel).

Full-page shells + a few HTMX partials; the rest of the partials live in the
_routes_*.py sub-modules included below (admin, auth, branches, channels, chat,
coach, knowledge, members, products).

Full pages registered here:
  GET  /ui/inbox /ui/knowledge /ui/coach /ui/reports — full shell
  GET  /ui/threads /ui/funnel                        — HTMX partials
  GET  /ui/lang/{c}                                  — language cookie + redirect
"""
from __future__ import annotations

import html as _h
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.admin._branch import branch_ids_from_request, is_super_admin
from app.domain.enums import ChannelKind

from ._i18n import LANG_COOKIE, LANGS, apply_lang, t
from ._query import (
    AD_FUNNEL_GROUPS,
    AWAITING_BASE,
    IN_QUEUE_EXTRA,
    _branch_where,
    awaiting_cutoff,
    fetch_audience_segment_stage_dist,
    fetch_blocked_count,
    fetch_bot_enabled_count,
    fetch_coach_data,
    fetch_report_data,
    fetch_segment_dist,
    fetch_stage_counts,
)
from ._routes_admin import (
    _ad_editor_data,
    _agent_toggles_html,  # noqa: F401 (re-exported for tests)
)
from ._routes_admin import router as _admin_router
from ._routes_branches import router as _branches_router
from ._routes_channels import router as _channels_router
from ._routes_chat import router as _chat_router
from ._routes_coach import router as _coach_router
from ._routes_knowledge import router as _knowledge_router
from ._routes_mcpadmin import router as _mcpadmin_router
from ._routes_members import router as _members_router
from ._routes_products import router as _products_router
from ._ui_html import (
    app_shell,
    funnel_html,
    set_render_tz,
    thread_list_html,
    viewer_tz_offset,
)
from ._ui_kb import kb_tree_html
from ._ui_panels import coach_chat_html, reports_panel_html


async def _apply_viewer_tz(request: Request) -> None:
    """Router-level dependency: pin this request's timestamp rendering to the VIEWER's own tz
    (from the `tzoff` cookie), so every /ui timestamp shows in the admin's zone, not the
    branch's. MUST be async: a sync dependency runs in a threadpool, so the contextvar it sets
    would land in the wrong thread and never reach the endpoint. Async → same request task →
    the contextvar propagates. The Reports 'activity by hour' histogram opts back into
    branch-local on its own (it never reads this contextvar)."""
    set_render_tz(viewer_tz_offset(request))


router = APIRouter(prefix="/ui", dependencies=[Depends(_apply_viewer_tz)])
router.include_router(_channels_router)
router.include_router(_chat_router)
router.include_router(_coach_router)
router.include_router(_knowledge_router)
router.include_router(_members_router)
router.include_router(_products_router)
router.include_router(_admin_router)
router.include_router(_branches_router)
router.include_router(_mcpadmin_router)

_CHANNEL_KINDS = frozenset(k.value for k in ChannelKind)  # valid inbox connector-filter values

_THREAD_TMPL = (
    "SELECT ct.id, l.display_name, l.stage,"
    " COALESCE(GREATEST(ct.last_in_at, ct.last_out_at), ct.created_at) AS last_act,"
    " l.phone_e164, ct.product_slug, l.ig_username, l.avatar_url,"
    " l.follower_count, l.following_count, l.agent_enabled,"
    " lm.text AS last_msg, lm.direction AS last_dir,"
    " mc.cnt_in, mc.cnt_out,"
    " b.name AS branch_name, b.tz_offset_h, ch.kind AS channel_kind"
    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
    " JOIN branch b ON b.id = l.branch_id"
    " JOIN channel ch ON ch.id = ct.channel_id"
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
async def inbox(
    request: Request, stage: str = "", ad_id: str = "", grp: str = "", lead_type: str = "",
    audience: str = "", awaiting: str = "", kind: str = "",
) -> HTMLResponse:
    lang = apply_lang(request)
    empty = f'<div class="emp">{_h.escape(t("inbox.select"))}</div>'
    return HTMLResponse(app_shell(lang, empty, active_nav="inbox", stage=stage.strip(),
                                  ad_id=ad_id.strip(), grp=grp.strip(),
                                  lead_type=lead_type.strip(), audience=audience.strip(),
                                  awaiting=awaiting.strip(), kind=kind.strip(),
                                  is_super=is_super_admin(request)))


@router.get("/knowledge", response_class=HTMLResponse)
async def knowledge_page(request: Request) -> HTMLResponse:
    lang = apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        # A linked branch has no KB of its own — show its source's docs (read-only edits
        # land on the shared source). Resolves the "empty / 403 KB tab" on such branches.
        if branch_ids:
            from app.modules.knowledge.source import effective_kb_branch  # noqa: PLC0415
            branch_ids = list({await effective_kb_branch(session, b) for b in branch_ids})
        where, params = _branch_where(branch_ids, col="k.branch_id")
        q = (
            "SELECT k.id, k.slug, k.title, k.content, k.category, k.sort_order,"  # noqa: S608
            " k.updated_by, b.name"
            " FROM knowledge_doc k JOIN branch b ON b.id = k.branch_id"
            f" {where} ORDER BY b.name, k.sort_order, k.id"
        )
        docs = (await session.execute(text(q), params)).all()
    thr = kb_tree_html(list(docs))
    empty = f'<div class="emp">{_h.escape(t("know.select"))}</div>'
    return HTMLResponse(app_shell(lang, empty, active_nav="know", thr_html=thr,
                                  is_super=is_super_admin(request)))


@router.get("/coach", response_class=HTMLResponse)
async def coach_page(request: Request) -> HTMLResponse:
    lang = apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    branch_id = branch_ids[0] if branch_ids else 1
    async with session_scope() as session:
        edits, notes = await fetch_coach_data(session, branch_id)
    panel = coach_chat_html(branch_id, edits, notes)
    return HTMLResponse(app_shell(lang, panel, active_nav="coach",
                                  is_super=is_super_admin(request)))


# ─── thread list ──────────────────────────────────────────────────────────────

@router.get("/funnel", response_class=HTMLResponse)
async def funnel_partial(request: Request, stage: str = "") -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        counts = await fetch_stage_counts(session, branch_ids)
        bot_on = await fetch_bot_enabled_count(session, branch_ids)
        blocked = await fetch_blocked_count(session, branch_ids)
    return HTMLResponse(
        funnel_html(counts, active_stage=stage.strip(), bot_on=bot_on, blocked=blocked))


@router.get("/threads", response_class=HTMLResponse)
async def threads_partial(
    request: Request, stage: str = "", ad_id: str = "", grp: str = "", lead_type: str = "",
    audience: str = "", awaiting: str = "", kind: str = "",
) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    conditions, params = [], {}
    if branch_ids:
        conditions.append("l.branch_id = ANY(:bids)")
        params["bids"] = branch_ids
    knd = kind.strip()
    if knd in _CHANNEL_KINDS:  # connector filter (server-side): the LIMIT is per-kind, so an
        conditions.append("ch.kind = :kind")  # older Meta chat isn't hidden behind newer IG ones
        params["kind"] = knd
    s = stage.strip()
    if s == "blocked":  # is_blocked is a flag, not a stage — the funnel's 🚫 chip filters on it
        conditions.append("l.is_blocked = true")
    elif s:
        conditions.append("l.stage = :stage")
        params["stage"] = s
    lt = lead_type.strip()
    if lt == "unclear":  # tree buckets an unset lead_type as 'unclear' — match both
        conditions.append("(l.lead_type = 'unclear' OR l.lead_type IS NULL)")
    elif lt:  # "open this segment's chats" from the reports segment tree
        conditions.append("l.lead_type = :lead_type")
        params["lead_type"] = lt
    aud = audience.strip()
    if aud == "unknown":  # tree buckets an unset audience as 'unknown' — match NULL too
        conditions.append("(l.audience = 'unknown' OR l.audience IS NULL)")
    elif aud:  # "open this audience's chats" — pairs with lead_type for a warm+student link
        conditions.append("l.audience = :audience")
        params["audience"] = aud
    ad = ad_id.strip()
    if ad:  # "open this ad's chats" from the reports ad-funnel table
        conditions.append("ct.ad_id = :ad_id")
        params["ad_id"] = ad
    grp_stages = AD_FUNNEL_GROUPS.get(grp.strip())
    if grp_stages:  # a funnel count column (В работе / Закрытые / Спящие) was clicked
        names = [f":g{i}" for i in range(len(grp_stages))]
        conditions.append(f"l.stage IN ({', '.join(names)})")
        params.update({f"g{i}": st for i, st in enumerate(grp_stages)})
    aw = awaiting.strip()
    if aw:  # unanswered chats; 'queue' = Stepan will reply, 'off' = won't, else = all unanswered
        if aw == "queue":
            conditions.append(f"({AWAITING_BASE}) AND ({IN_QUEUE_EXTRA})")
            params["awaiting_cutoff"] = awaiting_cutoff()
        elif aw == "off":
            conditions.append(f"({AWAITING_BASE}) AND NOT ({IN_QUEUE_EXTRA})")
            params["awaiting_cutoff"] = awaiting_cutoff()
        else:
            conditions.append(AWAITING_BASE)
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
    # Carry the active filter into each row's chat URL so opening a chat (and any later full
    # reload of it) keeps the filtered list rather than reverting to the whole inbox.
    filter_qs = urlencode({k: v for k, v in
                           (("stage", s), ("lead_type", lt), ("audience", aud),
                            ("ad_id", ad), ("grp", grp.strip()),
                            ("kind", knd if knd in _CHANNEL_KINDS else "")) if v})
    return HTMLResponse(thread_list_html(rows, active_tid, show_branch=show_branch,
                                         filter_qs=filter_qs))


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request) -> HTMLResponse:
    lang = apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        stage_counts, hour_in, hour_out, ad_funnel, discovery = (
            await fetch_report_data(session, branch_ids)
        )
        products, ad_mappings, ad_suggestions = await _ad_editor_data(session, branch_ids)
        segments = await fetch_segment_dist(session, branch_ids)
        seg_stage = await fetch_audience_segment_stage_dist(session, branch_ids)
    segment_stages: dict[str, dict[str, dict[str, int]]] = {}
    for a, seg, st, n in seg_stage:
        segment_stages.setdefault(str(a), {}).setdefault(str(seg), {})[str(st)] = int(n)
    panel = reports_panel_html(stage_counts, hour_in, hour_out, ad_funnel, discovery,
                               ad_mappings=ad_mappings, ad_suggestions=ad_suggestions,
                               products=products, segments=segments,
                               segment_stages=segment_stages)
    return HTMLResponse(app_shell(lang, panel, active_nav="reports",
                                  is_super=is_super_admin(request)))


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
