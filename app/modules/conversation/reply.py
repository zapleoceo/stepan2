"""ReplyService — turn a thread's dialog into a Decision, then queue the reply.

LLM stays behind LLMPort (injected, so tests use a fake) and all DB access goes through
BranchScoped repos. No branch_id filtering by hand; no sending here — only enqueue."""
from __future__ import annotations

import logging
import random
import re
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.channels.ig_parse import IMAGE_PENDING_PH, VOICE_PENDING_PH
from app.adapters.db.models import Branch, Lead, Outbox, Product, StageEvent, ThreadLog
from app.adapters.meta_capi import MetaCapi
from app.config import settings
from app.domain.enums import HUMAN_LED_STAGES, Stage
from app.modules.knowledge.service import KnowledgeService
from app.modules.leads.phone import to_e164
from app.modules.notifications.alerts import AlertService
from app.modules.settings.service import BranchSettings
from app.ports.llm import LLMPort
from app.ports.notify import NotifierPort

from . import guard
from .decision import Decision, parse_decision
from .engine import DecisionEngine, _fmt_llm_meta, _retrieval_query  # noqa: F401 — re-exported
from .needs import merge_needs, parse_needs
from .repository import CoachingNoteRepo, MessageRepo, OutboxRepo, ThreadRepo
from .routing import FAST, SMART, parse_smart_stages, pick_capability

logger = logging.getLogger(__name__)

_BUBBLE_GAP_S = settings().bubble_gap_s  # stagger between split reply bubbles
_MAX_BUBBLES = settings().max_bubbles
_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")
# A needs_manager turn always mutes the bot (agent_enabled=False) — but the model's own
# reply that same turn is about answering the lead's question, not about announcing a
# hand-off; nothing told the LEAD a human is now taking over. A lead who then sends a
# follow-up (e.g. a phone number, thread 1023) gets pure silence — no bot, no confirmation,
# because the account is already muted. Append this deterministic closing line whenever the
# stage newly flips to MANAGER, so the lead is never left hanging without an explanation.
_MANAGER_HANDOFF_CLOSING = (
    "Terima kasih ya Kak! Untuk ini tim kami yang akan bantu langsung - nanti dihubungi via "
    "telepon atau WhatsApp di jam kerja (Senin-Jumat, 09.00-18.00 WIB) ya 🙏"
)
# The sale/READY exit muted the bot like MANAGER but never guaranteed the lead a closing —
# it relied on the model's own reply confirming next steps, which isn't guaranteed. Append
# this on the fresh READY flip so a won lead always knows what happens next (same shape as
# the manager closing, but about the enrollment: registration → payment during work hours).
_READY_HANDOFF_CLOSING = (
    "Siap Kak! Pendaftaran Kakak aku teruskan ke tim ya - nanti dihubungi via telepon atau "
    "WhatsApp di jam kerja (Senin-Jumat, 09.00-18.00 WIB) untuk langkah pembayaran & jadwal 🙏"
)
# After this many lead turns the discovery gate stops forcing more questions — a lead who
# hasn't yielded a real need by now won't from a third question; present on what we have.
_DISCOVERY_TURN_CAP = 2
# A static KB rule alone wasn't reliable (live testing kept seeing a 3rd-4th discovery
# question) — this is injected into the prompt AT THE EXACT TURN the cap is exceeded, the
# same mechanism the reply-guard uses for its correction nudge, so it lands as an immediate
# instruction rather than competing with the rest of a large static prompt.
_DISCOVERY_CAP_NUDGE = (
    "[System: you have already asked discovery questions for {n} turns without the lead "
    "voicing a clear need — do NOT ask another discovery question this turn. If they asked "
    "something directly, answer it now with the fact from the product card. Otherwise "
    "present the best-fit product: ONE concrete value line tied to what they've told you, "
    "then a light next step. Lead with the full price/DP breakdown ONLY if they explicitly "
    "asked about price/payment or signaled they want to enroll — a lead asking how to solve "
    "their problem (not how much it costs) gets a value answer, not a price dump; save the "
    "full price for when they ask or the conversation clearly calls for it. Return the JSON "
    "as usual.]"
)
# A lead the model itself has already classified non_target (wrong audience, off-topic,
# trolling, selling us something) but that keeps getting re-engaged turn after turn — the
# live example was thread 2027, a domain seller the bot kept trying to pitch Vibe Coding to
# across many turns. Once that classification has already stuck from an EARLIER turn, wrap
# up instead of continuing the sales motion.
_NON_TARGET_NUDGE = (
    "[System: this lead was already classified non_target (wrong audience / off-topic / "
    "not interested in our programs) in an earlier turn and is still off-topic. Do NOT "
    "keep pitching or asking discovery questions — write ONE short, polite closing line "
    "and stop there; only re-engage if THEY bring up a real interest in one of our "
    "programs. Return the JSON as usual.]"
)

# A live reply that repeats a question already asked in this thread — same failure mode
# followup.py guards against (chat 1830), but on the live-reply path, which had NO dedup
# check at all (thread 2260, 2026-07-08: the SECOND occurrence of a re-asked discovery
# question was a live reply, not a followup, and slipped straight through).
_DUPLICATE_RATIO = 0.6
_REPEAT_CORRECTION = (
    "[System: your draft repeats something you already said in this thread almost "
    "word-for-word: {prior!r}. Do NOT send it again — react SPECIFICALLY to what the lead "
    "just said ({last_in!r}), not a generic reaction word disconnected from it (thread 2085: "
    "a bare 'Mantap, Kak!' with no anchor to their actual message got 'Mantap apa nya kak?' "
    "back — confusion, not re-engagement). Pick a genuinely different angle grounded in "
    "their own words (their stated need, a cheaper entry point, a concrete yes/no question). "
    "Return the JSON as usual.]"
)
# A '?'-ending clause, so a specific discovery question can be compared on its own —
# whole-message similarity dilutes when the SAME question is wrapped in different framing
# (a new intro sentence, a story, different padding) each time it's re-asked.
_QUESTION_RE = re.compile(r"[^.!?\n]*\?")


