"""Channel management routes — CRUD and per-kind credential flows."""
from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.adapters.channels.ig_client import build_ig_client
from app.adapters.crypto import encrypt
from app.adapters.db.models import Channel
from app.adapters.db.session import session_scope
from app.admin._branch import (
    allowed_branch_ids,
    is_branch_forbidden,
    writable_branch_ids,
)
from app.config import settings
from app.domain.enums import ChannelKind
from app.modules.channels.service import ChannelService

from ._i18n import apply_lang
from ._ui_panels import (
    _ch_form_for,
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


async def _channel_branch(session: Any, ch_id: int, allowed: list[int] | None) -> int | None:
    """Channel's branch_id, or None if missing / outside the caller's allowed branches —
    the tenant-ownership guard blocking cross-branch credential/edit IDOR."""
    row = (
        await session.execute(
            text("SELECT branch_id FROM channel WHERE id=:id"), {"id": ch_id}
        )
    ).first()
    if row is None or is_branch_forbidden(row[0], allowed):
        return None
    return row[0]


_FORBIDDEN = '<div class="emp">Forbidden</div>'


# ─── list + new form ──────────────────────────────────────────────────────────

@router.get("/channels/branch/{branch_id}", response_class=HTMLResponse)
async def channels_list(branch_id: int, request: Request) -> HTMLResponse:
    """HTMX partial: channel table for #ch-list in branch edit page."""
    apply_lang(request)
    if is_branch_forbidden(branch_id, allowed_branch_ids(request)):
        return HTMLResponse(_FORBIDDEN, status_code=403)
    async with session_scope() as session:
        return HTMLResponse(await _ch_list_html(session, branch_id))


@router.get("/channels/branch/{branch_id}/new", response_class=HTMLResponse)
async def channel_new(branch_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    if is_branch_forbidden(branch_id, allowed_branch_ids(request)):
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
    if is_branch_forbidden(branch_id, writable_branch_ids(request)):  # WRITE role required
        return HTMLResponse(_FORBIDDEN, status_code=403)
    kind_val = kind if kind in (k.value for k in ChannelKind) else ChannelKind.INSTAGRAM.value
    async with session_scope() as session:
        session.add(Channel(
            branch_id=branch_id,
            kind=ChannelKind(kind_val),
            handle=handle.strip() or None,
            account_id=account_id.strip() or None,
            is_active=bool(is_active),
        ))
        await session.flush()
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
    allowed = writable_branch_ids(request)  # write route: enforce WRITE role for the branch
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
    """Delete a channel AND cascade its conversation data (threads/messages/media/…),
    dropping only leads left with no thread on any other channel (see ChannelService)."""
    apply_lang(request)
    async with session_scope() as session:
        branch_id = await _channel_branch(session, ch_id, writable_branch_ids(request))
        if branch_id is None:
            return HTMLResponse(_FORBIDDEN, status_code=403)
        result = await ChannelService(session, branch_id).purge(ch_id)
        if result is None:
            return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
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


@router.get("/channels/{ch_id}/form", response_class=HTMLResponse)
async def channel_form(ch_id: int, request: Request) -> HTMLResponse:
    """Force the kind-specific entry form (reconnect on an already-active channel)."""
    apply_lang(request)
    async with session_scope() as session:
        if await _channel_branch(session, ch_id, allowed_branch_ids(request)) is None:
            return HTMLResponse(_FORBIDDEN, status_code=403)
        row = (await session.execute(
            text("SELECT kind FROM channel WHERE id=:id"), {"id": ch_id},
        )).first()
    if not row:
        return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
    return HTMLResponse(_ch_form_for(ch_id, row[0]))


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
        if await _channel_branch(session, ch_id, writable_branch_ids(request)) is None:
            return HTMLResponse(_ch_ig_form(ch_id, error="Forbidden"))
        lang, tz = await _channel_geo(session, ch_id)
    if session_json.strip():
        try:
            dump = json.loads(session_json.strip())
        except Exception:
            return HTMLResponse(_ch_ig_form(ch_id, error="Invalid JSON"))
        return await _ig_save(ch_id, dump)

    if not username.strip() or not password.strip():
        return HTMLResponse(_ch_ig_form(ch_id, error="Username and password required"))

    # Same proxy+geo as the worker (build_channel_port) — a login geo that differs from
    # the polling geo triggers an instant checkpoint. See ig_client.build_ig_client.
    cl = build_ig_client(proxy=settings().ig_proxy, lang=lang, tz_offset_h=tz)
    fid = secrets.token_hex(8)
    return await _attempt_ig_login(cl, ch_id, username.strip(), password.strip(), fid)


def _is_manual_challenge(exc: Exception) -> bool:
    """True when instagrapi itself says a text code can't resolve this checkpoint at all —
    a Bloks redirect / auth-platform / native in-app approval. instagrapi's own
    ChallengeRequired._message_for_payload prefixes exactly these three cases with "Manual
    verification required" (checked in its source, 2.18.3) — the remaining cases (legacy
    email/SMS challenge, a named challenge step) are still worth trying
    challenge_code_handler on, so only THIS exact marker routes to the no-code retry flow.

    A SECOND, separate instagrapi code path hits the same dead end from a different
    exception: when a real 2FA code is submitted, instagrapi's re-login can fall back to
    Bloks-based 2FA (_login_with_bloks_two_factor) and raise a TwoFactorRequired whose
    message ends in "Complete verification in the Instagram app..." when Instagram's login
    response never included the two_step_verification_context the Bloks fallback needs —
    same "no code can fix this, go approve in the real app" situation, just phrased by a
    different function (real report, 2026-07-08: this fired on the 2FA-code submit, not
    the initial login, so the original marker alone didn't catch it)."""
    msg = str(exc)
    return ("Manual verification required" in msg
            or "Complete verification in the Instagram app" in msg)


async def _attempt_ig_login(
    cl: Any, ch_id: int, user: str, pw: str, fid: str, *, verification_code: str = "",
) -> HTMLResponse:
    """Shared login attempt for both the first credentials submit and a retry after the
    operator resolves a challenge out-of-band (2FA code, challenge code, or a manual in-app
    approval). Always reuses the SAME client instance across retries: instagrapi's device
    fingerprint (uuid/phone_id/device_id/proxy) lives on `cl`, and Instagram ties a manual
    approval to that exact fingerprint — a fresh client for the retry would just get
    challenged again."""
    try:
        from instagrapi.exceptions import (  # noqa: PLC0415
            ChallengeRequired,
            TwoFactorRequired,
        )
    except ImportError:
        _ig_flows.pop(fid, None)
        return HTMLResponse(_ch_ig_form(ch_id, error="instagrapi not installed on server"))
    try:
        if verification_code:
            await asyncio.to_thread(cl.login, user, pw, verification_code=verification_code)
        else:
            await asyncio.to_thread(cl.login, user, pw)
    except TwoFactorRequired:
        _ig_flows[fid] = {"client": cl, "channel_id": ch_id, "kind": "2fa",
                          "username": user, "password": pw}
        return HTMLResponse(_ch_ig_form(ch_id, step="2fa", flow_id=fid, kind="2fa",
                                        username=user))
    except ChallengeRequired as exc:
        if _is_manual_challenge(exc):
            _ig_flows[fid] = {"client": cl, "channel_id": ch_id, "kind": "manual",
                              "username": user, "password": pw}
            return HTMLResponse(_ch_ig_form(ch_id, step="2fa", flow_id=fid, kind="manual",
                                            username=user))
        # username kept only for display here — the challenge is resolved via
        # challenge_code_handler on the live client, not a re-login call, so it never
        # needs the password like the 2fa/manual branches do (see _resolve_ig_code).
        _ig_flows[fid] = {"client": cl, "channel_id": ch_id, "kind": "challenge",
                          "username": user}
        return HTMLResponse(_ch_ig_form(ch_id, step="2fa", flow_id=fid, kind="challenge",
                                        username=user))
    except Exception as exc:
        _ig_flows.pop(fid, None)
        return HTMLResponse(_ch_ig_form(ch_id, error=str(exc)[:200]))
    return await _ig_save(ch_id, cl.get_settings())


async def _channel_geo(session: Any, ch_id: int) -> tuple[str, int]:
    """(branch lang, tz_offset_h) for a channel — drives instagrapi geo alignment."""
    row = (await session.execute(
        text("SELECT b.lang, b.tz_offset_h FROM channel c"
             " JOIN branch b ON b.id = c.branch_id WHERE c.id = :id"),
        {"id": ch_id},
    )).first()
    return (row[0], int(row[1])) if row else ("en", 0)


def _resolve_ig_code(cl: Any, flow: dict[str, Any], code: str) -> None:
    """Apply the verification code per challenge kind. instagrapi resolves 2FA by
    re-login with verification_code, but an email/SMS challenge by challenge_resolve
    driven through challenge_code_handler — the two are not interchangeable."""
    if flow.get("kind") == "challenge":
        cl.challenge_code_handler = lambda username, choice: code
        cl.challenge_resolve(cl.last_json)
    else:
        cl.login(flow["username"], flow["password"], verification_code=code)


@router.post("/channels/{ch_id}/ig/verify", response_class=HTMLResponse)
async def ig_login_verify(
    ch_id: int,
    request: Request,
    flow_id: str = Form(default=""),
    code: str = Form(default=""),
    skip_code: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        if await _channel_branch(session, ch_id, writable_branch_ids(request)) is None:
            return HTMLResponse(_ch_ig_form(ch_id, error="Forbidden"))
    flow = _ig_flows.get(flow_id)
    if not flow or flow["channel_id"] != ch_id:
        return HTMLResponse(_ch_ig_form(ch_id, error="Flow expired — please login again"))
    cl = flow["client"]
    if flow.get("kind") == "manual" or (skip_code and flow.get("password")):
        # No code to apply — either this checkpoint is only cleared by approving in the
        # real Instagram app (kind='manual'), or the operator says they already approved a
        # parallel push notification there (skip_code — Instagram can prompt for a 2FA code
        # AND send an in-app "was this you?" push for the SAME login attempt at once; typing
        # a code the operator never needed just to reach the manual retry was pointless).
        # Retry on the SAME client (same flow_id) either way.
        return await _attempt_ig_login(cl, ch_id, flow["username"], flow["password"], flow_id)
    try:
        await asyncio.to_thread(_resolve_ig_code, cl, flow, code.strip())
    except Exception as exc:  # keep the flow so the user can retry
        if _is_manual_challenge(exc) and flow.get("password"):
            # The code just submitted can never work (instagrapi's own Bloks-2FA fallback
            # says so) — switch this flow to the no-code "confirm in the app, then retry"
            # step instead of re-showing the same doomed code field with a red error.
            flow["kind"] = "manual"
            return HTMLResponse(_ch_ig_form(ch_id, step="2fa", flow_id=flow_id, kind="manual",
                                            username=flow.get("username", "")))
        return HTMLResponse(_ch_ig_form(
            ch_id, step="2fa", flow_id=flow_id, error=str(exc)[:160],
            kind=flow.get("kind", "2fa"), username=flow.get("username", "")))
    _ig_flows.pop(flow_id, None)
    return await _ig_save(ch_id, cl.get_settings())


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
        if await _channel_branch(session, ch_id, writable_branch_ids(request)) is None:
            return HTMLResponse(_ch_meta_form(ch_id, error="Forbidden"))
    if not token.strip():
        return HTMLResponse(_ch_meta_form(ch_id, error="Access token is required"))
    dump = {
        "token": token.strip(),
        "account_id": page_id.strip(),
        "base_url": f"https://graph.instagram.com/{settings().ig_graph_version}",
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
        if await _channel_branch(session, ch_id, writable_branch_ids(request)) is None:
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
