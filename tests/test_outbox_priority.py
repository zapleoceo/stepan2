"""send_outbox must never let a proactive follow-up crowd out a real reply when the
hourly/daily send cap is tight — a thread with a reply pending drains before a
thread with only a follow-up pending."""
from __future__ import annotations

from app.adapters.db.models import Branch, Outbox
from app.worker import wiring


async def _branch(db_session) -> int:
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    return b.id


async def test_reply_thread_drains_before_followup_only_thread(db_session) -> None:
    bid = await _branch(db_session)
    # thread 2's followup was queued FIRST (lower id) — without priority it would drain first
    db_session.add(Outbox(branch_id=bid, thread_id=2, text="nudge", source="followup"))
    db_session.add(Outbox(branch_id=bid, thread_id=1, text="reply", source="agent"))
    await db_session.flush()

    order = await wiring.threads_with_pending_outbox(db_session, bid)
    assert order == [1, 2]  # reply-bearing thread first despite the higher id


async def test_thread_with_both_counts_as_reply_priority(db_session) -> None:
    bid = await _branch(db_session)
    db_session.add(Outbox(branch_id=bid, thread_id=5, text="nudge", source="followup"))
    db_session.add(Outbox(branch_id=bid, thread_id=5, text="reply", source="manager"))
    db_session.add(Outbox(branch_id=bid, thread_id=6, text="nudge only", source="followup"))
    await db_session.flush()

    order = await wiring.threads_with_pending_outbox(db_session, bid)
    assert order == [5, 6]  # mixed thread still ranks as reply-priority


async def test_oldest_queued_breaks_ties_within_a_tier(db_session) -> None:
    bid = await _branch(db_session)
    db_session.add(Outbox(branch_id=bid, thread_id=8, text="earlier reply", source="agent"))
    db_session.add(Outbox(branch_id=bid, thread_id=9, text="later reply", source="agent"))
    await db_session.flush()

    order = await wiring.threads_with_pending_outbox(db_session, bid)
    assert order == [8, 9]  # lower outbox id (queued first) wins within the same tier
