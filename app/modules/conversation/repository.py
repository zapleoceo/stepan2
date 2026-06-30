"""Conversation repos — reuse the leads module's branch-scoped Thread/Message repos.

ChannelThread carries no branch_id, so isolation is the Lead-join `_q()` that already
lives in leads.repository; we only add the conversation-specific reads (dialog, by_id,
oldest_pending) on top, keeping a single isolation primitive."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import ChannelThread, CoachingNote, Message, Outbox
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


class CoachingNoteRepo:
    """Read active coaching directives for a branch — injected into the system prompt."""

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        self._s = session
        self._branch_id = branch_id

    async def active_manager_notes(self) -> list[str]:
        """Texts of active manager-role notes — the bot's mandatory rules."""
        rows = await self._s.exec(
            select(CoachingNote).where(
                CoachingNote.branch_id == self._branch_id,
                CoachingNote.role == "manager",
                CoachingNote.active.is_(True),
            )
        )
        return [r.text for r in rows.all()]


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

    async def count_sent_since(self, since: datetime) -> int:
        """How many lines this branch actually sent since `since` — hourly/daily cap accounting."""
        q = self._q().where(Outbox.status == "sent", Outbox.sent_at >= since)
        return len((await self.session.exec(q)).all())
