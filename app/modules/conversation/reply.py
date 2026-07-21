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

from . import critic, guard
from .decision import Decision, parse_decision
from .engine import DecisionEngine, _fmt_llm_meta, _retrieval_query  # noqa: F401 — re-exported
from .needs import _content_tokens, is_question, lead_grounded, merge_needs, parse_needs
from .repository import CoachingNoteRepo, MessageRepo, OutboxRepo, ThreadRepo
from .routing import FAST, SMART, pick_capability
from .situations import (
    AD_TEMPLATE_RE as _AD_TEMPLATE_RE,
)
from .situations import (
    DISCOVERY_TURN_CAP as _DISCOVERY_TURN_CAP,
)
from .situations import (
    NEED_PAYOFF_NUDGE as _NEED_PAYOFF_NUDGE,
)
from .situations import (
    PREMATURE_CONTACT_CORRECTION,
    premature_contact_ask,
    with_situation,
)
from .situations import (
    SOFT_NO_RE as _SOFT_NO_RE,
)
from .situations import (
    is_answerable_question as _is_answerable_question,
)
from .situations import (
    lead_spoke_own_words as _lead_spoke_own_words,
)
from .situations import (
    pick_nudge as _pick_situational_nudge,
)
from .situations import (
    postpone_days as _postpone_days,
)

logger = logging.getLogger(__name__)

_BUBBLE_GAP_S = settings().bubble_gap_s  # stagger between split reply bubbles
_MAX_BUBBLES = settings().max_bubbles
_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")
# Indonesian mobile number as typed in chat: 08…, 628…, +628…, with optional spaces/dashes.
_ID_PHONE_RE = re.compile(r"(?:\+?62[\s\-]?|0)8\d(?:[\s\-]?\d){6,10}")
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

# A live reply that repeats a question already asked in this thread — same failure mode
# followup.py guards against (chat 1830), but on the live-reply path, which had NO dedup
# check at all (thread 2260, 2026-07-08: the SECOND occurrence of a re-asked discovery
# question was a live reply, not a followup, and slipped straight through).
_DUPLICATE_RATIO = 0.6
# Two questions sharing this fraction of content words are the SAME question reworded — tuned
# on the 2026-07-19 week sweep to catch reworded discovery re-asks without tripping distinct
# questions that happen to share a noun.
_QUESTION_REPEAT_JACCARD = 0.55
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
                # Content-word overlap of the two QUESTIONS catches a REWORDED discovery re-ask
                # that the char ratio slides under: 'apa target utama belajar coding?' re-asked
                # as 'Kakak pengen capai apa lewat coding?' shares the key nouns but not the
                # surface (live 4531/3154/4306 — a lead's answered qualifier got asked again).
                nqw, pqw = _content_words(new_q), _content_words(prior_q)
                if len(nqw) >= 4 and len(pqw) >= 4:
                    qj = len(nqw & pqw) / len(nqw | pqw)
                    if qj >= _QUESTION_REPEAT_JACCARD:
                        ratio = max(ratio, _DUPLICATE_RATIO)
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


# Horizontal-rule / lone-heading lines the model sometimes leaks into a DM (markdown bleed).
_MD_ARTIFACT_RE = re.compile(r"^[ \t]*(?:-{3,}|\*{3,}|_{3,}|—{3,}|#{1,6})[ \t]*$", re.MULTILINE)


