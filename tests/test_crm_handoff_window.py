"""Семидневное окно подметателя хендоффов не должно молчать о том, что выбрасывает.

29.07.2026 в филиале 1 нашлись три лида (3085, 2524, 2676) в стадиях ready/manager, с
телефонами, эскалированные 13-16 июля и без единого маркера передачи. Окно их не берёт, и
взять уже некому — но очередь при этом показывала «ждут отправки: 2», то есть выглядела так,
будто всё разобрано. Ограничение, которое отбрасывает работу, обязано быть видимым.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, StageEvent
from app.domain.enums import ChannelKind
from app.modules.crm.push_mcp import (
    HANDOFF_WINDOW_DAYS,
    PUSHED_HANDOFF_REASON,
    VERIFIED_PRESENT_REASON,
    _log_window_drops,
    fetch_unpushed_handoffs,
)

_NOW = datetime.now(UTC).replace(tzinfo=None)


async def _lead(session, branch_id: int, channel_id: int, *, phone: str,
                escalated_at: datetime) -> int:
    lead = Lead(branch_id=branch_id, stage="manager", phone_e164=phone)
    session.add(lead)
    await session.flush()
    session.add(ChannelThread(          # у channel_thread нет branch_id — филиал берётся с лида
        channel_id=channel_id, lead_id=lead.id,
        external_thread_id=f"t{lead.id}", last_in_at=escalated_at))
    session.add(StageEvent(
        branch_id=branch_id, lead_id=lead.id, from_stage="qualifying", to_stage="manager",
        actor="bot", reason="needs_manager", created_at=escalated_at))
    await session.flush()
    return lead.id


async def _fixture(session):  # noqa: ANN001, ANN201
    branch = Branch(name="b", lang="id", tz_offset_h=7)
    session.add(branch)
    await session.flush()
    channel = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM, is_active=True)
    session.add(channel)
    await session.flush()
    return branch.id, channel.id


async def test_a_bookkeeping_row_does_not_reopen_the_window(db_session) -> None:
    """29.07.2026: stamping 23 aged-out leads as reconciled wrote a StageEvent per lead with
    from_stage == to_stage == 'ready'. The window probe only looked at to_stage and a date, so
    every one of them read as a brand-new hand-off: the queue went from 2 to 27, and the next
    cron would have announced 23 long-closed leads to managers as "contact immediately".
    Only a real transition opens the window."""
    bid, cid = await _fixture(db_session)
    lead_id = await _lead(db_session, bid, cid, phone="+6284444444444",
                          escalated_at=_NOW - timedelta(days=HANDOFF_WINDOW_DAYS + 6))
    db_session.add(StageEvent(          # ровно та отметка, что всё сломала
        branch_id=bid, lead_id=lead_id, from_stage="ready", to_stage="ready",
        actor="system", reason=VERIFIED_PRESENT_REASON))
    await db_session.flush()
    assert await fetch_unpushed_handoffs(db_session, bid, now=_NOW) == []


async def test_a_recent_handoff_is_picked_up(db_session) -> None:
    bid, cid = await _fixture(db_session)
    await _lead(db_session, bid, cid, phone="+6281111111111",
                escalated_at=_NOW - timedelta(hours=2))
    leads = await fetch_unpushed_handoffs(db_session, bid, now=_NOW)
    assert [x.phone for x in leads] == ["+6281111111111"]
    assert await _log_window_drops(db_session, bid, _NOW) == 0


async def test_a_handoff_older_than_the_window_is_counted_not_silently_dropped(
    db_session,
) -> None:
    bid, cid = await _fixture(db_session)
    await _lead(db_session, bid, cid, phone="+6282222222222",
                escalated_at=_NOW - timedelta(days=HANDOFF_WINDOW_DAYS + 6))
    # The sweep still refuses it — that part is deliberate, managers have worked it by hand.
    assert await fetch_unpushed_handoffs(db_session, bid, now=_NOW) == []
    # …but it is no longer invisible.
    assert await _log_window_drops(db_session, bid, _NOW) == 1


@pytest.mark.parametrize("reason", [PUSHED_HANDOFF_REASON, VERIFIED_PRESENT_REASON])
async def test_a_reconciled_old_lead_is_not_counted_as_a_drop(db_session, reason: str) -> None:
    """The count is about work nobody will do, not about history that is done.

    Two ways to leave it: we pushed the lead, or a phone search confirmed the CRM already has
    them. The first reconciliation run found all 23 aged-out leads already in the CRM — without
    the second reason the warning would print the same 23 forever and be tuned out, which is
    exactly how the silent version failed."""
    bid, cid = await _fixture(db_session)
    lead_id = await _lead(db_session, bid, cid, phone="+6283333333333",
                          escalated_at=_NOW - timedelta(days=HANDOFF_WINDOW_DAYS + 6))
    assert await _log_window_drops(db_session, bid, _NOW) == 1
    db_session.add(StageEvent(
        branch_id=bid, lead_id=lead_id, from_stage="manager", to_stage="manager",
        actor="system", reason=reason))
    await db_session.flush()
    assert await _log_window_drops(db_session, bid, _NOW) == 0
