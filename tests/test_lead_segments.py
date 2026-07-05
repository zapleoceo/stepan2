"""Lead-type segment (Phase 1): decision parse, persistence, the reports distribution query
and its widget. Classification only — no routing behaviour change yet."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from datetime import UTC, datetime  # noqa: E402

from app.adapters.db.models import Branch, Lead  # noqa: E402
from app.api._query import fetch_segment_dist  # noqa: E402
from app.modules.conversation.decision import parse_decision  # noqa: E402
from app.modules.conversation.prompt import _DECISION_CONTRACT  # noqa: E402

_NOW = datetime.now(UTC).replace(tzinfo=None)


def test_decision_parses_valid_lead_type() -> None:
    d = parse_decision('{"reply":"hi","stage":"qualifying","lead_type":"no_budget"}')
    assert d.lead_type == "no_budget"


def test_decision_rejects_unknown_lead_type() -> None:
    d = parse_decision('{"reply":"hi","stage":"qualifying","lead_type":"vip"}')
    assert d.lead_type is None


def test_decision_parses_audience_independent_of_lead_type() -> None:
    d = parse_decision(
        '{"reply":"hi","stage":"qualifying","lead_type":"hot","audience":"student"}')
    assert d.lead_type == "hot"      # a student can still be hot
    assert d.audience == "student"


def test_decision_remaps_legacy_student_lead_type_to_audience() -> None:
    # Old/cached contract emitted student as a lead_type — it must land on the audience axis.
    d = parse_decision('{"reply":"hi","stage":"qualifying","lead_type":"student"}')
    assert d.lead_type is None
    assert d.audience == "student"


def test_prompt_contract_has_both_axes() -> None:
    assert "LEAD TYPE" in _DECISION_CONTRACT
    assert '"lead_type"' in _DECISION_CONTRACT
    assert "AUDIENCE" in _DECISION_CONTRACT
    assert '"audience"' in _DECISION_CONTRACT


async def test_segment_dist_groups_by_audience_and_intent(db_session) -> None:
    branch = Branch(name="T", lang="id")
    db_session.add(branch)
    await db_session.flush()
    bid = branch.id
    # adults: 2 warm (1 won); students: 1 hot (won), 1 cold; 1 unclassified adult (NULLs)
    db_session.add(Lead(branch_id=bid, audience="adult", lead_type="warm",
                        stage="ready", created_at=_NOW))
    db_session.add(Lead(branch_id=bid, audience="adult", lead_type="warm",
                        stage="qualifying", created_at=_NOW))
    db_session.add(Lead(branch_id=bid, audience="student", lead_type="hot",
                        stage="ready", created_at=_NOW))
    db_session.add(Lead(branch_id=bid, audience="student", lead_type="cold",
                        stage="new", created_at=_NOW))
    db_session.add(Lead(branch_id=bid, audience=None, lead_type=None,
                        stage="new", created_at=_NOW))
    await db_session.flush()
    rows = {(str(r[0]), str(r[1])): (int(r[2]), int(r[3] or 0))
            for r in await fetch_segment_dist(db_session, [bid])}
    assert rows[("adult", "warm")] == (2, 1)      # 2 warm adults, 1 won
    assert rows[("student", "hot")] == (1, 1)     # a hot student — intent kept, not hidden
    assert rows[("student", "cold")] == (1, 0)
    assert rows[("adult", "unclear")] == (1, 0)   # NULLs → adult / unclear


async def test_segment_dist_row_shape_total_at_index_2(db_session) -> None:
    """Consumers (reports_panel total_leads) index the row as (audience, lead_type, total,
    won) — total lives at [2]. Guards the regression where the 3→4-tuple shift made
    sum(int(s[1])) try to int() a lead_type string."""
    branch = Branch(name="T", lang="id")
    db_session.add(branch)
    await db_session.flush()
    db_session.add(Lead(branch_id=branch.id, audience="adult", lead_type="warm",
                        stage="new", created_at=_NOW))
    db_session.add(Lead(branch_id=branch.id, audience="student", lead_type="hot",
                        stage="new", created_at=_NOW))
    await db_session.flush()
    rows = await fetch_segment_dist(db_session, [branch.id])
    assert all(len(r) == 4 for r in rows)
    assert all(isinstance(r[0], str) and isinstance(r[1], str) for r in rows)  # aud, seg
    assert sum(int(r[2]) for r in rows) == 2  # total_leads — the reports_panel expression


def test_segment_widget_renders_audience_subtrees() -> None:
    from app.api._i18n import _lang
    from app.api._ui_panels import reports_panel_html
    _lang.set("en")
    html = reports_panel_html(
        {"new": 3}, {}, {}, [], None,
        segments=[("adult", "warm", 10, 2), ("student", "hot", 4, 2),
                  ("adult", "unclear", 20, 0)])
    assert "seg-tree" in html
    assert "Lead segments" in html
    assert "Adults" in html and "Students" in html     # both audience roots shown
    assert "won 20%" in html                           # adult warm: 2/10
    assert "won 50%" in html                            # student hot: 2/4
    assert "/ui/inbox?lead_type=hot" in html
