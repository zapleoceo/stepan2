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

from . import guard
from .engine import DecisionEngine, _fmt_llm_meta
from .reply import (
    _BUBBLE_GAP_S,
    _DUPLICATE_RATIO,
    _REPEAT_CORRECTION,
    _most_similar_prior,
    _reply_bubble_cap,
    _split_bubbles,
    guard_decision,
    raise_manager_alert,
)
from .repository import CoachingNoteRepo, MessageRepo, OutboxRepo, ThreadRepo
from .situations import (
    AD_TEMPLATE_RE,
    FAKE_SERENDIPITY_RE,
    FOLLOWUP_BREVITY_SUFFIX,
    FOLLOWUP_NEED_ANCHOR,
    FOLLOWUP_PRODUCT_DISCIPLINE,
    FOLLOWUP_SILENT_CLICKER_EXTRA,
    NO_REPEAT_SERENDIPITY_NUDGE,
    followup_angle,
    lead_spoke_own_words,
    with_situation,
)
from .situations import (
    is_answerable_question as _is_answerable_question,
)

if TYPE_CHECKING:
    from app.modules.knowledge.service import KnowledgeService
    from app.ports.llm import LLMPort
    from app.ports.notify import NotifierPort

logger = logging.getLogger(__name__)

# Due threads: bot spoke last (lead silent), timer matured, steps remain, nothing
# already queued. Whitelist of stages the bot actively works (S1 ACTIVE_STAGES —
# `new` is excluded: an untouched lead gets a live reply, not a nudge).
_FOLLOWUP_Q = (  # noqa: S608
    "SELECT ct.id, ct.product_slug, ct.followups_sent, ct.channel_id"
    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
    " WHERE l.branch_id = :bid"
    "   AND l.stage IN ('nurturing', 'qualifying', 'presenting', 'objection')"
    "   AND l.agent_enabled = :on"
    "   AND ct.next_followup_at IS NOT NULL"
    "   AND ct.next_followup_at <= :now"
    "   AND ct.last_out_at IS NOT NULL"
    "   AND (ct.last_in_at IS NULL OR ct.last_in_at <= ct.last_out_at)"
    "   AND NOT EXISTS (SELECT 1 FROM outbox o"
    "        WHERE o.thread_id = ct.id AND o.status = 'pending')"
)

