"""Admin routes: leads, outbox, members, settings, agent toggle, branches."""
from __future__ import annotations

import html as _h
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import bindparam, text

from app.adapters.db.session import session_scope
from app.admin._branch import (
    allowed_branch_ids,
    branch_ids_from_request,
    is_branch_write_forbidden,
    is_super_admin,
    writable_branch_ids,
)
from app.domain.clock import utc_now
from app.modules.ads import AdMappingService
from app.modules.knowledge.repository import ProductRepo
from app.modules.settings import schema as settings_schema
from app.modules.settings.service import invalidate

from ._i18n import apply_lang, t
from ._ig_preview import fetch_creative_bytes
from ._query import (
    _branch_where,
    fetch_ad_funnel,
    fetch_branch_tz,
    fetch_broker_log,
    fetch_discovery_metrics,
    fetch_segment_dist,
    fetch_stage_flow,
    fetch_stage_reach,
)
from ._routes_chat import _actor_name
from ._ui_panels import (
    admap_cell_inner,
    broker_log_panel_html,
    leads_panel_html,
    outbox_count_html,
    outbox_panel_html,
    reports_panel_html,
)
from ._ui_settings import field_html, settings_form_html

router = APIRouter()

_BRANCH_COOKIE = "stepan2_branch"


@router.get("/leads/panel", response_class=HTMLResponse)
async def leads_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    where, params = _branch_where(branch_ids)
    async with session_scope() as session:
        q = (
            "SELECT id, display_name, phone_e164, stage, created_at, branch_id"  # noqa: S608
            f" FROM lead {where} ORDER BY created_at DESC LIMIT 200"
        )
        rows = (await session.execute(text(q), params)).all()
        seen_ids = {r[5] for r in rows if r[5] is not None}
        tz_by_branch = await fetch_branch_tz(session, list(seen_ids))
    return HTMLResponse(leads_panel_html(list(rows), tz_by_branch))


@router.get("/outbox/count", response_class=HTMLResponse)
async def outbox_count(request: Request) -> HTMLResponse:
    """Polled every 15s by the sidebar nav badge — how many sends are queued right now."""
    branch_ids = branch_ids_from_request(request)
    where, params = _branch_where(branch_ids)
    status_clause = "AND status = 'pending'" if where else "WHERE status = 'pending'"
    async with session_scope() as session:
        n = (
            await session.execute(
                text(f"SELECT count(*) FROM outbox {where} {status_clause}"),  # noqa: S608
                params,
            )
        ).scalar() or 0
    return HTMLResponse(outbox_count_html(int(n)))


@router.get("/outbox/panel", response_class=HTMLResponse)
async def outbox_panel(request: Request) -> HTMLResponse:
    """Queued (pending) sends only — a sent/failed row belongs in the message history or
    the broker log, not in a queue monitor meant to show what's about to go out."""
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    where, params = _branch_where(branch_ids)
    status_clause = "AND status = 'pending'" if where else "WHERE status = 'pending'"
    async with session_scope() as session:
        q = (
            "SELECT id, thread_id, status, source, text, scheduled_at, sent_at, branch_id"  # noqa: S608
            f" FROM outbox {where} {status_clause} ORDER BY scheduled_at LIMIT 100"
        )
        rows = (await session.execute(text(q), params)).all()
        seen_ids = {r[7] for r in rows if r[7] is not None}
        tz_by_branch = await fetch_branch_tz(session, list(seen_ids))
        quiet_by_branch: dict[int, tuple[int, int]] = {}
        if seen_ids:  # quiet hours hold follow-up sends until they lift — needed for the ETA
            stmt = text(
                "SELECT branch_id, key, value FROM app_setting"
                " WHERE branch_id IN :bids AND key IN ('quiet_start','quiet_end')"
            ).bindparams(bindparam("bids", expanding=True))  # portable (SQLite + Postgres)
            qrows = (await session.execute(stmt, {"bids": list(seen_ids)})).all()
            tmp: dict[int, dict[str, int]] = {}
            for bid, key, val in qrows:
                try:
                    tmp.setdefault(bid, {})[key] = int(val)
                except (TypeError, ValueError):
                    continue
            quiet_by_branch = {
                bid: (d.get("quiet_start", 0), d.get("quiet_end", 0)) for bid, d in tmp.items()}
    return HTMLResponse(outbox_panel_html(list(rows), tz_by_branch, quiet_by_branch))


