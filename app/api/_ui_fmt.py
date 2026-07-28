"""Viewer-local time: the offset, and the formatters that apply it.

Split out of _ui_html.py on 2026-07-28. Four modules — panels, reports, the MCP page and
the persona editor — imported a date formatter from the module that draws the page shell,
the funnel and the chat feed, and none of that is needed to render a timestamp.

The offset travels with the formatters on purpose. The contextvar carries the viewing
admin's timezone and the formatters are the only thing that applies it; apart, the rule
would live in one file and its enforcement in another — which is how three panels came to
print server time while everything around them was local (test_ui_time.py).
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from ._i18n import t

_render_tz_h: ContextVar[float] = ContextVar("render_tz_h", default=0.0)

# Cookie the shell's inline JS writes with the browser's UTC offset in hours (e.g. "7", "5.5").
VIEWER_TZ_COOKIE = "tzoff"


def set_render_tz(offset_h: float) -> None:
    """Set the tz offset (hours) for timestamp rendering in this request/task."""
    try:
        _render_tz_h.set(float(offset_h or 0))
    except (TypeError, ValueError):
        _render_tz_h.set(0.0)


def viewer_tz_offset(request: object) -> float:
    """The viewing admin's own UTC offset in hours, from the `tzoff` cookie the shell sets;
    0 (UTC) until the browser has reported it. Bounded to a sane [-14, +14] range."""
    raw = ""
    cookies = getattr(request, "cookies", None)
    if isinstance(cookies, dict):
        raw = cookies.get(VIEWER_TZ_COOKIE, "") or ""
    try:
        return max(-14.0, min(14.0, float(raw)))
    except (TypeError, ValueError):
        return 0.0


def _ago(dt: datetime | None) -> str:
    if dt is None:
        return ""
    secs = max(0, int((datetime.now(UTC).replace(tzinfo=None) - dt).total_seconds()))
    if secs < 3600:
        return f"{secs // 60}{t('time.m')}"
    if secs < 86400:
        return f"{secs // 3600}{t('time.h')}"
    return f"{secs // 86400}{t('time.d')}"


def _as_dt(v: object) -> datetime | None:
    """Coerce a raw SQL value (datetime on Postgres, ISO str on SQLite) to naive datetime."""
    if v is None or isinstance(v, datetime):
        return v  # type: ignore[return-value]
    try:
        return datetime.fromisoformat(str(v).replace("Z", "")).replace(tzinfo=None)
    except ValueError:
        return None


def viewer_local(dt: datetime | None) -> datetime | None:
    """A stored (UTC) timestamp moved into the viewing admin's own zone.

    Every timestamp on /ui goes through here. Formatting a raw value with .strftime prints
    server time, which is UTC and therefore wrong for everyone — and wrong in a way nobody
    notices, because 09:26 is a perfectly plausible time of day. Three panels were doing it
    (MCP tokens, the ads-synced stamp, the persona edit date) while everything around them was
    already local, so the same page showed two different clocks."""
    return None if dt is None else dt + timedelta(hours=_render_tz_h.get())


def fmt_dt(dt: datetime | None, pattern: str, empty: str = "") -> str:
    """Viewer-local timestamp in an arbitrary pattern — the only sanctioned way for a UI module
    outside this file to format one. A test pins that rule (test_ui_time.py)."""
    local = viewer_local(_as_dt(dt))
    return local.strftime(pattern) if local is not None else empty


def _fmt_time(dt: datetime | None) -> str:
    """Viewer-local DD.MM HH:MM:SS — always includes the date, not just time-of-day, so a
    message/event timestamp is never ambiguous about which day it happened."""
    if dt is None:
        return ""
    local = dt + timedelta(hours=_render_tz_h.get())
    return local.strftime("%d.%m %H:%M:%S")


def _fmt_dt_short(dt: datetime | None) -> str:
    """Viewer-local DD.MM HH:MM (no seconds) — for the compact sidebar thread list, where
    an explicit last-message date/time replaces the old vague '2h ago' style label."""
    if dt is None:
        return ""
    local = dt + timedelta(hours=_render_tz_h.get())
    return local.strftime("%d.%m %H:%M")
