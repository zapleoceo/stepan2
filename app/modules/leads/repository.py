"""Lead/thread/message repos — thin BranchScoped subclasses; isolation stays in base."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import ChannelThread, Lead, Message
from app.adapters.db.repository import BranchScoped

_DEDUP_WINDOW = timedelta(seconds=2)


class LeadRepo(BranchScoped[Lead]):
    """Leads of one branch — merged across channels by phone_e164."""

    model = Lead

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        super().__init__(session, branch_id)

    async def by_phone(self, phone_e164: str) -> Lead | None:
        """Branch-scoped phone lookup — the cross-channel merge key."""
        q = self._q().where(Lead.phone_e164 == phone_e164)
        return (await self.session.exec(q)).first()


class ThreadRepo(BranchScoped[ChannelThread]):
    """Channel threads of one branch. ChannelThread has no branch_id of its own,
    so reads join through Lead to keep isolation in one place."""

    model = ChannelThread

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        super().__init__(session, branch_id)

    def _q(self):  # type: ignore[override] — thread carries no branch_id; scope via Lead
        from sqlmodel import select

        return (
            select(ChannelThread)
            .join(Lead, Lead.id == ChannelThread.lead_id)  # type: ignore[arg-type]
            .where(Lead.branch_id == self.branch_id)
        )

    async def add(self, obj: ChannelThread) -> ChannelThread:  # type: ignore[override]
        """ChannelThread has no branch_id — bypass the base's forced assignment."""
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def by_external(
        self, channel_id: int, external_thread_id: str
    ) -> ChannelThread | None:
        """Existing thread for (channel, external id) within this branch, or None."""
        q = self._q().where(
            ChannelThread.channel_id == channel_id,
            ChannelThread.external_thread_id == external_thread_id,
        )
        return (await self.session.exec(q)).first()


class MessageRepo(BranchScoped[Message]):
    """Messages of one branch — deduped by (channel_id, external_id)."""

    model = Message

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        super().__init__(session, branch_id)

    async def by_external(self, channel_id: int, external_id: str) -> Message | None:
        """Branch-scoped dedup lookup for an inbound message."""
        q = self._q().where(
            Message.channel_id == channel_id,
            Message.external_id == external_id,
        )
        return (await self.session.exec(q)).first()

    async def duplicate_by_content(
        self, thread_id: int, direction: str, text: str, occurred_at: datetime
    ) -> bool:
        """Same-text message already in this thread within a 2s window — the pending→main
        inbox id drift reappears the same message under a new external id, so item-level
        dedup misses it. Text-only (callers exclude media: placeholders collide)."""
        q = self._q().where(
            Message.thread_id == thread_id,
            Message.direction == direction,
            Message.text == text,
            Message.occurred_at >= occurred_at - _DEDUP_WINDOW,
            Message.occurred_at <= occurred_at + _DEDUP_WINDOW,
        ).limit(1)
        return (await self.session.exec(q)).first() is not None
