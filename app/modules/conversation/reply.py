"""v3 reply generation — one call over a dossier, instead of eight rewrites of one draft.

v2 put every draft through need-payoff regen → dedup regen → guard regens → clarify → premature
-contact regen → promised-handoff → answer-don't-escalate regen → phone-gate → critic regen.
Each step fixed its own incident and knew nothing of the others, so what finally reached the
lead could be the fourth rewrite of one answer, written to three conflicting corrections at
once — or a canned stub.

Here a turn is: load what we know → pick the tier → generate once → merge what was learned.
Repetition is prevented by telling the model what it has already used (dossier `spent`) rather
than by diffing its output against its own history. The quality gate is a separate concern
(money_gate), deliberately not another rewrite pass.

Everything downstream — enqueue, bubbles, stage events, hand-off, outbox — is shared with v2:
this replaces how a reply is produced, not how it is delivered."""
from __future__ import annotations

import logging
from dataclasses import replace

from app.adapters.channels.ig_parse import IMAGE_PENDING_PH, VOICE_PENDING_PH

from . import critic
from .contract import build_messages_v3
from .decision import Decision, TurnDecision, generate
from .delivery import ReplyDelivery, _script_lang
from .dossier import merge_dossier
from .engine import _ASSISTANT_LAST_NUDGE, DecisionEngine
from .money_gate import MONEY_CORRECTION, MONEY_ESCALATION_REASON, money_issues
from .prompt import lead_name_hint, source_hint
from .repository import DossierRepo
from .routing import SMART, pick_capability

logger = logging.getLogger(__name__)

class ReplyService(ReplyDelivery):
    """Produce one reply for one thread, and remember what it learned.

    Splits cleanly from delivery: this module decides WHAT to say and records what it learned;
    ReplyDelivery (delivery.py) owns getting it to the lead. Neither knows the other's
    internals."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self.dossiers = DossierRepo(self.session, self.branch_id)
        self.last_decision: TurnDecision | None = None  # the raw v3 answer, for logging/tests

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
        lang = script_lang or await self._lang(lead)
        if script_lang and lead is not None and lead.preferred_language != script_lang:
            lead.preferred_language = script_lang
            self.session.add(lead)

        is_first_reply = not any(m.direction == "out" for m in ctx.dialog)
        capability = pick_capability(stored, is_first_reply=is_first_reply)
        context = await engine.kb_context(ctx, thread_id, light=False)
        messages = build_messages_v3(
            context, ctx.dialog, lang, stored,
            coaching_notes=await self.coaching.active_manager_notes(),
            source_block=source_hint(ctx.thread.lead_source),
            name_block=lead_name_hint(lead.display_name if lead is not None else None),
            manager_note=lead.manager_note if lead is not None else None,
            now_block=await engine._now_block(),  # noqa: SLF001 — branch-local clock, engine owns it
        )
        if messages[-1]["role"] == "assistant":
            # A re-triggered tick can reach here with the bot's own last message trailing.
            # Mistral hard-rejects an assistant-trailing array outright (code 3230; 285 such
            # errors in 24h when this was missing), and other providers silently treat it as a
            # continuation, which isn't the intent either. Nudge a fresh turn instead.
            messages.append({"role": "user", "content": _ASSISTANT_LAST_NUDGE})

        decision, _meta = await generate(
            engine, ctx, messages, thread_id, workflow=workflow,
            capability=capability, branch_id=self.branch_id)
        if decision is None:
            return None
        decision = await self._vet(
            engine, ctx, messages, thread_id, decision,
            workflow=workflow, capability=capability, context=context, lang=lang,
            last_inbound=(last_in.text if last_in is not None else "") or "")

        merged = merge_dossier(stored, decision.dossier)
        await self.dossiers.save(lead.id if lead is not None else None, merged)
        self.last_decision = decision
        logger.info("v3 branch=%d thread=%d move=%s tier=%s first=%s",
                    self.branch_id, thread_id, decision.move, capability, is_first_reply)
        return decision.to_legacy(merged)

    async def _vet(  # noqa: PLR0913
        self, engine: DecisionEngine, ctx, messages: list[dict], thread_id: int,  # noqa: ANN001
        decision: TurnDecision, *, workflow: str, capability: str, context: str, lang: str,
        last_inbound: str,
    ) -> TurnDecision:
        """Two gates, deliberately asymmetric.

        The money gate fails CLOSED, because quoting a price the school never set is a promise
        it has to honour. The critic fails OPEN, because an unreviewed real answer beats a stub
        — v2 had this the wrong way round and converted broker hiccups into lost leads.

        Together with generation this caps a turn at three calls: the money gate and the critic
        never both spend a rewrite."""
        issues = money_issues(decision.reply, context)
        if issues:
            logger.warning("v3 money gate branch=%d thread=%d: %s",
                           self.branch_id, thread_id, "; ".join(issues))
            fixed = await self._regenerate(
                engine, ctx, messages, thread_id, workflow=workflow,
                correction=MONEY_CORRECTION.format(issues="; ".join(issues)))
            if fixed is None or money_issues(fixed.reply, context):
                # The one place v3 escalates on its own: we cannot let an invented figure
                # reach the lead, and we will not answer money questions with silence.
                logger.error("v3 money gate unfixable branch=%d thread=%d — escalating",
                             self.branch_id, thread_id)
                return replace(fixed or decision, needs_human=True,
                               human_reason=MONEY_ESCALATION_REASON)
            return fixed

        if capability != SMART:
            return decision  # routine turn — not worth a second call
        verdict = await critic.review(
            self.llm, reply=decision.reply, context=context, last_inbound=last_inbound,
            lang=lang, branch_id=self.branch_id, thread_id=thread_id, budget=ctx.budget)
        if verdict.sells:
            return decision
        logger.info("v3 critic branch=%d thread=%d rejected: %s",
                    self.branch_id, thread_id, verdict.why)
        rewritten = await self._regenerate(
            engine, ctx, messages, thread_id, workflow=workflow,
            correction=critic.CRITIC_CORRECTION.format(why=verdict.why, fix=verdict.fix))
        # Whatever comes back ships — it is NOT judged again. A second rejection is what sent
        # v2 to a stub and switched the lead's bot off.
        return rewritten or decision

    async def _regenerate(  # noqa: PLR0913
        self, engine: DecisionEngine, ctx, messages: list[dict], thread_id: int, *,  # noqa: ANN001
        workflow: str, correction: str,
    ) -> TurnDecision | None:
        """One rewrite on the strong model. None when it comes back unparseable, in which case
        the caller keeps the original draft rather than losing the turn."""
        rewritten, _meta = await generate(
            engine, ctx, [*messages, {"role": "user", "content": correction}], thread_id,
            workflow=workflow, capability=SMART, branch_id=self.branch_id)
        return rewritten



def _awaiting_media(dialog: list) -> bool:
    """The newest inbound is a voice/image the broker hasn't transcribed yet — hold the turn so
    the reply answers the CONTENT, not the placeholder."""
    newest = dialog[-1] if dialog else None
    return (newest is not None and newest.direction == "in"
            and (newest.text or "").strip() in (VOICE_PENDING_PH, IMAGE_PENDING_PH))
