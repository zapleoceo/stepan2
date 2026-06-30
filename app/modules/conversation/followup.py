"""Follow-up scheduler — proactively re-engages cold threads via the outbox.

Strategy:
 1. reset_timers(): for threads where the lead spoke last and no timer is set,
    schedule next_followup_at = last_in_at + schedule[0] hours.
 2. run(): send all threads whose next_followup_at has passed, then advance
    the timer to the next step (or clear it when schedule is exhausted).

This module writes only to outbox and channel_thread; all sends go through
the normal OutboxSender → channel transport path, so caps and window checks
are applied uniformly."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Outbox
from app.modules.settings.service import BranchSettings

from .prompt import build_messages
from .repository import CoachingNoteRepo, MessageRepo, OutboxRepo, ThreadRepo

if TYPE_CHECKING:
    from app.modules.knowledge.service import KnowledgeService
    from app.ports.llm import LLMPort

_FOLLOWUP_Q = (  # noqa: S608
    "SELECT ct.id, ct.product_slug"
    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
    " WHERE l.branch_id = :bid"
    "   AND l.stage NOT IN ('ready', 'handed_off', 'dormant', 'manager')"
    "   AND ct.next_followup_at IS NOT NULL"
    "   AND ct.next_followup_at <= :now"
)

_RESET_Q = (  # noqa: S608
    "UPDATE channel_thread"
    " SET next_followup_at = last_in_at + :interval"
    " FROM lead l"
    " WHERE channel_thread.lead_id = l.id"
    "   AND l.branch_id = :bid"
    "   AND l.stage NOT IN ('ready', 'handed_off', 'dormant', 'manager')"
    "   AND channel_thread.last_in_at IS NOT NULL"
    "   AND channel_thread.next_followup_at IS NULL"
    "   AND (channel_thread.last_out_at IS NULL"
    "        OR channel_thread.last_out_at < channel_thread.last_in_at)"
)

_FOLLOWUP_NUDGE = (
    "[System: the lead has not replied for a while. "
    "Write a short friendly follow-up in {lang} to re-engage them naturally. "
    "Do not mention the wait. Return the JSON as usual.]"
)


class FollowupService:
    """Manages next_followup_at timers and queues proactive outbox rows."""

    def __init__(
        self,
        session: AsyncSession,
        branch_id: int,
        llm: LLMPort,
        knowledge: KnowledgeService,
        settings: BranchSettings,
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.llm = llm
        self.knowledge = knowledge
        self.settings = settings
        self.threads = ThreadRepo(session, branch_id)
        self.messages = MessageRepo(session, branch_id)
        self.outbox = OutboxRepo(session, branch_id)
        self.coaching = CoachingNoteRepo(session, branch_id)

    async def reset_timers(self) -> int:
        """Schedule next_followup_at for threads that have none yet."""
        schedule = self.settings.followup_schedule_h
        if not schedule:
            return 0
        result = await self.session.execute(
            text(_RESET_Q),
            {"bid": self.branch_id, "interval": timedelta(hours=schedule[0])},
        )
        return result.rowcount  # type: ignore[return-value]

    async def run(self) -> int:
        """Send due follow-ups and advance each thread's timer."""
        if not self.settings.followup_enabled or not self.settings.followup_schedule_h:
            return 0
        now = datetime.now(UTC).replace(tzinfo=None)
        rows = (
            await self.session.execute(text(_FOLLOWUP_Q), {"bid": self.branch_id, "now": now})
        ).all()
        sent = 0
        for thread_id, product_slug in rows:
            if await self._queue_followup(thread_id, product_slug, now):
                await self._advance_timer(thread_id, now)
                sent += 1
        return sent

    async def _lang(self) -> str:
        branch = await self.session.get(Branch, self.branch_id)
        return branch.lang if branch is not None else "id"

    async def _queue_followup(
        self, thread_id: int, product_slug: str | None, now: datetime,
    ) -> bool:
        dialog = await self.messages.dialog(thread_id)
        if not dialog:
            return False
        lang = await self._lang()
        context = await self.knowledge.knowledge_context(product_slug)
        notes = await self.coaching.active_manager_notes()
        messages = build_messages(context, dialog, lang, coaching_notes=notes)
        messages.append({"role": "user", "content": _FOLLOWUP_NUDGE.format(lang=lang)})
        raw, _ = await self.llm.chat(
            messages, capability="chat:smart", require_json_schema=True
        )
        from .decision import parse_decision  # noqa: PLC0415 (avoid circular at module level)
        decision = parse_decision(raw)
        if not decision or not decision.reply:
            return False
        await self.outbox.add(Outbox(
            branch_id=self.branch_id,
            thread_id=thread_id,
            text=decision.reply,
            source="followup",
            scheduled_at=now,
        ))
        return True

    async def _advance_timer(self, thread_id: int, now: datetime) -> None:
        """Move next_followup_at to next schedule step, or NULL when exhausted."""
        row = (await self.session.execute(
            text(
                "SELECT next_followup_at, last_in_at"
                " FROM channel_thread WHERE id=:id"
            ),
            {"id": thread_id},
        )).first()
        if not row or not row[1]:
            return
        last_in_at: datetime = row[1]
        schedule = self.settings.followup_schedule_h
        elapsed_h = (now - last_in_at).total_seconds() / 3600
        current_step = -1
        for i, h in enumerate(schedule):
            if elapsed_h >= h:
                current_step = i
        if current_step + 1 >= len(schedule):
            next_at = None
        else:
            next_at = last_in_at + timedelta(hours=schedule[current_step + 1])
        await self.session.execute(
            text("UPDATE channel_thread SET next_followup_at=:next_at WHERE id=:id"),
            {"next_at": next_at, "id": thread_id},
        )
