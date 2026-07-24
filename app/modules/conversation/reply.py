"""Reply generation — one free-mode turn: the model sells, the code guards the money.

History: v2 ran eight rewrite passes per draft; the scripted v3 replaced them with a
moves-enum contract plus deterministic gates (pitch/answer), nine turn-notes and an LLM
critic — a scaffold built for weak models. The 2026-07 A/B on 10 sim personas retired it:
the strong chat:sales chain with a short goal contract doubled explicit agreements (6/10 vs
3/10) with zero forced hand-offs (0/10 vs 8/10). What remains here is exactly what still
earns its place: the templated first-contact openers (anti-ban, measured), the money gate
(fail-closed — a wrong figure is a promise the school must honour), and the dossier.

Everything downstream — enqueue, bubbles, stage events, hand-off, outbox — is shared with
delivery.py: this module decides WHAT to say; ReplyDelivery owns getting it to the lead."""
from __future__ import annotations

import logging
from dataclasses import replace

from app.adapters.channels.ig_parse import IMAGE_PENDING_PH, VOICE_PENDING_PH
from app.domain.enums import Stage

from .decision import Decision, TurnDecision, generate
from .delivery import ReplyDelivery, _script_lang
from .discovery import extract_discovery
from .dossier import merge_dossier
from .engine import _ASSISTANT_LAST_NUDGE, DecisionEngine
from .free_mode import build_messages_free
from .money_gate import (
    MONEY_CORRECTION,
    MONEY_ESCALATION_REASON,
    money_issues,
)
from .opener import (
    AD_TAP_OPENER,
    AD_TAP_OPENER_PRODUCT,
    JUNK_OPENER,
    STORY_OPENER,
    Entry,
)
from .opener import classify as classify_entry
from .prompt import (
    AD_TYPED_ENTRY_HINT,
    ORGANIC_ENTRY_HINT,
    lead_name_hint,
    source_hint,
)
from .repository import DossierRepo
from .routing import SALES, SMART, pick_capability
from .signals import AD_TEMPLATE_RE

logger = logging.getLogger(__name__)

# Sent instead of the offending draft whenever the money gate escalates — thread 5019 showed
# that flagging needs_human without replacing `.reply` protected the CRM record but still
# shipped the bad draft to the lead. Content-free and consistent with the tone of
# _MANAGER_HANDOFF_CLOSING so the lead doesn't get two conflicting "our team will help" lines.
ESCALATION_HOLD_REPLY = (
    "Kakak, bentar ya - aku cek dulu ke tim supaya infonya pas dan akurat. "
    "Nanti dibantu langsung di jam kerja (Senin-Jumat, 09.00-18.00 WIB) 🙏"
)


def _escalate(decision: TurnDecision, reason: str) -> TurnDecision:
    """Never ship the draft that triggered the escalation — only the reason and the dossier it
    already learned survive; the reply the lead actually sees is always the safe hold-line."""
    return replace(decision, reply=ESCALATION_HOLD_REPLY, needs_human=True, human_reason=reason)


