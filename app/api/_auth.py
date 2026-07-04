"""Authentication: Telegram Login verification + the request gate.

Enforcement is opt-in via STEPAN2_AUTH_ENABLED so the gate can ship dark and be
switched on once a login bot + domain are configured — this avoids locking out the
owner before the Telegram Login widget works. When enabled, every request outside the
public allowlist must carry a valid signed session cookie minted by the login callback.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.api._session import sign, verify
from app.config import settings

logger = logging.getLogger(__name__)

SESSION_COOKIE = "stepan2_session"
SESSION_MAX_AGE_S = 60 * 60 * 24 * 30  # 30 days

# Reachable without a session (exact path or prefix).
_PUBLIC_PREFIXES = ("/healthz", "/login", "/api/tg_login", "/logout", "/webhooks/")


def _secret() -> str:
    return settings().session_secret or settings().secret_key


def verify_telegram_login(
    data: dict[str, str], bot_token: str, max_age_s: int = 86400,
) -> int | None:
    """Validate a Telegram Login Widget payload; return telegram_id iff the hash checks
    out and auth_date is fresh. See core.telegram.org/widgets/login#checking-authorization."""
    received = data.get("hash", "")
    if not received or not bot_token:
        return None
    check_string = "\n".join(sorted(f"{k}={v}" for k, v in data.items() if k != "hash"))
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        if time.time() - int(data.get("auth_date", "0")) > max_age_s:
            return None
        return int(data["id"])
    except (KeyError, ValueError):
        return None


def mint_session(
    *, telegram_id: int, user_id: int, name: str, is_super: bool, branch_ids: list[int],
) -> str:
    return sign(
        {
            "tg": telegram_id, "uid": user_id, "nm": name,
            "sa": is_super, "br": branch_ids, "iat": int(time.time()),
        },
        _secret(),
    )


def read_session(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    return verify(token, _secret(), SESSION_MAX_AGE_S) if token else None


def _is_public(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in _PUBLIC_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """Gate every non-public request on a valid session when auth is enabled.

    Disabled (default) → pass-through, so deploying this code changes nothing until the
    owner sets STEPAN2_AUTH_ENABLED=true. A valid session attaches the identity and the
    user's allowed branch_ids to request.state (None = super_admin / all branches)."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if not settings().auth_enabled:
            return await call_next(request)

        sess = read_session(request)
        if sess is not None:
            request.state.user = sess
            request.state.allowed_branch_ids = (
                None if sess.get("sa") else list(sess.get("br") or [])
            )
            return await call_next(request)

        if _is_public(request.url.path) or request.method == "OPTIONS":
            return await call_next(request)

        if request.headers.get("HX-Request") == "true" or request.method != "GET":
            resp = Response(status_code=401)
            resp.headers["HX-Redirect"] = "/login"
            return resp
        return RedirectResponse(url="/login", status_code=303)


class AdminGuardMiddleware(BaseHTTPMiddleware):
    """The SQLAdmin dashboard (/admin/**) is raw, unfiltered CRUD over every model —
    Membership, Branch, AppSetting included — so it must be super_admin-only. This runs
    ALWAYS, independent of auth_enabled: with auth off there is no session, so /admin is
    closed rather than wide open (the previous behaviour). SQLAdmin owns /admin/*
    exclusively; the branch-filter meta API lives under /_admin/, unaffected."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        path = request.url.path
        if path == "/admin" or path.startswith("/admin/"):
            sess = read_session(request)
            if sess is None or not sess.get("sa"):
                return RedirectResponse(url="/ui/inbox", status_code=303)
        return await call_next(request)
