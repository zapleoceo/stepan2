"""Conversation repos — reuse the leads module's branch-scoped Thread/Message repos.

ChannelThread carries no branch_id, so isolation is the Lead-join `_q()` that already
lives in leads.repository; we only add the conversation-specific reads (dialog, by_id,
oldest_pending) on top, keeping a single isolation primitive."""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import ChannelThread, Message, Outbox
from app.adapters.db.repository import BranchScoped
from app.modules.leads.repository import MessageRepo as _LeadMessageRepo
from app.modules.leads.repository import ThreadRepo as _LeadThreadRepo


class ThreadRepo(_LeadThreadRepo):
    """Adds a branch-scoped id lookup (base get() can't — thread has no branch_id)."""

    async def by_id(self, thread_id: int) -> ChannelThread | None:
        """The thread by id if it belongs to this branch, else None — via the Lead join."""
        q = self._q().where(ChannelThread.id == thread_id)
        return (await self.session.exec(q)).first()


class MessageRepo(_LeadMessageRepo):
    """Adds dialog loading for the prompt builder."""

    async def dialog(self, thread_id: int) -> list[Message]:
        """A thread's messages oldest-first — the dialog handed to the prompt builder."""
        q = self._q().where(Message.thread_id == thread_id).order_by(
            Message.occurred_at, Message.id
        )
        return list((await self.session.exec(q)).all())


class OutboxRepo(BranchScoped[Outbox]):
    """The branch's single outgoing queue — caps/windows apply here once."""

    model = Outbox

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        super().__init__(session, branch_id)

    async def oldest_pending(self, thread_id: int) -> Outbox | None:
        """Next queued line for a thread (FIFO), or None when nothing is pending."""
        q = (
            self._q()
            .where(Outbox.thread_id == thread_id, Outbox.status == "pending")
            .order_by(Outbox.scheduled_at, Outbox.id)
        )
        return (await self.session.exec(q)).first()
