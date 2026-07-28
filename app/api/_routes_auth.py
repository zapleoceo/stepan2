"""Auth routes — Telegram Login widget page, login callback, logout."""
from __future__ import annotations

import html as _h
import logging
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.adapters.db.models import Membership
from app.adapters.db.session import session_scope
from app.api._auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE_S,
    mint_session,
    verify_telegram_login,
)
from app.api._ui_html import _FAVICON
from app.config import settings
from app.domain.enums import Role
from app.modules.auth.rbac import Action, can
from app.modules.auth.repository import MembershipRepo, UserRepo
from app.modules.auth.service import AuthService

router = APIRouter()
log = logging.getLogger(__name__)

# Breadcrumb dropped alongside the session cookie so a login that succeeds and then fails to
# stick can be told apart from never having logged in at all. Deliberately short: it describes
# ONE attempt, and must not still be around to misdiagnose the next one.
LOGIN_PROBE_COOKIE = "stepan2_login_probe"
LOGIN_PROBE_MAX_AGE_S = 120


def _safe_next(dest: str) -> str:
    """A post-login destination is only honoured if it's a same-site absolute PATH — never an
    absolute URL, a protocol-relative '//evil', or an auth endpoint. Blocks open-redirect and
    a login→login bounce; anything unsafe falls back to the inbox."""
    if (dest.startswith("/") and not dest.startswith(("//", "/\\"))
            and not dest.startswith(("/login", "/logout", "/api/"))):
        return dest
    return "/ui/inbox"


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "") -> HTMLResponse:  # noqa: A002 — ?next=
    if not settings().auth_enabled:
        return HTMLResponse("", status_code=302, headers={"Location": "/ui/inbox"})
    # Arriving here with the breadcrumb still set means the last login DID succeed — the
    # server minted a session and sent it — and the browser did not send it back. Without
    # this, that is an infinite bounce indistinguishable from "you are not signed in", which
    # is exactly how it was reported: "I am logged in and it keeps asking me to log in."
    looped = request.cookies.get(LOGIN_PROBE_COOKIE) == "1"
    resp = HTMLResponse(
        _cookie_blocked_html() if looped
        else _login_html(settings().tg_login_bot_username, _safe_next(next)))
    if looped:
        resp.delete_cookie(LOGIN_PROBE_COOKIE)  # one diagnosis, then a clean retry
    return resp


@router.get("/api/tg_login")
async def tg_login(request: Request):  # noqa: ANN201 (HTMLResponse | RedirectResponse)
    # `next` is OUR param, not part of Telegram's signed payload — pop it before verifying or
    # the hash check fails (it signs only its own fields).
    params = dict(request.query_params)
    dest = _safe_next(params.pop("next", ""))
    tg_id = verify_telegram_login(params, settings().tg_bot_token)
    if tg_id is None:
        return HTMLResponse(_msg_html("Login verification failed."), status_code=403)

    async with session_scope() as s:
        user = await AuthService(s).resolve(tg_id)
        if user is None and tg_id == settings().bootstrap_super_admin and tg_id:
            user = await UserRepo(s).create(tg_id, request.query_params.get("first_name"))
            s.add(Membership(user_id=user.id, branch_id=None, role=Role.SUPER_ADMIN))
            await s.flush()
            log.debug("self-provisioned platform owner tg=%d", tg_id)
        if user is None:
            return HTMLResponse(_msg_html("Not authorized."), status_code=403)
        if not user.name:
            # A user invited via the admin panel (not self-provisioned) has no name — every
            # ThreadLog/StageEvent entry they write then falls back to the generic literal
            # "manager" (_actor_name), so the chat timeline can't say WHICH manager acted.
            # Telegram's own login payload already carries their name; backfill it here so it
            # sticks from their very first login instead of staying blank forever.
            tg_name = " ".join(
                p for p in (params.get("first_name"), params.get("last_name")) if p
            ).strip() or params.get("username") or ""
            if tg_name:
                user.name = tg_name
                s.add(user)
        memberships = await MembershipRepo(s).memberships_for_user(user.id)
        is_super = any(m.role == Role.SUPER_ADMIN for m in memberships)
        branch_ids = [m.branch_id for m in memberships if m.branch_id is not None]
        # Branches where this role grants WRITE (branch_admin) — the rbac grant table is
        # the single source of who-may-write. branch_viewer → [] (read-only).
        writable = [
            m.branch_id for m in memberships
            if m.branch_id is not None and can(m.role, Action.WRITE)
        ]
        token = mint_session(
            telegram_id=tg_id, user_id=user.id, name=user.name or "",
            is_super=is_super, branch_ids=branch_ids, writable_branch_ids=writable,
        )

    # Land the cookie on a 200 HTML page that then redirects, NOT on the 303 itself: many
    # mobile in-app WebViews (Telegram's browser, older Android WebView) drop a Set-Cookie that
    # rides a 3xx response, so the very next request arrived with no session and bounced back to
    # /login — an endless login loop on phones while desktop worked (real report 2026-07-17).
    resp = HTMLResponse(_post_login_html(dest))
    resp.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_MAX_AGE_S,
        httponly=True, samesite="lax", secure=True,
    )
    # A breadcrumb next to the session, with the SAME attributes minus HttpOnly, so it lives
    # or dies with it. If the next request bounces back to /login still carrying this, the
    # session cookie was issued and dropped — a browser problem we can name instead of
    # bouncing the person forever. Short-lived: it must not outlast the attempt it describes.
    resp.set_cookie(
        LOGIN_PROBE_COOKIE, "1", max_age=LOGIN_PROBE_MAX_AGE_S,
        httponly=False, samesite="lax", secure=True,
    )
    return resp