def _valid_date(value: str) -> str:
    """Accept only a YYYY-MM-DD string (empty otherwise) — safe to inline as a bound param."""
    import datetime as _dt  # noqa: PLC0415
    try:
        return _dt.date.fromisoformat(value).isoformat() if value else ""
    except ValueError:
        return ""


_QUICK_RANGES: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "4h": timedelta(hours=4),
    "8h": timedelta(hours=8),
    "12h": timedelta(hours=12),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "60d": timedelta(days=60),
    "90d": timedelta(days=90),
}


async def _ad_editor_data(
    session, branch_ids: list[int] | None,
) -> tuple[list[tuple[str, str]], dict[str, str], dict[str, str]]:
    """(products, ad→product mappings, history suggestions) for the ad-funnel editor.

    Editor is single-branch only (the map is per branch); returns empties otherwise so a
    cross-branch report renders the funnel read-only without a product column."""
    if not branch_ids or len(branch_ids) != 1:
        return [], {}, {}
    branch_id = branch_ids[0]
    products = [(p.slug, p.title) for p in await ProductRepo(session, branch_id).active()]
    svc = AdMappingService(session, branch_id)
    return products, await svc.all_mappings(), await svc.suggest_from_history()


@router.post("/ads/{ad_id}/product", response_class=HTMLResponse)
async def ad_product_map(
    ad_id: str, request: Request, product: str = Form(default=""),
) -> HTMLResponse:
    """Upsert (or clear) the ad→product mapping for the operator's single active branch;
    returns the re-rendered mapping cell inner HTML for the htmx swap."""
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    if not branch_ids or len(branch_ids) != 1:
        return HTMLResponse('<span class="emp">—</span>', status_code=400)
    branch_id = branch_ids[0]
    if is_branch_write_forbidden(branch_id, writable_branch_ids(request)):  # WRITE role required
        return HTMLResponse('<span class="emp">—</span>', status_code=403)
    slug = product.strip()
    async with session_scope() as session:
        products = [(p.slug, p.title) for p in await ProductRepo(session, branch_id).active()]
        valid = {s for s, _ in products}
        svc = AdMappingService(session, branch_id)
        if slug and slug in valid:
            await svc.upsert(ad_id, slug, actor=_actor_name(request))
        elif not slug:
            await svc.clear(ad_id)
        mapped = await svc.product_for_ad(ad_id)
        suggested = (await svc.suggest_from_history()).get(ad_id)
    return HTMLResponse(admap_cell_inner(ad_id, mapped, suggested, products))


