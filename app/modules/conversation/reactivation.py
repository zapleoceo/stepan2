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
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Lead, Outbox, StageEvent
from app.domain.enums import Stage
from app.modules.settings.service import BranchSettings

from .decision_v3 import generate
from .dossier import merge_dossier
from .engine import DecisionEngine, _fmt_llm_meta
from .guard_v3 import money_issues
from .prompt_v3 import build_messages_v3
from .reply import _BUBBLE_GAP_S, _split_bubbles
from .repository import DossierRepo, OutboxRepo, ThreadRepo
from .routing import FAST

if TYPE_CHECKING:
    from app.modules.knowledge.service import KnowledgeService
    from app.ports.llm import LLMPort
    from app.ports.notify import NotifierPort

logger = logging.getLogger(__name__)

# A hard refusal (not a postponement): declined interest or backed out. Postponers
# ('nanti dulu', 'nabung dulu') stay eligible — see the suppress comment below.
_HARD_REFUSAL_RE = re.compile(
    r"(belum|blm|ga|gak|nggak|ngga|ndak|tidak|tdk)\s*(tertarik|minat|berminat)"
    r"|(tidak|tdk|gak|ga|nggak|ngga|ndak|gk)\s*jadi\b|sudah\s*tidak", re.IGNORECASE)

MIN_DORMANT_DAYS = 3
# 2026-07-22: widened from 21 to cover the account's full IG history (oldest thread predates
# Stepan, ~460 days) at Dima's explicit request — reach every stalled lead, not just recent
# ones. Pace stays SLOW on purpose: this does NOT change BATCH_PER_RUN/cron cadence/CAP/GAP, so
# the ~1283-thread backlog drains at the same ~40 new touches/day this always ran at, and
# reactivation is already lowest send-priority behind live replies (see outbox has_reply
# ordering) — a wider window means more ELIGIBLE leads, not a faster or bigger blast.
MAX_DORMANT_DAYS = 550
REACTIVATION_GAP_DAYS = 14
REACTIVATION_CAP = 2
BATCH_PER_RUN = 20
_REASON = "reactivation"

# Stalled leads with a real dialog, quiet for [MIN, MAX] days, not reactivated recently or too
# often, not annoyed. `now - MAX` bounds the archaeology; `now - MIN` enforces the cooldown.
# NOT limited to stage='dormant': that label is model self-reported (same unreliable pattern as
# open_objections) and a lead that stalls in qualifying/nurturing without the model ever calling
# it dormant was invisible to this whole safety net — followups exhaust at 120h (5 days) and
# nothing picked it up after. Any non-terminal stage that's been quiet long enough is eligible;
# 'ready'/'handed_off'/'manager' are the only stages where the bot is deliberately silent.
_DUE_Q = (  # noqa: S608
    "SELECT ct.id, ct.product_slug, l.id"
    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
    " WHERE l.branch_id = :bid AND l.stage NOT IN ('ready', 'handed_off', 'manager')"
    "   AND l.is_blocked = false"
    "   AND ct.last_in_at IS NOT NULL"
    "   AND ct.last_in_at < :min_cutoff AND ct.last_in_at > :max_cutoff"
    "   AND (SELECT count(*) FROM stage_event se WHERE se.lead_id = l.id"
    "        AND se.reason = :reason) < :cap"
    "   AND NOT EXISTS (SELECT 1 FROM stage_event se WHERE se.lead_id = l.id"
    "        AND se.reason = :reason AND se.created_at > :gap_cutoff)"
    "   AND NOT EXISTS (SELECT 1 FROM outbox o WHERE o.thread_id = ct.id AND o.status = 'pending')"
    " ORDER BY ct.last_in_at DESC LIMIT :batch"
)

_REACTIVATION_FRAMING = (
    "[System: this lead went quiet days ago and is parked as dormant. This is ONE personalized "
    "attempt to earn a single reply — not to close. Reopen on what the dossier says they "
    "actually cared about, or the exact point the conversation stalled, and give ONE concrete "
    "reason to look again that they have not already been given. Signal that time has passed, "
    "the way a person re-opening a quiet chat does. If you have no genuinely fresh hook, "
    "return an empty reply — a stale echo costs more than staying quiet.]"
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
        """One re-engagement touch, decided from the dossier.

        v2 read the last three messages through two refusal regexes and policed the draft with
        SequenceMatcher. The dossier already records how firmly this lead said no and what they
        have already been told, so both go away — and unlike v2, what the touch learns is
        written back."""
        engine = DecisionEngine(self.session, self.branch_id, self.llm, self.knowledge)
        ctx = await engine.prepare(thread_id, workflow="followup")
        if ctx is None:
            return False
        dossiers = DossierRepo(self.session, self.branch_id)
        stored = await dossiers.load(lead_id)
        if stored.refusal in ("blunt", "vague"):
            # A made choice. Touching again earns a report, and a polite close-out is still a
            # close-out. Only the postponer — the warmest dormant segment, who literally asked
            # for later — is worth waking.
            await self._suppress(thread_id, lead_id, now)
            return False

        branch = await self.session.get(Branch, self.branch_id)
        lang = branch.lang if branch is not None else "id"
        context = await engine.kb_context(ctx, thread_id, light=True)
        messages = build_messages_v3(context, ctx.dialog, lang, stored,
                                     now_block=await engine._now_block())  # noqa: SLF001
        messages.append({"role": "user", "content": _REACTIVATION_FRAMING})
        # The lowest-stakes traffic there is: a month-old dormant lead. Draft cheap; the money
        # gate below is the only thing that can stop it, and it costs nothing when there is no
        # figure in the text.
        decision, meta = await generate(
            engine, ctx, messages, thread_id, workflow="followup",
            capability=FAST, branch_id=self.branch_id)
        if decision is None:
            return False  # transient bad JSON — retries next run, not suppressed
        if not decision.reply.strip() or money_issues(decision.reply, context):
            # No fresh hook, or a figure we cannot stand behind. Suppress so this lead is not
            # re-picked and re-generated every run, blocking a slot another dormant lead could use.
            await self._suppress(thread_id, lead_id, now)
            return False

        await dossiers.save(lead_id, merge_dossier(stored, decision.dossier))
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
