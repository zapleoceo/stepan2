"""First-reply response rate — the safety-net metric for opener regressions.

Guards the exact blind spot that let the campus-boilerplate opener grow 4% → 71% over ten
days unnoticed: only inbound messages that arrive AFTER the bot's first reply count as an
answer, so an ad-click prefill that preceded it can't inflate the number.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
from app.domain.enums import ChannelKind, Stage
from app.modules.reports.first_reply import FirstReplyDay, first_reply_stats

_NOW = datetime.now(UTC).replace(tzinfo=None)


async def _branch(s) -> tuple[int, int]:  # noqa: ANN001
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    s.add(ch)
    await s.flush()
    return b.id, ch.id


async def _thread(s, branch_id: int, channel_id: int, ext: str) -> int:  # noqa: ANN001
    lead = Lead(branch_id=branch_id, stage=Stage.QUALIFYING)
    s.add(lead)
    await s.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=channel_id, external_thread_id=ext)
    s.add(th)
    await s.flush()
    return th.id


async def _msg(s, branch_id: int, channel_id: int, thread_id: int,  # noqa: ANN001, PLR0913
                ext: str, direction: str, at: datetime) -> None:
    s.add(Message(branch_id=branch_id, thread_id=thread_id, channel_id=channel_id,
                  external_id=ext, direction=direction,
                  sent_by="bot" if direction == "out" else "lead",
                  text="x", occurred_at=at))
    await s.flush()


async def test_opener_the_lead_answered_counts_as_answered(db_session) -> None:  # noqa: ANN001
    bid, cid = await _branch(db_session)
    tid = await _thread(db_session, bid, cid, "t-1")
    await _msg(db_session, bid, cid, tid, "in-1", "in", _NOW - timedelta(hours=3))
    await _msg(db_session, bid, cid, tid, "out-1", "out", _NOW - timedelta(hours=2))
    await _msg(db_session, bid, cid, tid, "in-2", "in", _NOW - timedelta(hours=1))

    stats = await first_reply_stats(db_session, bid)
    assert stats == [FirstReplyDay((_NOW - timedelta(hours=2)).date().isoformat(), 1, 1)]
    assert stats[0].pct == 100.0


async def test_opener_that_killed_the_thread_counts_as_unanswered(db_session) -> None:  # noqa: ANN001
    """The 1710-lead failure mode: the lead wrote once, got the opener, never came back."""
    bid, cid = await _branch(db_session)
    tid = await _thread(db_session, bid, cid, "t-1")
    await _msg(db_session, bid, cid, tid, "in-1", "in", _NOW - timedelta(hours=3))
    await _msg(db_session, bid, cid, tid, "out-1", "out", _NOW - timedelta(hours=2))

    stats = await first_reply_stats(db_session, bid)
    assert stats[0].first_replies == 1
    assert stats[0].answered == 0
    assert stats[0].pct == 0.0


async def test_inbound_before_the_opener_does_not_count_as_an_answer(db_session) -> None:  # noqa: ANN001
    """An ad-click prefill arrives before the bot speaks — it is not a reply to the opener."""
    bid, cid = await _branch(db_session)
    tid = await _thread(db_session, bid, cid, "t-1")
    for i, hours in enumerate((5, 4, 3)):
        await _msg(db_session, bid, cid, tid, f"in-{i}", "in", _NOW - timedelta(hours=hours))
    await _msg(db_session, bid, cid, tid, "out-1", "out", _NOW - timedelta(hours=2))

    assert (await first_reply_stats(db_session, bid))[0].answered == 0


async def test_only_the_first_outbound_of_a_thread_is_the_opener(db_session) -> None:  # noqa: ANN001
    """A thread contributes exactly one opener no matter how much the bot said afterwards."""
    bid, cid = await _branch(db_session)
    tid = await _thread(db_session, bid, cid, "t-1")
    await _msg(db_session, bid, cid, tid, "out-1", "out", _NOW - timedelta(hours=4))
    await _msg(db_session, bid, cid, tid, "out-2", "out", _NOW - timedelta(hours=3))
    await _msg(db_session, bid, cid, tid, "out-3", "out", _NOW - timedelta(hours=2))

    stats = await first_reply_stats(db_session, bid)
    assert sum(d.first_replies for d in stats) == 1


async def test_other_branches_are_not_counted(db_session) -> None:  # noqa: ANN001
    bid_a, cid_a = await _branch(db_session)
    bid_b, cid_b = await _branch(db_session)
    tid_b = await _thread(db_session, bid_b, cid_b, "t-b")
    await _msg(db_session, bid_b, cid_b, tid_b, "out-b", "out", _NOW - timedelta(hours=2))

    assert await first_reply_stats(db_session, bid_a) == []
    assert len(await first_reply_stats(db_session, bid_b)) == 1


async def test_openers_older_than_the_window_are_excluded(db_session) -> None:  # noqa: ANN001
    bid, cid = await _branch(db_session)
    old = await _thread(db_session, bid, cid, "t-old")
    recent = await _thread(db_session, bid, cid, "t-new")
    await _msg(db_session, bid, cid, old, "out-old", "out", _NOW - timedelta(days=30))
    await _msg(db_session, bid, cid, recent, "out-new", "out", _NOW - timedelta(days=1))

    stats = await first_reply_stats(db_session, bid, days=7)
    assert sum(d.first_replies for d in stats) == 1


async def test_days_are_returned_oldest_first(db_session) -> None:  # noqa: ANN001
    bid, cid = await _branch(db_session)
    for i, days_ago in enumerate((1, 5, 3)):
        tid = await _thread(db_session, bid, cid, f"t-{i}")
        await _msg(db_session, bid, cid, tid, f"out-{i}", "out", _NOW - timedelta(days=days_ago))

    days = [d.day for d in await first_reply_stats(db_session, bid)]
    assert days == sorted(days)


def test_pct_of_a_day_with_no_openers_is_zero_not_a_crash() -> None:
    assert FirstReplyDay("2026-07-22", 0, 0).pct == 0.0
