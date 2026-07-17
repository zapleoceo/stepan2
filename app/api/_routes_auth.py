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


def _safe_next(dest: str) -> str:
    """A post-login destination is only honoured if it's a same-site absolute PATH — never an
    absolute URL, a protocol-relative '//evil', or an auth endpoint. Blocks open-redirect and
    a login→login bounce; anything unsafe falls back to the inbox."""
    if (dest.startswith("/") and not dest.startswith(("//", "/\\"))
            and not dest.startswith(("/login", "/logout", "/api/"))):
        return dest
    return "/ui/inbox"


@router.get("/login", response_class=HTMLResponse)
async def login_page(next: str = "") -> HTMLResponse:  # noqa: A002 — matches the ?next= param
    if not settings().auth_enabled:
        return HTMLResponse("", status_code=302, headers={"Location": "/ui/inbox"})
    return HTMLResponse(_login_html(settings().tg_login_bot_username, _safe_next(next)))


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