@router.get("/ig-preview/{media_id}")
async def ig_preview(media_id: str) -> Response:
    """Proxy the IG ad-creative thumbnail same-origin for the reports hover preview.
    404 (→ the hover just hides) when the media id isn't numeric or has no public creative."""
    if not media_id.isdigit():
        return Response(status_code=404)
    got = await fetch_creative_bytes(media_id)
    if got is None:
        return Response(status_code=404)
    content, ctype = got
    return Response(content=content, media_type=ctype,
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/reports/panel", response_class=HTMLResponse)
async def reports_panel(
    request: Request, date_from: str = "", date_to: str = "",
    range_: str = Query("", alias="range"),
) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    # A quick-range button wins over manually-picked dates — it always sends its own
    # request with no date_from/date_to, but guard anyway so stale query params can't
    # combine two conflicting filters.
    active_range = range_ if range_ in _QUICK_RANGES else ""
    df, dt_ = ("", "") if active_range else (_valid_date(date_from), _valid_date(date_to))
    # date range filters by the lead's conversation-start (lead.created_at); dt_ is inclusive.
    date_and = ""
    params: dict = {}
    if branch_ids:
        date_and += " AND l.branch_id = ANY(:bids)"
        params["bids"] = branch_ids
    if active_range:
        date_and += " AND l.created_at >= :since"
        params["since"] = utc_now() - _QUICK_RANGES[active_range]
    elif df:
        # Both halves of the fix are needed: bind a real date object (asyncpg 500s on a
        # bare ISO string — it needs a typed value to compare against a timestamp column),
        # AND keep the explicit CAST(:df AS date) (without it, Postgres's untyped-parameter
        # inference in "$n + INTERVAL '1 day'" resolves $n as interval, not date, and
        # "timestamp < interval" then fails to parse). Cast with CAST(name AS type), never
        # a double-colon cast right after the bind name — that collides with SQLAlchemy's
        # own bind-parameter syntax.
        date_and += " AND l.created_at >= CAST(:df AS date)"
        params["df"] = date.fromisoformat(df)
    if dt_:
        date_and += " AND l.created_at < (CAST(:dt AS date) + INTERVAL '1 day')"
        params["dt"] = date.fromisoformat(dt_)
    # Same window as date_and above, as plain datetimes — the ad-funnel table and the
    # discovery KPI build their own SQL and can't reuse date_and's CAST(:df AS date)
    # placeholders directly, so the selected date range is threaded through separately.
    since_dt: datetime | None = params.get("since")
    if not active_range and df:
        since_dt = datetime.combine(date.fromisoformat(df), time.min)
    until_dt = (
        datetime.combine(date.fromisoformat(dt_), time.min) + timedelta(days=1) if dt_ else None
    )
    lead_where = ("WHERE" + date_and[4:]) if date_and else ""
    # date_and / lead_where are built ONLY from fixed fragments; all values are bound params.
    _sc = f"SELECT l.stage, COUNT(*) FROM lead l {lead_where} GROUP BY l.stage"  # noqa: S608
    _hour = (
        # Branch-local hour bucket — see _query._HOUR_Q for why the shift is needed.
        "SELECT EXTRACT(HOUR FROM m.occurred_at + make_interval(hours => b.tz_offset_h))::int,"
        " COUNT(*)"
        " FROM message m JOIN channel_thread ct ON ct.id=m.thread_id"
        " JOIN lead l ON l.id=ct.lead_id JOIN branch b ON b.id=l.branch_id"
        " WHERE m.direction=:dir{da} GROUP BY 1"
    )
    _hi = _hour.format(da=date_and)  # noqa: S608
    _ho = _hi
    fb_bid = fb_acct = ""
    async with session_scope() as session:
        sc = (await session.execute(text(_sc), params)).all()
        hi = (await session.execute(text(_hi), {**params, "dir": "in"})).all()
        ho = (await session.execute(text(_ho), {**params, "dir": "out"})).all()
        ad_funnel = await fetch_ad_funnel(session, branch_ids, since=since_dt, until=until_dt)
        discovery = await fetch_discovery_metrics(
            session, branch_ids, since=since_dt, until=until_dt)
        if branch_ids and len(branch_ids) == 1:
            fb = (await session.execute(
                text("SELECT key, value FROM app_setting WHERE branch_id=:b"
                     " AND key IN ('fb_business_id','fb_account_id')"),
                {"b": branch_ids[0]})).all()
            fbm = {k: v for k, v in fb}
            fb_bid, fb_acct = fbm.get("fb_business_id", ""), fbm.get("fb_account_id", "")
        products, ad_mappings, ad_suggestions = await _ad_editor_data(session, branch_ids)
        segments = await fetch_segment_dist(session, branch_ids, since=since_dt, until=until_dt)
        stage_flow = await fetch_stage_flow(session, branch_ids, since=since_dt, until=until_dt)
        stage_reach = await fetch_stage_reach(session, branch_ids, since=since_dt, until=until_dt)
    stage_counts = {r[0]: int(r[1]) for r in sc}
    hour_in = {int(r[0]): int(r[1]) for r in hi}
    hour_out = {int(r[0]): int(r[1]) for r in ho}
    return HTMLResponse(
        reports_panel_html(stage_counts, hour_in, hour_out, ad_funnel, discovery,
                           fb_business_id=fb_bid, fb_account_id=fb_acct,
                           date_from=df, date_to=dt_, active_range=active_range,
                           ad_mappings=ad_mappings, ad_suggestions=ad_suggestions,
                           products=products, segments=segments, stage_flow=stage_flow,
                           stage_reach=stage_reach,
                           total_leads=sum(int(s[1]) for s in segments)))


@router.get("/settings/panel", response_class=HTMLResponse)
async def settings_panel(request: Request) -> HTMLResponse:
    lang = apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    where, params = _branch_where(branch_ids)
    async with session_scope() as session:
        q = f"SELECT key, value FROM app_setting {where} ORDER BY key"  # noqa: S608
        rows = (await session.execute(text(q), params)).all()
    return HTMLResponse(settings_form_html({k: v for k, v in rows}, lang))




_LOG_PAGE_SIZE = 50


@router.get("/settings/log", response_class=HTMLResponse)
async def broker_log_page(request: Request, page: int = 0) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    page = max(0, page)
    async with session_scope() as session:
        rows, total = await fetch_broker_log(session, branch_ids, page, _LOG_PAGE_SIZE)
        seen_ids = {r.branch_id for r in rows if r.branch_id is not None}
        tz_by_branch = await fetch_branch_tz(session, list(seen_ids))
    return HTMLResponse(broker_log_panel_html(rows, page, _LOG_PAGE_SIZE, total, tz_by_branch))


@router.post("/settings/save", response_class=HTMLResponse)
async def settings_save_by_key(
    request: Request, key: str = Form(...), value: str = Form(default=""),
) -> HTMLResponse:
    """Upsert one setting by key for the active branch and re-render that field.

    A blank secret is treated as "keep current" (the value is never echoed back)."""
    lang = apply_lang(request)
    field = settings_schema.field_for(key)
    if field is None:
        return HTMLResponse("", status_code=400)
    # Settings write → scope by WRITE right (viewer can't); middleware blocks a pure viewer.
    writable = writable_branch_ids(request)
    bid = writable[0] if writable else 1
    val = value.strip()
    async with session_scope() as session:
        if field.kind == "secret" and not val:
            cur = (
                await session.execute(
                    text("SELECT value FROM app_setting WHERE branch_id=:b AND key=:k"),
                    {"b": bid, "k": key},
                )
            ).first()
            return HTMLResponse(field_html(field, cur[0] if cur else "", lang))
        await session.execute(
            text(
                "INSERT INTO app_setting (branch_id, key, value) VALUES (:b, :k, :v)"
                " ON CONFLICT (branch_id, key) DO UPDATE SET value=:v"
            ),
            {"b": bid, "k": key, "v": val},
        )
    invalidate(bid)
    return HTMLResponse(field_html(field, val, lang, saved=True))


_PLATFORM_KEY = "agent_enabled_platform"  # branch_id IS NULL row = whole-platform switch
_BRANCH_KEY = "agent_enabled_global"      # per-branch switch (legacy key name)


def _truthy(value: str | None) -> bool:
    return (value or "true").strip().lower() in ("true", "1", "yes")


async def _read_flag(session, branch_id: int | None, key: str) -> bool:
    clause = "branch_id = :bid" if branch_id is not None else "branch_id IS NULL"
    row = (await session.execute(
        text(f"SELECT value FROM app_setting WHERE {clause} AND key=:k"),  # noqa: S608
        {"bid": branch_id, "k": key})).first()
    return _truthy(row[0]) if row else True


async def _write_flag(session, branch_id: int | None, key: str, on: bool) -> None:
    if branch_id is not None:
        await session.execute(
            text("INSERT INTO app_setting (branch_id, key, value) VALUES (:bid, :k, :v)"
                 " ON CONFLICT (branch_id, key) DO UPDATE SET value=:v"),
            {"bid": branch_id, "k": key, "v": "true" if on else "false"})
    else:
        # branch_id NULL can't use the (branch_id,key) unique-constraint upsert cleanly
        upd = await session.execute(
            text("UPDATE app_setting SET value=:v WHERE branch_id IS NULL AND key=:k"),
            {"k": key, "v": "true" if on else "false"})
        if not upd.rowcount:
            await session.execute(
                text("INSERT INTO app_setting (branch_id, key, value) VALUES (NULL, :k, :v)"),
                {"k": key, "v": "true" if on else "false"})


async def _single_selected_branch(request: Request) -> int | None:
    """The ONE branch the sidebar filter currently narrows to, or None when the view
    spans multiple/all branches — in which case there's no single branch left for the
    per-branch toggle to mean anything, so callers must not silently guess one."""
    branch_ids = branch_ids_from_request(request)
    return branch_ids[0] if branch_ids and len(branch_ids) == 1 else None


@router.get("/agent-status", response_class=HTMLResponse)
async def agent_status(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_id = await _single_selected_branch(request)
    is_super = is_super_admin(request)
    async with session_scope() as session:
        platform_on = await _read_flag(session, None, _PLATFORM_KEY)
        branch_on = await _read_flag(session, branch_id, _BRANCH_KEY) if branch_id else None
    return HTMLResponse(_agent_toggles_html(branch_id, platform_on, branch_on, is_super))


@router.post("/agent-toggle", response_class=HTMLResponse)
async def agent_toggle(
    request: Request, scope: str = Form(default="branch"), branch_id: int = Form(default=1),
) -> HTMLResponse:
    apply_lang(request)
    is_super = is_super_admin(request)
    allowed = branch_ids_from_request(request)
    if allowed and branch_id not in allowed:
        branch_id = allowed[0]
    selected = await _single_selected_branch(request)
    async with session_scope() as session:
        if scope == "platform":
            # The platform switch is only rendered for super admins (_agent_toggles_html),
            # but a non-super-admin could still POST scope=platform directly — the kill
            # switch for the ENTIRE platform must not be reachable by a branch-scoped role.
            if is_super:
                new = not await _read_flag(session, None, _PLATFORM_KEY)
                await _write_flag(session, None, _PLATFORM_KEY, new)
        elif selected is not None and not is_branch_write_forbidden(
            selected, writable_branch_ids(request)
        ):
            # The branch-scope button only renders when a single branch is selected (see
            # _agent_toggles_html) — a POST for scope=branch with no branch actually
            # selected (e.g. a stale form from an "all branches" view) is a no-op, not a
            # silent branch-1 guess. A branch_viewer of the selected branch can't flip it.
            new = not await _read_flag(session, selected, _BRANCH_KEY)
            await _write_flag(session, selected, _BRANCH_KEY, new)
            invalidate(selected)
        platform_on = await _read_flag(session, None, _PLATFORM_KEY)
        branch_on = await _read_flag(session, selected, _BRANCH_KEY) if selected else None
    return HTMLResponse(_agent_toggles_html(selected, platform_on, branch_on, is_super))


def _switch(scope: str, branch_id: int, label: str, on: bool) -> str:
    knob = "translateX(1.05rem)" if on else "translateX(0)"
    track = "#51cf66" if on else "#4a5568"
    status = _h.escape(t("bot.on" if on else "bot.off"))
    st_color = "#7ee2a8" if on else "#ff9b9b"
    return (
        f'<form hx-post="/ui/agent-toggle" hx-target="#bot-tog-wrap" hx-swap="innerHTML"'
        f' class="tgl-row"><input type="hidden" name="scope" value="{scope}">'
        f'<input type="hidden" name="branch_id" value="{branch_id}">'
        f'<button type="submit" class="tgl-btn" title="{_h.escape(label)}">'
        f'<span class="tgl-lbl">{_h.escape(label)}</span>'
        f'<span class="tgl-status" style="color:{st_color}">{status}</span>'
        f'<span class="tgl-track" style="background:{track}">'
        f'<span class="tgl-knob" style="transform:{knob}"></span></span>'
        f'</button></form>'
    )


def _agent_toggles_html(
    branch_id: int | None, platform_on: bool, branch_on: bool | None, is_super: bool = True,
) -> str:
    platform_switch = _switch("platform", branch_id or 0, t("bot.platform"), platform_on)
    if branch_id is None or branch_on is None:
        hint = f'<div class="tgl-hint">{_h.escape(t("bot.pick_branch"))}</div>'
        return (platform_switch if is_super else "") + hint
    return (
        (platform_switch if is_super else "")
        + _switch("branch", branch_id, t("bot.branch"), branch_on)
    )


@router.get("/branches/widget", response_class=HTMLResponse)
async def branches_widget(request: Request) -> HTMLResponse:
    apply_lang(request)
    current = request.cookies.get(_BRANCH_COOKIE, "")
    allowed = allowed_branch_ids(request)
    # A branch-scoped user must not see other branches' names in this dropdown, even
    # though the filter cookie itself can't grant access beyond `allowed` (_branch.py
    # intersects it server-side) — this is purely about not leaking names.
    where, params = ("WHERE is_active", {})
    if allowed is not None:
        where, params = "WHERE is_active AND id = ANY(:bids)", {"bids": allowed}
    async with session_scope() as session:
        rows = (
            await session.execute(
                text(f"SELECT id, name FROM branch {where} ORDER BY id"),  # noqa: S608
                params,
            )
        ).all()
    all_lbl = _h.escape(t("branch.all"))
    sel_all = "selected" if not current else ""
    opts = f'<option value="" {sel_all}>{all_lbl}</option>'
    for bid, bname in rows:
        sel = "selected" if str(bid) in current.split(",") else ""
        opts += f'<option value="{bid}" {sel}>{_h.escape(bname)}</option>'
    return HTMLResponse(
        f'<form method="post" action="/ui/branch-filter">'
        f'<select name="bid" class="bft-sel" onchange="this.form.submit()">'
        f'{opts}</select></form>'
    )


@router.post("/branch-filter")
async def branch_filter(
    request: Request, bid: str = Form(default=""),
) -> RedirectResponse:
    referer = request.headers.get("referer", "/ui/inbox")
    resp = RedirectResponse(referer, status_code=303)
    if bid.strip():
        resp.set_cookie(
            _BRANCH_COOKIE, bid.strip(), path="/", max_age=86400 * 30,
            httponly=False, samesite="lax", secure=True,
        )
    else:
        resp.delete_cookie(_BRANCH_COOKIE, path="/")
    return resp
