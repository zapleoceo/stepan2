"""Dormant-lead reactivation — ONE personalized re-engagement, adapted to the lead's own past
dialog, for leads who went quiet and were parked as dormant.

Distinct from FollowupService (which only works ACTIVE_STAGES on the bot's own send timer):
this harvests DORMANT leads that already exhausted follow-ups or self-closed, waits a real
cooldown, and takes a single fresh run at them built entirely on what THEY said. Opt-in per
branch (reactivation_enabled, default off) and heavily rate-limited — a dormant lead is cold,
so a mistimed or spammy touch is a report risk, not a sale.

Anti-ban / anti-spam invariants:
- only leads who actually SPOKE (a real conversation to adapt to), never silent ad-clicks;
- a cooldown window [MIN, MAX] days since last activity — not too soon, not archaeology;
- at most REACTIVATION_CAP touches ever, and never twice within REACTIVATION_GAP_DAYS;
- an annoyed lead is skipped forever;
- a small per-run batch, quiet hours respected by the outbox send layer."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Lead, Outbox, StageEvent
from app.domain.enums import Stage
from app.modules.settings.service import BranchSettings

from . import guard
from .decision import parse_decision
from .engine import DecisionEngine, _fmt_llm_meta
from .reply import (
    _BUBBLE_GAP_S,
    _DUPLICATE_RATIO,
    _most_similar_prior,
    _split_bubbles,
    guard_decision,
)
from .repository import OutboxRepo, ThreadRepo
from .routing import SMART
from .situations import SOFT_NO_RE, lead_spoke_own_words

if TYPE_CHECKING:
    from app.modules.knowledge.service import KnowledgeService
    from app.ports.llm import LLMPort
    from app.ports.notify import NotifierPort

logger = logging.getLogger(__name__)

MIN_DORMANT_DAYS = 3
MAX_DORMANT_DAYS = 21
REACTIVATION_GAP_DAYS = 14
REACTIVATION_CAP = 2
BATCH_PER_RUN = 20
_REASON = "reactivation"

# Dormant leads with a real dialog, quiet for [MIN, MAX] days, not reactivated recently or too
# often, not annoyed. `now - MAX` bounds the archaeology; `now - MIN` enforces the cooldown.
_DUE_Q = (  # noqa: S608
    "SELECT ct.id, ct.product_slug, l.id"
    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
    " WHERE l.branch_id = :bid AND l.stage = 'dormant' AND l.is_blocked = false"
    "   AND ct.last_in_at IS NOT NULL"
    "   AND ct.last_in_at < :min_cutoff AND ct.last_in_at > :max_cutoff"
    "   AND (SELECT count(*) FROM stage_event se WHERE se.lead_id = l.id"
    "        AND se.reason = :reason) < :cap"
    "   AND NOT EXISTS (SELECT 1 FROM stage_event se WHERE se.lead_id = l.id"
    "        AND se.reason = :reason AND se.created_at > :gap_cutoff)"
    "   AND NOT EXISTS (SELECT 1 FROM outbox o WHERE o.thread_id = ct.id AND o.status = 'pending')"
    " ORDER BY ct.last_in_at DESC LIMIT :batch"
)

_REACTIVATION_NUDGE = (
    "[System: this lead went quiet days ago and is parked as dormant. This is ONE personalized "
    "reactivation in {lang} - your only job is to earn a single reply, not to close. READ the "
    "prior dialog first and build the message ON WHAT THEY ACTUALLY SAID: reopen on their own "
    "stated goal/pain or the exact point the chat stalled, and give ONE fresh, concrete reason "
    "to look again - a real upcoming intake/event from the KB, a genuine answer to the doubt "
    "they left on, or a low-friction yes/no. Signal that time has passed ('eh Kak, kepikiran "
    "obrolan kita soal ...') like a real person re-opening a quiet chat, never a cold generic "
    "blast and never a bare 'masih tertarik?'. ONE short message, warm, no pressure. FACTS ONLY "
    "from the KB - never invent an intake date, a discount, a case, or a number. If you have no "
    "real fresh hook for this lead, return an empty reply and we skip them. Return JSON as usual.]"
)


class ReactivationService:
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
        self.outbox = OutboxRepo(session, branch_id)

    async def due(self, now: datetime) -> list[tuple[int, str | None, int]]:
        if not self.settings.agent_enabled or not self.settings.reactivation_enabled:
            return []
        rows = (await self.session.execute(text(_DUE_Q), {
            "bid": self.branch_id,
            "min_cutoff": now - timedelta(days=MIN_DORMANT_DAYS),
            "max_cutoff": now - timedelta(days=MAX_DORMANT_DAYS),
            "gap_cutoff": now - timedelta(days=REACTIVATION_GAP_DAYS),
            "reason": _REASON, "cap": REACTIVATION_CAP, "batch": BATCH_PER_RUN,
        })).all()
        return [(tid, slug, lid) for tid, slug, lid in rows]

    async def reactivate_one(self, thread_id: int, lead_id: int) -> bool:
        now = datetime.now(UTC).replace(tzinfo=None)
        try:
            return await self._reactivate(thread_id, lead_id, now)
        except Exception:
            logger.exception(
                "reactivation failed branch=%d thread=%d", self.branch_id, thread_id)
            return False

    async def _reactivate(self, thread_id: int, lead_id: int, now: datetime) -> bool:
        engine = DecisionEngine(self.session, self.branch_id, self.llm, self.knowledge)
        ctx = await engine.prepare(thread_id, workflow="followup")
        if ctx is None:
            return False
        if not lead_spoke_own_words(ctx.dialog):
            return False  # never spoke → nothing to adapt to; leave dormant
        recent_in = [m.text or "" for m in reversed(ctx.dialog) if m.direction == "in"][:3]
        # A lead who declined ('gak tertarik', 'nanti dulu') or got annoyed made a CHOICE -
        # re-nudging them soon is exactly the spam that earns a report. Scan the last few
        # messages, not just the last: a refusal is often followed by a polite 'makasih' /
        # 'oke' (thread 3060: 'saya sudah tidak tertarik' then 'terimakasih'). Suppress them
        # (record the touch so the gap/cap excludes them for 14d) and send nothing.
        if any(guard.lead_signaled_annoyance(t) or SOFT_NO_RE.search(t) for t in recent_in):
            await self._suppress(thread_id, lead_id, now)
            return False
        branch = await self.session.get(Branch, self.branch_id)
        lang = branch.lang if branch is not None else "id"
        nudge = _REACTIVATION_NUDGE.format(lang=lang)
        raw, meta = await engine.complete(
            ctx, thread_id, lang=lang, workflow="followup",
            extra_user_msg=nudge, capability=SMART)
        try:
            decision = parse_decision(raw)
        except ValueError:
            return False
        # No usable re-engagement (model found no fresh hook, or it would just repeat / stall):
        # suppress so this lead isn't re-picked and re-generated every run, blocking a slot other
        # dormant leads could use. A transient bad-JSON above is NOT suppressed — that retries.
        if not decision.reply:
            await self._suppress(thread_id, lead_id, now)
            return False
        _, ratio = _most_similar_prior(decision.reply, ctx.dialog)
        if ratio >= _DUPLICATE_RATIO:
            await self._suppress(thread_id, lead_id, now)
            return False  # nothing new to say → don't send a stale echo
        decision, meta = await guard_decision(
            self.session, self.branch_id, self.settings, self.llm,
            engine, ctx, thread_id, lang, "followup", True, decision, meta, situational=nudge)
        if not decision.reply or decision.reply.strip() in (
                guard.SAFE_FALLBACK, guard.CLARIFY_FALLBACK) \
                or guard.promised_handoff(decision.reply):
            await self._suppress(thread_id, lead_id, now)
            return False
        meta_line = _fmt_llm_meta(meta)
        for i, bubble in enumerate(_split_bubbles(decision.reply)):
            await self.outbox.add(Outbox(
                branch_id=self.branch_id, thread_id=thread_id, text=bubble, source=_REASON,
                scheduled_at=now + timedelta(seconds=i * _BUBBLE_GAP_S), llm_info=meta_line))
        # Wake the lead so a reply is actually handled, and record the touch so due() won't
        # re-pick it (cap + gap both key off this StageEvent).
        lead = await self.session.get(Lead, lead_id)
        if lead is not None:
            self.session.add(StageEvent(
                branch_id=self.branch_id, lead_id=lead_id, thread_id=thread_id,
                from_stage=str(lead.stage), to_stage=str(Stage.NURTURING),
                actor="system", reason=_REASON))
            lead.stage = Stage.NURTURING
            lead.agent_enabled = True
            self.session.add(lead)
        await self.session.flush()
        logger.info("reactivation queued branch=%d thread=%d", self.branch_id, thread_id)
        return True

    async def _suppress(self, thread_id: int, lead_id: int, now: datetime) -> None:
        """Record a reactivation touch WITHOUT sending, so a declined/annoyed lead isn't
        re-picked every run (the gap/cap key off this StageEvent). The lead stays dormant."""
        lead = await self.session.get(Lead, lead_id)
        if lead is None:
            return
        self.session.add(StageEvent(
            branch_id=self.branch_id, lead_id=lead_id, thread_id=thread_id,
            from_stage=str(lead.stage), to_stage=str(lead.stage),
            actor="system", reason=_REASON, created_at=now))
        await self.session.flush()
