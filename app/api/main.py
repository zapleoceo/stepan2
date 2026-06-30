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
from app.api.ui import router as ui_router
from app.api.webhooks import router as webhooks_router

logger = logging.getLogger(__name__)

# Matches /ui/<section>/panel but NOT /ui/chat/<tid>/panel (handled separately)
_PANEL_RE = re.compile(r"^/ui/([^/]+)/panel$")


class _PartialShellMiddleware(BaseHTTPMiddleware):
    """Wrap panel fragments in the full app shell on direct (non-HTMX) browser load.

    HTMX navigation pushes partial URLs like /ui/settings/panel to the address
    bar.  On page refresh the browser requests that URL directly — the route
    returns only a fragment, which renders without CSS or JS.  This middleware
    intercepts such requests (no HX-Request header) and wraps the fragment in
    the full app_shell(), giving the user a proper full page."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        from app.api._i18n import DEFAULT_LANG, LANG_COOKIE, LANGS, _lang  # noqa: PLC0415
        from app.api._ui_html import app_shell  # noqa: PLC0415

        is_htmx = request.headers.get("HX-Request") == "true"
        path = request.url.path
        m = _PANEL_RE.match(path)

        if not is_htmx and m:
            section = m.group(1)
            # chat/{tid}/panel is a different shape; skip (handled by chat route)
            if section == "chat":
                return await call_next(request)

            response = await call_next(request)
            if response.status_code != 200:
                return response

            raw = request.cookies.get(LANG_COOKIE, DEFAULT_LANG)
            lang = raw if raw in LANGS else DEFAULT_LANG
            _lang.set(lang)

            body = b""
            async for chunk in response.body_iterator:
                body += chunk

            # Map URL section to sidebar nav_id
            _NAV = {
                "knowledge": "know",
                "coach": "coach",
            }
            active_nav = _NAV.get(section, section)
            full_html = app_shell(lang, body.decode("utf-8"), active_nav=active_nav)
            return HTMLResponse(full_html, status_code=200)

        return await call_next(request)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """No connections or writes on boot — schema/seed live in migrations and CLI."""
    yield


def create_app() -> FastAPI:
    """Build the HTTP app: health probe, webhook router, admin dashboard."""
    app = FastAPI(title="stepan2", lifespan=_lifespan)
    app.add_middleware(_PartialShellMiddleware)

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