class ReplyService(ReplyDelivery):
    """Produce one reply for one thread, and remember what it learned."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self.dossiers = DossierRepo(self.session, self.branch_id)
        self.last_decision: TurnDecision | None = None  # the raw answer, for logging/tests

    async def decide(self, thread_id: int, workflow: str = "reply") -> Decision | None:
        """Run one turn. None when the thread is foreign, silent, or waiting on media."""
        engine = DecisionEngine(self.session, self.branch_id, self.llm, self.knowledge,
                                broker_budget_s=self._broker_budget_s)
        ctx = await engine.prepare(thread_id, workflow=workflow, allow_over_budget=True)
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

        outs = [(m.text or "").strip() for m in ctx.dialog if m.direction == "out"]
        is_first_reply = not outs
        if is_first_reply and script_lang is None:
            # Silent/junk first contacts ship a pure template — classified by CODE, zero LLM,
            # zero cost (see opener.py for the incident history). A TYPED entry goes to the
            # full pipeline: writing the opener is exactly the judgement the model owns now.
            # Gated on the lead writing in the branch's own script: the templates are
            # Bahasa-only, so a Cyrillic opener goes straight to the full pipeline.
            fc = classify_entry(ctx.dialog, ctx.thread.lead_source, ctx.thread.ad_id)
            templated: str | None = None
            if fc.entry is Entry.AD_SILENT:
                title = await self._product_title(ctx.thread.product_slug)
                templated = (AD_TAP_OPENER_PRODUCT.format(title=title) if title
                             else AD_TAP_OPENER)
            elif fc.entry is Entry.STORY and not fc.typed_text:
                templated = STORY_OPENER
            elif fc.entry is Entry.JUNK:
                templated = JUNK_OPENER
            if templated is not None:
                decision = TurnDecision(
                    reply=templated, move="discover_motive", stage=Stage.QUALIFYING)
                self.last_decision = decision
                logger.info("reply branch=%d thread=%d tier=templated first=True",
                            self.branch_id, thread_id)
                return decision.to_legacy(stored)
        if ctx.over_budget:
            # prepare() was told to let the zero-cost template branch through; everything
            # from here on calls the broker, so the original budget gate applies now.
            logger.warning("branch=%d over daily LLM budget — %s skipped",
                           self.branch_id, workflow)
            return None

        # The first LLM turn — a plain first reply, OR the turn right after the templated
        # opener: the highest-stakes generation, always on the strong chain.
        first_llm_turn = is_first_reply or all(t == AD_TAP_OPENER for t in outs)
        tier = pick_capability(stored, is_first_reply=first_llm_turn)
        capability = SALES if tier == SMART else tier
        context = await engine.free_kb_context()
        messages = build_messages_free(
            context, ctx.dialog, lang, stored,
            coaching_notes=await self.coaching.active_manager_notes(),
            source_block=_entry_hint(ctx),
            name_block=lead_name_hint(lead.display_name if lead is not None else None),
            manager_note=lead.manager_note if lead is not None else None,
            now_block=await engine._now_block(),  # noqa: SLF001 — branch-local clock, engine owns it
            is_first_reply=is_first_reply,
        )
        if messages[-1]["role"] == "assistant":
            # A re-triggered tick can reach here with the bot's own last message trailing.
            # Mistral hard-rejects an assistant-trailing array outright (code 3230); other
            # providers silently treat it as a continuation. Nudge a fresh turn instead.
            messages.append({"role": "user", "content": _ASSISTANT_LAST_NUDGE})

        decision, _meta = await self._generate(
            engine, ctx, messages, thread_id, workflow=workflow, capability=capability)
        if decision is None:
            return None
        merged = merge_dossier(stored, decision.dossier)
        if not merged.has_discovery():
            extra = await extract_discovery(
                self.llm, ctx.dialog, merged, lang, self.branch_id, thread_id,
                budget=ctx.budget)
            merged = merge_dossier(merged, extra)
        decision = await self._vet(
            engine, ctx, messages, thread_id, decision, workflow=workflow, context=context)
        await self.dossiers.save(lead.id if lead is not None else None, merged)
        self.last_decision = decision
        logger.info("reply branch=%d thread=%d move=%s tier=%s first=%s",
                    self.branch_id, thread_id, decision.move, capability, is_first_reply)
        return decision.to_legacy(merged)

    async def _generate(  # noqa: PLR0913
        self, engine: DecisionEngine, ctx, messages: list[dict], thread_id: int, *,  # noqa: ANN001
        workflow: str, capability: str,
    ) -> tuple[TurnDecision | None, dict]:
        """One generation, falling back to chat:smart when the chat:sales chain is down,
        capped, or returns an unparseable body — degrade to the cheaper chain's quality,
        never to silence."""
        try:
            decision, meta = await generate(
                engine, ctx, messages, thread_id, workflow=workflow,
                capability=capability, branch_id=self.branch_id)
        except Exception as exc:  # noqa: BLE001 — transport-level; the fallback chain owns it
            if capability != SALES:
                raise
            logger.warning(
                "reply: chat:sales failed branch=%d thread=%d (%s) — falling back to chat:smart",
                self.branch_id, thread_id, exc)
            return await generate(
                engine, ctx, messages, thread_id, workflow=workflow,
                capability=SMART, branch_id=self.branch_id)
        if decision is None and capability == SALES:
            logger.warning(
                "reply: unparseable chat:sales decision branch=%d thread=%d — retry on smart",
                self.branch_id, thread_id)
            return await generate(
                engine, ctx, messages, thread_id, workflow=workflow,
                capability=SMART, branch_id=self.branch_id)
        return decision, meta

    async def _vet(  # noqa: PLR0913
        self, engine: DecisionEngine, ctx, messages: list[dict], thread_id: int,  # noqa: ANN001
        decision: TurnDecision, *, workflow: str, context: str,
    ) -> TurnDecision:
        """The one gate that fails closed — the money gate: a price, link, income figure or
        service not in the KB never ships. One rewrite on the strong chain, then the safe
        hold-line + escalation. Everything else about the reply is the model's own call."""
        issues = money_issues(decision.reply, context)
        if not issues:
            return decision
        logger.warning("money gate branch=%d thread=%d: %s",
                       self.branch_id, thread_id, "; ".join(issues))
        try:
            fixed, _meta = await generate(
                engine, ctx,
                [*messages, {"role": "user",
                             "content": MONEY_CORRECTION.format(issues="; ".join(issues))}],
                thread_id, workflow=workflow, capability=SALES, branch_id=self.branch_id)
        except Exception as exc:  # noqa: BLE001 — a failed rewrite means the hold-line ships
            logger.warning("money rewrite failed branch=%d thread=%d: %s",
                           self.branch_id, thread_id, exc)
            fixed = None
        if fixed is None or money_issues(fixed.reply, context):
            logger.error("money gate unfixable branch=%d thread=%d — escalating",
                         self.branch_id, thread_id)
            return _escalate(fixed or decision, MONEY_ESCALATION_REASON)
        return fixed

    async def _product_title(self, slug: str | None) -> str | None:
        """Display title of the ad-mapped product for the enriched templated opener."""
        if not slug:
            return None
        from sqlalchemy import select  # noqa: PLC0415

        from app.adapters.db.models import Product  # noqa: PLC0415
        row = (await self.session.execute(
            select(Product.title).where(
                Product.branch_id == self.branch_id, Product.slug == slug,
                Product.is_active == True))).first()  # noqa: E712 — SQLAlchemy needs the comparison
        return (row[0] or "").strip() or None if row else None


