"""Headline KPI tiles: event-in-window only, each with its own per-day sparkline.

The panel used to mix event counts with cohort reads ("of the leads that arrived, how many
are NOW in stage X"). Two tiles could both be right and still disagree, so the cohort ones
are gone and the snapshot lives in the funnel line instead.
"""
from __future__ import annotations

from datetime import datetime

from app.adapters.db.models import (
    Branch,
    Channel,
    ChannelThread,
    CrmLeadState,
    Lead,
    StageEvent,
)
from app.api._i18n import _lang, t
from app.api._query import (
    fetch_daily_kpis,
    fetch_deals_count,
    fetch_event_bookings_count,
)
from app.api._ui_panels import _sparkline, reports_panel_html
from app.domain.enums import ChannelKind, Stage


def test_sparkline_fills_missing_days_so_the_axis_stays_calendar_true() -> None:
    html = _sparkline("2026-07-01:4,2026-07-03:2", "#fff")
    assert html.count("<i ") == 3            # the quiet 2nd is drawn, not skipped
    assert 'title="2026-07-02: 0"' in html
    assert "height:100%" in html             # the 4 is the peak


def test_sparkline_needs_more_than_one_day() -> None:
    assert _sparkline("2026-07-01:4", "#fff") == ""
    assert _sparkline("", "#fff") == ""


def test_panel_drops_the_cohort_tiles_and_keeps_the_event_ones() -> None:
    _lang.set("ru")
    html = reports_panel_html(
        stage_counts={"new": 3, "qualifying": 2, "ready": 1, "dormant": 9},
        hour_in={9: 4}, hour_out={9: 5},
        closed_in_period=7, deals=2,
        daily_kpis={"leads": {"2026-07-01": 2, "2026-07-02": 1},
                    "handoff": {}, "deal": {}, "dormant": {"2026-07-02": 4},
                    "messages": {}},
    )
    assert t("rep.deal") in html
    assert t("rep.closed_period") in html
    assert t("rep.dormant_period") in html
    assert t("rep.pipeline") not in html      # cohort snapshot — the funnel line owns it
    assert "kpi-spark" in html
    # 'Спящие' is the EVENT count from the series (4), never the 9 sitting in that stage.
    assert ">4</div>" in html
    assert ">9</div>" not in html


async def test_daily_kpis_are_event_dated_not_cohort_dated(db_session) -> None:
    """A lead that arrived long before the window still counts on the day it converted."""
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    db_session.add(ch)
    await db_session.flush()
    old = Lead(branch_id=b.id, stage=Stage.HANDED_OFF, created_at=datetime(2026, 1, 1))
    db_session.add(old)
    await db_session.flush()
    db_session.add_all([
        ChannelThread(lead_id=old.id, channel_id=ch.id, external_thread_id="ig-1",
                      ad_id="ad-1"),
        StageEvent(branch_id=b.id, lead_id=old.id, thread_id=None, from_stage="presenting",
                   to_stage="handed_off", actor="bot", created_at=datetime(2026, 7, 2)),
        CrmLeadState(branch_id=b.id, lead_id=old.id, deal_won=True,
                     deal_won_at=datetime(2026, 7, 3)),
    ])
    await db_session.flush()

    daily = await fetch_daily_kpis(
        db_session, [b.id], since=datetime(2026, 7, 1), until=datetime(2026, 8, 1))
    assert daily["handoff"] == {"2026-07-02": 1}
    assert daily["deal"] == {"2026-07-03": 1}
    assert daily["leads"] == {}   # the lead itself arrived in January


async def test_deals_count_is_dated_by_the_close_not_by_the_lead(db_session) -> None:
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    lead = Lead(branch_id=b.id, stage=Stage.HANDED_OFF, created_at=datetime(2026, 1, 1))
    db_session.add(lead)
    await db_session.flush()
    db_session.add(CrmLeadState(branch_id=b.id, lead_id=lead.id, deal_won=True,
                                deal_won_at=datetime(2026, 7, 3)))
    await db_session.flush()

    inside = await fetch_deals_count(
        db_session, [b.id], since=datetime(2026, 7, 1), until=datetime(2026, 8, 1))
    outside = await fetch_deals_count(
        db_session, [b.id], since=datetime(2026, 8, 1), until=datetime(2026, 9, 1))
    assert (inside, outside) == (1, 0)


async def test_undated_win_counts_only_on_the_all_time_range(db_session) -> None:
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    lead = Lead(branch_id=b.id, stage=Stage.HANDED_OFF)
    db_session.add(lead)
    await db_session.flush()
    db_session.add(CrmLeadState(branch_id=b.id, lead_id=lead.id, deal_won=True,
                                deal_won_at=None))
    await db_session.flush()

    assert await fetch_deals_count(db_session, [b.id]) == 1
    assert await fetch_deals_count(
        db_session, [b.id], since=datetime(2026, 7, 1), until=datetime(2026, 8, 1)) == 0


async def test_bookings_are_dated_by_the_signup_not_by_the_event(db_session) -> None:
    """The event is one date; signing up for it is another, weeks earlier.

    Two clients booked onto the 08/08 demo signed up on 30 July and 4 August. The first cut
    stored only the event's date, so the tile counted both bookings in every window — they
    showed up under "last hour". Caught by the owner asking why."""
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    lead = Lead(branch_id=b.id, stage=Stage.HANDED_OFF, created_at=datetime(2026, 7, 1))
    db_session.add(lead)
    await db_session.flush()
    db_session.add(CrmLeadState(
        branch_id=b.id, lead_id=lead.id, event_name="VIBE CODING DEMO 08/08/2026",
        event_at=datetime(2026, 8, 8), event_booked_at=datetime(2026, 7, 30)))
    await db_session.flush()

    inside = await fetch_event_bookings_count(
        db_session, [b.id], since=datetime(2026, 7, 29), until=datetime(2026, 7, 31))
    assert inside == 1
    # The window the EVENT falls in, but not the sign-up: counting here is the bug.
    outside = await fetch_event_bookings_count(
        db_session, [b.id], since=datetime(2026, 8, 7), until=datetime(2026, 8, 9))
    assert outside == 0


async def test_a_booking_made_before_our_first_message_is_not_ours(db_session) -> None:
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    lead = Lead(branch_id=b.id, stage=Stage.HANDED_OFF, created_at=datetime(2026, 7, 1))
    db_session.add(lead)
    await db_session.flush()
    db_session.add(CrmLeadState(
        branch_id=b.id, lead_id=lead.id, event_name="older",
        event_at=datetime(2026, 6, 1), event_booked_at=datetime(2026, 7, 30)))
    await db_session.flush()

    assert await fetch_event_bookings_count(db_session, [b.id]) == 0
