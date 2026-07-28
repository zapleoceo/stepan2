"""Every timestamp on /ui is shown in the VIEWING ADMIN's zone, never the server's.

The mechanism has been in place a while — a `tzoff` cookie the shell writes, a contextvar the
router pins per request, and _fmt_time/_fmt_dt_short applying it. What was missing is anything
stopping a new panel from calling .strftime() on a raw value, which silently prints UTC. Three
had: the MCP tokens table, the ads-synced stamp and the persona edit date. Nobody noticed,
because 09:26 is a perfectly ordinary time of day — it just wasn't the right one, and the same
page was showing two clocks at once.

So the rule is enforced here rather than remembered: outside _ui_html.py, a UI module formats a
timestamp through fmt_dt() or not at all.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_UI_DIR = Path(__file__).resolve().parent.parent / "app" / "api"
# _ui_html.py owns the formatters themselves. _ui_panels.py keeps ONE deliberate exception:
# the Reports "activity by hour" histogram is labelled in BRANCH-local time on purpose — the
# question it answers is "when do leads in Jakarta write", which is not about the viewer.
_ALLOWED = {"_ui_html.py"}
_BRANCH_LOCAL_MARKER = "start_dt + tz"

# A .strftime() call on a value, or an f-string interpolating one ({x:%d.%m}). Both print
# whatever zone the value is already in — which, straight out of the database, is UTC.
_RAW_FORMAT = re.compile(r"\.strftime\(|\{[^{}]+:%[-\w]")


def _offending_lines(path: Path) -> list[str]:
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not _RAW_FORMAT.search(line):
            continue
        if _BRANCH_LOCAL_MARKER in line:
            continue
        if line.lstrip().startswith("#"):
            continue
        out.append(f"{path.name}:{i}: {line.strip()}")
    return out


def test_no_ui_module_formats_a_timestamp_without_the_viewer_offset() -> None:
    offenders: list[str] = []
    for path in sorted(_UI_DIR.glob("_ui_*.py")):
        if path.name in _ALLOWED:
            continue
        offenders.extend(_offending_lines(path))
    assert not offenders, (
        "format timestamps with _ui_html.fmt_dt() — a raw .strftime prints server time:\n"
        + "\n".join(offenders))


def test_fmt_dt_applies_the_viewer_offset() -> None:
    from app.api._ui_html import fmt_dt, set_render_tz

    utc = datetime(2026, 7, 27, 2, 26, 48)
    set_render_tz(7)  # Jakarta — the live case: 02:26 UTC is 09:26 to the admin reading it
    assert fmt_dt(utc, "%d.%m %H:%M") == "27.07 09:26"
    set_render_tz(-5)
    assert fmt_dt(utc, "%d.%m %H:%M") == "26.07 21:26"
    set_render_tz(0)
    assert fmt_dt(utc, "%d.%m %H:%M") == "27.07 02:26"


def test_fmt_dt_survives_the_shapes_a_panel_actually_receives() -> None:
    """Raw SQL hands back a datetime on Postgres and an ISO string on SQLite; a missing value
    must render as the caller's placeholder, not crash the panel."""
    from app.api._ui_html import fmt_dt, set_render_tz

    set_render_tz(7)
    assert fmt_dt("2026-07-27T02:26:48", "%H:%M") == "09:26"
    assert fmt_dt(None, "%H:%M") == ""
    assert fmt_dt(None, "%H:%M", empty="—") == "—"
    assert fmt_dt("not a date", "%H:%M", empty="—") == "—"


def test_the_reports_bridge_survives_the_linter() -> None:
    """_ui_panels re-exports the reports names so the 2026-07-28 move changed no call site.
    Those imports look unused to a linter, and `ruff --fix` deleted them once already — which
    broke 21 test files at collection time. The noqa is load-bearing; this is what says so."""
    from app.api import _ui_panels

    for name in ("reports_panel_html", "admap_cell_inner", "broker_log_panel_html",
                 "_ad_tree_html", "_funnel_flow_html", "_date_range_form_html"):
        assert hasattr(_ui_panels, name), name


def test_the_stage_order_is_defined_once() -> None:
    """_ui_panels carried its own copy of the funnel order, identical to _ui_html's and
    required to stay that way — two tuples that must agree is one tuple with extra steps, and
    on 2026-07-25 both had to be edited by hand for the same change."""
    from app.api import _ui_panels
    from app.api._ui_html import _STAGES

    assert not hasattr(_ui_panels, "_ALL_STAGES")
    assert _STAGES[0] == "new" and _STAGES[4] == "handed_off"
