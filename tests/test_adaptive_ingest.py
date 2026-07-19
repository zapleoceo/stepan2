"""Adaptive interleave poll only wakes for channels with a live, unanswered conversation."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead
from app.domain.enums import ChannelKind, Stage
from app.worker.main import _channels_with_live_convo


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _channel(s, bid: int, handle: str) -> int:
    ch = Channel(branch_id=bid, kind=ChannelKind.INSTAGRAM, handle=handle,
                 account_id=handle, is_active=True)
    s.add(ch)
    await s.flush()
    return ch.id


async def test_live_convo_detection(db_session) -> None:
    s = db_session
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING)
    s.add(lead)
    await s.flush()
    now = _now()

    active = await _channel(s, b.id, "active")       # lead spoke 2m ago, bot silent → LIVE
    answered = await _channel(s, b.id, "answered")   # bot already replied → not live
    stale = await _channel(s, b.id, "stale")         # lead spoke 20m ago → outside window
    idle = await _channel(s, b.id, "idle")           # no thread at all

    s.add(ChannelThread(lead_id=lead.id, channel_id=active, external_thread_id="a",
                        last_in_at=now - timedelta(minutes=2), last_out_at=now - timedelta(minutes=3)))
    s.add(ChannelThread(lead_id=lead.id, channel_id=answered, external_thread_id="b",
                        last_in_at=now - timedelta(minutes=2), last_out_at=now - timedelta(minutes=1)))
    s.add(ChannelThread(lead_id=lead.id, channel_id=stale, external_thread_id="c",
                        last_in_at=now - timedelta(minutes=20), last_out_at=now - timedelta(minutes=30)))
    await s.flush()

    cutoff = now - timedelta(minutes=6)
    got = await _channels_with_live_convo(s, [active, answered, stale, idle], cutoff)
    assert got == {active}, got


async def test_live_convo_empty_input(db_session) -> None:
    assert await _channels_with_live_convo(db_session, [], _now()) == set()
