"""A lead already sitting in the manager's queue is not escalated a second time.

Thread 4044 is the case: `ready_openhouse` fired 15.07 17:19, two minutes after the lead
volunteered his phone, and the fifth follow-up cycle raised `needs_manager` for that same
twelve-day-old phone on 27.07, five seconds before the thread wound down to dormant. Two cards
for one silence. The dedup helper existed and was never called; when it was wired in it also had
to stop looking only at `needs_manager`, since the first alert came through a different door.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.db.models import (
    Branch,
    Channel,
    ChannelThread,
    Lead,
    ManagerAlert,
)
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation.followup import _ESCALATION_KINDS, FollowupService

_NOW = datetime.now(UTC).replace(tzinfo=None)
_LEAD_SPOKE = _NOW - timedelta(days=12)


async def _thread(s, *, alert_kind: str | None, alert_at: datetime | None = None) -> int:
    b = Branch(name="T", lang="id", tz_offset_h=7)
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    s.add(ch)
    await s.flush()
    lead = Lead(branch_id=b.id, stage=Stage.PRESENTING, phone_e164="+6285779285487")
    s.add(lead)
    await s.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-4044",
                       last_in_at=_LEAD_SPOKE)
    s.add(th)
    await s.flush()
    if alert_kind is not None:
        s.add(ManagerAlert(branch_id=b.id, lead_id=lead.id, thread_id=th.id, kind=alert_kind,
                           created_at=alert_at or _LEAD_SPOKE + timedelta(minutes=2)))
        await s.flush()
    return b.id, th.id


def _svc(session, branch_id: int) -> FollowupService:
    return FollowupService(session, branch_id, llm=None, knowledge=None, settings=None)


@pytest.mark.parametrize("kind", _ESCALATION_KINDS)
async def test_any_escalation_since_last_inbound_blocks_a_second(db_session, kind) -> None:
    branch_id, thread_id = await _thread(db_session, alert_kind=kind)
    assert await _svc(db_session, branch_id)._already_alerted_since_lead(thread_id)


async def test_no_alert_at_all_does_not_block(db_session) -> None:
    branch_id, thread_id = await _thread(db_session, alert_kind=None)
    assert not await _svc(db_session, branch_id)._already_alerted_since_lead(thread_id)


async def test_bot_off_message_is_not_an_escalation(db_session) -> None:
    """It reports that a muted thread received a message, not that the lead needs handling —
    letting it suppress would swallow the one alert that matters."""
    branch_id, thread_id = await _thread(db_session, alert_kind="bot_off_message")
    assert not await _svc(db_session, branch_id)._already_alerted_since_lead(thread_id)


async def test_alert_older_than_the_lead_s_last_message_does_not_block(db_session) -> None:
    """A new inbound is new information: whatever the manager was told before it is stale, so
    the next needs_human turn is entitled to raise a fresh card."""
    branch_id, thread_id = await _thread(
        db_session, alert_kind="ready_deal", alert_at=_LEAD_SPOKE - timedelta(hours=1))
    assert not await _svc(db_session, branch_id)._already_alerted_since_lead(thread_id)
