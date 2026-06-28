"""OutboxSender — the single egress: drain one queued line through the channel.

Channel stays behind ChannelPort (injected, faked in tests). On success the sent text
is recorded as an outgoing Message so it becomes part of the dialog; on failure the row
is marked failed with the error and nothing is recorded."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Message, Outbox
from app.ports.channel import ChannelPort

from .repository import MessageRepo, OutboxRepo, ThreadRepo


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
        """Pick the oldest pending line, send it, flip status, record on success."""
        row = await self.outbox.oldest_pending(thread_id)
        if row is None:
            return None
        thread = await self.threads.by_id(thread_id)
        if thread is None:
            return None

        result = await self.channel.send_text(thread.external_thread_id, row.text)
        if result.ok:
            row.status = "sent"
            row.sent_at = datetime.now(UTC)
            row.error = None
            await self.messages.add(self._outgoing(thread, row, result.external_message_id))
        else:
            row.status = "failed"
            row.error = result.error
        self.session.add(row)
        await self.session.flush()
        return row

    def _outgoing(self, thread, row: Outbox, external_id: str | None) -> Message:
        return Message(
            branch_id=self.branch_id,
            thread_id=row.thread_id,
            channel_id=thread.channel_id,
            external_id=external_id or f"out-{row.id}",
            direction="out",
            sent_by="agent",
            text=row.text,
        )
