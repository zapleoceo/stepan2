"""Channel management routes — CRUD and per-kind credential flows."""
from __future__ import annotations

import json
import secrets
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.adapters.crypto import encrypt
from app.adapters.db.session import session_scope
from app.admin._branch import allowed_branch_ids
from app.domain.enums import ChannelKind

from ._i18n import apply_lang
from ._ui_panels import (
    _ch_ig_form,
    _ch_meta_form,
    _ch_wa_form,
    channel_credential_html,
    channel_edit_form_html,
    channel_list_partial_html,
    channel_new_form_html,
)

router = APIRouter()

_CH_Q = (  # noqa: S608
    "SELECT id, kind, handle, account_id, is_active FROM channel"
    " WHERE branch_id=:bid ORDER BY id"
)
_CS_Q = (  # noqa: S608
    "SELECT channel_id, status FROM channel_session"
    " WHERE channel_id=ANY(:ids) AND status='active' ORDER BY channel_id"
)

# Pending IG login flows: {flow_id: {"client": Any, "channel_id": int}}
_ig_flows: dict[str, dict[str, Any]] = {}


async def _ch_list_html(session: Any, branch_id: int) -> str:
    channels = (await session.execute(text(_CH_Q), {"bid": branch_id})).all()
    ids = [r[0] for r in channels] or [0]
    sessions = (await session.execute(text(_CS_Q), {"ids": ids})).all()
    return channel_list_partial_html(list(channels), list(sessions), branch_id)


def _branch_forbidden(branch_id: int, allowed: list[int] | None) -> bool:
    return allowed is not None and branch_id not in allowed


async def _channel_branch(session: Any, ch_id: int, allowed: list[int] | None) -> int | None:
    """Channel's branch_id, or None if missing / outside the caller's allowed branches —
    the tenant-ownership guard blocking cross-branch credential/edit IDOR."""
    row = (
        await session.execute(
            text("SELECT branch_id FROM channel WHERE id=:id"), {"id": ch_id}
        )
    ).first()
    if row is None or _branch_forbidden(row[0], allowed):
        return None
    return row[0]


_FORBIDDEN = '<div class="emp">Forbidden</div>'


# ─── list + new form ──────────────────────────────────────────────────────────

@router.get("/channels/branch/{branch_id}", response_class=HTMLResponse)
async def channels_list(branch_id: int, request: Request) -> HTMLResponse:
    """HTMX partial: channel table for #ch-list in branch edit page."""
    apply_lang(request)
    if _branch_forbidden(branch_id, allowed_branch_ids(request)):
        return HTMLResponse(_FORBIDDEN, status_code=403)
    async with session_scope() as session:
        return HTMLResponse(await _ch_list_html(session, branch_id))


