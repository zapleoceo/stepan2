"""Funnel-flow: the stage_event transition query and its SVG widget. Reconstructs each lead's
path first-message → transitions → exit, scoped like the rest of the reports panel."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from datetime import UTC, datetime  # noqa: E402

from app.adapters.db.models import Branch, Lead, StageEvent  # noqa: E402
from app.api._query import fetch_stage_flow, fetch_stage_reach  # noqa: E402

_NOW = datetime.now(UTC).replace(tzinfo=None)


async def test_stage_flow_groups_edges_and_scopes_branch(db_session) -> None:
    b1 = Branch(name="A", lang="id")
    b2 = Branch(name="B", lang="id")
    db_session.add(b1)
    db_session.add(b2)
    await db_session.flush()
    lead1 = Lead(branch_id=b1.id, stage="presenting", created_at=_NOW)
    lead1b = Lead(branch_id=b1.id, stage="presenting", created_at=_NOW)
    lead2 = Lead(branch_id=b2.id, stage="dormant", created_at=_NOW)
    db_session.add(lead1)
    db_session.add(lead1b)
    db_session.add(lead2)
    await db_session.flush()
    # branch 1: 2 distinct leads new→presenting (one logs it TWICE — must count as 1 lead), one
    # new→dormant ; branch 2: one new→dormant (excluded by branch scope)
    for frm, to, lid, bid in [
        ("new", "presenting", lead1.id, b1.id),
        ("new", "presenting", lead1.id, b1.id),   # duplicate event, same lead → still 1 distinct
        ("new", "presenting", lead1b.id, b1.id),
        ("new", "dormant", lead1.id, b1.id),
        ("new", "new", lead1.id, b1.id),          # no-op transition, must be filtered
        ("new", "dormant", lead2.id, b2.id),
    ]:
        db_session.add(StageEvent(branch_id=bid, lead_id=lid, from_stage=frm,
                                  to_stage=to, actor="bot", created_at=_NOW))
    await db_session.flush()
    rows = {(r[0], r[1]): int(r[2]) for r in await fetch_stage_flow(db_session, [b1.id])}
    assert rows[("new", "presenting")] == 2      # distinct leads, not 3 raw events
    assert rows[("new", "dormant")] == 1
    assert ("new", "new") not in rows            # self-edge filtered
    assert sum(1 for (f, _t) in rows if _t == "dormant") == 1  # branch 2 excluded
    # reach = distinct leads that touched each stage (≤ total leads), not summed edge counts
    reach = await fetch_stage_reach(db_session, [b1.id])
    assert reach["new"] == 2                      # 2 distinct leads passed through new (not 3)
    assert reach["presenting"] == 2
    assert reach["dormant"] == 1


def test_flow_widget_renders_and_falls_back() -> None:
    from app.api._i18n import _lang
    from app.api._ui_panels import _funnel_flow_html
    _lang.set("en")
    flow = [("new", "presenting", 90), ("new", "dormant", 40), ("presenting", "qualifying", 20)]
    html = _funnel_flow_html(flow)
    assert "<svg" in html
    assert html.count("<path") == 3           # one link per edge
    assert _funnel_flow_html([]) == ""        # empty → caller falls back to line funnel
