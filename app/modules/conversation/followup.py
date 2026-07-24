"""Follow-up scheduler — S1 semantics: the timer lives off the BOT's last send.

Arming happens in OutboxSender after a successful bot send (next_followup_at =
sent_at + schedule[followups_sent]); a fresh inbound resets the cycle in ingest.
This service only harvests DUE threads: re-checks the lead is still silent, skips
threads with queued outbox, generates the nudge, increments followups_sent and
queues the row. Exhaustion → dormant happens in OutboxSender after the last send.
One broken thread never aborts the rest (per-thread try)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Lead, Outbox, StageEvent
from app.domain.enums import Stage
from app.modules.settings.service import BranchSettings, get_channel_settings

from .decision import generate
from .delivery import _BUBBLE_GAP_S, _reply_bubble_cap, _split_bubbles
from .discovery import extract_discovery
from .dossier import merge_dossier
from .engine import DecisionEngine, _fmt_llm_meta
from .free_mode import build_messages_free
from .money_gate import PITCH_CORRECTION, money_issues, uninvited_price
from .repository import (
    CoachingNoteRepo,
    DossierRepo,
    MessageRepo,
    OutboxRepo,
    ThreadRepo,
)
from .routing import FAST, SMART

if TYPE_CHECKING:
    from app.modules.knowledge.service import KnowledgeService
    from app.ports.llm import LLMPort
    from app.ports.notify import NotifierPort

logger = logging.getLogger(__name__)

# Cooldown after a failed generation attempt (broker error/timeout) — see queue_one's except
# block. Short enough that a real broker blip only costs one missed cycle, long enough that a
# sustained outage doesn't get re-billed every 10-min cron tick.
_FAILURE_BACKOFF_MIN = 30

FOLLOWUP_FRAMING = """\
[System: the lead has gone quiet — there is no new message to answer this turn. This is nudge \
{n} of {total}. Write ONE short message that earns a reply: something concrete they have NOT \
heard yet, tied to what the dossier says they care about. Never "masih minat?" or "ada yang \
bisa dibantu?" — that is begging, not selling. FACTS ONLY FROM THE KNOWLEDGE BASE above: \
never invent an alumni story, an ROI/percentage figure, a case study tailored to their \
industry, a discount, or a deadline that is not written there — live nudges fabricated \
"ROI 30%" and a "manufacturing-plant Meta Ads case" and earned zero replies; the KB's real \
differentiators always beat invented ones. ESCALATE THE ANGLE by nudge number so each touch \
is a new reason, not a repeat: an early nudge gives a fresh concrete hook or a real case from \
the KB; a middle nudge invites them to the low-cost Demo Event — a real, cheap way to see it \
live before deciding; a late nudge names a GENUINE deadline (the nearest intake or the \
book-now window) and asks for their WhatsApp to secure a spot — only if that deadline is real \
in the KB. {refusal_note}If you have nothing genuinely new to say, return an empty reply \
rather than padding — a nudge that repeats you costs more than silence.]"""

_REFUSAL_NOTES = {
    "soft": "They already said they'd think about it, so do NOT argue or re-pitch: one light, "
            "easy-to-ignore touch that gives them a reason to come back. ",
    "vague": "They already closed the conversation politely — keep this minimal and graceful, "
             "and make it easy to say nothing at all. ",
}


def followup_framing(attempt: int, total: int, refusal: str) -> str:
    """The extra turn-instruction for a nudge. Refusal degree changes the tone, not the fact
    that we're writing — except for a blunt no, which the caller drops before it gets here."""
    return FOLLOWUP_FRAMING.format(
        n=attempt, total=total, refusal_note=_REFUSAL_NOTES.get(refusal, ""))

# Due threads: bot spoke last (lead silent), timer matured, steps remain, nothing
# already queued. Whitelist of stages the bot actively works (S1 ACTIVE_STAGES —
# `new` is excluded: an untouched lead gets a live reply, not a nudge).
_FOLLOWUP_Q = (  # noqa: S608
    "SELECT ct.id, ct.product_slug, ct.followups_sent, ct.channel_id"
    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
    " WHERE l.branch_id = :bid"
    "   AND l.stage IN ('qualifying', 'presenting', 'objection', 'nurturing')"
    "   AND l.agent_enabled = :on"
    "   AND ct.next_followup_at IS NOT NULL"
    "   AND ct.next_followup_at <= :now"
    "   AND ct.last_out_at IS NOT NULL"
    "   AND (ct.last_in_at IS NULL OR ct.last_in_at <= ct.last_out_at)"
    "   AND NOT EXISTS (SELECT 1 FROM outbox o"
    "        WHERE o.thread_id = ct.id AND o.status = 'pending')"
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
        notifier: NotifierPort | None = None,
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.llm = llm
        self.knowledge = knowledge
        self.settings = settings
        self.notifier = notifier
        self.threads = ThreadRepo(session, branch_id)
        self.messages = MessageRepo(session, branch_id)
        self.outbox = OutboxRepo(session, branch_id)
        self.coaching = CoachingNoteRepo(session, branch_id)

    async def due_threads(self, now: datetime) -> list[tuple[int, str | None, int]]:
        """Threads eligible for a follow-up right now — a pure query, no broker/DB writes.

        Follow-up enablement and the step-count bound are per-connector: a thread's schedule
        comes from its channel (Meta's shorter cadence vs Instagram's). The branch agent
        kill-switch still gates everything. Quiet hours do NOT filter this list — only the
        SEND (OutboxSender.send_next) holds a follow-up-sourced row until quiet hours end."""
        if not self.settings.agent_enabled:
            return []  # branch global OFF: no generation at all
        rows = (
            await self.session.execute(
                text(_FOLLOWUP_Q),
                {"bid": self.branch_id, "now": now, "on": True},
            )
        ).all()
        due: list[tuple[int, str | None, int]] = []
        for tid, product_slug, followups_sent, channel_id in rows:
            ch = await get_channel_settings(self.session, self.branch_id, channel_id)
            if not ch.followup_enabled or not ch.followup_schedule_h:
                continue
            if followups_sent >= len(ch.followup_schedule_h):
                continue  # this connector's schedule exhausted for the thread
            due.append((tid, product_slug, followups_sent))
        return due

    async def queue_one(
        self, thread_id: int, product_slug: str | None, sent_so_far: int,
    ) -> bool:
        """Generate+queue a single due thread's nudge; isolates one thread's failure so the
        caller can commit per-thread (see worker/main.py's schedule_followups, which opens
        a fresh session/transaction per call — a job-timeout kill mid-cycle used to roll
        back every already-generated follow-up from that cycle because they all shared one
        open transaction across the whole branch's due list, 2026-07-07)."""
        now = datetime.now(UTC).replace(tzinfo=None)
        try:
            return await self._queue_followup(thread_id, product_slug, sent_so_far, now)
        except Exception:
            logger.exception(
                "followup failed branch=%d thread=%d", self.branch_id, thread_id
            )
            # Cost leak (2026-07-22): a failed attempt (broker timeout/error) left
            # next_followup_at untouched, so the SAME thread got re-picked and re-billed every
            # 10-min tick until it happened to succeed — 763 followup broker calls that day for
            # only 196 sent messages, mostly wasted retries during a broker-instability window.
            # Push the timer forward so a failure gets one cooldown before the next attempt,
            # instead of an immediate, likely-doomed retry.
            thread = await self.threads.by_id(thread_id)
            if thread is not None:
                thread.next_followup_at = now + timedelta(minutes=_FAILURE_BACKOFF_MIN)
                self.session.add(thread)
                await self.session.flush()
            return False

    async def run(self) -> int:
        """Queue nudges for every due thread in the CALLER's session/transaction.

        Convenience for tests and other single-session callers. The worker does NOT use
        this — schedule_followups calls due_threads() then queue_one() per thread, each in
        its own transaction, so one slow/killed thread can't discard others' progress."""
        now = datetime.now(UTC).replace(tzinfo=None)
        rows = await self.due_threads(now)
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
        """One nudge, generated from the dossier.

        v2 assembled this from five hardcoded suffixes plus a four-rung angle ladder, then
        policed the result with SequenceMatcher regens — and never wrote back a word of what
        it learned, so a follow-up that uncovered an objection threw it away. Here the dossier
        both drives the nudge and records it."""
        engine = DecisionEngine(self.session, self.branch_id, self.llm, self.knowledge)
        ctx = await engine.prepare(thread_id, workflow="followup")
        if ctx is None:
            return False
        dossiers = DossierRepo(self.session, self.branch_id)
        lead_id = ctx.lead.id if ctx.lead is not None else None
        stored = await dossiers.load(lead_id)
        if stored.refusal == "blunt":
            # A flat no ends outreach. v2 needed a regex over the last message to notice; the
            # dossier carries it, so it also survives the lead never repeating themselves.
            logger.info("followup: branch=%d thread=%d lead refused outright — no more nudges",
                        self.branch_id, thread_id)
            await self._cancel_timer(thread_id)
            return False

        lang = await self._lang()
        context = await engine.free_kb_context()
        messages = build_messages_free(
            context, ctx.dialog, lang, stored,
            coaching_notes=await self.coaching.active_manager_notes(),
            manager_note=ctx.lead.manager_note if ctx.lead is not None else None,
            now_block=await engine._now_block())  # noqa: SLF001 — engine owns the branch clock
        messages.append({"role": "user", "content": followup_framing(
            sent_so_far + 1, len(self.settings.followup_schedule_h), stored.refusal)})
        capability = SMART if stored.open_objections() else FAST

        decision, meta = await generate(
            engine, ctx, messages, thread_id, workflow="followup",
            capability=capability, branch_id=self.branch_id)
        if decision is None:
            return False
        if uninvited_price(decision.reply, stored):
            # A nudge is never a reply to a fresh question — a price in one is always
            # volunteered (thread 4849). One rewrite, same as reply.py's money gate; if it
            # still quotes a figure, drop the nudge rather than send it.
            logger.info("followup pitch gate branch=%d thread=%d: uninvited price",
                        self.branch_id, thread_id)
            regen_messages = [*messages, {"role": "user", "content": PITCH_CORRECTION}]
            fixed, meta = await generate(
                engine, ctx, regen_messages, thread_id, workflow="followup",
                capability=SMART, branch_id=self.branch_id)
            if fixed is None or uninvited_price(fixed.reply, stored):
                logger.warning("followup pitch gate unfixable branch=%d thread=%d — dropped",
                                self.branch_id, thread_id)
                await self._burn_dry_step(thread_id, now)
                return False
            decision = fixed
        if not decision.reply.strip():
            # The model was told to say nothing rather than repeat itself. Burn the step: a
            # thread with nothing left to say used to regenerate a dropped nudge every tick,
            # the single biggest token sink measured (~48% of all input tokens).
            logger.info("followup: branch=%d thread=%d nothing new to say — step burned",
                        self.branch_id, thread_id)
            await self._burn_dry_step(thread_id, now)
            return False
        issues = money_issues(decision.reply, context)
        if issues:
            # Nobody asked, so there is nothing to escalate — just don't send a wrong number.
            logger.warning("followup: branch=%d thread=%d ungrounded money claim (%s) — dropped",
                           self.branch_id, thread_id, "; ".join(issues))
            await self._burn_dry_step(thread_id, now)
            return False
        if await self._lead_replied_meanwhile(thread_id):
            return False  # race: the lead answered while we were generating
        if decision.needs_human:
            # A nudge turn in which the model itself decided a human is needed used to set the
            # flag into the void — nothing read it here, no alert fired, the nudge shipped
            # anyway (the exact pre-2026-07-07 gap raise_manager_alert's docstring warns both
            # paths must cover). Alert the manager and drop the nudge: needs_human means the
            # bot should not keep talking.
            from .delivery import raise_manager_alert  # noqa: PLC0415
            await raise_manager_alert(
                self.session, self.branch_id, self.notifier, self.llm,
                thread_id, lead_id, decision.to_legacy(stored),
                ctx.lead.phone_e164 if ctx.lead is not None else None)
            await self._burn_dry_step(thread_id, now)
            return False

        merged = merge_dossier(stored, decision.dossier)
        if not merged.has_discovery():
            # Same backstop as reply.py's decide() — skip once discovery is already complete.
            extra = await extract_discovery(
                self.llm, ctx.dialog, merged, lang, self.branch_id, thread_id,
                budget=ctx.budget)
            merged = merge_dossier(merged, extra)
        await dossiers.save(lead_id, merged)
        meta_line = _fmt_llm_meta(meta)
        for i, bubble in enumerate(
            _split_bubbles(decision.reply, max_parts=_reply_bubble_cap(decision.reply))):
            await self.outbox.add(Outbox(
                branch_id=self.branch_id,
                thread_id=thread_id,
                text=bubble,
                source="followup",
                scheduled_at=now + timedelta(seconds=i * _BUBBLE_GAP_S),
                llm_info=meta_line,
            ))
        # Consume the timer so run() won't re-pick it; the step count is bumped only when the
        # row actually sends (OutboxSender), so a failed send never burns a step.
        await self._cancel_timer(thread_id)
        return True

    async def _cancel_timer(self, thread_id: int) -> None:
        thread = await self.threads.by_id(thread_id)
        if thread is not None:
            thread.next_followup_at = None
            self.session.add(thread)
            await self.session.flush()

    async def _burn_dry_step(self, thread_id: int, now: datetime) -> None:
        """A nudge we couldn't compose without repeating ourselves shouldn't exist — consume
        the schedule step and arm the NEXT one (hours away), or wind down to dormant when the
        schedule is exhausted. Mirrors OutboxSender._plan_followup/_to_dormant semantics, just
        without a send."""
        thread = await self.threads.by_id(thread_id)
        if thread is None:
            return
        thread.followups_sent += 1
        schedule = self.settings.followup_schedule_h
        if self.settings.followup_enabled and schedule \
                and thread.followups_sent < len(schedule):
            thread.next_followup_at = now + timedelta(
                hours=schedule[thread.followups_sent])
        else:
            thread.next_followup_at = None
            lead = await self.session.get(Lead, thread.lead_id)
            if lead is not None and lead.stage != Stage.DORMANT:
                self.session.add(StageEvent(
                    branch_id=self.branch_id, lead_id=lead.id, thread_id=thread.id,
                    from_stage=str(lead.stage), to_stage=str(Stage.DORMANT),
                    actor="system", reason="followup schedule exhausted (dry)",
                ))
                lead.stage = Stage.DORMANT
                lead.agent_enabled = False
                self.session.add(lead)
        self.session.add(thread)
        await self.session.flush()

    async def _already_alerted_since_lead(self, thread_id: int) -> bool:
        """True when a needs_manager alert already went out for this same silence — re-raising
        the same gap on every follow-up cycle just buries the owner in duplicates."""
        row = (
            await self.session.execute(
                text(
                    "SELECT 1 FROM manager_alert ma"
                    " JOIN channel_thread ct ON ct.id = ma.thread_id"
                    " WHERE ma.thread_id = :t AND ma.kind = 'needs_manager'"
                    "   AND (ct.last_in_at IS NULL OR ma.created_at > ct.last_in_at)"
                    " LIMIT 1"
                ),
                {"t": thread_id},
            )
        ).first()
        return row is not None

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
