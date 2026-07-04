"""Follow-up scheduler — S1 semantics: the timer lives off the BOT's last send.

Arming happens in OutboxSender after a successful bot send (next_followup_at =
sent_at + schedule[followups_sent]); a fresh inbound resets the cycle in ingest.
This service only harvests DUE threads: re-checks the lead is still silent, skips
threads with queued outbox, generates the nudge, increments followups_sent and
queues the row. Exhaustion → dormant happens in OutboxSender after the last send.
One broken thread never aborts the rest (per-thread try)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Lead, Outbox
from app.modules.settings.service import BranchSettings

from .needs import needs_summary, parse_needs
from .prompt import build_messages
from .reply import _retrieval_query
from .repository import CoachingNoteRepo, MessageRepo, OutboxRepo, ThreadRepo

if TYPE_CHECKING:
    from app.modules.knowledge.service import KnowledgeService
    from app.ports.llm import LLMPort

logger = logging.getLogger(__name__)

# Due threads: bot spoke last (lead silent), timer matured, steps remain, nothing
# already queued. Whitelist of stages the bot actively works (S1 ACTIVE_STAGES —
# `new` is excluded: an untouched lead gets a live reply, not a nudge).
_FOLLOWUP_Q = (  # noqa: S608
    "SELECT ct.id, ct.product_slug, ct.followups_sent"
    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
    " WHERE l.branch_id = :bid"
    "   AND l.stage IN ('nurturing', 'qualifying', 'presenting', 'objection')"
    "   AND l.agent_enabled = :on"
    "   AND ct.next_followup_at IS NOT NULL"
    "   AND ct.next_followup_at <= :now"
    "   AND ct.followups_sent < :max_steps"
    "   AND ct.last_out_at IS NOT NULL"
    "   AND (ct.last_in_at IS NULL OR ct.last_in_at <= ct.last_out_at)"
    "   AND NOT EXISTS (SELECT 1 FROM outbox o"
    "        WHERE o.thread_id = ct.id AND o.status = 'pending')"
)

_FOLLOWUP_NUDGE = (
    "[System: the lead has not replied since your last message. This is follow-up"
    " attempt {n} of {total}. Write a short friendly follow-up in {lang} to"
    " re-engage them naturally — vary the angle, do not mention the wait or the"
    " attempt number. Return the JSON as usual.]"
)


class FollowupService:
    """Harvest due follow-up threads and queue nudges via the outbox."""

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

    async def run(self) -> int:
        """Queue nudges for every due thread; one failure never blocks the rest."""
        cfg = self.settings
        if not cfg.followup_enabled or not cfg.followup_schedule_h:
            return 0
        if not cfg.agent_enabled:
            return 0  # global OFF: no generation at all
        # Quiet hours do NOT block generation here — only the SEND (OutboxSender.send_next)
        # holds a follow-up-sourced row until quiet hours end. Queueing now means it's ready
        # to go out the moment quiet hours lift, instead of losing that whole cron cycle.
        now = datetime.now(UTC).replace(tzinfo=None)
        rows = (
            await self.session.execute(
                text(_FOLLOWUP_Q),
                {
                    "bid": self.branch_id, "now": now, "on": True,
                    "max_steps": len(cfg.followup_schedule_h),
                },
            )
        ).all()
        queued = 0
        for thread_id, product_slug, sent_so_far in rows:
            try:
                if await self._queue_followup(thread_id, product_slug, sent_so_far, now):
                    queued += 1
            except Exception:
                logger.exception(
                    "followup failed branch=%d thread=%d", self.branch_id, thread_id
                )
        if rows:
            logger.info(
                "followups branch=%d: %d due, %d queued", self.branch_id, len(rows), queued
            )
        return queued

    async def _lang(self) -> str:
        branch = await self.session.get(Branch, self.branch_id)
        return branch.lang if branch is not None else "id"

    async def _queue_followup(
        self, thread_id: int, product_slug: str | None, sent_so_far: int, now: datetime,
    ) -> bool:
        from app.modules.budget import BudgetService  # noqa: PLC0415 (avoid module cycle)
        budget = BudgetService(self.session, self.branch_id)
        if await budget.over_budget():
            logger.warning("branch=%d over daily LLM budget — followups held", self.branch_id)
            return False
        thread = await self.threads.by_id(thread_id)
        since = thread.context_cleared_at if thread is not None else None
        dialog = await self.messages.dialog(thread_id, since=since)
        if not dialog:
            return False
        lang = await self._lang()
        context = await self.knowledge.knowledge_context(
            product_slug, query=_retrieval_query(dialog))
        notes = await self.coaching.active_manager_notes()
        lead = await self.session.get(Lead, thread.lead_id) if thread is not None else None
        needs_block = needs_summary(parse_needs(lead.needs if lead is not None else None))
        messages = build_messages(context, dialog, lang, coaching_notes=notes,
                                  needs_block=needs_block)
        total = len(self.settings.followup_schedule_h)
        messages.append({
            "role": "user",
            "content": _FOLLOWUP_NUDGE.format(lang=lang, n=sent_so_far + 1, total=total),
        })
        raw, meta = await self.llm.chat(
            messages, capability="chat:smart", require_json_schema=True,
            workflow="followup", thread_id=thread_id, branch_id=self.branch_id,
        )
        await budget.record(float(meta.get("cost_usd") or 0.0))
        from .decision import parse_decision  # noqa: PLC0415 (avoid circular at module level)
        try:
            decision = parse_decision(raw)
        except ValueError:
            logger.warning(
                "followup: unparseable decision branch=%d thread=%d — attempt not burned",
                self.branch_id, thread_id,
            )
            return False
        if not decision.reply:
            return False
        if await self._lead_replied_meanwhile(thread_id):
            return False  # race: lead answered while we were generating (S1 guard)
        from .reply import _fmt_llm_meta  # noqa: PLC0415 (same package, avoid cycle)
        await self.outbox.add(Outbox(
            branch_id=self.branch_id,
            thread_id=thread_id,
            text=decision.reply,
            source="followup",
            scheduled_at=now,
            llm_info=_fmt_llm_meta(meta),
        ))
        # consume the timer so run() won't re-pick it; the step count is bumped only
        # when the row actually sends (OutboxSender), so a failed send never burns a step
        thread = await self.threads.by_id(thread_id)
        if thread is not None:
            thread.next_followup_at = None
            self.session.add(thread)
            await self.session.flush()
        return True

    async def _lead_replied_meanwhile(self, thread_id: int) -> bool:
        row = (
            await self.session.execute(
                text("SELECT last_in_at, last_out_at FROM channel_thread WHERE id=:id"),
                {"id": thread_id},
            )
        ).first()
        if not row:
            return True  # thread vanished — do not send
        last_in, last_out = row
        return last_in is not None and (last_out is None or last_in > last_out)
