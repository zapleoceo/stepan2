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
from app.admin._branch import (
    branch_ids_from_request,
    is_super_admin,
    writable_selected_branch_id,
)
from app.domain.enums import ChannelKind

from ._i18n import LANG_COOKIE, LANGS, apply_lang, t
from ._query import (
    AD_FUNNEL_GROUPS,
    DEAL_WON,
    EVENT_BOOKED,
    IN_QUEUE_EXTRA,
    SETTLED_EXTRA,
    _branch_where,
    awaiting_base,
    awaiting_cutoff,
    fetch_blocked_count,
    fetch_bot_enabled_count,
    fetch_coach_data,
    fetch_stage_counts,
)
from ._routes_admin import (
    _agent_toggles_html,  # noqa: F401 (re-exported for tests)
)
from ._routes_admin import router as _admin_router
from ._routes_branches import router as _branches_router
from ._routes_channels import router as _channels_router
from ._routes_chat import router as _chat_router
from ._routes_coach import router as _coach_router
from ._routes_comments import router as _comments_router
from ._routes_knowledge import router as _knowledge_router
from ._routes_mcpadmin import router as _mcpadmin_router
from ._routes_members import router as _members_router
from ._routes_personas import router as _personas_router
from ._routes_products import router as _products_router
from ._ui_html import (
    app_shell,
    funnel_html,
    pick_branch_html,
    set_render_tz,
    thread_list_html,
    viewer_tz_offset,
)
from ._ui_kb import kb_all_html
from ._ui_panels import coach_chat_html


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
router.include_router(_comments_router)
router.include_router(_coach_router)
router.include_router(_knowledge_router)
router.include_router(_members_router)
router.include_router(_personas_router)
router.include_router(_products_router)
router.include_router(_admin_router)
router.include_router(_branches_router)
router.include_router(_mcpadmin_router)

_CHANNEL_KINDS = frozenset(k.value for k in ChannelKind)  # valid inbox connector-filter values

# Pick the top-100 threads FIRST (cheap: PK joins + an ORDER/LIMIT on channel_thread), THEN
# run the two per-thread message LATERALs on only those 100. Evaluating the LATERALs inline
# made Postgres compute the last-message lookup AND a full per-thread message COUNT for EVERY
# thread in the branch (3.5k+) before the LIMIT — the count aggregate alone read ~28k buffers
# for rows nobody sees. Limiting first cuts that ~30x (a branch with 3.5k threads: 87ms -> ~10ms),
# and this query is polled every 30s per open inbox, so it's constant DB load, not a one-off.
_THREAD_TMPL = (
    "SELECT t.id, t.display_name, t.stage, t.last_act,"
    " t.phone_e164, t.product_slug, t.ig_username, t.avatar_url,"
    " t.follower_count, t.following_count, t.agent_enabled,"
    " lm.text AS last_msg, lm.direction AS last_dir,"
    " mc.cnt_in, mc.cnt_out,"
    " t.branch_name, t.tz_offset_h, t.channel_kind, t.external_thread_id"
    " FROM ("
    "  SELECT ct.id, l.display_name, l.stage,"
    "   COALESCE(GREATEST(ct.last_in_at, ct.last_out_at), ct.created_at) AS last_act,"
    "   l.phone_e164, ct.product_slug, l.ig_username, l.avatar_url,"
    "   l.follower_count, l.following_count, l.agent_enabled,"
    "   b.name AS branch_name, b.tz_offset_h, ch.kind AS channel_kind,"
    "   ct.external_thread_id"
    "  FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
    "  JOIN branch b ON b.id = l.branch_id"
    "  JOIN channel ch ON ch.id = ct.channel_id"
    "  {where}"
    "  ORDER BY COALESCE(GREATEST(ct.last_in_at, ct.last_out_at), ct.created_at)"
    "  DESC NULLS LAST LIMIT 100"
    " ) t"
    " LEFT JOIN LATERAL ("
    "  SELECT m.text, m.direction FROM message m WHERE m.thread_id = t.id"
    "  ORDER BY m.occurred_at DESC, m.id DESC LIMIT 1) lm ON TRUE"
    " LEFT JOIN LATERAL ("
    "  SELECT COUNT(*) FILTER (WHERE m.direction = 'in') AS cnt_in,"
    "         COUNT(*) FILTER (WHERE m.direction = 'out') AS cnt_out"
    "  FROM message m WHERE m.thread_id = t.id) mc ON TRUE"
    " ORDER BY t.last_act DESC NULLS LAST"
)

