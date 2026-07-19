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
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.api._session import sign, verify
from app.config import settings

logger = logging.getLogger(__name__)

SESSION_COOKIE = "stepan2_session"
SESSION_MAX_AGE_S = 60 * 60 * 24 * 30  # 30 days

# Reachable without a session (exact path or prefix). Trailing slashes on the mount
# prefixes so "/connector" can't be widened to "/connectorevil" by a startswith match.
# Exact-match public paths (no trailing slash → must NOT be prefix-matched, or a future
# "/loginhistory" route would be silently public). Subpath surfaces stay as prefixes below.
_PUBLIC_EXACT = ("/healthz", "/login", "/api/tg_login", "/logout", "/", "/privacy",
                 "/whats-new", "/robots.txt", "/sitemap.xml", "/og.svg", "/og.png")
_PUBLIC_PREFIXES = ("/webhooks/", "/mcp/", "/connector/", "/reader/", "/demo/")


def _secret() -> str:
    """The HMAC key for session cookies. When auth is enforced it MUST be non-empty — an
    empty key signs everything to the same value, making a session cookie forgeable, so we
    fail fast rather than run wide open."""
    secret = settings().session_secret or settings().secret_key
    if settings().auth_enabled and not secret:
        raise RuntimeError(
            "auth_enabled=true but no session secret set — set STEPAN2_SESSION_SECRET "
            "(or STEPAN2_SECRET_KEY); refusing to sign sessions with an empty key")
    return secret


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
    writable_branch_ids: list[int] | None = None,
) -> str:
    return sign(
        {
            "tg": telegram_id, "uid": user_id, "nm": name,
            "sa": is_super, "br": branch_ids,
            # branches where this user may WRITE (branch_admin); a subset of `br`.
            # A branch_viewer has [] here — read-only. super_admin (sa) spans all.
            "wr": list(writable_branch_ids or []),
            "iat": int(time.time()),
        },
        _secret(),
    )


def read_session(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    return verify(token, _secret(), SESSION_MAX_AGE_S) if token else None


def _is_public(path: str) -> bool:
    # "/" is the public marketing landing (exact match only — a prefix would open everything).
    if path == "/":
        return True
    return path in _PUBLIC_EXACT or any(path.startswith(p) for p in _PUBLIC_PREFIXES)


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
            is_super = bool(sess.get("sa"))
            request.state.allowed_branch_ids = (
                None if is_super else list(sess.get("br") or [])
            )
            # Deny-by-default for writes: a session minted before "wr" existed (or a
            # branch_viewer) has no writable branches. super_admin writes everywhere.
            request.state.writable_branch_ids = (
                None if is_super else list(sess.get("wr") or [])
            )
            return await call_next(request)

        if _is_public(request.url.path) or request.method == "OPTIONS":
            return await call_next(request)

        if request.headers.get("HX-Request") == "true" or request.method != "GET":
            resp = Response(status_code=401)
            resp.headers["HX-Redirect"] = "/login"
            return resp
        # Remember where they were headed so login lands them there, not always /ui/inbox.
        # Never round-trip an auth endpoint back into itself (avoids a /login?next=/login loop).
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        if target.startswith(("/login", "/logout", "/api/")):
            return RedirectResponse(url="/login", status_code=303)
        return RedirectResponse(url=f"/login?next={quote(target, safe='')}", status_code=303)


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


def _is_read_support_post(path: str) -> bool:
    """POSTs a branch_viewer may still call — they don't mutate business data, they
    support READING: message/draft translation caches, loading older history, and setting
    the (server-clamped) branch-filter view cookie."""
    if path == "/ui/branch-filter":
        return True
    return path.endswith(("/translate", "/tr-draft", "/tr", "/load-context"))


class WriteGuardMiddleware(BaseHTTPMiddleware):
    """Enforce branch_admin vs branch_viewer: a viewer is read-only, so it may not reach
    any state-changing /ui POST. Enforcement is centralized here (not sprinkled across
    ~30 write routes) so no mutating route can be forgotten. super_admin passes; the
    read-support POSTs above are always allowed. Reads the session cookie directly (like
    AdminGuard) so it doesn't depend on cross-middleware request.state propagation.

    A user with WRITE on *some* branch passes here; cross-branch writes are still confined
    by the per-row branch guards (is_branch_forbidden) every write route already runs."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if (
            settings().auth_enabled
            and request.method == "POST"
            and request.url.path.startswith("/ui/")
            and not _is_read_support_post(request.url.path)
        ):
            sess = read_session(request)
            # No session → AuthMiddleware already redirected; here, deny writes to a
            # non-super session (sa=False) with no writable branches (viewer, or a
            # pre-"wr" cookie — fail closed until re-login).
            if sess is not None and not sess.get("sa") and not sess.get("wr"):
                resp = Response(status_code=403)
                resp.headers["HX-Reswap"] = "none"  # htmx: don't blank the panel on deny
                return resp
        return await call_next(request)
