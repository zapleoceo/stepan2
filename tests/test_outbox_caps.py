"""Anti-ban send caps: OutboxSender holds back automated lines when a branch is over
its hourly/daily budget; manager-sent lines bypass the cap. Self-contained (own fakes)
so it does not couple to the conversation test module."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.adapters.db.models import (
    AppSetting,
    Branch,
    Channel,
    ChannelThread,
    Lead,
    Outbox,
)
from app.domain.enums import ChannelKind
from app.modules.conversation.outbox import OutboxSender
from app.modules.conversation.repository import OutboxRepo
from app.modules.settings.service import invalidate
from app.ports.channel import SendResult


class FakeChannel:
    kind = ChannelKind.INSTAGRAM

    def __init__(self, *, ok: bool = True, error: str | None = None) -> None:
        self._ok = ok
        self._error = error
        self.sent: list[tuple[str, str]] = []

    async def fetch_inbound(self) -> list[Any]:
        return []

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        self.sent.append((external_thread_id, text))
        return SendResult(
            ok=self._ok,
            external_message_id="ext-1" if self._ok else None,
            error=self._error,
        )

    async def session_status(self) -> Any:
        return None


async def _setup(
    s, *, hourly_cap: int, daily_cap: int, sent_now: int, pending_source: str = "agent",
) -> tuple[int, int]:
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    s.add(AppSetting(branch_id=branch.id, key="hourly_cap", value=str(hourly_cap)))
    s.add(AppSetting(branch_id=branch.id, key="daily_cap", value=str(daily_cap)))
    channel = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM)
    s.add(channel)
    await s.flush()
    lead = Lead(branch_id=branch.id)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(
        lead_id=lead.id, channel_id=channel.id, external_thread_id="ig-1",
    )
    s.add(thread)
    await s.flush()

    now = datetime.now(UTC).replace(tzinfo=None)
    for _ in range(sent_now):
        s.add(Outbox(
            branch_id=branch.id, thread_id=thread.id, text="x",
            source="agent", status="sent", sent_at=now,
        ))
    s.add(Outbox(
        branch_id=branch.id, thread_id=thread.id, text="hi",
        source=pending_source, status="pending", scheduled_at=now - timedelta(seconds=5),
    ))
    await s.flush()
    invalidate(branch.id)  # drop any settings cached for this id by an earlier test
    return branch.id, thread.id


async def test_count_sent_since_counts_only_window(db_session) -> None:
    bid, _ = await _setup(db_session, hourly_cap=999, daily_cap=999, sent_now=3)
    repo = OutboxRepo(db_session, bid)
    now = datetime.now(UTC).replace(tzinfo=None)
    assert await repo.count_sent_since(now - timedelta(hours=1)) == 3
    assert await repo.count_sent_since(now + timedelta(hours=1)) == 0


async def test_hourly_cap_blocks_automated_line(db_session) -> None:
    bid, tid = await _setup(db_session, hourly_cap=2, daily_cap=999, sent_now=2)
    channel = FakeChannel()
    assert await OutboxSender(db_session, bid, channel).send_next(tid) is None
    assert channel.sent == []


async def test_daily_cap_blocks_automated_line(db_session) -> None:
    bid, tid = await _setup(db_session, hourly_cap=999, daily_cap=3, sent_now=3)
    channel = FakeChannel()
    assert await OutboxSender(db_session, bid, channel).send_next(tid) is None
    assert channel.sent == []


async def test_under_cap_sends(db_session) -> None:
    bid, tid = await _setup(db_session, hourly_cap=999, daily_cap=999, sent_now=0)
    channel = FakeChannel()
    row = await OutboxSender(db_session, bid, channel).send_next(tid)
    assert row is not None and row.status == "sent"
    assert len(channel.sent) == 1


async def test_manager_line_bypasses_cap(db_session) -> None:
    bid, tid = await _setup(
        db_session, hourly_cap=1, daily_cap=1, sent_now=5, pending_source="manager",
    )
    channel = FakeChannel()
    row = await OutboxSender(db_session, bid, channel).send_next(tid)
    assert row is not None and row.status == "sent"
    assert len(channel.sent) == 1


async def test_cap_zero_means_unlimited(db_session) -> None:
    bid, tid = await _setup(db_session, hourly_cap=0, daily_cap=0, sent_now=50)
    channel = FakeChannel()
    row = await OutboxSender(db_session, bid, channel).send_next(tid)
    assert row is not None and row.status == "sent"


async def test_soft_block_reschedules_instead_of_failing(db_session) -> None:
    bid, tid = await _setup(db_session, hourly_cap=999, daily_cap=999, sent_now=0)
    channel = FakeChannel(ok=False, error="challenge_required")
    row = await OutboxSender(db_session, bid, channel).send_next(tid)
    assert row is not None
    assert row.status == "pending"  # retried later, not dropped
    assert row.scheduled_at > datetime.now(UTC).replace(tzinfo=None)


async def test_hard_error_marks_failed(db_session) -> None:
    bid, tid = await _setup(db_session, hourly_cap=999, daily_cap=999, sent_now=0)
    channel = FakeChannel(ok=False, error="recipient not found")
    row = await OutboxSender(db_session, bid, channel).send_next(tid)
    assert row is not None and row.status == "failed"
