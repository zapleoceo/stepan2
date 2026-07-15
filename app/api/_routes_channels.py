"""Channel management routes — CRUD and per-kind credential flows."""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import Any

import httpx
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
from app.modules.meta.tokens import page_access_token
from app.modules.settings.repository import SettingRepo
from app.modules.settings.service import get_settings

from ._i18n import apply_lang, t
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
from ._ui_settings import channel_settings_html

logger = logging.getLogger(__name__)

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
    lang = apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        branch_id = await _channel_branch(session, ch_id, allowed)
        if branch_id is None:
            return HTMLResponse(_FORBIDDEN, status_code=403)
        row = (await session.execute(
            text("SELECT id, kind, handle, account_id, is_active FROM channel WHERE id=:id"),
            {"id": ch_id},
        )).first()
        if not row:
            return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
        values = await SettingRepo(session).load_all(branch_id, ch_id)
        cap_usage = await _channel_cap_usage(session, branch_id, ch_id)
    body = channel_edit_form_html(row[0], row[1], row[2] or "", row[3] or "", bool(row[4]))
    return HTMLResponse(body + channel_settings_html(row[1], values, lang, ch_id, cap_usage))


async def _channel_cap_usage(
    session: Any, branch_id: int, channel_id: int,
) -> dict[str, tuple[int, int]]:
    """Live per-connector anti-ban usage (sent this hour/day vs the channel's cap), computed
    fresh from real sent counts — the badge shown under hourly_cap/daily_cap in the editor."""
    from datetime import timedelta  # noqa: PLC0415

    from app.domain.clock import branch_day_start_utc, utc_now  # noqa: PLC0415
    from app.modules.conversation.repository import OutboxRepo  # noqa: PLC0415
    from app.modules.settings.service import get_channel_settings  # noqa: PLC0415
    cfg = await get_channel_settings(session, branch_id, channel_id)
    repo = OutboxRepo(session, branch_id)
    now = utc_now()
    usage: dict[str, tuple[int, int]] = {}
    if cfg.hourly_cap > 0:
        usage["hourly_cap"] = (
            await repo.count_sent_since(now - timedelta(hours=1), channel_id), cfg.hourly_cap)
    if cfg.daily_cap > 0:
        day_start = branch_day_start_utc(now, cfg.tz_offset_h)
        usage["daily_cap"] = (
            await repo.count_sent_since(day_start, channel_id), cfg.daily_cap)
    return usage


@router.post("/channels/{ch_id}/save", response_class=HTMLResponse)
async def channel_save(
    ch_id: int,
    request: Request,
    handle: str = Form(default=""),
    account_id: str = Form(default=""),
    is_active: str = Form(default=""),
) -> HTMLResponse:
    lang = apply_lang(request)
    allowed = writable_branch_ids(request)  # write route: enforce WRITE role for the branch
    async with session_scope() as session:
        branch_id = await _channel_branch(session, ch_id, allowed)
        if branch_id is None:
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
        values = await SettingRepo(session).load_all(branch_id, ch_id)
    body = channel_edit_form_html(row[0], row[1], row[2] or "", row[3] or "", bool(row[4]))
    resp = HTMLResponse(body + channel_settings_html(row[1], values, lang, ch_id))
    # Refresh the channel LIST too (like create/delete/connect do): saving can flip is_active,
    # and without this the row keeps showing the old on/off state — looked like the save didn't
    # take (manager toggled "channel active" off, list still showed it on).
    resp.headers["HX-Trigger"] = "refreshChannelList"
    return resp


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
    sessionid: str = Form(default=""),
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

    if sessionid.strip():
        # Adopt a session a browser already established. Instagram's 2FA now lives on its
        # Bloks endpoints while instagrapi still calls the legacy accounts/two_factor_login/
        # (subzeroid/instagrapi#2231), so a 2FA account cannot complete the password flow
        # here at all — this carries the finished session in instead. Same proxy+geo as the
        # worker, or the first poll from a different geo trips a checkpoint.
        cl = build_ig_client(proxy=settings().ig_proxy, lang=lang, tz_offset_h=tz)
        try:
            await asyncio.to_thread(cl.login_by_sessionid, sessionid.strip())
        except Exception as exc:  # noqa: BLE001 — surface the reason; never log the sessionid
            logger.warning("IG sessionid login failed channel=%d: %s: %s",
                           ch_id, type(exc).__name__, str(exc)[:200])
            return HTMLResponse(_ch_ig_form(ch_id, error=str(exc)[:200]))
        return await _ig_save(ch_id, cl.get_settings())

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
    attempt: int = 0,
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
        # Instagram's 2FA is NOT always a typed code. Read two_factor_info: only TOTP/SMS need
        # a code — otherwise it's a device-approval push ("tap Approve on your phone"), where the
        # right move is to re-login on the SAME client, not to demand a code that never arrives
        # (the itstep.kl "keeps asking for a code" bug). Render the matching step.
        kind = _two_factor_kind(cl)
        _ig_flows[fid] = {"client": cl, "channel_id": ch_id, "kind": kind,
                          "username": user, "password": pw}
        return HTMLResponse(_ch_ig_form(ch_id, step="2fa", flow_id=fid, kind=kind,
                                        username=user, attempt=attempt))
    except ChallengeRequired as exc:
        logger.warning("IG challenge channel=%d manual=%s: %s", ch_id,
                       _is_manual_challenge(exc), str(exc)[:300])
        if _is_manual_challenge(exc):
            _ig_flows[fid] = {"client": cl, "channel_id": ch_id, "kind": "manual",
                              "username": user, "password": pw}
            return HTMLResponse(_ch_ig_form(ch_id, step="2fa", flow_id=fid, kind="manual",
                                            username=user, attempt=attempt))
        # username kept only for display here — the challenge is resolved via
        # challenge_code_handler on the live client, not a re-login call, so it never
        # needs the password like the 2fa/manual branches do (see _resolve_ig_code).
        _ig_flows[fid] = {"client": cl, "channel_id": ch_id, "kind": "challenge",
                          "username": user}
        return HTMLResponse(_ch_ig_form(ch_id, step="2fa", flow_id=fid, kind="challenge",
                                        username=user))
    except Exception as exc:
        # Anything instagrapi raises that is NOT 2FA/challenge lands here and used to be
        # swallowed into the red box on the operator's screen and nowhere else — leaving the
        # server logs showing only a bare "[400] POST /accounts/login/" with no reason, so a
        # failing connect could not be diagnosed without asking the operator to read the
        # screen. Log the exception TYPE and message (never the password, never the payload).
        logger.warning("IG login failed channel=%d user=%s: %s: %s",
                       ch_id, user, type(exc).__name__, str(exc)[:300])
        _ig_flows.pop(fid, None)
        return HTMLResponse(_ch_ig_form(ch_id, error=str(exc)[:200]))
    return await _ig_save(ch_id, cl.get_settings())