def _last_question(text: str) -> str | None:
    matches = _QUESTION_RE.findall(text or "")
    return matches[-1].strip() if matches else None


_WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def _content_words(text: str) -> set[str]:
    """Word set (≥3 chars) for a rough topic-overlap signal — short function words dropped so
    the overlap reflects shared CONTENT, not shared grammar."""
    return {w for w in _WORD_RE.findall((text or "").lower()) if len(w) >= 3}


def _most_similar_prior(new_text: str, dialog) -> tuple[str, float]:  # noqa: ANN001
    """The prior bot message most similar to new_text, and that similarity ratio.

    Checks the WHOLE message (catches a broadly repeated pitch), just the closing QUESTION
    (catches one specific discovery question re-asked inside an otherwise different reply),
    AND each individual '|||' bubble on its own (thread 237: a 3-bubble followup opened with
    a bubble byte-for-byte identical to an earlier live reply's opening line - "Untuk
    Sabtu/Minggu kantor kami memang tutup Kak..." - but the two EXTRA bubbles that followed
    diluted the whole-message ratio well under the 0.6 gate, even though bubble #1 alone was
    a 100% duplicate). See followup.py's identical helper for the live cases that motivated
    the first two checks."""
    best_text, best_ratio = "", 0.0
    new_norm = (new_text or "").strip().lower()
    new_q = _last_question(new_text)
    new_bubbles = [b.lower() for b in _split_bubbles(new_text)]
    new_words = _content_words(new_text)
    for m in dialog:
        if m.direction != "out" or not (m.text or "").strip():
            continue
        prior = m.text.strip()
        prior_lower = prior.lower()
        ratio = SequenceMatcher(None, new_norm, prior_lower).ratio()
        if new_q:
            prior_q = _last_question(prior)
            if prior_q:
                ratio = max(ratio, SequenceMatcher(None, new_q.lower(), prior_q.lower()).ratio())
        for bubble in new_bubbles:
            ratio = max(ratio, SequenceMatcher(None, bubble, prior_lower).ratio())
        # Word-overlap (Jaccard) catches a REWORDED repeat — the same greeting/point in fresh
        # phrasing — that the char-sequence ratio slides under (threads 2047/2143: a re-sent
        # opener / reassurance). Only for messages long enough that overlap is meaningful, so a
        # short "Baik Kak 🙏" doesn't collide with another short line.
        prior_words = _content_words(prior)
        if len(new_words) >= 5 and len(prior_words) >= 5:
            jaccard = len(new_words & prior_words) / len(new_words | prior_words)
            ratio = max(ratio, jaccard)
        if ratio > best_ratio:
            best_text, best_ratio = prior, ratio
    return best_text, best_ratio


def _script_lang(text: str) -> str | None:
    """Cyrillic in the lead's own text -> 'ru', independent of the model's self-report.

    decision.reply_language is only set when the model remembers to fill it in - live
    threads showed it drifting back to the branch default (Bahasa) mid-conversation even
    after the lead explicitly switched to Russian, because that self-report was the ONLY
    thing persisting the switch. A lead's own script is a much stronger, cheap signal."""
    return "ru" if _CYRILLIC_RE.search(text or "") else None


def _split_bubbles(reply: str, max_parts: int = _MAX_BUBBLES) -> list[str]:
    """Split the model's reply on '|||' into ≤max_parts non-empty bubbles; overflow is
    merged into the last one so we never send more than max_parts messages."""
    parts = [p.strip() for p in reply.split("|||") if p.strip()]
    if len(parts) <= max_parts:
        return parts
    return [*parts[: max_parts - 1], " ".join(parts[max_parts - 1:])]


async def guard_prompt(session: AsyncSession, branch_id: int) -> str | None:
    """The reply-guard checker prompt, editable per branch via the `guard_verify` KB
    doc (resolved through a shared-KB link); None → guard.py's built-in default."""
    from app.modules.knowledge.repository import KnowledgeRepo  # noqa: PLC0415
    from app.modules.knowledge.source import effective_kb_branch  # noqa: PLC0415
    kb = await effective_kb_branch(session, branch_id)
    doc = await KnowledgeRepo(session, kb).by_slug("guard_verify")
    return doc.content if doc and (doc.content or "").strip() else None


async def raise_manager_alert(
    session: AsyncSession, branch_id: int, notifier: NotifierPort | None, llm: LLMPort,
    thread_id: int, lead_id: int, decision: Decision, lead_phone: str | None = None,
) -> None:
    """Notify a human that this thread needs one — the KB genuinely has no answer, so a
    manager works it and (per policy) feeds the missing fact back into the KB afterward.
    Shared by the live-reply path AND follow-up nudges: a needs_manager decision means the
    same thing regardless of which path produced it, so both must actually alert (a nudge
    that silently sets needs_manager with no alert was the pre-2026-07-07 followup gap)."""
    q = decision.manager_question or ""
    gap = decision.kb_gap or ""
    summary_en = q or gap or "Thread handed to a human"
    if q:
        summary_ru = f"Вопрос: {q}"
        if gap:
            summary_ru += f"\nПробел в KB: {gap}"
    elif gap:
        summary_ru = gap  # a guard-forced or model-named reason — never claim the lead asked
    else:
        summary_ru = "Лид запросил менеджера"
    alerts = AlertService(session, branch_id, notifier, llm=llm)
    try:
        await alerts.raise_alert(
            lead_id=lead_id,
            kind="needs_manager",
            summary_en=summary_en,
            summary_ru=summary_ru,
            thread_id=thread_id,
            lead_phone=lead_phone,
        )
    except Exception:
        logger.warning(
            "manager alert failed thread=%s lead=%s", thread_id, lead_id, exc_info=True
        )