# The connector filter is a VIEW preference, not a place. It used to live only in the query
# string, so every plain load of /ui/inbox — a bookmark, a new tab, F5 — silently switched
# every connector back on, and an operator who had hidden the managers' WhatsApp numbers
# found them back in the list each morning. The cookie is the memory; an explicit `kind` in
# the URL still wins, so a shared link keeps meaning what it said.
KIND_COOKIE = "s2_kind"
_KIND_MAX_AGE_S = 60 * 60 * 24 * 365


def _remembered_kind(request: Request, kind: str) -> str:
    return kind.strip() or (request.cookies.get(KIND_COOKIE) or "").strip()


def _remember_kind(resp: HTMLResponse, kind: str) -> HTMLResponse:
    """Persist the chip selection. 'all' is the default view and needs no memory."""
    if kind and kind != "all":
        resp.set_cookie(KIND_COOKIE, kind, max_age=_KIND_MAX_AGE_S,
                        httponly=False, samesite="lax")
    else:
        resp.delete_cookie(KIND_COOKIE)
    return resp


# ─── full pages ───────────────────────────────────────────────────────────────

@router.get("/inbox", response_class=HTMLResponse)
async def inbox(
    request: Request, stage: str = "", ad_id: str = "", grp: str = "", lead_type: str = "",
    audience: str = "", awaiting: str = "", kind: str = "", q: str = "", no_ad: str = "",
) -> HTMLResponse:
    lang = apply_lang(request)
    empty = f'<div class="emp">{_h.escape(t("inbox.select"))}</div>'
    kind = _remembered_kind(request, kind)
    resp = HTMLResponse(app_shell(lang, empty, active_nav="inbox", stage=stage.strip(),
                                  ad_id=ad_id.strip(), grp=grp.strip(), no_ad=no_ad.strip(),
                                  lead_type=lead_type.strip(), audience=audience.strip(),
                                  awaiting=awaiting.strip(), kind=kind, q=q.strip(),
                                  is_super=is_super_admin(request)))
    return _remember_kind(resp, kind)


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
    # Every doc on one page, no picker. The tree earned its place when a branch carried the
    # 14-doc canonical skeleton; the free-only cutover left four documents that all ride in
    # the same prompt, so an index in front of four items costs more than it saves.
    return HTMLResponse(app_shell(lang, kb_all_html(list(docs)), active_nav="know",
                                  is_super=is_super_admin(request)))


@router.get("/coach", response_class=HTMLResponse)
async def coach_page(request: Request) -> HTMLResponse:
    lang = apply_lang(request)
    # Same resolver as the coach partial and its write routes, so the page can't show one
    # branch's coaching history while the writes land on another (it showed branch 1's for
    # every super_admin with no filter, and coached branch 1 to match).
    branch_id = writable_selected_branch_id(request)
    if branch_id is None:
        return HTMLResponse(app_shell(lang, pick_branch_html(), active_nav="coach",
                                      is_super=is_super_admin(request)))
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


# A search term only reaches the phone column once it carries this many digits. Below it the
# term is almost always a name — "62" would otherwise match every Indonesian number in the
# inbox and bury what the person was actually looking for.
_MIN_PHONE_DIGITS = 4


def _phone_needle(term: str) -> str | None:
    """The digits to look for in a stored number, or None when the term isn't a number search.

    Handles the three ways the same Indonesian number gets typed: +6281211120213 as stored,
    081211120213 as it is written locally, and any fragment of either. The local form differs
    from the stored one only by 0 vs +62, so dropping a leading zero makes both land on the
    same digits — 81211120213, which sits inside 6281211120213."""
    digits = "".join(ch for ch in term if ch.isdigit())
    if len(digits) < _MIN_PHONE_DIGITS:
        return None
    return digits.lstrip("0") or digits