_FOLLOWUP_NUDGE = (
    "[System: the lead has not replied since your last message. This is follow-up"
    " attempt {n} of {total}. Write a short friendly follow-up in {lang} to"
    " re-engage them.\n"
    "SIGNAL THIS IS A CHECK-IN, don't just continue like the lead just spoke — a real "
    "person re-opening a quiet chat gives some small, casual sign time passed ('eh iya '"
    " / 'btw ' / 'oh iya jadi keinget' / a fresh greeting), not a bare reaction word like"
    " 'Baik' or 'Wah sip!' that implies they just said something. Never state the wait"
    " length or attempt number, never sound like an automated nag — one natural, human"
    " beat is enough, then move straight to value.\n"
    "DO NOT REPEAT A QUESTION YOU ALREADY ASKED, in ANY wording — read your own prior"
    " messages first. If your last message asked something and got no answer, do NOT ask"
    " it again reworded ('apa tujuan Kakak' vs 'Kakak pengen fokus ke mana' are the SAME"
    " question) - either give them a concrete value/answer instead, or ask about a"
    " completely different angle. CHANGE THE ANGLE each attempt for real: a concrete case"
    " tied to their stated need, a cheaper entry point (Skill Booster / bootcamp) if price"
    " was the sticking point, or a low-friction yes/no question instead of an open one.\n"
    "FACTS ONLY FROM THE KNOWLEDGE BASE: never invent an alumni story, an ROI/percentage "
    "figure, a discount, a deadline, or a class schedule that is not written there (live "
    "follow-ups fabricated 'ROI 30% in the first month' and 'an app used by thousands' — "
    "one screenshot of a made-up claim costs more than the lead). No real fact for your "
    "angle → use a general truthful line or pick another angle."
    " Return the JSON as usual.]"
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
        engine = DecisionEngine(self.session, self.branch_id, self.llm, self.knowledge)
        ctx = await engine.prepare(thread_id, workflow="followup")
        if ctx is None:
            return False
        last_in = next((m.text or "" for m in reversed(ctx.dialog) if m.direction == "in"), "")
        if guard.lead_signaled_annoyance(last_in):
            # Cancel the timer outright, not just skip: a skipped-but-still-due thread was
            # re-picked every 10-min tick forever. An annoyed lead gets NO more nudges; a
            # fresh inbound resets the cycle anyway (ingest._reset_followup_cycle).
            logger.warning(
                "followup: branch=%d thread=%d lead signaled annoyance at being contacted "
                "— cancelling further nudges", self.branch_id, thread_id)
            thread = await self.threads.by_id(thread_id)
            if thread is not None:
                thread.next_followup_at = None
                self.session.add(thread)
                await self.session.flush()
            return False
        lang = await self._lang()
        total = len(self.settings.followup_schedule_h)
        from .decision import parse_decision  # noqa: PLC0415 (avoid circular at module level)
        from .routing import FAST, SMART, pick_capability  # noqa: PLC0415
        # A nudge to a quiet lead is the lowest-stakes traffic → cheap model; escalate once if
        # the cheap model returns a broken decision so the follow-up still goes out.
        cap = pick_capability(workflow="followup", stage=None, lead_type=None,
                              last_inbound="", followup_attempt=sent_so_far)
        nudge = _FOLLOWUP_NUDGE.format(lang=lang, n=sent_so_far + 1, total=total)
        nudge += FOLLOWUP_PRODUCT_DISCIPLINE
        nudge += followup_angle(sent_so_far)
        # A follow-up that re-opens with the lead's OWN stated pain/goal re-engages far better
        # than a generic "masih tertarik?" — it proves we listened. Only when we actually have
        # their words on record (never invent one); the model already has the full needs block.
        needs = getattr(ctx, "stored_needs", None)
        anchor = ((needs.pains or needs.gains or needs.jobs)[:1] if needs else [])
        if anchor:
            nudge += FOLLOWUP_NEED_ANCHOR.format(need=anchor[0])
        if not lead_spoke_own_words(ctx.dialog):
            # a button click is not the lead speaking — no price/pitch in their follow-ups
            nudge += FOLLOWUP_SILENT_CLICKER_EXTRA
        else:
            # …and that clicker nudge asks for a short numbered menu, so its length is earned;
            # every other follow-up has no such excuse.
            nudge += FOLLOWUP_BREVITY_SUFFIX
        raw, meta = await engine.complete(
            ctx, thread_id, lang=lang, workflow="followup",
            extra_user_msg=nudge, capability=cap,
        )
        try:
            decision = parse_decision(raw)
        except ValueError:
            if cap == FAST:
                raw, meta = await engine.complete(
                    ctx, thread_id, lang=lang, workflow="followup",
                    extra_user_msg=nudge, capability=SMART)
                try:
                    decision = parse_decision(raw)
                except ValueError:
                    logger.warning(
                        "followup: unparseable decision branch=%d thread=%d — attempt not burned",
                        self.branch_id, thread_id)
                    return False
            else:
                logger.warning(
                    "followup: unparseable decision branch=%d thread=%d — attempt not burned",
                    self.branch_id, thread_id)
                return False
        if not decision.reply:
            return False
        prior, ratio = _most_similar_prior(decision.reply, ctx.dialog)
        if ratio >= _DUPLICATE_RATIO:
            logger.warning(
                "followup: branch=%d thread=%d near-duplicate nudge (ratio=%.2f) → regen",
                self.branch_id, thread_id, ratio)
            last_in = next(
                (m.text or "" for m in reversed(ctx.dialog) if m.direction == "in"), "")
            raw, meta = await engine.complete(
                ctx, thread_id, lang=lang, workflow="followup", capability=SMART,
                extra_user_msg=with_situation(
                    _REPEAT_CORRECTION.format(prior=prior, last_in=last_in), nudge))
            try:
                decision = parse_decision(raw)
            except ValueError:
                logger.warning(
                    "followup: unparseable regen branch=%d thread=%d — attempt not burned",
                    self.branch_id, thread_id)
                return False
            if not decision.reply:
                return False
        decision, meta = await guard_decision(
            self.session, self.branch_id, self.settings, self.llm,
            engine, ctx, thread_id, lang, "followup", True, decision, meta,
            situational=nudge)
        if not decision.reply:
            return False
        # Don't reuse the fake-serendipity opener ('kebetulan…', 'baru aja ada alumni…') twice
        # in one chat — it reads as a canned script the second time (thread 1754: sent 17:23
        # then 21:31). Regen once with a different opening if an earlier bot message used it too.
        if FAKE_SERENDIPITY_RE.search(decision.reply) and any(
                m.direction == "out" and FAKE_SERENDIPITY_RE.search(m.text or "")
                for m in ctx.dialog):
            raw, meta = await engine.complete(
                ctx, thread_id, lang=lang, workflow="followup", capability=SMART,
                extra_user_msg=with_situation(NO_REPEAT_SERENDIPITY_NUDGE, nudge))
            try:
                reworded = parse_decision(raw)
                if reworded.reply:
                    decision = reworded
            except ValueError:
                pass  # keep the guarded draft rather than drop the nudge
        # guard_decision can regenerate the draft too (for an UNRELATED violation elsewhere
        # in the text) — that regeneration is never re-checked against dialog history, so it
        # can silently reintroduce the exact near-duplicate the check above already rejected
        # once. Live case (thread 2087, 2026-07-08): the dedup check passed on a fresh draft
        # about a fabricated "Rp 750rb bootcamp"; guard caught the fabrication and
        # regenerated, and that correction converged word-for-word onto an answer already
        # sent as a live reply an hour earlier. Re-check the FINAL text — a repeat nudge is
        # low-value; skip sending rather than risk another regen loop, next attempt retries.
        _, post_guard_ratio = _most_similar_prior(decision.reply, ctx.dialog)
        if post_guard_ratio >= _DUPLICATE_RATIO:
            # We had nothing new to say — a nudge that would only repeat ourselves. Burn the
            # STEP, not just the attempt: leaving the timer due meant this thread regenerated
            # (and dropped) a nudge every 10-min tick — the single biggest token sink measured
            # (~1.3k followup generations/day vs ~0.6k live replies, ~48% of all input tokens).
            logger.warning(
                "followup: branch=%d thread=%d still near-duplicate after guard regen "
                "(ratio=%.2f) — dry step, backing off", self.branch_id, thread_id,
                post_guard_ratio)
            await self._burn_dry_step(thread_id, now)
            return False
        if await self._lead_replied_meanwhile(thread_id):
            return False  # race: lead answered while we were generating (S1 guard)
        # A nudge can trip needs_manager too (an unfixable guard violation, or the model
        # itself surfacing a KB gap) — it must alert same as a live reply, or a human never
        # finds out (the pre-2026-07-07 gap: this used to queue the nudge silently).
        #
        # BUT here the lead is SILENT by definition — that's why we're nudging. `manager_question`
        # ("the lead's question in their words") then has nothing to quote and the model INVENTS
        # one: thread 3072 alerted "⚠️ Jadwal kelas kapan?" 30h after the lead's last message,
        # about a schedule the BOT itself raised in its own follow-up; the owner opened the chat
        # and found no such question. 24 of 87 needs_manager alerts in a week were this. So alert
        # only when the lead's OWN last message really does ask something, and only once per
        # silence — thread 2532 pinged twice, three days apart, for one question.
        # …and the "question" must be the LEAD's, not the ad button's: the prefill text
        # contains 'biaya', so _is_answerable_question alone let thread 3926 raise a phantom
        # "Berapa biaya?" alert for a lead who never typed a word (and whose price the
        # follow-up itself had already quoted).
        if (decision.needs_manager and ctx.lead is not None
                and lead_spoke_own_words(ctx.dialog)
                and _is_answerable_question(last_in)
                and not AD_TEMPLATE_RE.match(last_in)
                and not await self._already_alerted_since_lead(thread_id)):
            await raise_manager_alert(
                self.session, self.branch_id, self.notifier, self.llm,
                thread_id, ctx.lead.id, decision, ctx.lead.phone_e164)
        # A follow-up nobody asked for must never ship a canned stub. SAFE_FALLBACK ("I'll
        # check with the team") answers a question the lead never asked (thread 1230,
        # 2026-07-17: sent into a 14-day silence), the clarify menu clarifies nothing, and
        # an unprompted hand-off promise strands the lead waiting for a call. The alert (if
        # genuinely due) is already raised above — the text itself has no value: burn the
        # step instead of sending it.
        if decision.reply.strip() in (guard.SAFE_FALLBACK, guard.CLARIFY_FALLBACK) \
                or guard.promised_handoff(decision.reply):
            logger.warning(
                "followup: branch=%d thread=%d canned stub / hand-off promise as a nudge — "
                "dropped, step burned", self.branch_id, thread_id)
            await self._burn_dry_step(thread_id, now)
            return False
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
        # consume the timer so run() won't re-pick it; the step count is bumped only
        # when the row actually sends (OutboxSender), so a failed send never burns a step
        thread = await self.threads.by_id(thread_id)
        if thread is not None:
            thread.next_followup_at = None
            self.session.add(thread)
            await self.session.flush()
        return True

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