def _deterministic_issues(reply: str, context: str) -> list[str]:
    """Every KB-context-free check — no LLM call needed, always on regardless of
    reply_guard mode. Re-run on a regenerated draft too, so a still-broken reply is caught
    before it ships rather than trusted on faith."""
    return [
        *guard.ungrounded_urls(reply, context),
        *guard.false_delivery_claims(reply),
        *guard.multiple_questions(reply),
        *guard.impossible_capability_offers(reply),
        *guard.wrong_channel_claims(reply),
        *guard.whatsapp_delivery_offers(reply),
    ]


def _bump_guard_regen_count(lead: Lead) -> None:
    """A regen fired for this lead — persist it as a per-lead routing signal (see
    routing.pick_capability's guard_regen_count) so future turns lean toward chat:smart
    for a lead the cheap model has already stumbled on, not just this one turn."""
    lead.guard_regen_count += 1


async def guard_decision(
    session: AsyncSession, branch_id: int, branch_settings: BranchSettings | None,
    llm: LLMPort, engine: DecisionEngine, ctx, thread_id: int, lang: str, workflow: str,
    bill: bool, decision: Decision, meta: dict,
) -> tuple[Decision, dict]:
    """Block fabricated facts and a handful of conversation-quality failures: ungrounded
    links, false delivery claims, more than one question in a turn, impossible capability
    offers, and telling an Instagram lead to go DM on Instagram — all deterministic — plus
    an LLM grounding check on risky replies. One correcting regeneration, then a safe
    hand-off — never send the violation. Off when reply_guard='off'. Shared by live replies
    AND follow-up nudges.

    Returns (decision, meta) — meta is the regen's broker-log line when a regen
    happened, else the meta passed in unchanged."""
    mode = branch_settings.reply_guard if branch_settings is not None else "full"
    if mode == "off" or not decision.reply:
        return decision, meta
    context = engine.last_context
    regenerated = False
    if decision.needs_manager:
        # Mutually exclusive, most-specific-first: a price question already answered in KB
        # gets the targeted correction; anything else with no stated reason gets the generic
        # one. Only ONE extra regen per turn either way — never chain both on the same
        # decision.
        last_in = next((m.text or "" for m in reversed(ctx.dialog) if m.direction == "in"), "")
        if guard.premature_manager_handoff(last_in, context):
            logger.warning(
                "guard: branch=%d thread=%d premature needs_manager on a price question "
                "already answered in KB → regen", branch_id, thread_id)
            regenerated = True
            raw, regen_meta = await engine.complete(
                ctx, thread_id, lang=lang, workflow=workflow, capability=SMART, bill=bill,
                extra_user_msg=guard.MANAGER_HANDOFF_CORRECTION)
            try:
                fixed = parse_decision(raw)
            except ValueError:
                fixed = None
            # Only adopt the regen if it actually stopped escalating — a model that still
            # insists on needs_manager after being told the fact is in context probably has
            # a real reason; better a genuine gap reaches a human than looping on a refusal.
            if fixed is not None and fixed.reply and not fixed.needs_manager:
                decision, meta = fixed, regen_meta
        elif guard.unexplained_manager_handoff(
            decision.needs_manager, decision.manager_question, decision.kb_gap,
        ):
            logger.warning(
                "guard: branch=%d thread=%d needs_manager with no manager_question/kb_gap "
                "→ regen", branch_id, thread_id)
            regenerated = True
            raw, regen_meta = await engine.complete(
                ctx, thread_id, lang=lang, workflow=workflow, capability=SMART, bill=bill,
                extra_user_msg=guard.UNEXPLAINED_HANDOFF_CORRECTION)
            try:
                fixed = parse_decision(raw)
            except ValueError:
                fixed = None
            # Adopt the regen either way: it either named the gap (still escalating, but now
            # with something for the manager to act on) or stopped escalating outright — both
            # are strictly better than the original unexplained hand-off.
            if fixed is not None and fixed.reply:
                decision, meta = fixed, regen_meta
    issues = _deterministic_issues(decision.reply, context)
    if mode == "full" and guard.is_risky(decision.reply):
        issues += await guard.verify_grounding(
            llm, decision.reply, context, branch_id=branch_id,
            thread_id=thread_id, bill=bill, system=await guard_prompt(session, branch_id))
    if not issues:
        if regenerated and ctx.lead is not None:
            _bump_guard_regen_count(ctx.lead)
        return decision, meta
    logger.warning("guard: branch=%d thread=%d fabrication → regen: %s",
                   branch_id, thread_id, issues[:3])
    raw, regen_meta = await engine.complete(
        ctx, thread_id, lang=lang, workflow=workflow, capability=SMART, bill=bill,
        extra_user_msg=guard.CORRECTION.format(issues="; ".join(issues[:5])))
    try:
        fixed = parse_decision(raw)
    except ValueError:
        fixed = decision
    if ctx.lead is not None:
        _bump_guard_regen_count(ctx.lead)
    # Only the deterministic checks are re-verified (an LLM re-verify would double cost);
    # a still-broken draft means we can't trust it → hand off.
    from dataclasses import replace  # noqa: PLC0415
    remaining = _deterministic_issues(fixed.reply, context) if fixed.reply else ["empty reply"]
    if not remaining:
        return fixed, regen_meta
    # A still-doubled-up question after the regen is a style slip, not a fabrication risk —
    # trim to the first question deterministically instead of wasting a manager's attention
    # on a lead who asked something the KB already answers (threads 2159/2160: "ceritakan
    # lebih detail" got a full hand-off purely because the regen ALSO asked two questions).
    if all("question mark" in issue for issue in remaining):
        trimmed = guard.truncate_to_one_question(fixed.reply)
        if not _deterministic_issues(trimmed, context):
            return replace(fixed, reply=trimmed), regen_meta
    logger.error("guard: branch=%d thread=%d unfixable violation → hand-off",
                 branch_id, thread_id)
    # Guard-origin escalation: stamp its own reason so the alert and chat log don't
    # misattribute it to the lead or to the model's stage_reason (keep a real model-named
    # gap if it happened to set one).
    return replace(fixed, reply=guard.SAFE_FALLBACK, needs_manager=True,
                   kb_gap=fixed.kb_gap or guard.GUARD_HANDOFF_REASON), regen_meta


