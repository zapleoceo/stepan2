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
            "SELECT id, display_name, phone_e164, stage, created_at"  # noqa: S608
            f" FROM lead {where} ORDER BY created_at DESC LIMIT 200"
        )
        rows = (await session.execute(text(q), params)).all()
    return HTMLResponse(leads_panel_html(list(rows)))


@router.get("/outbox/panel", response_class=HTMLResponse)
async def outbox_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    where, params = _branch_where(branch_ids)
    async with session_scope() as session:
        q = (
            "SELECT id, thread_id, status, source, text, scheduled_at, sent_at"  # noqa: S608
            f" FROM outbox {where} ORDER BY id DESC LIMIT 100"
        )
        rows = (await session.execute(text(q), params)).all()
    return HTMLResponse(outbox_panel_html(list(rows)))


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


@router.get("/reports/panel", response_class=HTMLResponse)
async def reports_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    where, params = _branch_where(branch_ids)
    and_where = "AND l.branch_id = ANY(:bids)" if branch_ids else ""
    _sc = (  # noqa: S608
        "SELECT l.stage, COUNT(*) FROM lead l {where} GROUP BY l.stage"
    )
    _hi = (  # noqa: S608
        "SELECT EXTRACT(HOUR FROM m.occurred_at)::int, COUNT(*)"
        " FROM message m JOIN channel_thread ct ON ct.id=m.thread_id"
        " JOIN lead l ON l.id=ct.lead_id WHERE m.direction='in' {and_where} GROUP BY 1"
    )
    _ho = (  # noqa: S608
        "SELECT EXTRACT(HOUR FROM m.occurred_at)::int, COUNT(*)"
        " FROM message m JOIN channel_thread ct ON ct.id=m.thread_id"
        " JOIN lead l ON l.id=ct.lead_id WHERE m.direction='out' {and_where} GROUP BY 1"
    )
    async with session_scope() as session:
        sc = (await session.execute(text(_sc.format(where=where)), params)).all()
        hi = (await session.execute(text(_hi.format(and_where=and_where)), params)).all()
        ho = (await session.execute(text(_ho.format(and_where=and_where)), params)).all()
        ad_funnel = await fetch_ad_funnel(session, branch_ids)
        discovery = await fetch_discovery_metrics(session, branch_ids)
    stage_counts = {r[0]: int(r[1]) for r in sc}
    hour_in = {int(r[0]): int(r[1]) for r in hi}
    hour_out = {int(r[0]): int(r[1]) for r in ho}
    return HTMLResponse(
        reports_panel_html(stage_counts, hour_in, hour_out, ad_funnel, discovery))


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


@router.get("/agent-status", response_class=HTMLResponse)
async def agent_status(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    branch_id = branch_ids[0] if branch_ids else 1
    async with session_scope() as session:
        platform_on = await _read_flag(session, None, _PLATFORM_KEY)
        branch_on = await _read_flag(session, branch_id, _BRANCH_KEY)
    return HTMLResponse(_agent_toggles_html(branch_id, platform_on, branch_on))


@router.post("/agent-toggle", response_class=HTMLResponse)
async def agent_toggle(
    request: Request, scope: str = Form(default="branch"), branch_id: int = Form(default=1),
) -> HTMLResponse:
    apply_lang(request)
    allowed = branch_ids_from_request(request)
    if allowed and branch_id not in allowed:
        branch_id = allowed[0]
    async with session_scope() as session:
        if scope == "platform":
            new = not await _read_flag(session, None, _PLATFORM_KEY)
            await _write_flag(session, None, _PLATFORM_KEY, new)
        else:
            new = not await _read_flag(session, branch_id, _BRANCH_KEY)
            await _write_flag(session, branch_id, _BRANCH_KEY, new)
            invalidate(branch_id)
        platform_on = await _read_flag(session, None, _PLATFORM_KEY)
        branch_on = await _read_flag(session, branch_id, _BRANCH_KEY)
    return HTMLResponse(_agent_toggles_html(branch_id, platform_on, branch_on))


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


def _agent_toggles_html(branch_id: int, platform_on: bool, branch_on: bool) -> str:
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
