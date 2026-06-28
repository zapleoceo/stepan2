"""FastAPI app factory. Lifespan does no DB I/O on startup; the admin dashboard is
mounted lazily and guarded so the app (and /healthz) stays up even without a live DB."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.adapters.db.session import engine
from app.admin.setup import mount_admin
from app.api.webhooks import router as webhooks_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """No connections or writes on boot — schema/seed live in migrations and CLI."""
    yield


def create_app() -> FastAPI:
    """Build the HTTP app: health probe, webhook router, admin dashboard."""
    app = FastAPI(title="stepan2", lifespan=_lifespan)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/admin/", status_code=302)

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        """Liveness probe — no dependencies touched."""
        return {"ok": True, "service": "stepan2"}

    app.include_router(webhooks_router)
    _try_mount_admin(app)
    return app


def _try_mount_admin(app: FastAPI) -> None:
    """Mount SQLAdmin, but never let a missing/broken engine take down the app."""
    try:
        mount_admin(app, engine())
    except Exception:
        logger.exception("admin dashboard not mounted; continuing without it")


app = create_app()
