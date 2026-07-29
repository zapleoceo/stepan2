"""CRM close timestamps must reach the database as naive UTC.

Live failure on 2026-07-29, branch 1: the CRM returns an offset
("2025-11-12T10:39:44+07:00"), parse_won_at handed that tz-aware value straight to
crm_lead_state.deal_won_at, and asyncpg refused it — every column here is TIMESTAMP WITHOUT
TIME ZONE. sync_outcomes catches, logs and moves on, so won deals simply never landed: the CRM
read worked, the write did not, and nothing said so beyond one line per lead per tick.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.modules.crm.gate import parse_won_at
from app.modules.crm.pull import _ours


class _State:
    def __init__(self, won_at: object) -> None:
        self.won_at = won_at


class _Lead:
    def __init__(self, created_at: datetime | None) -> None:
        self.created_at = created_at


def test_offset_timestamp_becomes_naive_utc() -> None:
    at = parse_won_at("2025-11-12T10:39:44+07:00")
    assert at is not None
    assert at.tzinfo is None, "asyncpg rejects tz-aware values for TIMESTAMP WITHOUT TIME ZONE"
    assert at == datetime(2025, 11, 12, 3, 39, 44)  # +07:00 shifted to UTC


def test_timestamp_without_offset_is_read_as_utc() -> None:
    at = parse_won_at("2025-11-12T10:39:44")
    assert at == datetime(2025, 11, 12, 10, 39, 44)
    assert at.tzinfo is None


def test_missing_or_unparseable_degrades_to_none() -> None:
    """A sale with no timestamp is still a sale — this must never raise."""
    assert parse_won_at(None) is None
    assert parse_won_at("") is None
    assert parse_won_at("not a date") is None


def test_attribution_compares_naive_against_naive() -> None:
    """The other caller compares won_at with lead.created_at. Mixing awareness raises
    TypeError, which would have replaced one silent failure with another."""
    lead = _Lead(datetime(2025, 11, 1, 0, 0, 0))
    assert _ours(_State("2025-11-12T10:39:44+07:00"), lead) is True
    assert _ours(_State("2025-10-01T10:39:44+07:00"), lead) is False


def test_attribution_survives_a_legacy_aware_created_at() -> None:
    lead = _Lead(datetime(2025, 11, 1, 0, 0, 0, tzinfo=UTC))
    assert _ours(_State("2025-11-12T10:39:44+07:00"), lead) is True


def test_unknown_date_counts_as_ours() -> None:
    """Under-reporting revenue is the more expensive mistake — pinned by the docstring."""
    assert _ours(_State(None), _Lead(datetime(2025, 11, 1))) is True
