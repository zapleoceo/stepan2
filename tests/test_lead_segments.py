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


def test_prompt_contract_has_lead_type() -> None:
    assert "LEAD TYPE" in _DECISION_CONTRACT
    assert '"lead_type"' in _DECISION_CONTRACT


async def test_segment_dist_groups_and_buckets_null(db_session) -> None:
    branch = Branch(name="T", lang="id")
    db_session.add(branch)
    await db_session.flush()
    bid = branch.id
    # 2 warm (1 won via 'ready'), 1 student, 1 unclassified (NULL → 'unclear')
    db_session.add(Lead(branch_id=bid, lead_type="warm", stage="ready", created_at=_NOW))
    db_session.add(Lead(branch_id=bid, lead_type="warm", stage="qualifying", created_at=_NOW))
    db_session.add(Lead(branch_id=bid, lead_type="student", stage="dormant", created_at=_NOW))
    db_session.add(Lead(branch_id=bid, lead_type=None, stage="new", created_at=_NOW))
    await db_session.flush()
    rows = {r[0]: (int(r[1]), int(r[2] or 0)) for r in await fetch_segment_dist(db_session, [bid])}
    assert rows["warm"] == (2, 1)     # 2 warm, 1 of them won
    assert rows["student"] == (1, 0)
    assert rows["unclear"] == (1, 0)  # NULL bucketed as 'unclear'


def test_segment_widget_renders() -> None:
    from app.api._i18n import _lang
    from app.api._ui_panels import reports_panel_html
    _lang.set("en")
    html = reports_panel_html(
        {"new": 3}, {}, {}, [], None,
        segments=[("warm", 10, 2), ("student", 6, 0), ("unclear", 20, 0)])
    assert "seg-tree" in html
    assert "Lead segments" in html
    assert "won 20%" in html  # warm: 2/10
    assert "/ui/inbox?lead_type=warm" in html  # leaf links to that segment's chats