@router.get("/threads", response_class=HTMLResponse)
async def threads_partial(
    request: Request, stage: str = "", ad_id: str = "", grp: str = "", lead_type: str = "",
    audience: str = "", awaiting: str = "", kind: str = "", q: str = "", no_ad: str = "",
) -> HTMLResponse:
    apply_lang(request)
    kind = _remembered_kind(request, kind)
    branch_ids = branch_ids_from_request(request)
    conditions, params = [], {}
    if branch_ids:
        conditions.append("l.branch_id = ANY(:bids)")
        params["bids"] = branch_ids
    # Search runs HERE, not in the browser: the list is capped at 100 rows, so a client-side
    # filter could only ever match the chats already on screen and silently missed every
    # older one. Same fields the old data-search index used (name + IG handle), now across
    # the whole branch-scoped inbox.
    needle = q.strip()
    if needle:
        matches = ["LOWER(COALESCE(l.display_name, '')) LIKE :q",
                   "LOWER(COALESCE(l.ig_username, '')) LIKE :q"]
        params["q"] = f"%{needle.lower()}%"
        if (phone := _phone_needle(needle)) is not None:
            # Stored E.164 keeps its leading '+', so strip that on the column side and compare
            # digits to digits. REPLACE rather than a regex because the tests run on SQLite and
            # regexp_replace is Postgres-only — and a search nobody can test is a search that
            # quietly stops working.
            matches.append("REPLACE(COALESCE(l.phone_e164, ''), '+', '') LIKE :phone")
            params["phone"] = f"%{phone}%"
        conditions.append("(" + " OR ".join(matches) + ")")
    # Connector filter is a MULTI-TOGGLE facet: `kind` is a comma-list of the channels to SHOW
    # (empty/absent = all, the literal 'none' = every chip toggled off). Server-side (not a CSS
    # hide) so an older Meta chat isn't hidden behind newer IG ones past the per-query LIMIT.
    knd = kind.strip()
    if knd == "none":
        conditions.append("1=0")  # all connectors toggled off → show nothing
    elif knd:
        sel = [k for k in knd.split(",") if k in _CHANNEL_KINDS]
        if sel and len(sel) < len(_CHANNEL_KINDS):  # a real subset (all or garbage → no filter)
            ph = ", ".join(f":knd{i}" for i in range(len(sel)))
            conditions.append(f"ch.kind IN ({ph})")  # noqa: S608 — ph is bound params, not input
            params.update({f"knd{i}": k for i, k in enumerate(sel)})
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
    # A reports row can cover several Instagram ad ids that resolve to one Meta ad, so the
    # link carries them comma-joined — one id is just the single-element case.
    ads = [a for a in (p.strip() for p in ad_id.split(",")) if a]
    if no_ad.strip():  # the organic row — leads that came from no ad at all
        conditions.append("ct.ad_id IS NULL")
    elif ads:  # "open this ad's chats" from the reports ad-funnel table
        names = [f":ad{i}" for i in range(len(ads))]
        conditions.append(f"ct.ad_id IN ({', '.join(names)})")
        params.update({f"ad{i}": a for i, a in enumerate(ads)})
    if grp.strip() == "deal":  # the Сделка column — a CRM outcome, not a stage
        conditions.append(DEAL_WON)
    if grp.strip() == "event":  # the second number in that same cell — booked onto an event
        conditions.append(EVENT_BOOKED)
    grp_stages = AD_FUNNEL_GROUPS.get(grp.strip())
    if grp_stages:  # a funnel count column (В работе / Закрытые / Спящие) was clicked
        names = [f":g{i}" for i in range(len(grp_stages))]
        conditions.append(f"l.stage IN ({', '.join(names)})")
        params.update({f"g{i}": st for i, st in enumerate(grp_stages)})
    aw = awaiting.strip()
    # unanswered chats, split three ways: 'settled' = no reply is owed (handed off, or the CRM
    # gate already refused a send), 'queue' = Stepan will reply, 'off' = he won't, else = all.
    if aw:
        if aw == "settled":
            conditions.append(f"({awaiting_base()}) AND {SETTLED_EXTRA}")
            params["awaiting_cutoff"] = awaiting_cutoff()
        elif aw == "queue":
            conditions.append(
                f"({awaiting_base()}) AND NOT {SETTLED_EXTRA} AND ({IN_QUEUE_EXTRA})")
            params["awaiting_cutoff"] = awaiting_cutoff()
        elif aw == "off":
            conditions.append(
                f"({awaiting_base()}) AND NOT {SETTLED_EXTRA} AND NOT ({IN_QUEUE_EXTRA})")
            params["awaiting_cutoff"] = awaiting_cutoff()
        else:
            conditions.append(awaiting_base())
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
    if knd == "none":
        kind_qs = "none"
    else:
        _sel = [k for k in knd.split(",") if k in _CHANNEL_KINDS]
        kind_qs = ",".join(_sel) if (0 < len(_sel) < len(_CHANNEL_KINDS)) else ""
    filter_qs = urlencode({k: v for k, v in
                           (("stage", s), ("lead_type", lt), ("audience", aud),
                            ("ad_id", ",".join(ads)), ("no_ad", no_ad.strip()),
                            ("grp", grp.strip()), ("awaiting", aw),
                            ("kind", kind_qs), ("q", needle)) if v})
    # The chip click lands here, so this is where the new selection becomes the remembered
    # one — the full page is not reloaded and would never get the chance.
    return _remember_kind(
        HTMLResponse(thread_list_html(rows, active_tid, show_branch=show_branch,
                                      filter_qs=filter_qs)),
        kind)


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request) -> RedirectResponse:
    """The full report lives at /ui/reports/panel (wrapped by the partial-shell middleware
    on direct load); this full-page twin rendered a degraded subset and is kept only as a
    redirect for old bookmarks."""
    return RedirectResponse("/ui/reports/panel", status_code=307)


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
