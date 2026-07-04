"""FastAPI app factory. Lifespan does no DB I/O on startup; the admin dashboard is
mounted lazily and guarded so the app (and /healthz) stays up even without a live DB."""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from app.adapters.db.session import engine
from app.admin.api import router as admin_meta_router
from app.admin.setup import mount_admin
from app.api._auth import AuthMiddleware
from app.api._routes_auth import router as auth_router
from app.api.ui import router as ui_router
from app.api.webhooks import router as webhooks_router

logger = logging.getLogger(__name__)

# Full-page routes that already return a complete shell — never re-wrap these.
_FULL_PAGE_ROUTES = frozenset({"/ui/inbox", "/ui/coach", "/ui/knowledge", "/ui/reports"})

# First path segment after /ui/ → sidebar nav_id
_SECTION_NAV = {
    "chat": "inbox",
    "funnel": "inbox",
    "threads": "inbox",
    "knowledge": "know",
    "coach": "coach",
    "reports": "reports",
}

_UI_SECTION_RE = re.compile(r"^/ui/([^/]+)")


class _PartialShellMiddleware(BaseHTTPMiddleware):
    """Wrap any /ui/** fragment in the full shell on direct (non-HTMX) browser load.

    HTMX navigation pushes partial URLs (/ui/settings/panel, /ui/chat/123/panel,
    /ui/knowledge/5/edit, /ui/branches/new …) to the address bar.  On page
    refresh the browser GETs that URL and gets a raw HTML fragment without CSS.
    This middleware intercepts any non-HTMX GET to /ui/** whose response body
    does NOT already start with <!DOCTYPE and wraps it in app_shell()."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        from app.admin._branch import is_super_admin  # noqa: PLC0415
        from app.api._i18n import DEFAULT_LANG, LANG_COOKIE, LANGS, _lang  # noqa: PLC0415
        from app.api._ui_html import app_shell  # noqa: PLC0415

        path = request.url.path
        is_htmx = request.headers.get("HX-Request") == "true"

        # Only intercept direct (non-HTMX) GET requests under /ui/
        if is_htmx or request.method != "GET" or not path.startswith("/ui/"):
            return await call_next(request)
        # Full-page routes already return a complete shell
        if path in _FULL_PAGE_ROUTES:
            return await call_next(request)

        response = await call_next(request)
        if response.status_code != 200:
            return response

        # Only wrap HTML responses
        ct = response.headers.get("content-type", "")
        if "text/html" not in ct:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        decoded = body.decode("utf-8")

        # If the route already returns a full page, pass through unchanged
        if decoded.lstrip().lower().startswith("<!"):
            return HTMLResponse(decoded, status_code=200,
                                headers=dict(response.headers))

        raw = request.cookies.get(LANG_COOKIE, DEFAULT_LANG)
        lang = raw if raw in LANGS else DEFAULT_LANG
        _lang.set(lang)

        m = _UI_SECTION_RE.match(path)
        section = m.group(1) if m else ""
        active_nav = _SECTION_NAV.get(section, section)

        full_html = app_shell(lang, decoded, active_nav=active_nav,
                              is_super=is_super_admin(request))
        return HTMLResponse(full_html, status_code=200)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """No connections or writes on boot — schema/seed live in migrations and CLI."""
    yield


def create_app() -> FastAPI:
    """Build the HTTP app: health probe, webhook router, admin dashboard."""
    app = FastAPI(title="stepan2", lifespan=_lifespan)
    app.add_middleware(_PartialShellMiddleware)
    app.add_middleware(AuthMiddleware)  # added last → outermost → runs first

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/ui/inbox", status_code=302)

    @app.get("/admin", include_in_schema=False)
    async def admin_root() -> RedirectResponse:
        return RedirectResponse(url="/ui/inbox", status_code=302)

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        """Liveness probe — no dependencies touched."""
        return {"ok": True, "service": "stepan2"}

    app.include_router(auth_router)
    app.include_router(webhooks_router)
    app.include_router(admin_meta_router)
    app.include_router(ui_router)
    _try_mount_admin(app)
    return app


def _try_mount_admin(app: FastAPI) -> None:
    """Mount SQLAdmin, but never let a missing/broken engine take down the app."""
    try:
        mount_admin(app, engine())
    except Exception:
        logger.exception("admin dashboard not mounted; continuing without it")


app = create_app()
