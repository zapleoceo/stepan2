"""Missions, and the account budget they have to share.

The bug these pin is live TODAY, before any new mission exists: DM sends count against
hourly_cap (150) in outbox.py, comment replies against comment_hourly_cap (20) in
comments/service.py, and the two counters do not know about each other. The account can emit
170 actions in an hour with both limits reported as respected — and Instagram counts actions
per account. It already hit a soft block twice on DM volume alone.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.connectors.registry import spec_for  # noqa: E402
from app.connectors.spec import Capability  # noqa: E402
from app.domain.enums import ChannelKind  # noqa: E402
from app.modules.missions import (  # noqa: E402
    ALL,
    COMMENT_REPLY,
    INBOUND_REPLY,
    Grounding,
    Initiative,
    Spend,
    mission,
    missions_for,
    share_of,
)


def test_shares_cannot_exceed_the_account_budget() -> None:
    """The whole point. Missions divide ONE account budget; if the shares sum past 1.0 the
    account is over the platform's limit while every mission believes it is within its own."""
    assert sum(m.budget_share for m in ALL) <= 1.0


def test_a_mission_only_runs_where_the_connector_can_carry_it() -> None:
    """The website cannot comment and cannot write first. That has to fall out of the
    declaration, not out of a branch-id check somewhere in a worker."""
    website = spec_for(ChannelKind.WEBSITE).capabilities
    keys = {m.key for m in missions_for(website)}
    assert "comment_reply" not in keys
    assert "inbound_reply" in keys  # answering a visitor is exactly what it does


def test_instagram_carries_both_reactive_missions() -> None:
    ig = spec_for(ChannelKind.INSTAGRAM).capabilities
    assert {m.key for m in missions_for(ig)} == {"inbound_reply", "comment_reply"}


def test_the_official_meta_connector_cannot_comment() -> None:
    """It declares no COMMENTS capability — the comment path lives on the unofficial API only,
    which is worth knowing before anyone plans a migration off it."""
    meta = spec_for(ChannelKind.META_BUSINESS).capabilities
    assert Capability.COMMENTS not in meta
    assert "comment_reply" not in {m.key for m in missions_for(meta)}


def test_public_work_is_held_to_a_stricter_standard_than_private() -> None:
    """A wrong price in a DM is a conversation to fix. Under a public post it is a screenshot."""
    assert COMMENT_REPLY.grounding is Grounding.STRICT
    assert INBOUND_REPLY.grounding is Grounding.NORMAL


def test_everything_registered_today_is_reactive() -> None:
    """Proactive missions stay out until the reactive pair runs under one budget. Tuning three
    counters at once, on the account carrying the whole funnel, is not a thing to do first."""
    assert all(m.initiative is Initiative.REACTIVE for m in ALL)


def test_a_spent_budget_gives_a_mission_nothing() -> None:
    assert share_of(Spend(used=150, cap=150), 0.75) == 0


def test_a_mission_never_gets_more_than_what_is_left() -> None:
    """Entitlement is not availability: a mission owed 40% of a nearly spent budget gets what
    remains. Handing out the full share here is how three missions put the account over."""
    spend = Spend(used=148, cap=150)
    assert share_of(spend, 0.75) <= spend.left == 2


def test_the_shares_of_a_fresh_budget_do_not_oversubscribe_it() -> None:
    spend = Spend(used=0, cap=100)
    total = sum(share_of(spend, m.budget_share) for m in ALL)
    assert total <= spend.cap


def test_shares_stay_within_budget_as_it_is_consumed() -> None:
    """Checked across the whole range, because the failure mode is arithmetic and shows up in
    the middle: each mission's claim is reduced by what it has already taken."""
    for used in range(0, 151, 10):
        spend = Spend(used=used, cap=150)
        assert sum(share_of(spend, m.budget_share) for m in ALL) <= spend.left


@pytest.mark.parametrize("bad", [-1.0, 2.0])
def test_a_nonsense_share_cannot_blow_the_budget(bad: float) -> None:
    """Clamped rather than trusted: a typo in a share must not become an unbounded send loop
    against a live account."""
    assert 0 <= share_of(Spend(used=0, cap=100), bad) <= 100


def test_an_unknown_mission_fails_loudly_and_says_what_exists() -> None:
    with pytest.raises(KeyError, match="inbound_reply"):
        mission("outreach_cold")  # not registered yet, and must not silently be None


def test_a_cap_of_zero_means_off_not_silence() -> None:
    """Every other cap in this project treats <= 0 as "limit off" — outbox._cap_reached and
    the settings schema both do. Reading it as "zero actions allowed" would mute a branch that
    had deliberately turned the ceiling off.

    Not hypothetical: the first version of this file did exactly that, and the comment-service
    tests (which build settings with hourly_cap=0) went red. On production every branch runs a
    positive cap, so it would have shipped quietly and stopped a branch the day someone
    cleared the field."""
    off = Spend(used=500, cap=0)
    assert not off.exhausted
    assert share_of(off, 0.15) > 0


def test_a_reached_cap_still_stops_everything() -> None:
    """The other half: off must mean off, and reached must mean reached."""
    reached = Spend(used=150, cap=150)
    assert reached.exhausted
    assert all(share_of(reached, m.budget_share) == 0 for m in ALL)
