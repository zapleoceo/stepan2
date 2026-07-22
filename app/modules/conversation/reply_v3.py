"""v3 reply generation — one call over a dossier, instead of eight rewrites of one draft.

v2 put every draft through need-payoff regen → dedup regen → guard regens → clarify → premature
-contact regen → promised-handoff → answer-don't-escalate regen → phone-gate → critic regen.
Each step fixed its own incident and knew nothing of the others, so what finally reached the
lead could be the fourth rewrite of one answer, written to three conflicting corrections at
once — or a canned stub.

Here a turn is: load what we know → pick the tier → generate once → merge what was learned.
Repetition is prevented by telling the model what it has already used (dossier `spent`) rather
than by diffing its output against its own history. The quality gate is a separate concern
(guard_v3), deliberately not another rewrite pass.

Everything downstream — enqueue, bubbles, stage events, hand-off, outbox — is shared with v2:
this replaces how a reply is produced, not how it is delivered."""
from __future__ import annotations

import logging

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.channels.ig_parse import IMAGE_PENDING_PH, VOICE_PENDING_PH
from app.adapters.db.models import Branch
from app.modules.knowledge.service import KnowledgeService
from app.modules.settings.service import BranchSettings
from app.ports.llm import LLMPort
from app.ports.notify import NotifierPort

from .decision import Decision
from .decision_v3 import DecisionV3, parse_decision_v3
from .dossier import merge_dossier
from .engine import DecisionEngine
from .prompt import lead_name_hint, source_hint
from .prompt_v3 import build_messages_v3
from .reply import _script_lang
from .repository import CoachingNoteRepo, DossierRepo, MessageRepo, ThreadRepo
from .routing_v3 import FAST, SMART, pick_capability_v3

logger = logging.getLogger(__name__)


class ReplyServiceV3:
    """Produce one reply for one thread, and remember what it learned."""

    def __init__(  # noqa: PLR0913
        self,
        session: AsyncSession,
        branch_id: int,
        llm: LLMPort,
        knowledge: KnowledgeService,
        branch_settings: BranchSettings | None = None,
        notifier: NotifierPort | None = None,
        broker_budget_s: float | None = None,
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.llm = llm
        self.knowledge = knowledge
        self.settings = branch_settings
        self._notifier = notifier
        self._broker_budget_s = broker_budget_s
        self.threads = ThreadRepo(session, branch_id)
        self.messages = MessageRepo(session, branch_id)
        self.coaching = CoachingNoteRepo(session, branch_id)
        self.dossiers = DossierRepo(session, branch_id)
        self.last_decision: DecisionV3 | None = None  # the raw v3 answer, for logging/tests

    async def decide(self, thread_id: int, workflow: str = "reply") -> Decision | None:
        """Run one turn. None when the thread is foreign, silent, or waiting on media."""
        engine = DecisionEngine(self.session, self.branch_id, self.llm, self.knowledge,
                                broker_budget_s=self._broker_budget_s)
        ctx = await engine.prepare(thread_id, workflow=workflow)
        if ctx is None or _awaiting_media(ctx.dialog):
            return None

        lead = ctx.lead
        stored = await self.dossiers.load(lead.id if lead is not None else None)
        last_in = next((m for m in reversed(ctx.dialog) if m.direction == "in"), None)
        script_lang = _script_lang(last_in.text if last_in is not None else "")
        lang = script_lang or await self._lang()
        if script_lang and lead is not None and lead.preferred_language != script_lang:
            lead.preferred_language = script_lang
            self.session.add(lead)

        is_first_reply = not any(m.direction == "out" for m in ctx.dialog)
        capability = pick_capability_v3(stored, is_first_reply=is_first_reply)
        messages = build_messages_v3(
            await engine.kb_context(ctx, thread_id, light=False),
            ctx.dialog, lang, stored,
            coaching_notes=await self.coaching.active_manager_notes(),
            source_block=source_hint(ctx.thread.lead_source),
            name_block=lead_name_hint(lead.display_name if lead is not None else None),
            manager_note=lead.manager_note if lead is not None else None,
            now_block=await engine._now_block(),  # noqa: SLF001 — branch-local clock, engine owns it
        )

        decision = await self._generate(engine, ctx, messages, thread_id,
                                        workflow=workflow, capability=capability)
        if decision is None:
            return None

        merged = merge_dossier(stored, decision.dossier)
        await self.dossiers.save(lead.id if lead is not None else None, merged)
        self.last_decision = decision
        logger.info("v3 branch=%d thread=%d move=%s tier=%s first=%s",
                    self.branch_id, thread_id, decision.move, capability, is_first_reply)
        return decision.to_legacy(merged)

    async def _generate(  # noqa: PLR0913
        self, engine: DecisionEngine, ctx, messages: list[dict], thread_id: int, *,  # noqa: ANN001
        workflow: str, capability: str,
    ) -> DecisionV3 | None:
        """Generate once; a cheap model that returns unparseable JSON is retried on the strong
        one. Two attempts is the ceiling — a third rewrite is what v2 did, and it is what
        produced answers written to conflicting corrections."""
        raw, _meta = await engine.run(ctx, messages, thread_id,
                                      workflow=workflow, capability=capability)
        try:
            return parse_decision_v3(raw)
        except ValueError:
            if capability != FAST:
                logger.warning("v3: unparseable smart decision branch=%d thread=%d — skip",
                               self.branch_id, thread_id)
                return None
        logger.warning("v3: unparseable fast decision branch=%d thread=%d — retry on smart",
                       self.branch_id, thread_id)
        raw, _meta = await engine.run(ctx, messages, thread_id,
                                      workflow=workflow, capability=SMART)
        try:
            return parse_decision_v3(raw)
        except ValueError:
            logger.warning("v3: unparseable on both tiers branch=%d thread=%d — skip",
                           self.branch_id, thread_id)
            return None

    async def _lang(self) -> str:
        branch = await self.session.get(Branch, self.branch_id)
        return branch.lang if branch is not None else "id"


def _awaiting_media(dialog: list) -> bool:
    """The newest inbound is a voice/image the broker hasn't transcribed yet — hold the turn so
    the reply answers the CONTENT, not the placeholder."""
    newest = dialog[-1] if dialog else None
    return (newest is not None and newest.direction == "in"
            and (newest.text or "").strip() in (VOICE_PENDING_PH, IMAGE_PENDING_PH))
