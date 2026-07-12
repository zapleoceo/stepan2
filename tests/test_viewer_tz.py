"""Viewer-local timestamp rendering: timestamps show in the ADMIN's own tz (from the browser
`tzoff` cookie), not the branch's. Covers the offset parse, the render shift, and the router
dependency that pins the contextvar per request."""
from __future__ import annotations

import os
from datetime import datetime

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.api import _ui_html as uh  # noqa: E402
from app.api.ui import _apply_viewer_tz  # noqa: E402


class _Req:
    def __init__(self, tzoff: str | None) -> None:
        self.cookies = {} if tzoff is None else {"tzoff": tzoff}


def test_viewer_tz_offset_parses_bounds_and_junk() -> None:
    assert uh.viewer_tz_offset(_Req("7")) == 7.0
    assert uh.viewer_tz_offset(_Req("5.5")) == 5.5          # fractional zones (+5:30)
    assert uh.viewer_tz_offset(_Req("-3")) == -3.0
    assert uh.viewer_tz_offset(_Req("99")) == 14.0          # clamped to sane max
    assert uh.viewer_tz_offset(_Req("-99")) == -14.0
    assert uh.viewer_tz_offset(_Req("garbage")) == 0.0      # unparseable → UTC
    assert uh.viewer_tz_offset(_Req(None)) == 0.0           # no cookie yet → UTC


def test_fmt_time_shifts_by_render_tz() -> None:
    dt = datetime(2026, 7, 12, 0, 30, 0)  # stored UTC
    uh.set_render_tz(7)
    assert uh._fmt_time(dt) == "12.07 07:30:00"             # +7 → Jakarta
    uh.set_render_tz(5.5)
    assert uh._fmt_time(dt) == "12.07 06:00:00"             # +5:30 fractional
    uh.set_render_tz(0)
    assert uh._fmt_time(dt) == "12.07 00:30:00"             # UTC


def test_router_dependency_propagates_tz_via_real_request() -> None:
    """Guards the sync-vs-async trap: a SYNC dependency runs in a threadpool, so the contextvar
    it sets never reaches the (event-loop) endpoint and every timestamp silently renders in
    UTC. Driving it through a real request proves the async dependency propagates in-task."""
    from fastapi import APIRouter, Depends, FastAPI
    from fastapi.testclient import TestClient

    r = APIRouter(dependencies=[Depends(_apply_viewer_tz)])

    @r.get("/probe")
    async def _probe() -> dict:
        return {"tz": uh._render_tz_h.get()}

    app2 = FastAPI()
    app2.include_router(r)
    c = TestClient(app2)
    assert c.get("/probe", headers={"Cookie": "tzoff=7"}).json()["tz"] == 7.0
    assert c.get("/probe", headers={"Cookie": "tzoff=3.5"}).json()["tz"] == 3.5
    assert c.get("/probe").json()["tz"] == 0.0             # no cookie → UTC


def test_shell_emits_browser_tz_capture_script() -> None:
    from app.api._i18n import _lang
    _lang.set("en")
    html = uh.app_shell("en", "<div>m</div>", active_nav="inbox")
    assert "tzoff" in html
    assert "getTimezoneOffset" in html                      # browser reports its own offset