def _entry_hint(ctx) -> str | None:  # noqa: ANN001
    """The one entry-context hint this thread earns.

    The ad-entry hint asserts "they did not type it and did not ask you anything" — true
    ONLY when the opening message really was the untouched button prefill. IG's composer is
    editable: a lead can clear it and type a real question (thread 4972), and the metadata
    still says ad_clicktomsg — injecting the tap hint then contradicts answering on the very
    message it matters most for, so the typed-ad variant keeps the product anchor (thread
    5097). A walk-in with no ad/story signal at all gets the deep-discovery hint."""
    first_in = next((m for m in ctx.dialog if m.direction == "in"), None)
    pure_prefill_entry = bool(
        first_in and AD_TEMPLATE_RE.match((first_in.text or "").strip()))
    src = ctx.thread.lead_source
    if src == "ad_clicktomsg" and not pure_prefill_entry:
        return AD_TYPED_ENTRY_HINT
    hint = source_hint(src)
    if hint is None and not src and not ctx.thread.ad_id:
        return ORGANIC_ENTRY_HINT
    return hint


def _awaiting_media(dialog: list) -> bool:
    """The newest inbound is a voice/image the broker hasn't transcribed yet — hold the turn so
    the reply answers the CONTENT, not the placeholder."""
    newest = dialog[-1] if dialog else None
    return (newest is not None and newest.direction == "in"
            and (newest.text or "").strip() in (VOICE_PENDING_PH, IMAGE_PENDING_PH))
