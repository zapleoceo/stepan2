"""Admin routes: leads, outbox, members, settings, agent toggle, branches."""
from __future__ import annotations

import html as _h

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.admin._branch import branch_ids_from_request
from app.modules.settings import schema as settings_schema
from app.modules.settings.service import invalidate

from ._i18n import apply_lang, t
from ._query import (
    _branch_where,
    fetch_ad_funnel,
    fetch_branch_tz,
    fetch_broker_log,
    fetch_discovery_metrics,
)
from ._ui_panels import (
    broker_log_panel_html,
    leads_panel_html,
    members_panel_html,
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
    return HTMLResponse(outbox_panel_html(list(rows), tz_by_branch))


@router.get("/members/panel", response_class=HTMLResponse)
async def members_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    where, params = _branch_where(branch_ids, col="m.branch_id")
    async with session_scope() as session:
        q = (
            "SELECT u.id, u.telegram_id, m.role, u.name, m.branch_id"  # noqa: S608
            " FROM membership m JOIN app_user u ON u.id = m.user_id"
            f" {where} ORDER BY m.branch_id, m.role, u.name"
        )
        rows = (await session.execute(text(q), params)).all()
    return HTMLResponse(members_panel_html(list(rows)))


def _valid_date(value: str) -> str:
    """Accept only a YYYY-MM-DD string (empty otherwise) — safe to inline as a bound param."""
    import datetime as _dt  # noqa: PLC0415
    try:
        return _dt.date.fromisoformat(value).isoformat() if value else ""
    except ValueError:
        return ""


@router.get("/reports/panel", response_class=HTMLResponse)
async def reports_panel(
    request: Request, date_from: str = "", date_to: str = "",
) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    df, dt_ = _valid_date(date_from), _valid_date(date_to)
    # date range filters by the lead's conversation-start (lead.created_at); dt_ is inclusive.
    date_and = ""
    params: dict = {}
    if branch_ids:
        date_and += " AND l.branch_id = ANY(:bids)"
        params["bids"] = branch_ids
    if df:
        # CAST(... AS date), not ::date — asyncpg needs a typed bind, and SQLAlchemy's
        # text() colon-parameter syntax collides with Postgres's "::" cast operator.
        date_and += " AND l.created_at >= CAST(:df AS date)"
        params["df"] = df
    if dt_:
        date_and += " AND l.created_at < (CAST(:dt AS date) + INTERVAL '1 day')"
        params["dt"] = dt_
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
        ad_funnel = await fetch_ad_funnel(session, branch_ids)
        discovery = await fetch_discovery_metrics(session, branch_ids)
        if branch_ids and len(branch_ids) == 1:
            fb = (await session.execute(
                text("SELECT key, value FROM app_setting WHERE branch_id=:b"
                     " AND key IN ('fb_business_id','fb_account_id')"),
                {"b": branch_ids[0]})).all()
            fbm = {k: v for k, v in fb}
            fb_bid, fb_acct = fbm.get("fb_business_id", ""), fbm.get("fb_account_id", "")
    stage_counts = {r[0]: int(r[1]) for r in sc}
    hour_in = {int(r[0]): int(r[1]) for r in hi}
    hour_out = {int(r[0]): int(r[1]) for r in ho}
    return HTMLResponse(
        reports_panel_html(stage_counts, hour_in, hour_out, ad_funnel, discovery,
                           fb_business_id=fb_bid, fb_account_id=fb_acct,
                           date_from=df, date_to=dt_))


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
    branch_ids = branch_ids_from_request(request)
    bid = branch_ids[0] if branch_ids else 1
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
    async with session_scope() as session:
        platform_on = await _read_flag(session, None, _PLATFORM_KEY)
        branch_on = await _read_flag(session, branch_id, _BRANCH_KEY) if branch_id else None
    return HTMLResponse(_agent_toggles_html(branch_id, platform_on, branch_on))


@router.post("/agent-toggle", response_class=HTMLResponse)
async def agent_toggle(
    request: Request, scope: str = Form(default="branch"), branch_id: int = Form(default=1),
) -> HTMLResponse:
    apply_lang(request)
    allowed = branch_ids_from_request(request)
    if allowed and branch_id not in allowed:
        branch_id = allowed[0]
    selected = await _single_selected_branch(request)
    async with session_scope() as session:
        if scope == "platform":
            new = not await _read_flag(session, None, _PLATFORM_KEY)
            await _write_flag(session, None, _PLATFORM_KEY, new)
        elif selected is not None:
            # The branch-scope button only renders when a single branch is selected (see
            # _agent_toggles_html) — a POST for scope=branch with no branch actually
            # selected (e.g. a stale form from an "all branches" view) is a no-op, not a
            # silent branch-1 guess.
            new = not await _read_flag(session, selected, _BRANCH_KEY)
            await _write_flag(session, selected, _BRANCH_KEY, new)
            invalidate(selected)
        platform_on = await _read_flag(session, None, _PLATFORM_KEY)
        branch_on = await _read_flag(session, selected, _BRANCH_KEY) if selected else None
    return HTMLResponse(_agent_toggles_html(selected, platform_on, branch_on))


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
    branch_id: int | None, platform_on: bool, branch_on: bool | None,
) -> str:
    if branch_id is None or branch_on is None:
        hint = f'<div class="tgl-hint">{_h.escape(t("bot.pick_branch"))}</div>'
        return _switch("platform", 0, t("bot.platform"), platform_on) + hint
    return (
        _switch("platform", branch_id, t("bot.platform"), platform_on)
        + _switch("branch", branch_id, t("bot.branch"), branch_on)
    )


@router.get("/branches/widget", response_class=HTMLResponse)
async def branches_widget(request: Request) -> HTMLResponse:
    apply_lang(request)
    current = request.cookies.get(_BRANCH_COOKIE, "")
    async with session_scope() as session:
        rows = (
            await session.execute(
                text("SELECT id, name FROM branch WHERE is_active ORDER BY id")
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
            httponly=False, samesite="lax",
        )
    else:
        resp.delete_cookie(_BRANCH_COOKIE, path="/")
    return resp
