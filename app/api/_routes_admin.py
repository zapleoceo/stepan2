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


@router.get("/agent-status", response_class=HTMLResponse)
async def agent_status(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    branch_id = branch_ids[0] if branch_ids else 1
    async with session_scope() as session:
        row = (
            await session.execute(
                text(
                    "SELECT value FROM app_setting"
                    " WHERE branch_id=:bid AND key='agent_enabled_global'"
                ),
                {"bid": branch_id},
            )
        ).first()
    enabled = (row[0].lower() in ("true", "1", "yes")) if row else True
    return HTMLResponse(_agent_toggle_html(branch_id, enabled))


@router.post("/agent-toggle", response_class=HTMLResponse)
async def agent_toggle(
    request: Request, branch_id: int = Form(default=1),
) -> HTMLResponse:
    apply_lang(request)
    allowed = branch_ids_from_request(request)
    if allowed and branch_id not in allowed:
        branch_id = allowed[0]
    async with session_scope() as session:
        row = (
            await session.execute(
                text(
                    "SELECT value FROM app_setting"
                    " WHERE branch_id=:bid AND key='agent_enabled_global'"
                ),
                {"bid": branch_id},
            )
        ).first()
        enabled = (row[0].lower() in ("true", "1", "yes")) if row else True
        new_val = "false" if enabled else "true"
        await session.execute(
            text(
                "INSERT INTO app_setting (branch_id, key, value)"
                " VALUES (:bid, 'agent_enabled_global', :v)"
                " ON CONFLICT (branch_id, key) DO UPDATE SET value=:v"
            ),
            {"bid": branch_id, "v": new_val},
        )
    return HTMLResponse(_agent_toggle_html(branch_id, not enabled))


def _agent_toggle_html(branch_id: int, enabled: bool) -> str:
    lbl = _h.escape(t("bot.on" if enabled else "bot.off"))
    color = "#51cf66" if enabled else "#ff6b6b"
    bg = "rgba(31,58,31,.9)" if enabled else "rgba(58,31,31,.9)"
    return (
        f'<form id="bot-tog" hx-post="/ui/agent-toggle" hx-target="#bot-tog"'
        f' hx-swap="outerHTML" style="margin-top:.35rem">'
        f'<input type="hidden" name="branch_id" value="{branch_id}">'
        f'<button type="submit" style="width:100%;padding:.28rem .5rem;'
        f'background:{bg};border:1px solid {color};border-radius:5px;'
        f'color:{color};font-size:.72rem;font-weight:700;cursor:pointer;'
        f'text-align:center">{lbl}</button>'
        f'</form>'
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