def _clean_bubble(text: str) -> str:
    """Strip markdown artifacts that read as noise in a chat bubble — a horizontal rule
    (---, ***, ___) or a lone heading marker (live thread 2778: a trailing '---' shipped to
    the lead). Conservative: only removes lines that are ONLY the artifact, never trims real
    content (so 'Rp 500.000 - 600.000' is untouched)."""
    cleaned = _MD_ARTIFACT_RE.sub("", text or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return guard.normalize_address(cleaned.strip())


def _split_bubbles(reply: str, max_parts: int = _MAX_BUBBLES) -> list[str]:
    """Split the model's reply on '|||' into ≤max_parts non-empty bubbles; overflow is
    merged into the last one so we never send more than max_parts messages."""
    parts = [c for p in reply.split("|||") if (c := _clean_bubble(p))]
    if len(parts) <= max_parts:
        return parts
    return [*parts[: max_parts - 1], " ".join(parts[max_parts - 1:])]


def _reply_bubble_cap(reply: str) -> int:
    """At most TWO messages in a row for a normal reply — three DMs with no lead turn between
    reads as a monolog and raises spam-detection risk (20-chat audit: bot share ~65-70%). The
    numbered-menu turns (ad opener, clarify/goal menu) genuinely need their 3rd bubble for the
    options, so those keep the full _MAX_BUBBLES; everything else is capped at 2."""
    return _MAX_BUBBLES if "1️⃣" in (reply or "") else 2


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


def _deterministic_issues(
    reply: str, context: str, lead_spoke: bool = True, lead_words: str = "",
) -> list[str]:
    """Every KB-context-free check — no LLM call needed, always on regardless of
    reply_guard mode. Re-run on a regenerated draft too, so a still-broken reply is caught
    before it ships rather than trusted on faith. `lead_words` is everything the lead has
    typed in their own words (ad prefill excluded) — the payment-details gate needs it."""
    return [
        *guard.ungrounded_urls(reply, context),
        *guard.false_delivery_claims(reply),
        *guard.multiple_questions(reply),
        *guard.impossible_capability_offers(reply),
        *guard.wrong_channel_claims(reply),
        *guard.whatsapp_delivery_offers(reply),
        *guard.price_before_lead_spoke(reply, lead_spoke),
        *guard.stale_dates(reply),
        *guard.booster_wrong_duration(reply),
        *guard.vibe_wrong_duration(reply),
        *guard.fabricated_income_figure(reply),
        *guard.ungrounded_times(reply, context),
        *guard.price_order_wrong(reply),
        *guard.ungrounded_biz_counts(reply, context),
        *guard.career_service_claims(reply),
        *guard.open_house_as_event(reply),
        *guard.open_house_online_claims(reply),
        *guard.game_offering_claims(reply),
        *guard.nonexistent_hardware_claims(reply),
        *guard.student_discount_to_adult(reply),
        *guard.premature_payment_details(reply, lead_words),
        *guard.invented_price_no_card(reply, context),
    ]


def _own_words(dialog) -> str:  # noqa: ANN001
    """Everything the lead typed themselves — the ad's prefilled opener is a button click,
    not their words, so it never counts."""
    return " ".join(
        m.text or "" for m in dialog
        if m.direction == "in" and not _AD_TEMPLATE_RE.search(m.text or ""))


# Vague continuations / particles that carry no goal-content: 'terus gimana', 'jelasin dong'
# must NOT count as substantive (they're exactly when a clarify IS warranted), while
# 'nyari magang', 'mau switch karier' must.
_VAGUE_WORDS = frozenset({
    "terus", "trus", "lanjut", "gimana", "gmn", "kenapa", "napa", "jelasin", "jelaskan",
    "ceritain", "dong", "sih", "deh", "nah", "kok", "lah", "aja", "kan", "nih", "tuh",
    "kek", "kayak", "gitu", "gtu", "gimanaa",
})


def _is_substantive_statement(text: str) -> bool:
    """The lead said something with real CONTENT (a goal/pain/context, e.g. 'nyari magang',
    'mau switch karier') — NOT a bare menu tap ('4'), an ack ('iya'), a greeting, or a vague
    continuation ('terus gimana', 'jelasin dong'). Such a statement must be engaged, never
    brushed off with a 'be more specific' menu. Requires: not a question, and ≥2 content words
    once fillers (needs._STOP) and vague continuations are removed."""
    if is_question(text):
        return False
    return len(_content_tokens(text or "") - _VAGUE_WORDS) >= 2


def _bump_guard_regen_count(lead: Lead) -> None:
    """A regen fired for this lead — persist it as a per-lead routing signal (see
    routing.pick_capability's guard_regen_count) so future turns lean toward chat:smart
    for a lead the cheap model has already stumbled on, not just this one turn."""
    lead.guard_regen_count += 1


async def guard_decision(
    session: AsyncSession, branch_id: int, branch_settings: BranchSettings | None,
    llm: LLMPort, engine: DecisionEngine, ctx, thread_id: int, lang: str, workflow: str,
    bill: bool, decision: Decision, meta: dict, situational: str | None = None,
) -> tuple[Decision, dict]:
    """Block fabricated facts and a handful of conversation-quality failures: ungrounded
    links, false delivery claims, more than one question in a turn, impossible capability
    offers, and telling an Instagram lead to go DM on Instagram — all deterministic — plus
    an LLM grounding check on risky replies. One correcting regeneration, then a safe
    hand-off — never send the violation. Off when reply_guard='off'. Shared by live replies
    AND follow-up nudges.

    `situational` is the turn's nudge (situations.pick_nudge), re-attached to every
    correction: a regen re-answers the SAME turn, so dropping it silently un-does the
    situational layer at the worst moment. Thread 4092 (2026-07-16) is the live proof — the
    first draft correctly greeted an ad-clicker with no price, got regenerated for naming a
    product that isn't in the KB, and the regen — no longer told this lead had never spoken
    — answered a button click with the full price and DP. Exactly incident 3926 through
    another door.

    Returns (decision, meta) — meta is the regen's broker-log line when a regen
    happened, else the meta passed in unchanged."""
    mode = branch_settings.reply_guard if branch_settings is not None else "full"
    if mode == "off" or not decision.reply:
        return decision, meta
    context = engine.last_context
    regenerated = False

    def _correct(correction: str) -> str:
        return with_situation(correction, situational)
    if decision.needs_manager:
        # Mutually exclusive, most-specific-first: a price question already answered in KB
        # gets the targeted correction; anything else with no stated reason gets the generic
        # one. Only ONE extra regen per turn either way — never chain both on the same
        # decision.
        # A price/pay question can sit 1-2 turns back: thread 4710 — 'Brpa aja kak' → the bot
        # asked for the phone → the lead sent it → THIS turn handed off, so the last inbound is
        # the phone number, not the question, and a last-message-only check missed it. Scan the
        # last few inbounds so a just-asked, KB-answerable price/pay question still blocks the
        # hand-off, not only when it is the very last message.
        recent_in = " ".join(
            reversed([m.text or "" for m in reversed(ctx.dialog) if m.direction == "in"][:3]))
        if guard.premature_manager_handoff(recent_in, context):
            logger.warning(
                "guard: branch=%d thread=%d premature needs_manager on a price question "
                "already answered in KB → regen", branch_id, thread_id)
            regenerated = True
            raw, regen_meta = await engine.complete(
                ctx, thread_id, lang=lang, workflow=workflow, capability=SMART, bill=bill,
                extra_user_msg=_correct(guard.MANAGER_HANDOFF_CORRECTION))
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
                extra_user_msg=_correct(guard.UNEXPLAINED_HANDOFF_CORRECTION))
            try:
                fixed = parse_decision(raw)
            except ValueError:
                fixed = None
            # Adopt the regen either way: it either named the gap (still escalating, but now
            # with something for the manager to act on) or stopped escalating outright — both
            # are strictly better than the original unexplained hand-off.
            if fixed is not None and fixed.reply:
                decision, meta = fixed, regen_meta
    lead_spoke = _lead_spoke_own_words(ctx.dialog)
    lead_words = _own_words(ctx.dialog)
    issues = _deterministic_issues(decision.reply, context, lead_spoke, lead_words)
    # Skip the LLM verify when (a) the critic-gate is ON — its `grounded` dimension is a stricter,
    # fail-closed version of this exact check and it runs on EVERY reply, so verify would be a
    # redundant second smart grounding pass — or (b) the reply's only risk is a price that
    # string-matches the KB (a pure repetition of a grounded fact).
    critic_on = branch_settings is not None and branch_settings.critic_gate == "on"
    if mode == "full" and not critic_on and guard.is_risky(decision.reply) \
            and not guard.price_claims_grounded(decision.reply, context):
        issues += await guard.verify_grounding(
            llm, decision.reply, context, branch_id=branch_id, thread_id=thread_id,
            bill=bill, budget=ctx.budget, system=await guard_prompt(session, branch_id))
    if not issues:
        if regenerated and ctx.lead is not None:
            _bump_guard_regen_count(ctx.lead)
        return decision, meta
    logger.warning("guard: branch=%d thread=%d fabrication → regen: %s",
                   branch_id, thread_id, issues[:3])
    raw, regen_meta = await engine.complete(
        ctx, thread_id, lang=lang, workflow=workflow, capability=SMART, bill=bill,
        extra_user_msg=_correct(guard.CORRECTION.format(issues="; ".join(issues[:5]))))
    try:
        fixed = parse_decision(raw)
    except ValueError:
        fixed = decision
    if ctx.lead is not None:
        _bump_guard_regen_count(ctx.lead)
    # Only the deterministic checks are re-verified (an LLM re-verify would double cost);
    # a still-broken draft means we can't trust it → hand off.
    from dataclasses import replace  # noqa: PLC0415
    remaining = (_deterministic_issues(fixed.reply, context, lead_spoke, lead_words)
                 if fixed.reply else ["empty reply"])
    if not remaining:
        return fixed, regen_meta
    # A still-doubled-up question after the regen is a style slip, not a fabrication risk —
    # trim to the first question deterministically instead of wasting a manager's attention
    # on a lead who asked something the KB already answers (threads 2159/2160: "ceritakan
    # lebih detail" got a full hand-off purely because the regen ALSO asked two questions).
    if all("question mark" in issue for issue in remaining):
        trimmed = guard.truncate_to_one_question(fixed.reply)
        if not _deterministic_issues(trimmed, context, lead_spoke, lead_words):
            return replace(fixed, reply=trimmed), regen_meta
    logger.error("guard: branch=%d thread=%d unfixable violation → hand-off",
                 branch_id, thread_id)
    # Guard-origin escalation: stamp its own reason so the alert and chat log don't
    # misattribute it to the lead or to the model's stage_reason (keep a real model-named
    # gap if it happened to set one).
    return replace(fixed, reply=guard.SAFE_FALLBACK, needs_manager=True,
                   kb_gap=fixed.kb_gap or guard.GUARD_HANDOFF_REASON), regen_meta