@router.get("/logout")
async def logout() -> RedirectResponse:
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


def _login_html(bot_username: str, next_dest: str = "/ui/inbox") -> str:
    if bot_username:
        # The widget appends its signed &id=…&hash=… to data-auth-url, so ?next= rides along
        # and tg_login returns the user to where the gate first intercepted them.
        auth_url = "/api/tg_login"
        if next_dest and next_dest != "/ui/inbox":
            auth_url += f"?next={quote(next_dest, safe='')}"
        widget = (
            f'<script async src="https://telegram.org/js/telegram-widget.js?22"'
            f' data-telegram-login="{bot_username}" data-size="large"'
            f' data-auth-url="{_h.escape(auth_url)}" data-request-access="write"></script>'
        )
    else:
        widget = (
            '<p style="color:#e0a458;max-width:30rem">⚠ Telegram Login is not configured.'
            ' Set STEPAN2_TG_LOGIN_BOT_USERNAME and bind this domain to the bot in'
            ' BotFather (/setdomain).</p>'
        )
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'{_FAVICON}'
        '<title>Stepan 2 — Login</title><style>'
        'body{background:#0f1117;color:#e8eef4;font-family:system-ui,sans-serif;margin:0;'
        'min-height:100vh;display:flex;align-items:center;justify-content:center}'
        '.card{text-align:center}h1{font-weight:600;letter-spacing:.02em}'
        'p{color:#9aa7b4}</style></head><body><div class="card">'
        '<h1>Stepan 2</h1><p>Sign in with Telegram to continue</p>'
        f'{widget}</div></body></html>'
    )


def _post_login_html(dest: str) -> str:
    """A 200 page whose ONLY job is to let the browser commit the just-set session cookie,
    then navigate on. location.replace keeps the transient page out of history; the meta
    refresh is the fallback if JS is off, and the link is the fallback if both fail."""
    d = _h.escape(dest)
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0;url={d}">'
        f'{_FAVICON}<title>Signing in…</title>'
        '<style>body{background:#0f1117;color:#9aa7b4;font-family:system-ui,sans-serif;'
        'margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center}'
        'a{color:#4da6ff}</style></head><body>'
        f'<p>Signing in… <a href="{d}">continue</a></p>'
        f'<script>location.replace({d!r})</script></body></html>'
    )


def _cookie_blocked_html() -> str:
    """Shown instead of the login widget when a completed login did not stick.

    Names the one thing that is actually wrong — the browser is not keeping our cookie — and
    lists what fixes it, in the order most likely to work. Reported live on 2026-07-28: Opera
    134 on Linux looped, while Chrome on the same machine stayed signed in."""
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'{_FAVICON}'
        '<title>Stepan 2 — Login</title><style>'
        'body{background:#0f1117;color:#e8eef4;font-family:system-ui,sans-serif;margin:0;'
        'min-height:100vh;display:flex;align-items:center;justify-content:center;padding:1rem}'
        '.card{max-width:34rem}h1{font-weight:600;letter-spacing:.02em;font-size:1.3rem}'
        'p,li{color:#9aa7b4;line-height:1.5}a{color:#4da6ff}'
        'code{background:#1a1f2e;padding:.1rem .3rem;border-radius:3px;color:#e8eef4}'
        '</style></head><body><div class="card">'
        '<h1>Sign-in worked — your browser did not keep the session</h1>'
        '<p>Telegram confirmed who you are and we issued a session, but the next request '
        'came back without it. That is why this page keeps reappearing. Nothing is wrong '
        'with your account.</p>'
        '<p>What usually fixes it:</p><ul>'
        '<li>Allow cookies for <code>stepan2.zapleo.com</code> — check any tracker or '
        'ad blocker, and any "block third-party cookies" setting.</li>'
        '<li>Turn off private/incognito mode for this tab.</li>'
        '<li>Try another browser. If it works there, the setting above is the cause.</li>'
        '</ul>'
        '<p><a href="/login">← Try signing in again</a></p>'
        '</div></body></html>'
    )


def _msg_html(message: str) -> str:
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f'{_FAVICON}'
        '<title>Stepan 2</title><style>body{background:#0f1117;color:#e8eef4;'
        'font-family:system-ui,sans-serif;margin:0;min-height:100vh;display:flex;'
        'align-items:center;justify-content:center}a{color:#4da6ff}</style></head>'
        f'<body><div><p>{message}</p><p><a href="/login">← Back to login</a></p>'
        '</div></body></html>'
    )
