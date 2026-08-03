"""FastAPI app factory. Lifespan does no DB I/O on startup; the admin dashboard is
mounted lazily and guarded so the app (and /healthz) stays up even without a live DB."""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from app.adapters.db.session import engine
from app.admin.api import router as admin_meta_router
from app.admin.setup import mount_admin
from app.api._auth import AdminGuardMiddleware, AuthMiddleware, WriteGuardMiddleware
from app.api._routes_auth import router as auth_router
from app.api._routes_mcp import router as mcp_router
from app.api.mcp_reader import mcp as mcp_reader
from app.api.mcp_reader import reader_app
from app.api.mcp_remote import connector_app
from app.api.mcp_remote import mcp as mcp_connector
from app.api.ui import router as ui_router
from app.api.webhooks import router as webhooks_router
from app.config import settings

logger = logging.getLogger(__name__)

# Full-page routes that already return a complete shell — never re-wrap these.
_FULL_PAGE_ROUTES = frozenset({"/ui/inbox", "/ui/coach", "/ui/knowledge"})

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
    """No DB I/O on boot — schema/seed live in migrations. Each mounted Streamable-HTTP
    MCP (write connector + read-only reader) needs its session-manager task group running
    for the app's lifetime."""
    async with mcp_connector.session_manager.run(), mcp_reader.session_manager.run():
        yield


def _mute_transport_loggers() -> None:
    """httpx logs every request at INFO as a full URL — query string included.

    Meta's OAuth endpoints take the app secret and the access tokens as QUERY parameters, so a
    single INFO line writes `client_secret=…` and a live token into the container log in clear
    text (observed 2026-08-03, right after the first connect through the flow). The worker
    muted these loggers long ago; the API never did. Anyone who can read a log — us, a log
    shipper, a support dump — reads the credentials.
    """
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def create_app() -> FastAPI:
    """Build the HTTP app: health probe, webhook router, admin dashboard."""
    settings().validate_runtime()  # fail-fast on broken config before serving a request
    _mute_transport_loggers()
    app = FastAPI(title="stepan2", lifespan=_lifespan)
    app.add_middleware(_PartialShellMiddleware)
    app.add_middleware(WriteGuardMiddleware)  # branch_viewer = read-only (no /ui writes)
    app.add_middleware(AdminGuardMiddleware)  # gate /admin before it reaches SQLAdmin
    app.add_middleware(AuthMiddleware)  # added last → outermost → runs first

    @app.get("/", include_in_schema=False, response_class=HTMLResponse)
    async def root() -> HTMLResponse:
        # Public product landing (login lives top-right on it → /login → the app).
        from app.api._landing import landing_html  # noqa: PLC0415
        return HTMLResponse(landing_html())

    # Public legal pages. Meta App Review fetches all three anonymously, so they stay outside
    # auth and outside the DB — see app/api/_legal.py.
    @app.get("/privacy", include_in_schema=False, response_class=HTMLResponse)
    async def privacy() -> HTMLResponse:
        from app.api._legal import privacy_html  # noqa: PLC0415
        return HTMLResponse(privacy_html())

    @app.get("/terms", include_in_schema=False, response_class=HTMLResponse)
    async def terms() -> HTMLResponse:
        from app.api._legal import terms_html  # noqa: PLC0415
        return HTMLResponse(terms_html())

    @app.get("/data-deletion", include_in_schema=False, response_class=HTMLResponse)
    async def data_deletion() -> HTMLResponse:
        from app.api._legal import data_deletion_html  # noqa: PLC0415
        return HTMLResponse(data_deletion_html())

    @app.get("/llms.txt", include_in_schema=False, response_class=PlainTextResponse)
    async def llms_txt_route() -> PlainTextResponse:
        # The de-facto convention for telling answer engines what this product is, in the
        # plain-fact form they quote well. See app/api/_seo.py.
        from app.api._seo import llms_txt  # noqa: PLC0415
        return PlainTextResponse(llms_txt())

    @app.get("/hiw", include_in_schema=False, response_class=HTMLResponse)
    async def how_it_works(lang: str = "en") -> HTMLResponse:
        # Internal "how it works" map for the team, in English (default) or
        # Ukrainian (?lang=uk). NOT in the public allowlist (app/api/_auth.py)
        # → requires a session when auth is enabled.
        from app.api._hiw import hiw_html  # noqa: PLC0415
        return HTMLResponse(hiw_html(lang))

    @app.get("/robots.txt", include_in_schema=False, response_class=PlainTextResponse)
    async def robots() -> PlainTextResponse:
        from app.api._seo import robots_txt  # noqa: PLC0415
        return PlainTextResponse(robots_txt(), media_type="text/plain")

    @app.get("/sitemap.xml", include_in_schema=False)
    async def sitemap() -> Response:
        from app.api._seo import sitemap_xml  # noqa: PLC0415
        return Response(sitemap_xml(), media_type="application/xml")

    @app.get("/og.svg", include_in_schema=False)
    async def og_image() -> Response:
        from app.api._seo import og_svg  # noqa: PLC0415
        return Response(og_svg(), media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/og.png", include_in_schema=False)
    async def og_image_png() -> Response:
        # Messengers (Telegram/WhatsApp/iMessage) don't render an SVG og:image — the share
        # preview needs a raster. Rendered once per process, cached a day at the edge.
        from app.api._og import og_png  # noqa: PLC0415
        return Response(og_png(), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/admin", include_in_schema=False)
    async def admin_root() -> RedirectResponse:
        return RedirectResponse(url="/ui/inbox", status_code=302)

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        """Liveness probe — no dependencies touched."""
        return {"ok": True, "service": "stepan2"}

    app.include_router(auth_router)
    from app.api._routes_demo import router as demo_router  # noqa: PLC0415
    app.include_router(demo_router)  # public landing demo chat (/demo/chat)
    from app.api._routes_connect import router as connect_router  # noqa: PLC0415
    app.include_router(connect_router)  # public client connect flow (/connect/meta/...)
    app.include_router(mcp_router)
    app.mount("/connector", connector_app())  # remote write MCP (funnel ops, web clients)
    app.mount("/reader", reader_app())         # read-only MCP (dialogs + analysis)
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