async def apply_critic(
    branch_settings: BranchSettings | None, engine: DecisionEngine, ctx, thread_id: int,
    *, lang: str, workflow: str, bill: bool, decision: Decision, meta: dict,
    situational: str | None, last_inbound: str, open_objections: list[str],
) -> tuple[Decision, dict]:
    """Positive quality gate on the FINAL draft: judge it against critic.DIMENSIONS, and on
    failure regen ONCE with the critic's feedback, re-judge, then FAIL CLOSED to a human — the
    last word on quality, so nothing downstream can resurrect a rejected reply. Deliberate
    hand-offs and canned safety replies are skipped (the critic judges genuine sales replies,
    not a considered 'let me check with the team'). See critic.py."""
    mode = branch_settings.critic_gate if branch_settings is not None else "off"
    reply = (decision.reply or "").strip()
    canned = {guard.SAFE_FALLBACK.strip(), guard.CLARIFY_FALLBACK.strip(),
              guard.ASK_PHONE_BEFORE_HANDOFF.strip()}
    # 'suggest' is a manager-facing DRAFT preview — the manager is the human reviewer, so the
    # fail-closed hand-off ('let me check with the team') is useless here; skip the critic and
    # give them the best grounded methodology draft to send or edit.
    if (mode == "off" or workflow == "suggest" or not reply
            or decision.needs_manager or reply in canned):
        return decision, meta

    async def _judge(text: str) -> critic.Critique:
        return await critic.critique_reply(
            engine.llm, reply=text, last_inbound=last_inbound, dialog=ctx.dialog,
            context=engine.last_context, needs=ctx.stored_needs,
            open_objections=open_objections, lang=lang, branch_id=engine.branch_id,
            thread_id=thread_id, bill=bill, budget=ctx.budget)

    crit = await _judge(decision.reply)
    if mode == "shadow":
        logger.info("critic[shadow] branch=%d thread=%d ok=%s: %s",
                    engine.branch_id, thread_id, crit.ok, crit.summary())
        return decision, meta
    if crit.ok:
        return decision, meta
    logger.warning("critic branch=%d thread=%d rejected draft → regen: %s",
                   engine.branch_id, thread_id, crit.summary())
    raw, regen_meta = await engine.complete(
        ctx, thread_id, lang=lang, workflow=workflow, capability=SMART, bill=bill,
        extra_user_msg=with_situation(
            critic.CRITIC_CORRECTION.format(
                failures="; ".join(crit.failures[:5]), fix=crit.fix), situational))
    if ctx.lead is not None:
        _bump_guard_regen_count(ctx.lead)
    try:
        fixed = parse_decision(raw)
    except ValueError:
        fixed = None
    from dataclasses import replace  # noqa: PLC0415
    if fixed is not None and fixed.reply:
        recrit = await _judge(fixed.reply)
        det = _deterministic_issues(
            fixed.reply, engine.last_context,
            _lead_spoke_own_words(ctx.dialog), _own_words(ctx.dialog))
        if recrit.ok and not det:
            return fixed, regen_meta
        logger.warning(
            "critic branch=%d thread=%d regen still not top-tier (%s / det=%s) → hand-off",
            engine.branch_id, thread_id, recrit.summary(), det[:2])
    # Two drafts couldn't clear the bar — hand the lead to a human rather than send a sub-par
    # reply. This is the guarantee's teeth: only proven-good replies reach the lead.
    base = fixed if fixed is not None and fixed.reply else decision
    return replace(base, reply=guard.SAFE_FALLBACK, needs_manager=True,
                   kb_gap=base.kb_gap or critic.CRITIC_HANDOFF_REASON), regen_meta


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

        workflow tags the broker-log row: 'sim' (sandbox testing) routes exactly like a
        real reply and is logged distinctly. Sim runs BILL like everything else — they run
        on the dedicated sandbox branch, so its own llm_spend ledger carries the cost and
        the daily-budget gate stays meaningful (prepare() already enforces it for sims)."""
        bill = True
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
        inbound_count = await self.messages.inbound_count(thread_id)
        cap = pick_capability(
            workflow=route_wf, stage=lead.stage if lead is not None else None,
            lead_type=lead.lead_type if lead is not None else None,
            last_inbound=last_in.text if last_in is not None else "",
            inbound_count=inbound_count,
            guard_regen_count=lead.guard_regen_count if lead is not None else 0)
        # Situational steering — detectors, nudges, priorities and their conflict combos all
        # live in situations.pick_nudge (one chain, one owner; see that module's docstring).
        last_txt = (last_in.text if last_in is not None else "") or ""
        extra_user_msg = _pick_situational_nudge(
            lead_type=lead.lead_type if lead is not None else None,
            dialog=ctx.dialog,
            last_txt=last_txt,
            stored_needs=ctx.stored_needs,
            inbound_count=inbound_count)
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
                    ctx, thread_id, lang=lang, workflow=workflow, capability=SMART, bill=bill,
                    extra_user_msg=extra_user_msg)  # keep the turn's situational nudge on retry
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
        # The pain surfaced in THIS very turn, so _NEED_PAYOFF_NUDGE couldn't fire — it reads
        # the STORED profile, which was still empty when this reply was drafted. That one turn
        # is exactly where the audit found deals dying: the model hears the pain and answers
        # with the price list (or a bare "give me your WhatsApp"). Redraft it once, now that we
        # know a pain landed and no payoff exists. Costs one extra call, once per lead, at the
        # single highest-stakes beat of the sale; from the next turn the nudge takes over.
        # is_question (a real '?' / interrogative opener), NOT _is_answerable_question: the
        # latter fires on bare keywords, and 'tantangannya kurangnya modal' is a PAIN, not a
        # price question — it must not block the redraft it was meant to trigger.
        if (decision.reply and decision.pains and not ctx.stored_needs.pains
                and not (ctx.stored_needs.gains or decision.gains)
                and not is_question(last_txt)):
            logger.info(
                "%s: branch=%d thread=%d first pain caught, no payoff → need-payoff regen",
                workflow, self.branch_id, thread_id)
            raw, regen_meta = await engine.complete(
                ctx, thread_id, lang=lang, workflow=workflow, capability=cap, bill=bill,
                extra_user_msg=_NEED_PAYOFF_NUDGE)
            try:
                redrafted = parse_decision(raw)
            except ValueError:
                pass  # keep the original draft AND its meta — the regen is discarded
            else:
                if redrafted.reply:
                    from dataclasses import replace  # noqa: PLC0415
                    # keep what the FIRST draft extracted: the redraft is about the reply TEXT,
                    # and its own extraction pass can silently drop the pain we just caught
                    decision = replace(
                        redrafted,
                        pains=redrafted.pains or decision.pains,
                        jobs=redrafted.jobs or decision.jobs)
                    meta = regen_meta
        if decision.reply:
            prior, ratio = _most_similar_prior(decision.reply, ctx.dialog)
            if ratio >= _DUPLICATE_RATIO:
                logger.warning(
                    "%s: branch=%d thread=%d near-duplicate reply (ratio=%.2f) → regen",
                    workflow, self.branch_id, thread_id, ratio)
                raw, regen_meta = await engine.complete(
                    ctx, thread_id, lang=lang, workflow=workflow, capability=SMART, bill=bill,
                    extra_user_msg=with_situation(
                        _REPEAT_CORRECTION.format(
                            prior=prior, last_in=last_in.text if last_in is not None else ""),
                        extra_user_msg))
                try:
                    decision = parse_decision(raw)
                    meta = regen_meta  # adopt the regen's broker line only when its reply is used
                except ValueError:
                    pass  # keep the original draft AND its meta — the regen is discarded
        decision, meta = await guard_decision(
            self.session, self.branch_id, self.settings, self.llm,
            engine, ctx, thread_id, lang, workflow, bill, decision, meta,
            situational=extra_user_msg)
        if workflow == "suggest":
            # Manager DRAFT preview: return the guard-checked draft (fabrications still blocked)
            # WITHOUT the live-send flow — dedup-vs-history, clarify-loop, premature-contact and
            # the phone-gate stubs would overwrite the draft with a canned line, defeating the
            # feature. The critic is skipped for suggest inside apply_critic anyway; the caller
            # rolls the session back so no needs/stage is persisted.
            return decision
        # guard_decision's own regen (for an UNRELATED violation) is never re-checked against
        # dialog history, so it can silently reintroduce the exact duplicate rejected above —
        # same precedent as followup.py. A live reply can't just drop the send like a nudge
        # can, so ask the lead to narrow down instead of resending a duplicate. A repeat is a
        # STYLE dead-end, not a knowledge gap — don't summon a manager for it (that was the
        # top false-escalation driver on terse SMM threads 2541/2566); leave needs_manager to
        # the model's own decision.
        last_in_txt = next(
            (m.text or "" for m in reversed(ctx.dialog) if m.direction == "in"), "")
        if decision.reply:
            _, post_guard_ratio = _most_similar_prior(decision.reply, ctx.dialog)
            if post_guard_ratio >= _DUPLICATE_RATIO:
                from dataclasses import replace  # noqa: PLC0415
                # The numbered-menu clarify was sent ANYWHERE in this thread already, not just
                # last turn — bench 2864 fired the identical "1️⃣2️⃣3️⃣4️⃣" menu THREE times,
                # interspersed with other replies, so a last-out-only check kept missing it.
                clarify_norm = guard.CLARIFY_FALLBACK.strip().lower()
                looping = any(
                    SequenceMatcher(None, (m.text or "").strip().lower(),
                                    clarify_norm).ratio() >= 0.7
                    for m in ctx.dialog if m.direction == "out")
                if _is_answerable_question(last_in_txt) or _is_substantive_statement(last_in_txt):
                    # the lead asked a CONCRETE question, OR made a SUBSTANTIVE statement (voiced
                    # a goal/pain/context) — a "be more specific" menu on top of that reads as not
                    # listening. Keep the reply even if it echoes an earlier one; an on-topic near
                    # repeat beats a dismissive clarify (thread 2977: 'Apakah harus modal?' got the
                    # menu instead of the price; thread 4660: 'sebenernya aku nyari magang' got it
                    # instead of an answer about the internship path).
                    pass
                elif looping:
                    # We already asked the lead to narrow down LAST turn and still can't produce
                    # a fresh answer → the info genuinely isn't in the KB. Never repeat the
                    # identical "be more specific" (live loop in thread 2262: sent verbatim twice
                    # for a clear "show me the mini project" the KB had no example for). Hand the
                    # lead's real question to a human instead of looping.
                    logger.warning("%s: branch=%d thread=%d clarify loop → hand-off",
                                   workflow, self.branch_id, thread_id)
                    decision = replace(
                        decision, reply=guard.SAFE_FALLBACK, needs_manager=True,
                        kb_gap=decision.kb_gap or guard.GUARD_HANDOFF_REASON)
                else:
                    logger.warning(
                        "%s: branch=%d thread=%d still near-duplicate after guard regen "
                        "(ratio=%.2f) → clarify", workflow, self.branch_id, thread_id,
                        post_guard_ratio)
                    decision = replace(decision, reply=guard.CLARIFY_FALLBACK)
        # DON'T GRAB CONTACT BEFORE EARNING IT. Asking a still-cold lead (no pain surfaced, no
        # price/pay/buying signal, no phone yet) for their WhatsApp reads as a lead-capture bot,
        # and cold leads bail — the single biggest measured conversion leak (contact given by
        # only ~6/100 leads; thread 4615 tapped a menu number, got 'give me your WhatsApp', left).
        # Regen once to deliver value tied to their goal + a deepening question instead.
        _has_phone = bool(lead is not None and lead.phone_e164) \
            or bool((decision.phone or "").strip())
        if decision.reply and premature_contact_ask(
            decision.reply, last_in_txt,
            has_pains=bool(ctx.stored_needs.pains), has_phone=_has_phone,
            ready=bool(decision.ready),
            has_open_objection=bool(decision.open_objections),
        ):
            logger.info("%s: branch=%d thread=%d premature contact ask on a cold lead → regen",
                        workflow, self.branch_id, thread_id)
            raw, regen_meta = await engine.complete(
                ctx, thread_id, lang=lang, workflow=workflow, capability=SMART, bill=bill,
                extra_user_msg=with_situation(PREMATURE_CONTACT_CORRECTION, extra_user_msg))
            try:
                fixed = parse_decision(raw)
                if fixed.reply and not premature_contact_ask(
                    fixed.reply, last_in_txt, has_pains=bool(ctx.stored_needs.pains),
                    has_phone=False, ready=bool(fixed.ready),
                    has_open_objection=bool(fixed.open_objections)):
                    decision, meta = fixed, regen_meta
            except ValueError:
                pass  # keep the guarded draft rather than drop the reply
        # KEEP A HAND-OFF PROMISE. If the reply tells the lead a human is taking over, a human
        # must actually be notified — otherwise the bot promises a call, nobody gets the lead,
        # and the follow-up cycle keeps nudging the person it just handed off (thread 1230).
        # Set before the phone gate so a promise made without a contact still routes correctly.
        if decision.reply and not decision.needs_manager \
                and guard.promised_handoff(decision.reply):
            from dataclasses import replace  # noqa: PLC0415, F811
            logger.info(
                "guard: branch=%d thread=%d reply promises a hand-off → escalate for real",
                self.branch_id, thread_id)
            decision = replace(decision, needs_manager=True,
                               kb_gap=decision.kb_gap or guard.GUARD_HANDOFF_REASON)
        # ANSWER an answerable question rather than phone-gate it. When the model escalates on a
        # concrete, answerable question and we have no phone, the phone gate below would swallow
        # that question under a "give me your WhatsApp" stub — re-sent verbatim each time the
        # lead re-asked (thread 2733/S2: "how much?" → "when?" → "I want to register" all got
        # the identical contact-ask, never an answer; premature_manager_handoff missed it
        # because the price wasn't in the retrieved context that turn). Force one KB answer
        # first; only if it STILL escalates does the contact-ask below stand.
        if decision.needs_manager and lead is not None and not lead.phone_e164 \
                and not (decision.phone or "").strip() \
                and _is_answerable_question(last_in_txt) \
                and not _AD_TEMPLATE_RE.search(last_in_txt):
            from dataclasses import replace  # noqa: PLC0415, F401
            raw, regen_meta = await engine.complete(
                ctx, thread_id, lang=lang, workflow=workflow, capability=SMART, bill=bill,
                extra_user_msg=guard.ANSWER_DONT_ESCALATE_CORRECTION)
            try:
                answered = parse_decision(raw)
            except ValueError:
                answered = None
            # Accept the regen's ANSWER even when the model insists on escalating: with no
            # phone the escalation gets suppressed below anyway, so discarding a clean answer
            # here only trades it for the contact-ask stub (thread 4224: pain+gain captured,
            # "berapa biayanya?" still got the WhatsApp stub because the regen re-escalated).
            if answered is not None and answered.reply \
                    and not guard.promised_handoff(answered.reply) \
                    and not _deterministic_issues(
                        answered.reply, engine.last_context,
                        _lead_spoke_own_words(ctx.dialog), _own_words(ctx.dialog)):
                if answered.needs_manager:
                    answered = replace(answered, needs_manager=False,
                                       manager_question=None, kb_gap=None)
                decision, meta = answered, regen_meta
        # PHONE BEFORE HAND-OFF (hard gate): never mute the bot and hand a contact-less lead to
        # a manager who then has no way to reach them (lead 2757 went to MANAGER with a NULL
        # phone; the SAFE_FALLBACK path sets needs_manager, bypassing the prompt's soft rule).
        # If the model wants a manager but we have no phone — and the lead didn't just give one
        # — suppress the escalation, keep the bot on, and ask for a WhatsApp number first. A
        # later turn WITH a phone escalates for real (a manual UI move to MANAGER is unaffected).
        #
        # Deterministic phone backfill FIRST: the model's `phone` field misses numbers the
        # lead literally typed (live 4529: '081321654184' in-chat, and the bot then ASKED for
        # a WhatsApp number). Indonesian-mobile shape only (08…/+628…), so prices/IDs don't
        # false-positive; scanning the dialog means a number from ANY earlier turn counts.
        if not (decision.phone or "").strip() \
                and lead is not None and not lead.phone_e164:
            for m in reversed(ctx.dialog):
                if m.direction == "in" and (hit := _ID_PHONE_RE.search(m.text or "")):
                    from dataclasses import replace  # noqa: PLC0415
                    decision = replace(decision, phone=hit.group(0))
                    logger.info("phone backfilled from dialog branch=%d thread=%d",
                                self.branch_id, thread_id)
                    break
        if decision.needs_manager and lead is not None \
                and not lead.phone_e164 and not (decision.phone or "").strip():
            from dataclasses import replace  # noqa: PLC0415
            # The stub REPLACES the whole reply — never let it erase a real answer. Keep the
            # drafted reply (and just drop the escalation) when the lead asked an answerable
            # question in their own words (thread 4224: the price answer was drafted, then
            # overwritten by the WhatsApp stub) or hasn't typed a word yet (thread 4199: the
            # very FIRST bot message to an ad click was the WhatsApp stub). Canned fallbacks
            # and hand-off promises are not answers — those still funnel into the stub.
            reply_txt = (decision.reply or "").strip()
            keepable = bool(reply_txt) \
                and reply_txt not in (guard.SAFE_FALLBACK, guard.CLARIFY_FALLBACK) \
                and not guard.promised_handoff(reply_txt)
            asked = _is_answerable_question(last_in_txt) \
                and not _AD_TEMPLATE_RE.search(last_in_txt)
            if keepable and (asked or not _lead_spoke_own_words(ctx.dialog)):
                logger.info(
                    "guard: branch=%d thread=%d needs_manager without a phone → keep the "
                    "answer, drop the escalation", self.branch_id, thread_id)
                decision = replace(decision, needs_manager=False, manager_question=None,
                                   kb_gap=None)
            elif any(guard.ASK_PHONE_BEFORE_HANDOFF.strip()[:40] in (m.text or "")
                     for m in ctx.dialog if m.direction == "out"):
                # already asked for the phone once and the lead still hasn't given it — re-sending
                # the identical "give me your WhatsApp" is spam (bench 4113: a non-target lead got
                # it verbatim 3× in a row). Drop the escalation and let the model's own reply
                # stand rather than repeat the stub.
                logger.info(
                    "guard: branch=%d thread=%d phone already requested → drop repeat stub",
                    self.branch_id, thread_id)
                decision = replace(decision, needs_manager=False, manager_question=None,
                                   kb_gap=None)
            elif asked:
                # The lead asked a real question but no clean answer survived the guards
                # (live 2740, 2026-07-19: two failed regens → the question got answered with
                # the phone stub and, with needs_manager stripped, NO human ever saw it). The
                # phone-gate premise is false on IG — the thread itself is a reply channel:
                # send the honest SAFE_FALLBACK and KEEP the escalation so a human answers
                # in-thread.
                logger.info(
                    "guard: branch=%d thread=%d unanswerable real question without a phone "
                    "→ safe fallback + keep the alert", self.branch_id, thread_id)
                decision = replace(decision, reply=guard.SAFE_FALLBACK)
            else:
                logger.info(
                    "guard: branch=%d thread=%d needs_manager without a phone → ask for "
                    "contact", self.branch_id, thread_id)
                decision = replace(decision, needs_manager=False, manager_question=None,
                                   kb_gap=None, reply=guard.ASK_PHONE_BEFORE_HANDOFF)
        # QUALITY GATE (last word): judge the final draft against the positive sales rubric and
        # fail closed to a human if it can't be made top-tier. Runs after every deterministic
        # safety net so it sees exactly what would be sent; open_objections wires in with the
        # objection-state layer.
        decision, meta = await apply_critic(
            self.settings, engine, ctx, thread_id, lang=lang, workflow=workflow, bill=bill,
            decision=decision, meta=meta, situational=extra_user_msg, last_inbound=last_in_txt,
            open_objections=sorted(set(ctx.stored_needs.objections)
                                   | set(decision.open_objections)))
        self._last_llm_meta = meta
        if lead is not None:
            # Needs are recorded ONLY once the lead has typed something of their own. An ad's
            # prefilled opener is a button click, not their words — the model kept inventing a
            # job+gain out of the course name in the template (thread 2912: one template click
            # → "menjadi ahli keamanan siber" appeared in the needs cloud).
            if _lead_spoke_own_words(ctx.dialog):
                # …and only what's actually GROUNDED in those words. The ad prefill is excluded
                # from the lead's "own words" here too, so its copy can't become a need; a pain
                # that is merely the lead's own question ("ini ai ya?") is dropped as well.
                own = " ".join(
                    m.text or "" for m in ctx.dialog
                    if m.direction == "in" and not _AD_TEMPLATE_RE.search(m.text or ""))
                jobs = lead_grounded(decision.jobs, own)
                gains = lead_grounded(decision.gains, own)
                pains = [p for p in lead_grounded(decision.pains, own) if not is_question(p)]
                objections = lead_grounded(decision.open_objections, own)
                merged = merge_needs(ctx.stored_needs, jobs, pains, gains,
                                     decision.discovery_complete, objections=objections)
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
        bubbles = _split_bubbles(decision.reply, max_parts=_reply_bubble_cap(decision.reply))
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
        soft_no = await self._snooze_on_soft_no(lead, thread)
        new_stage = self._stage_for(decision, lead, inbound, eff_subtype, soft_no=soft_no)
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

    async def _snooze_on_soft_no(self, lead: Lead, thread) -> bool:  # noqa: ANN001
        """A polite 'not now' gets ONE dated re-contact — never a kill, never a nudge storm.

        Audit of threads >=2000: the model may set DORMANT itself, and did so on 7 leads whose
        last word was a soft no ("Nggak kak, makasih. Next time aja ya", "Nanti saya fikirkan
        lagi", "Skip dulu, harganya gak masuk") — dead on the spot, zero follow-ups. The
        funnel guards the opposite direction (only code may set READY) but left DORMANT wide
        open, so the bot could kill a warm lead unilaterally.

        The naive fix — forbid DORMANT — is worse: the lead then enters the normal 1/4/24/120h
        cycle and gets FOUR nudges after saying no, which is the ban vector that produced
        "Gak usah ganggu aku lagi" (threads 2045/1996). So collapse the remaining schedule to
        its LAST step instead: exactly one gentle re-contact (~5 days out), then dormant. That
        is the KB's own rule — "a vague 'later' is a lost lead; a dated 'later' is a plan".

        Returns True when the lead soft-no'd this turn (the stage gate then blocks DORMANT).
        """
        last_in = await self.messages.last_inbound_text(thread.id)
        if not last_in or not _SOFT_NO_RE.search(last_in):
            return False
        if guard.lead_signaled_annoyance(last_in):
            return False  # a real "stop bothering me" — the hard-stop path owns it, not a snooze
        if lead.stage in HUMAN_LED_STAGES or lead.lead_type == "non_target":
            return False
        # The lead may have NAMED their own re-contact time ("bulan depan", "abis gajian",
        # "2 minggu lagi") — a dated 'later' beats any fixed snooze: schedule the single
        # re-contact exactly when THEY said (owner idea 2026-07-19). The OutboxSender's
        # step-arming is bypassed by setting next_followup_at directly past the schedule.
        named_days = _postpone_days(last_in)
        schedule = self.settings.followup_schedule_h if self.settings else []
        if named_days is not None:
            from datetime import UTC, datetime, timedelta  # noqa: PLC0415
            thread.followups_sent = max(
                thread.followups_sent, len(schedule) - 1 if schedule else 0)
            thread.next_followup_at = datetime.now(UTC).replace(tzinfo=None) \
                + timedelta(days=named_days)
            self.session.add(thread)
            logger.info(
                "branch=%d thread=%d soft-no with NAMED time → re-contact in %dd",
                self.branch_id, thread.id, named_days)
        elif schedule and thread.followups_sent < len(schedule) - 1:
            thread.followups_sent = len(schedule) - 1  # only the final, longest step remains
            self.session.add(thread)
            logger.info(
                "branch=%d thread=%d soft-no → snoozed to one final nudge (+%dh)",
                self.branch_id, thread.id, schedule[-1])
        return True

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
        _hard_stop (no explicit stop demand); a fresh inbound with real interest revives it.

        A human gets ONE ping about the close — no phone required (the usual phone gate is
        for deals; a troll won't give a number and shouldn't be asked for one). 2026-07
        audit: money-beggars and spammers (threads 4237/4113/2707) kept getting pitches for
        days because nobody ever saw them; the owner decided: alert + go silent."""
        thread.next_followup_at = None
        self.session.add(thread)
        if lead.stage != Stage.DORMANT:
            self.session.add(StageEvent(
                branch_id=self.branch_id, lead_id=lead.id, thread_id=thread.id,
                from_stage=str(lead.stage), to_stage=str(Stage.DORMANT),
                actor="bot", reason="non_target",
            ))
            lead.stage = Stage.DORMANT
            try:
                await AlertService(
                    self.session, self.branch_id, self._notifier, llm=self.llm,
                ).raise_alert(
                    lead_id=lead.id, kind="non_target",
                    summary_en="Non-target lead closed — bot went silent",
                    summary_ru="Нецелевой лид: бот закрыл диалог и замолчал. "
                               "Гляньте на всякий случай — вдруг это живой клиент.",
                    thread_id=thread.id, lead_phone=lead.phone_e164)
            except Exception:
                logger.warning("non_target close alert failed lead=%s", lead.id, exc_info=True)
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
                   ready_subtype: str | None = None, soft_no: bool = False) -> Stage:
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
        # A polite "not now" is an OBJECTION to work later, not a corpse. The model reached
        # for DORMANT on 7 such leads (audit of threads >=2000) — killing them the moment they
        # hesitated. _snooze_on_soft_no has already collapsed the cycle to one final nudge, so
        # this costs one gentle re-contact, not a nudge storm.
        if soft_no and decision.stage == Stage.DORMANT:
            return Stage.OBJECTION
        return decision.stage

    @staticmethod
    def _needs_captured(decision: Decision, lead: Lead) -> bool:
        # discovery_complete only counts when backed by a captured PAIN — the model sets the
        # flag prematurely with pains=[] (thread 1081), which skips warm-up and leaves needs
        # uncollected. Require the pain here so the bot keeps discovering until it has one.
        this_turn = decision.has_needs() or (decision.discovery_complete and bool(decision.pains))
        return this_turn or parse_needs(lead.needs).captured()

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

        Notify only once we have a PHONE — per policy, no team ping for a contact-less RSVP:
        the bot keeps talking and asks for the WhatsApp first, and the ping fires the turn a
        number is in hand. Fires at most ONCE (ready_subtype='openhouse' is the 'already
        notified' marker, set ONLY when the alert actually goes out, so a phone-less RSVP can
        still ping later once the number arrives)."""
        if lead.ready_subtype == "openhouse":  # already notified
            return
        if not lead.phone_e164:
            # No phone yet — don't ping the team for a contact-less RSVP; keep the bot on to
            # collect the WhatsApp first. A later turn WITH a number fires the ping.
            return
        lead.ready_subtype = "openhouse"  # mark notified only now that the alert actually fires
        alerts = AlertService(self.session, self.branch_id, self._notifier, llm=self.llm)
        try:
            await alerts.raise_alert(
                lead_id=lead.id,
                kind="ready_openhouse",
                summary_en=(
                    f"Lead RSVP'd for an event · phone {lead.phone_e164} · IT STEP will call "
                    "back Mon-Fri, 09:00-18:00 WIB (no same-day callback outside those hours)"
                ),
                summary_ru=(
                    f"Лид согласился на ивент · телефон {lead.phone_e164} · перезвонят в "
                    "рабочее время IT STEP (Пн-Пт, 09:00-18:00 WIB, без обещания в тот же день)"
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