def _two_factor_kind(cl: Any) -> str:
    """Classify the 2FA Instagram is asking for from the client's last response.

    'code'  → TOTP app or SMS code the user must type (totp_two_factor_on / sms_two_factor_on).
    'device' → a login-approval push to the user's other device: no code exists; login is
               completed by re-attempting on the same client after the user taps Approve.
    Defaults to 'device' when neither code method is flagged (that's the notification path)."""
    info = {}
    last = getattr(cl, "last_json", None)
    if isinstance(last, dict):
        info = last.get("two_factor_info") or {}
    kind = "2fa" if (info.get("totp_two_factor_on") or info.get("sms_two_factor_on")) else "device"
    # Which branch we picked, and everything Instagram told us to pick it from. Without this a
    # wrong choice is invisible: the operator just sees a code box for a push approval (or the
    # reverse) and the log shows nothing but "[400] POST /accounts/login/".
    # WARNING, not INFO: the API runs at WARNING, and this fires only on an operator-triggered
    # connect (a few a day), so it costs nothing and is worthless if it can't be read.
    # `obfuscated_phone_number` is masked BY Instagram (e.g. "+62 *** *** 89") and is the only
    # way to see WHICH number a code that "never arrives" is actually being sent to.
    logger.warning(
        "IG 2FA classified as %s (totp=%s sms=%s whatsapp=%s trusted_push=%s phone=%s) keys=%s",
        kind, info.get("totp_two_factor_on"), info.get("sms_two_factor_on"),
        info.get("whatsapp_two_factor_on"), info.get("pending_trusted_notification"),
        info.get("obfuscated_phone_number"), sorted(info))
    return kind


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
    attempt: int = Form(default=0),
) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        if await _channel_branch(session, ch_id, writable_branch_ids(request)) is None:
            return HTMLResponse(_ch_ig_form(ch_id, error="Forbidden"))
    flow = _ig_flows.get(flow_id)
    if not flow or flow["channel_id"] != ch_id:
        # The flow (and its logged-in client) lives in this process's memory, so a restart
        # mid-login — a deploy, most often — drops it and there is nothing to continue.
        return HTMLResponse(_ch_ig_form(ch_id, error=t("ch.flow_expired")))
    cl = flow["client"]
    if flow.get("kind") in ("manual", "device") or (skip_code and flow.get("password")):
        # No code to apply — this login is cleared by APPROVING on the phone, not by typing a
        # code: either a challenge only the real Instagram app clears (kind='manual'), a login-
        # approval push to another device (kind='device'), or the operator says they approved a
        # parallel push (skip_code — Instagram can prompt for a 2FA code AND send an in-app
        # "was this you?" push for the SAME attempt at once). Retry on the SAME client either way
        # (the device fingerprint that got approved lives on it).
        return await _attempt_ig_login(cl, ch_id, flow["username"], flow["password"], flow_id,
                                       attempt=attempt)
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
    platform: str = Form(default="instagram_graph"),
) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        branch_id = await _channel_branch(session, ch_id, writable_branch_ids(request))
        if branch_id is None:
            return HTMLResponse(_ch_meta_form(ch_id, error="Forbidden"))

    resolved_token = token.strip()
    if not resolved_token and platform == "facebook_page" and page_id.strip():
        async with session_scope() as session:
            branch_cfg = await get_settings(session, branch_id)
        if not branch_cfg.meta_system_user_token:
            return HTMLResponse(
                _ch_meta_form(ch_id, error="No meta_system_user_token in branch settings")
            )
        try:
            resolved_token = await page_access_token(
                branch_cfg.meta_system_user_token, page_id.strip()
            )
        except (httpx.HTTPError, ValueError) as exc:
            return HTMLResponse(_ch_meta_form(ch_id, error=f"Auto token failed: {exc}"[:200]))
    if not resolved_token:
        return HTMLResponse(_ch_meta_form(ch_id, error="Access token is required"))

    base_url = (
        f"https://graph.facebook.com/{settings().ig_graph_version}"
        if platform == "facebook_page"
        else f"https://graph.instagram.com/{settings().ig_graph_version}"
    )
    dump = {
        "token": resolved_token,
        "account_id": page_id.strip(),
        "base_url": base_url,
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