@router.get("/channels/branch/{branch_id}/new", response_class=HTMLResponse)
async def channel_new(branch_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    if _branch_forbidden(branch_id, allowed_branch_ids(request)):
        return HTMLResponse(_FORBIDDEN, status_code=403)
    return HTMLResponse(channel_new_form_html(branch_id))


@router.post("/channels/branch/{branch_id}/create", response_class=HTMLResponse)
async def channel_create(
    branch_id: int,
    request: Request,
    kind: str = Form(default="instagram"),
    handle: str = Form(default=""),
    account_id: str = Form(default=""),
    is_active: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    branch_ids = allowed_branch_ids(request)
    if branch_ids and branch_id not in branch_ids:
        return HTMLResponse('<div class="emp">Forbidden</div>', status_code=403)
    kind_val = kind if kind in (k.value for k in ChannelKind) else ChannelKind.INSTAGRAM.value
    async with session_scope() as session:
        await session.execute(
            text(
                "INSERT INTO channel (branch_id, kind, handle, account_id, is_active)"
                " VALUES (:bid, :kind, :handle, :acct, :active)"
            ),
            {
                "bid": branch_id, "kind": kind_val,
                "handle": handle.strip() or None,
                "acct": account_id.strip() or None,
                "active": bool(is_active),
            },
        )
        html = await _ch_list_html(session, branch_id)
    resp = HTMLResponse(html)
    resp.headers["HX-Trigger"] = "refreshChannelList"
    return resp


# ─── edit + save ──────────────────────────────────────────────────────────────

@router.get("/channels/{ch_id}/edit", response_class=HTMLResponse)
async def channel_edit(ch_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        if await _channel_branch(session, ch_id, allowed) is None:
            return HTMLResponse(_FORBIDDEN, status_code=403)
        row = (await session.execute(
            text("SELECT id, kind, handle, account_id, is_active FROM channel WHERE id=:id"),
            {"id": ch_id},
        )).first()
    if not row:
        return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
    return HTMLResponse(
        channel_edit_form_html(row[0], row[1], row[2] or "", row[3] or "", bool(row[4]))
    )


@router.post("/channels/{ch_id}/save", response_class=HTMLResponse)
async def channel_save(
    ch_id: int,
    request: Request,
    handle: str = Form(default=""),
    account_id: str = Form(default=""),
    is_active: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        if await _channel_branch(session, ch_id, allowed) is None:
            return HTMLResponse(_FORBIDDEN, status_code=403)
        await session.execute(
            text(
                "UPDATE channel SET handle=:h, account_id=:a, is_active=:active WHERE id=:id"
            ),
            {
                "h": handle.strip() or None,
                "a": account_id.strip() or None,
                "active": bool(is_active),
                "id": ch_id,
            },
        )
        row = (await session.execute(
            text("SELECT id, kind, handle, account_id, is_active FROM channel WHERE id=:id"),
            {"id": ch_id},
        )).first()
    if not row:
        return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
    return HTMLResponse(
        channel_edit_form_html(row[0], row[1], row[2] or "", row[3] or "", bool(row[4]))
    )


# ─── delete ───────────────────────────────────────────────────────────────────

@router.post("/channels/{ch_id}/delete", response_class=HTMLResponse)
async def channel_delete(ch_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        br_row = (await session.execute(
            text("SELECT branch_id FROM channel WHERE id=:id"), {"id": ch_id}
        )).first()
        if not br_row:
            return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
        branch_id = br_row[0]
        branch_ids = allowed_branch_ids(request)
        if branch_ids and branch_id not in branch_ids:
            return HTMLResponse('<div class="emp">Forbidden</div>', status_code=403)
        await session.execute(
            text("DELETE FROM channel_session WHERE channel_id=:id"), {"id": ch_id}
        )
        await session.execute(
            text("DELETE FROM channel WHERE id=:id"), {"id": ch_id}
        )
        html = await _ch_list_html(session, branch_id)
    resp = HTMLResponse(html)
    resp.headers["HX-Trigger"] = "refreshChannelList"
    return resp


# ─── credential panel ─────────────────────────────────────────────────────────

@router.get("/channels/{ch_id}/credential", response_class=HTMLResponse)
async def channel_credential(ch_id: int, request: Request) -> HTMLResponse:
    """Show the kind-specific credential form in #ch-form."""
    apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        if await _channel_branch(session, ch_id, allowed) is None:
            return HTMLResponse(_FORBIDDEN, status_code=403)
        row = (await session.execute(
            text("SELECT id, kind, handle, account_id, is_active FROM channel WHERE id=:id"),
            {"id": ch_id},
        )).first()
        if not row:
            return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
        st_row = (await session.execute(
            text(
                "SELECT status FROM channel_session"
                " WHERE channel_id=:id AND status='active' LIMIT 1"
            ),
            {"id": ch_id},
        )).first()
    status = st_row[0] if st_row else "none"
    return HTMLResponse(channel_credential_html(ch_id, row[1], status))


# ─── Instagram login flow ─────────────────────────────────────────────────────

@router.post("/channels/{ch_id}/ig/start", response_class=HTMLResponse)
async def ig_login_start(
    ch_id: int,
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    session_json: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        if await _channel_branch(session, ch_id, allowed_branch_ids(request)) is None:
            return HTMLResponse(_ch_ig_form(ch_id, error="Forbidden"))
    if session_json.strip():
        try:
            dump = json.loads(session_json.strip())
        except Exception:
            return HTMLResponse(_ch_ig_form(ch_id, error="Invalid JSON"))
        return await _ig_save(ch_id, dump)

    if not username.strip() or not password.strip():
        return HTMLResponse(_ch_ig_form(ch_id, error="Username and password required"))

    try:
        from instagrapi import Client  # noqa: PLC0415 (lazy)
        cl = Client()
        try:
            cl.login(username.strip(), password.strip())
        except Exception as exc:
            name = type(exc).__name__.lower()
            if "twofactor" in name or "challenge" in name or "two" in name:
                fid = secrets.token_hex(8)
                _ig_flows[fid] = {"client": cl, "channel_id": ch_id}
                return HTMLResponse(_ch_ig_form(ch_id, step="2fa", flow_id=fid))
            return HTMLResponse(_ch_ig_form(ch_id, error=str(exc)[:160]))
        return await _ig_save(ch_id, cl.get_settings())
    except ImportError:
        return HTMLResponse(_ch_ig_form(ch_id, error="instagrapi not installed on server"))
    except Exception as exc:
        return HTMLResponse(_ch_ig_form(ch_id, error=str(exc)[:160]))


@router.post("/channels/{ch_id}/ig/verify", response_class=HTMLResponse)
async def ig_login_verify(
    ch_id: int,
    request: Request,
    flow_id: str = Form(default=""),
    code: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        if await _channel_branch(session, ch_id, allowed_branch_ids(request)) is None:
            return HTMLResponse(_ch_ig_form(ch_id, error="Forbidden"))
    flow = _ig_flows.pop(flow_id, None)
    if not flow or flow["channel_id"] != ch_id:
        return HTMLResponse(_ch_ig_form(ch_id, error="Flow expired — please login again"))
    try:
        cl = flow["client"]
        cl.challenge_resolve(code.strip())
        return await _ig_save(ch_id, cl.get_settings())
    except Exception as exc:
        return HTMLResponse(_ch_ig_form(ch_id, step="2fa", flow_id=flow_id, error=str(exc)[:160]))


async def _ig_save(ch_id: int, dump: dict) -> HTMLResponse:
    enc = encrypt(json.dumps(dump))
    async with session_scope() as session:
        await session.execute(
            text("DELETE FROM channel_session WHERE channel_id=:id"), {"id": ch_id}
        )
        await session.execute(
            text(
                "INSERT INTO channel_session (channel_id, secret_enc, status)"
                " VALUES (:ch, :sec, 'active')"
            ),
            {"ch": ch_id, "sec": enc},
        )
    resp = HTMLResponse(channel_credential_html(ch_id, "instagram", "active"))
    resp.headers["HX-Trigger"] = "refreshChannelList"
    return resp


# ─── Meta Business ────────────────────────────────────────────────────────────

@router.post("/channels/{ch_id}/meta/connect", response_class=HTMLResponse)
async def meta_connect(
    ch_id: int,
    request: Request,
    token: str = Form(default=""),
    page_id: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        if await _channel_branch(session, ch_id, allowed_branch_ids(request)) is None:
            return HTMLResponse(_ch_meta_form(ch_id, error="Forbidden"))
    if not token.strip():
        return HTMLResponse(_ch_meta_form(ch_id, error="Access token is required"))
    dump = {
        "token": token.strip(),
        "account_id": page_id.strip(),
        "base_url": "https://graph.instagram.com/v21.0",
    }
    enc = encrypt(json.dumps(dump))
    async with session_scope() as session:
        await session.execute(
            text("DELETE FROM channel_session WHERE channel_id=:id"), {"id": ch_id}
        )
        if page_id.strip():
            await session.execute(
                text("UPDATE channel SET account_id=:a WHERE id=:id"),
                {"a": page_id.strip(), "id": ch_id},
            )
        await session.execute(
            text(
                "INSERT INTO channel_session (channel_id, secret_enc, status)"
                " VALUES (:ch, :sec, 'active')"
            ),
            {"ch": ch_id, "sec": enc},
        )
    resp = HTMLResponse(channel_credential_html(ch_id, "meta_business", "active"))
    resp.headers["HX-Trigger"] = "refreshChannelList"
    return resp


# ─── WhatsApp Evolution ───────────────────────────────────────────────────────

@router.post("/channels/{ch_id}/wa/connect", response_class=HTMLResponse)
async def wa_connect(
    ch_id: int,
    request: Request,
    base_url: str = Form(default=""),
    instance: str = Form(default=""),
    api_key: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        if await _channel_branch(session, ch_id, allowed_branch_ids(request)) is None:
            return HTMLResponse(_ch_wa_form(ch_id, error="Forbidden"))
    if not base_url.strip() or not instance.strip() or not api_key.strip():
        return HTMLResponse(_ch_wa_form(ch_id, error="All three fields are required"))
    dump = {
        "base_url": base_url.strip(),
        "instance": instance.strip(),
        "api_key": api_key.strip(),
    }
    enc = encrypt(json.dumps(dump))
    async with session_scope() as session:
        await session.execute(
            text("DELETE FROM channel_session WHERE channel_id=:id"), {"id": ch_id}
        )
        await session.execute(
            text("UPDATE channel SET handle=:h WHERE id=:id"),
            {"h": instance.strip(), "id": ch_id},
        )
        await session.execute(
            text(
                "INSERT INTO channel_session (channel_id, secret_enc, status)"
                " VALUES (:ch, :sec, 'active')"
            ),
            {"ch": ch_id, "sec": enc},
        )
    resp = HTMLResponse(channel_credential_html(ch_id, "whatsapp", "active"))
    resp.headers["HX-Trigger"] = "refreshChannelList"
    return resp
