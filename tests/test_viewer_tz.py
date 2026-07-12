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


def test_apply_viewer_tz_dependency_pins_contextvar() -> None:
    uh.set_render_tz(0)
    _apply_viewer_tz(_Req("7"))                             # dependency reads cookie → sets tz
    dt = datetime(2026, 7, 12, 20, 0, 0)
    assert uh._fmt_time(dt) == "13.07 03:00:00"             # +7 rolls past midnight


def test_shell_emits_browser_tz_capture_script() -> None:
    from app.api._i18n import _lang
    _lang.set("en")
    html = uh.app_shell("en", "<div>m</div>", active_nav="inbox")
    assert "tzoff" in html
    assert "getTimezoneOffset" in html                      # browser reports its own offset
