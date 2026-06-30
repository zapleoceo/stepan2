"""OutboxSender — the single egress: drain one queued line through the channel.

Channel stays behind ChannelPort (injected, faked in tests). On success the sent text
is recorded as an outgoing Message so it becomes part of the dialog; on failure the row
is marked failed with the error and nothing is recorded. Hourly/daily send caps are
enforced here (the single egress) for anti-ban — automated lines are held back when the
branch is over budget; manager-sent lines bypass the cap (human override)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Message, Outbox
from app.modules.settings.service import get_settings
from app.ports.channel import ChannelPort

from .repository import MessageRepo, OutboxRepo, ThreadRepo


def _branch_day_start(now_utc_naive: datetime, tz_offset_h: int) -> datetime:
    """UTC instant of the branch-local midnight preceding `now` (daily cap window start)."""
    local = now_utc_naive + timedelta(hours=tz_offset_h)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(hours=tz_offset_h)


# IG/WA soft blocks (challenge, rate limit, transient) — retry later, don't drop the line.
_SOFT_BLOCK = (
    "challenge", "feedback_required", "login_required", "checkpoint", "please wait",
    "rate", "429", "spam", "blocked", "try again", "throttl", "temporarily",
)
_RETRY_AFTER = timedelta(minutes=15)


def _is_soft_block(error: str | None) -> bool:
    """True when a send error is transient (back off + retry) vs a hard, give-up failure."""
    low = (error or "").lower()
    return any(token in low for token in _SOFT_BLOCK)


class OutboxSender:
    """Send the next pending outbox row of one branch's thread via the channel."""

    def __init__(
        self, session: AsyncSession, branch_id: int, channel: ChannelPort
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.channel = channel
        self.threads = ThreadRepo(session, branch_id)
        self.messages = MessageRepo(session, branch_id)
        self.outbox = OutboxRepo(session, branch_id)

    async def send_next(self, thread_id: int) -> Outbox | None:
        """Pick the oldest due line (scheduled_at ≤ now) and send it, unless capped."""
        row = await self.outbox.oldest_pending(thread_id)
        if row is None:
            return None
        now = datetime.now(UTC).replace(tzinfo=None)
        if row.scheduled_at > now:
            return None  # not due yet — respect reply delay
        if row.source != "manager" and await self._cap_reached(now):
            return None  # hourly/daily send cap hit — leave queued for a later tick
        thread = await self.threads.by_id(thread_id)
        if thread is None:
            return None

        result = await self.channel.send_text(thread.external_thread_id, row.text)
        if result.ok:
            row.status = "sent"
            row.sent_at = now
            row.error = None
            await self.messages.add(self._outgoing(thread, row, result.external_message_id))
        elif _is_soft_block(result.error):
            row.status = "pending"  # transient (challenge/rate) — back off, don't drop
            row.scheduled_at = now + _RETRY_AFTER
            row.error = result.error
        else:
            row.status = "failed"
            row.error = result.error
        self.session.add(row)
        await self.session.flush()
        return row

    async def _cap_reached(self, now: datetime) -> bool:
        """True when the branch already hit its hourly or daily send cap (cap ≤ 0 = off)."""
        s = await get_settings(self.session, self.branch_id)
        if s.hourly_cap > 0:
            if await self.outbox.count_sent_since(now - timedelta(hours=1)) >= s.hourly_cap:
                return True
        if s.daily_cap > 0:
            day_start = _branch_day_start(now, s.tz_offset_h)
            if await self.outbox.count_sent_since(day_start) >= s.daily_cap:
                return True
        return False

    def _outgoing(self, thread, row: Outbox, external_id: str | None) -> Message:
        sent_by = row.source if row.source in ("manager", "agent") else "agent"
        return Message(
            branch_id=self.branch_id,
            thread_id=row.thread_id,
            channel_id=thread.channel_id,
            external_id=external_id or f"out-{row.id}",
            direction="out",
            sent_by=sent_by,
            text=row.text,
            llm_info=row.llm_info,
        )