class ReplyService:
    """Decide and enqueue the agent's reply for one branch's thread."""

    def __init__(
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
        self._broker_budget_s = broker_budget_s  # per-reply broker poll budget (None = default)
        self.threads = ThreadRepo(session, branch_id)
        self.messages = MessageRepo(session, branch_id)
        self.outbox = OutboxRepo(session, branch_id)
        self.coaching = CoachingNoteRepo(session, branch_id)
        self._last_llm_meta: dict = {}

    async def decide(self, thread_id: int, workflow: str = "reply") -> Decision | None:
        """Run the model over the thread; None if the thread is foreign or has no dialog.

        workflow tags the broker-log row and gates billing: 'sim' (sandbox testing) routes
        exactly like a real reply but is logged distinctly and does NOT charge the branch's
        daily LLM budget."""
        bill = workflow != "sim"
        route_wf = "reply" if workflow == "sim" else workflow  # sim mirrors reply routing
        engine = DecisionEngine(self.session, self.branch_id, self.llm, self.knowledge,
                                broker_budget_s=self._broker_budget_s)
        ctx = await engine.prepare(thread_id, workflow=workflow)
        if ctx is None:
            return None
        newest = ctx.dialog[-1] if ctx.dialog else None
        if newest is not None and newest.direction == "in" \
                and (newest.text or "").strip() in (VOICE_PENDING_PH, IMAGE_PENDING_PH):
            # Voice/image the broker hasn't transcribed/captioned yet — hold the reply so
            # Stepan answers the CONTENT, not the placeholder. Releases when backfill writes
            # the transcript ("🎤 <words>") / caption ("🖼 <desc>"), or a fallback on failure
            # (media/service._release_*_hold) so a broken media item never freezes the thread.
            return None
        lead = ctx.lead
        last_in = next((m for m in reversed(ctx.dialog) if m.direction == "in"), None)
        script_lang = _script_lang(last_in.text if last_in else "")
        lang = script_lang or await self._lang(lead)
        if script_lang and lead is not None and lead.preferred_language != script_lang:
            lead.preferred_language = script_lang  # sticks even if the model forgets to say so
            self.session.add(lead)
        mode = self.settings.reply_routing if self.settings is not None else "hybrid"
        smart_stages = parse_smart_stages(
            self.settings.smart_stages if self.settings is not None else None)
        inbound_count = await self.messages.inbound_count(thread_id)
        cap = pick_capability(
            workflow=route_wf, stage=lead.stage if lead is not None else None,
            lead_type=lead.lead_type if lead is not None else None,
            last_inbound=last_in.text if last_in is not None else "", mode=mode,
            smart_stages=smart_stages, inbound_count=inbound_count,
            guard_regen_count=lead.guard_regen_count if lead is not None else 0)
        extra_user_msg = None
        if lead is not None and lead.lead_type == "non_target":
            # lead.lead_type reflects the PRIOR turn's classification (persisted in
            # _apply_decision below) — reaching here means the model already called this
            # non_target once and the lead is back for another round; don't re-engage.
            extra_user_msg = _NON_TARGET_NUDGE
        else:
            needs_captured = ctx.stored_needs.discovery_complete or ctx.stored_needs.has_needs()
            if not needs_captured and inbound_count > _DISCOVERY_TURN_CAP:
                extra_user_msg = _DISCOVERY_CAP_NUDGE.format(n=inbound_count)
        raw, meta = await engine.complete(
            ctx, thread_id, lang=lang, workflow=workflow, capability=cap, bill=bill,
            extra_user_msg=extra_user_msg)
        try:
            decision = parse_decision(raw)
        except ValueError:
            if cap == FAST:  # a broken cheap decision escalates to the strong model, once
                logger.warning(
                    "%s: unparseable fast decision branch=%d thread=%d — retrying on smart",
                    workflow, self.branch_id, thread_id)
                raw, meta = await engine.complete(
                    ctx, thread_id, lang=lang, workflow=workflow, capability=SMART, bill=bill)
                try:
                    decision = parse_decision(raw)
                except ValueError:
                    # Both the fast AND the smart decision were unparseable — degrade the
                    # whole tick to None (the caller skips this thread and retries next tick)
                    # instead of letting the ValueError abort the reply job (asymmetric with
                    # followup.py, which already swallows this).
                    logger.warning(
                        "%s: unparseable smart decision too branch=%d thread=%d — skip",
                        workflow, self.branch_id, thread_id)
                    return None
            else:
                logger.warning(
                    "%s: unparseable smart decision branch=%d thread=%d — skip",
                    workflow, self.branch_id, thread_id)
                return None
        if decision.reply:
            prior, ratio = _most_similar_prior(decision.reply, ctx.dialog)
            if ratio >= _DUPLICATE_RATIO:
                logger.warning(
                    "%s: branch=%d thread=%d near-duplicate reply (ratio=%.2f) → regen",
                    workflow, self.branch_id, thread_id, ratio)
                raw, regen_meta = await engine.complete(
                    ctx, thread_id, lang=lang, workflow=workflow, capability=SMART, bill=bill,
                    extra_user_msg=_REPEAT_CORRECTION.format(
                        prior=prior, last_in=last_in.text if last_in is not None else ""))
                try:
                    decision = parse_decision(raw)
                    meta = regen_meta  # adopt the regen's broker line only when its reply is used
                except ValueError:
                    pass  # keep the original draft AND its meta — the regen is discarded
        decision, meta = await guard_decision(
            self.session, self.branch_id, self.settings, self.llm,
            engine, ctx, thread_id, lang, workflow, bill, decision, meta)
        # guard_decision's own regen (for an UNRELATED violation) is never re-checked against
        # dialog history, so it can silently reintroduce the exact duplicate rejected above —
        # same precedent as followup.py. A live reply can't just drop the send like a nudge
        # can, so ask the lead to narrow down instead of resending a duplicate. A repeat is a
        # STYLE dead-end, not a knowledge gap — don't summon a manager for it (that was the
        # top false-escalation driver on terse SMM threads 2541/2566); leave needs_manager to
        # the model's own decision.
        if decision.reply:
            _, post_guard_ratio = _most_similar_prior(decision.reply, ctx.dialog)
            if post_guard_ratio >= _DUPLICATE_RATIO:
                logger.warning(
                    "%s: branch=%d thread=%d still near-duplicate after guard regen "
                    "(ratio=%.2f) → clarify", workflow, self.branch_id, thread_id,
                    post_guard_ratio)
                from dataclasses import replace  # noqa: PLC0415
                decision = replace(decision, reply=guard.CLARIFY_FALLBACK)
        # PHONE BEFORE HAND-OFF (hard gate): never mute the bot and hand a contact-less lead to
        # a manager who then has no way to reach them (lead 2757 went to MANAGER with a NULL
        # phone; the SAFE_FALLBACK path sets needs_manager, bypassing the prompt's soft rule).
        # If the model wants a manager but we have no phone — and the lead didn't just give one
        # — suppress the escalation, keep the bot on, and ask for a WhatsApp number first. A
        # later turn WITH a phone escalates for real (a manual UI move to MANAGER is unaffected).
        if decision.needs_manager and lead is not None \
                and not lead.phone_e164 and not (decision.phone or "").strip():
            from dataclasses import replace  # noqa: PLC0415
            logger.info(
                "guard: branch=%d thread=%d needs_manager without a phone → ask for contact",
                self.branch_id, thread_id)
            decision = replace(decision, needs_manager=False, manager_question=None,
                               kb_gap=None, reply=guard.ASK_PHONE_BEFORE_HANDOFF)
        self._last_llm_meta = meta
        if lead is not None:
            merged = merge_needs(ctx.stored_needs, decision.jobs, decision.pains,
                                 decision.gains, decision.discovery_complete)
            lead.needs = merged.to_json()
            self.session.add(lead)
        return decision

    async def _lang(self, lead: Lead | None = None) -> str:
        """Reply language ladder: the lead's stated preference wins, else the branch default.
        The KB may be written in any language — this only controls what the bot replies in."""
        if lead is not None and lead.preferred_language:
            return lead.preferred_language
        branch = await self.session.get(Branch, self.branch_id)
        return branch.lang if branch is not None else "id"

    async def enqueue_reply(self, thread_id: int, decision: Decision) -> Outbox | None:
        """Queue the decided reply bubbles and apply the decision to the lead (S1 semantics)."""
        thread = await self.threads.by_id(thread_id)
        if thread is None:
            return None
        # Idempotency backstop: if a sibling run already answered this exact inbound while
        # THIS run was slow (a guard regen against a broker near its own timeout ceiling), the
        # watermark has already caught up — don't queue a second reply. This is the last line
        # of defense behind the advisory lock: a job ARQ killed for exceeding worker_job_timeout_s
        # can keep running as a zombie coroutine past that point (asyncio cancellation doesn't
        # always land mid-await), so the lock alone doesn't fully close the gap (2026-07-07).
        if thread.last_out_at is not None and thread.last_in_at is not None \
                and thread.last_out_at >= thread.last_in_at:
            logger.warning(
                "enqueue_reply: branch=%d thread=%d already answered by a sibling run — "
                "dropping the duplicate", self.branch_id, thread_id)
            return None
        base = self._scheduled_at()
        outbox: Outbox | None = None
        meta_line = _fmt_llm_meta(self._last_llm_meta)
        bubbles = _split_bubbles(decision.reply)
        for i, bubble in enumerate(bubbles):
            outbox = await self.outbox.add(
                Outbox(
                    branch_id=self.branch_id,
                    thread_id=thread_id,
                    text=bubble,
                    scheduled_at=base + timedelta(seconds=i * _BUBBLE_GAP_S),
                    llm_info=meta_line,  # every bubble of the reply shows the same broker line
                )
            )
        lead = await self.session.get(Lead, thread.lead_id)
        exit_kind: str | None = None
        if lead is not None:
            exit_kind = await self._apply_decision(lead, thread, decision)
        if exit_kind is not None:
            # The lead just exited the funnel (manager hand-off or a won deal) — say what
            # happens next instead of going silent (thread 1023: a lead who sent a follow-up
            # phone number 2 days after a needs_manager mute got zero acknowledgment). READY
            # relied on the model's reply confirming next steps; now it's guaranteed too.
            closing = (_MANAGER_HANDOFF_CLOSING if exit_kind == "manager"
                       else _READY_HANDOFF_CLOSING)
            outbox = await self.outbox.add(
                Outbox(
                    branch_id=self.branch_id,
                    thread_id=thread_id,
                    text=closing,
                    scheduled_at=base + timedelta(seconds=len(bubbles) * _BUBBLE_GAP_S),
                    llm_info=meta_line,
                )
            )
        is_openhouse_rsvp = decision.ready and decision.ready_subtype == "openhouse"
        if decision.needs_manager and not is_openhouse_rsvp:
            await self._raise_manager_alert(
                thread_id, thread.lead_id, decision,
                lead.phone_e164 if lead is not None else None,
            )
        return outbox

    def _sync_lead_fields(self, lead: Lead, thread, decision: Decision) -> None:
        """Copy this turn's observations onto the lead/thread — product, language, segment,
        and a freshly-typed phone. Pure field sync, no funnel-stage logic (see _apply_decision
        for that) — split out so each responsibility can be read and tested on its own."""
        # The model may re-qualify the product it inherited from the ad (product_source
        # 'ad') or from an earlier turn ('model'), but never overrides a manager's manual
        # pick ('manager').
        if (
            decision.product_slug
            and decision.product_slug != thread.product_slug
            and thread.product_source in (None, "ad", "model")
        ):
            thread.product_slug = decision.product_slug
            thread.product_source = "model"
            self.session.add(thread)
        if decision.reply_language and decision.reply_language != lead.preferred_language:
            lead.preferred_language = decision.reply_language  # lead switched language — remember
            self.session.add(lead)
        if decision.lead_type and decision.lead_type != lead.lead_type:
            lead.lead_type = decision.lead_type  # intent segment — for routing + reporting
            self.session.add(lead)
        if decision.audience and decision.audience != lead.audience:
            lead.audience = decision.audience  # who they are (adult/student) — reporting + path
            self.session.add(lead)
        # Capture a phone the lead typed in-chat (channel metadata rarely carries one). This
        # must land BEFORE _stage_for so the same turn the lead sends their number can pass the
        # hand-off gate — a manager can't work a deal without a contact.
        if decision.phone and not lead.phone_e164:
            cc = self.settings.phone_country_code if self.settings else "62"
            normalized = to_e164(decision.phone, cc)
            if normalized:
                lead.phone_e164 = normalized
                self.session.add(lead)

    async def _apply_decision(self, lead: Lead, thread, decision: Decision) -> str | None:
        """Move the funnel: stage priority ready+contact → READY, needs_manager →
        MANAGER, ready w/o contact → PRESENTING, else the model's stage. An openhouse RSVP
        is a side-channel notification, not a stage transition — see _stage_for.

        Returns "manager" / "ready" when the stage just flipped to that exit this turn (a
        fresh mute, not an already-exited lead) so the caller appends the matching closing
        line; None otherwise."""
        was_non_target = lead.lead_type == "non_target"
        self._sync_lead_fields(lead, thread, decision)
        if decision.hard_stop:
            await self._hard_stop(lead, thread)
            return None
        # Non-target terminal state: a lead already classified non_target on an EARLIER turn
        # and STILL off-topic now (the same condition that fed _NON_TARGET_NUDGE) has had its
        # one polite closing line — wind it down to DORMANT so a wrong-audience lead doesn't
        # linger in the active list burning a reply every inbound. A fresh inbound with real
        # interest still revives it (ingest._revive_bot), and the model can re-classify it.
        if was_non_target and lead.lead_type == "non_target" \
                and lead.stage not in HUMAN_LED_STAGES:
            await self._soft_close_dormant(lead, thread)
            return None
        # The bound product's kind decides deal-vs-RSVP: an event product is ALWAYS an
        # openhouse-style RSVP (notify team, bot stays on), regardless of what the model
        # guessed — the model only picks the subtype for non-event products.
        ready = self._is_ready(decision)
        eff_subtype = decision.ready_subtype
        if ready and thread.product_slug \
                and await self._product_kind(thread.product_slug) == "event":
            eff_subtype = "openhouse"
        if ready and eff_subtype == "openhouse":
            await self._handoff_openhouse(lead, thread)
        inbound = await self.messages.inbound_count(thread.id)
        new_stage = self._stage_for(decision, lead, inbound, eff_subtype)
        if new_stage == lead.stage:
            return None
        self.session.add(StageEvent(
            branch_id=self.branch_id, lead_id=lead.id, thread_id=thread.id,
            from_stage=str(lead.stage), to_stage=str(new_stage), actor="bot",
            reason="needs_manager" if decision.needs_manager else
                   ("ready" if ready else "model decision"),
        ))
        # Mirrors the manual stage-move reason popup, but for the bot's OWN decision — visible
        # in the same chat chronology so a manager can see WHY Stepan moved the funnel, not
        # just that it did. A forced MANAGER override needs its OWN reason, not the model's
        # stage_reason verbatim: that field describes the stage the model ITSELF asked for
        # (e.g. 'presenting'), which needs_manager then overrides to MANAGER regardless —
        # logging it as-is reads as a mismatch ("лид... — переход в presenting" next to a
        # presenting→manager row). kb_gap/manager_question are what actually explain the
        # escalation; stage_reason is only the right source for a non-escalation move. Model
        # non-compliance (the field left null despite being "required") also gets a fallback
        # here rather than a silent gap in the chronology.
        if new_stage == Stage.MANAGER and decision.needs_manager:
            # stage_reason is deliberately NOT in this chain — it describes the stage the
            # model asked for (e.g. presenting), which reads as a mismatch next to a MANAGER
            # row. A guard-forced hand-off stamps kb_gap, so this fallback is a last resort.
            reason_text = (
                decision.kb_gap or decision.manager_question
                or "эскалация на менеджера без указанной причины"
            )
        else:
            reason_text = decision.stage_reason
        if reason_text:
            self.session.add(ThreadLog(
                branch_id=self.branch_id, thread_id=thread.id,
                kind="stage_reason", detail=reason_text, actor="bot",
            ))
        lead.stage = new_stage
        if new_stage == Stage.MANAGER:
            lead.agent_enabled = False  # human takes over; manager may re-enable
        if new_stage == Stage.READY:
            await self._handoff(lead, thread, eff_subtype)
        self.session.add(lead)
        logger.info("branch=%d lead=%d stage → %s", self.branch_id, lead.id, new_stage)
        if new_stage == Stage.MANAGER:
            return "manager"
        if new_stage == Stage.READY:
            return "ready"
        return None

    async def _product_kind(self, slug: str) -> str:
        row = (await self.session.execute(
            select(Product.kind).where(
                Product.branch_id == self.branch_id, Product.slug == slug)
        )).first()
        return row[0] if row else "course"

    async def _hard_stop(self, lead: Lead, thread) -> None:
        """Lead explicitly demanded we stop: let the one queued apology go out, then silence
        the account — bot off, dormant, follow-up timer cleared. Anti-ban: a nudge after an
        explicit stop turns an annoyed lead into a spam report against the IG account."""
        thread.next_followup_at = None
        self.session.add(thread)
        if lead.stage != Stage.DORMANT:
            self.session.add(StageEvent(
                branch_id=self.branch_id, lead_id=lead.id, thread_id=thread.id,
                from_stage=str(lead.stage), to_stage=str(Stage.DORMANT),
                actor="bot", reason="hard_stop",
            ))
            lead.stage = Stage.DORMANT
        lead.agent_enabled = False
        self.session.add(lead)
        logger.info("branch=%d lead=%d hard-stop → dormant, bot off", self.branch_id, lead.id)

    async def _soft_close_dormant(self, lead: Lead, thread) -> None:
        """A repeatedly-off-topic non_target lead: the model's polite closing line is already
        queued this turn — now wind the funnel down to DORMANT (bot off, follow-up timer
        cleared) so a wrong-audience lead stops occupying the active queue. Softer than
        _hard_stop (no explicit stop demand); a fresh inbound with real interest revives it."""
        thread.next_followup_at = None
        self.session.add(thread)
        if lead.stage != Stage.DORMANT:
            self.session.add(StageEvent(
                branch_id=self.branch_id, lead_id=lead.id, thread_id=thread.id,
                from_stage=str(lead.stage), to_stage=str(Stage.DORMANT),
                actor="bot", reason="non_target",
            ))
            lead.stage = Stage.DORMANT
        lead.agent_enabled = False
        self.session.add(lead)
        logger.info("branch=%d lead=%d non_target → dormant, bot off", self.branch_id, lead.id)

    @staticmethod
    def _is_ready(decision: Decision) -> bool:
        """Readiness is either the `ready` flag OR the model putting stage='ready' directly.
        Both must go through the same phone gate — otherwise a model that writes stage='ready'
        hands a lead to a manager with no contact (the exact defect on lead 1561)."""
        return decision.ready or decision.stage == Stage.READY

    def _stage_for(self, decision: Decision, lead: Lead, inbound_count: int = 0,
                   ready_subtype: str | None = None) -> Stage:
        # Once a lead is in a HUMAN-LED stage (manager took it over, or it's already ready/
        # handed off), only a manual UI action may move it out — the bot never auto-moves the
        # funnel stage again, even if it keeps talking (agent_enabled can stay on; see
        # HUMAN_LED_STAGES). Live bug (thread 2274): a manager moved a lead to MANAGER, the
        # bot's very next decision moved it straight back to qualifying on its own read of
        # the conversation — the manager's call was silently overridden.
        if lead.stage in HUMAN_LED_STAGES:
            return lead.stage
        ready = self._is_ready(decision)
        if ready and ready_subtype == "openhouse":
            # An event RSVP is a notify-only side channel (see _handoff_openhouse) — it's
            # a sale (of a seat, not a course) but NOT a hand-off: the bot keeps talking,
            # so never let it force READY/MANAGER and silence the account. A model that wrote
            # stage='ready' directly would otherwise slip READY through here (→ _handoff mutes
            # the bot), so remap it down to PRESENTING — the same defensive depth as _is_ready.
            return Stage.PRESENTING if decision.stage == Stage.READY else decision.stage
        if ready and lead.phone_e164:
            return Stage.READY
        if decision.needs_manager:
            return Stage.MANAGER
        if ready:  # ready without a contact — keep selling / ask for the phone, don't hand off
            return Stage.PRESENTING
        # Discovery gate: don't present until a real need (pain + gain) is captured — the
        # code backstop behind the prompt's discover-first rule. BUT it's an EARLY gate, not
        # an infinite interrogation: once the lead has taken _DISCOVERY_TURN_CAP turns, stop
        # forcing discovery (a non-yielding lead is better served by a value pitch than a
        # sixth question) and trust the model's PRESENTING.
        if (
            decision.stage in (Stage.PRESENTING, Stage.OBJECTION)
            and inbound_count < _DISCOVERY_TURN_CAP
            and not self._needs_captured(decision, lead)
        ):
            return Stage.QUALIFYING
        return decision.stage

    @staticmethod
    def _needs_captured(decision: Decision, lead: Lead) -> bool:
        if decision.discovery_complete or decision.has_needs():
            return True
        stored = parse_needs(lead.needs)
        return stored.discovery_complete or stored.has_needs()

    async def _handoff(self, lead: Lead, thread, subtype: str | None) -> None:
        """Lead is ready with a contact: bot off, stamp, manager card, CAPI Lead event.

        subtype (deal|openhouse) distinguishes an enrollment from an open-house signup —
        it drives the alert kind and the Meta CAPI event, and feeds the Won-split report."""
        now = datetime.now(UTC).replace(tzinfo=None)
        lead.agent_enabled = False
        lead.handed_off_at = now
        # A genuine (non-openhouse) hand-off must not inherit a stale 'openhouse' marker
        # left by an earlier RSVP — the enrollment is the real outcome, so drop the stale
        # marker and re-derive the deal subtype (else the deal reports as ready_openhouse).
        if lead.ready_subtype == "openhouse" and subtype != "openhouse":
            lead.ready_subtype = None
        lead.ready_subtype = lead.ready_subtype or subtype or "deal"
        kind = f"ready_{lead.ready_subtype}"
        alerts = AlertService(self.session, self.branch_id, self._notifier, llm=self.llm)
        try:
            await alerts.raise_alert(
                lead_id=lead.id,
                kind=kind,
                summary_en=f"Lead is ready ({lead.ready_subtype}) · phone {lead.phone_e164}",
                summary_ru=f"Лид готов ({lead.ready_subtype}) · телефон {lead.phone_e164}",
                thread_id=thread.id,
                lead_phone=lead.phone_e164,
            )
        except Exception:
            logger.warning("handoff alert failed lead=%s", lead.id, exc_info=True)
        cfg = self.settings
        if cfg is not None and cfg.meta_pixel_id and cfg.meta_capi_token:
            await MetaCapi().send_lead(
                cfg.meta_pixel_id, cfg.meta_capi_token,
                event_id=f"handoff-{self.branch_id}-{lead.id}",
                phone=lead.phone_e164,
            )

    async def _handoff_openhouse(self, lead: Lead, thread) -> None:
        """Lead RSVP'd to an event (open house / demo day): notify the team with a
        callback-hours note, keep the bot ON. Unlike _handoff (a course deal), this is
        a seat sale, not a hand-off — no agent_enabled/stage change, no CAPI event.

        Fires the alert at most ONCE per lead: this path has no stage/mute gate (the bot
        stays on and the model keeps `ready=true`), so without a dedup an RSVP'd lead who
        keeps chatting re-pinged the team every single turn. ready_subtype='openhouse' is
        the 'already notified' marker."""
        already_notified = lead.ready_subtype == "openhouse"
        lead.ready_subtype = lead.ready_subtype or "openhouse"
        if already_notified:
            self.session.add(lead)
            return
        contact = lead.phone_e164 or (f"IG @{lead.ig_username}" if lead.ig_username else None) \
            or "no contact yet"
        alerts = AlertService(self.session, self.branch_id, self._notifier, llm=self.llm)
        try:
            await alerts.raise_alert(
                lead_id=lead.id,
                kind="ready_openhouse",
                summary_en=(
                    f"Lead RSVP'd for an event · contact {contact} · IT STEP will call back "
                    "Mon-Fri, 09:00-18:00 WIB (no same-day callback outside those hours)"
                ),
                summary_ru=(
                    f"Лид согласился на ивент · контакт {contact} · перезвонят в рабочее "
                    "время IT STEP (Пн-Пт, 09:00-18:00 WIB, без обещания в тот же день)"
                ),
                thread_id=thread.id,
                lead_phone=lead.phone_e164,
            )
        except Exception:
            logger.warning("openhouse alert failed lead=%s", lead.id, exc_info=True)
        self.session.add(lead)

    async def _raise_manager_alert(
        self, thread_id: int, lead_id: int, decision: Decision,
        lead_phone: str | None = None,
    ) -> None:
        await raise_manager_alert(
            self.session, self.branch_id, self._notifier, self.llm,
            thread_id, lead_id, decision, lead_phone)

    def _scheduled_at(self) -> datetime:
        """Return send time: now + random delay from settings (or immediate if none)."""
        if self.settings is None:
            return datetime.now(UTC).replace(tzinfo=None)
        delay_s = random.randint(  # noqa: S311 — jitter, not crypto
            self.settings.reply_delay_min_s,
            max(self.settings.reply_delay_min_s, self.settings.reply_delay_max_s),
        )
        return (datetime.now(UTC) + timedelta(seconds=delay_s)).replace(tzinfo=None)
