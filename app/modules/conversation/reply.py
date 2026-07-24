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
import re
from dataclasses import replace

from app.adapters.channels.ig_parse import IMAGE_PENDING_PH, VOICE_PENDING_PH
from app.domain.enums import Stage

from . import critic
from .contract import build_messages_v3
from .decision import Decision, TurnDecision, generate
from .delivery import ReplyDelivery, _script_lang
from .discovery import extract_discovery
from .dossier import merge_dossier
from .engine import _ASSISTANT_LAST_NUDGE, DecisionEngine
from .guard import quotes_price
from .money_gate import (
    MONEY_CORRECTION,
    MONEY_ESCALATION_REASON,
    PITCH_CORRECTION,
    PITCH_ESCALATION_REASON,
    money_issues,
    premature_pitch,
)
from .opener import (
    AD_TAP_OPENER,
    AD_TAP_OPENER_PRODUCT,
    JUNK_OPENER,
    SLOT_SYSTEM,
    STORY_OPENER,
    Entry,
    compose_typed_opener,
)
from .opener import classify as classify_entry
from .prompt import (
    AD_TYPED_ENTRY_HINT,
    ORGANIC_ENTRY_HINT,
    clean_first_name,
    lead_name_hint,
    source_hint,
)
from .repository import DossierRepo
from .routing import SMART, pick_capability
from .signals import (
    AD_TEMPLATE_RE,
    BUYING_SIGNAL_RE,
    DISCOVERY_TURN_CAP,
    PAYMENT_INTENT_RE,
    PRICE_QUESTION_RE,
    is_answerable_question,
)

logger = logging.getLogger(__name__)

ANSWER_FIRST_CORRECTION = (
    "[System: the lead asked you something directly and your draft did not answer it. Rewrite "
    "the SAME message so the FIRST sentence gives them the actual answer from the knowledge "
    "base, then continue as you intended. If you genuinely don't have the fact, say what you "
    "do know and offer to confirm the rest — never reply to a direct question with only a "
    "question back.]"
)

# Sent instead of the offending draft whenever a gate escalates — thread 5019: the pitch gate
# caught an unearned price+DP dump twice, correctly flagged needs_human=True and alerted a
# manager, but still SHIPPED that exact draft to the lead because `needs_human` only ever added
# a flag, never replaced `.reply`. The flag protected the CRM record, not the lead. This is the
# one message every escalation path ships instead: content-free, safe regardless of which gate
# tripped (invented price, uninvited pitch, ungrounded rewrite), and consistent with the tone of
# _MANAGER_HANDOFF_CLOSING so the lead doesn't get two conflicting "our team will help" lines.
ESCALATION_HOLD_REPLY = (
    "Kakak, bentar ya - aku cek dulu ke tim supaya infonya pas dan akurat. "
    "Nanti dibantu langsung di jam kerja (Senin-Jumat, 09.00-18.00 WIB) 🙏"
)


def _escalate(decision: TurnDecision, reason: str) -> TurnDecision:
    """Never ship the draft that triggered the escalation — only the reason and the dossier it
    already learned survive; the reply the lead actually sees is always the safe hold-line."""
    return replace(decision, reply=ESCALATION_HOLD_REPLY, needs_human=True, human_reason=reason)


# Deterministic per-turn coaching notes — injected as the LAST user message (same mechanism
# as followup_framing / _ASSISTANT_LAST_NUDGE) only on the turn their trigger fires, so they
# cost nothing on normal turns and never bloat the standing contract (which is at its size
# ceiling). Both address the two highest-frequency losses in the 24h sales audit.
BUYING_SIGNAL_NOTE = (
    "[System: the lead's last message is a YES / buying signal. Do NOT open a new discovery "
    "question — deliver exactly what you just offered AND move ONE concrete step toward "
    "enrolment: ask for their WhatsApp number so the team can secure their seat or send "
    "details, or confirm the schedule/DP. Re-asking about their goals after they said yes is "
    "how this sale gets lost.]"
)

# The lead's need is known (dossier has a pain AND a goal) but they haven't committed — the
# moment to stop discovering and START CLOSING. The 24h audit found only 5% of leads gave a
# phone: the bot kept the conversation open instead of converting a warm lead. Fires once
# discovery has landed and the lead isn't already `ready`.
CLOSING_NOTE = (
    "[System: you now KNOW this lead's need — see the dossier above (their pain and their "
    "goal). Stop asking discovery questions. Connect that need to the fitting product in one "
    "warm line using THEIR OWN words, then MOVE TO CLOSE: name one concrete next step with "
    "honest urgency drawn only from the knowledge base — the nearest intake date, the small "
    "group size, or the book-now discount if it's Vibe Coding — and ask for their WhatsApp "
    "naturally so the team can secure their seat / send details. Ask HOW, not WHETHER: two "
    "options that are both a yes (e.g. weekday vs weekend, visit vs online). Never invent a "
    "date, a limit, or a discount.]"
)
BARE_ACK_NOTE = (
    "[System: the lead has now answered twice in a row with bare acknowledgements — your open "
    "questions are not landing. Do not rephrase the previous question. Switch to ONE simple "
    "either-or choice or one concrete low-friction next step, in one short sentence.]"
)

# A low-information acknowledgement — the lead is nodding along without adding anything the
# conversation can build on. Thread 5042 answered "iya kk" TWELVE times: the old pattern only
# recognized "kak"/"kakak", so "kk"/"ka"/"kaka" and forms like "iya boleh"/"iya siap" all
# slipped through and the bot kept firing fresh discovery questions at a wall.
_BARE_ACK_RE = re.compile(
    r"^(?:iya*|iy|ya+|ok(?:e+)?|okok|sip|baik|siap|oh|hmm?|betul|bener)"
    r"(?:\s+(?:iya*|ya+|ok(?:e+)?|boleh+|sip|siap|aja|dong|deh))?"
    # optional address suffix: ka / kak / kaka / kakak / kk — 'k' then a short run of a/k
    r"[\s\W]*(?:k[ak]{0,4})?[\s\W]*$",
    re.IGNORECASE)
# Past this many consecutive bare acks the bot stops asking and forces a binary choice — a
# deterministic either-or, no LLM (the BARE_ACK_NOTE prose the model was given got ignored 12
# turns running on 5042). Two is the note's territory; three is a wall.
_BARE_ACK_HARD_CAP = 3

BARE_ACK_EITHER_OR = (
    "Kak, biar nggak muter-muter ya 😊 Kakak mau aku langsung bantuin ke proses daftar/"
    "booking-nya, atau masih ada yang pengen ditanyain dulu soal program atau biayanya?"
)

# A question about pay/career outcome — "gaji berapa", "prospek kerjanya gimana". Thread
# 5049: the bot met "gaji Nye brp" with a hold-line and a phone request, and Citra had to
# step in ("ini bukan lowongan kerjaan"). A salary question is one of the hottest buying
# signals there is; the answer (a grounded range) lives in facts_market's income section,
# but that section is gated behind an open job_outcome objection — which this bare question
# doesn't raise on its own, so the model answers with no figures in context and stalls.
# No trailing \b on the multi-word alternatives — "prospek kerjanya" carries a suffix, so a
# word boundary after "kerja" would (wrongly) fail to match.
_SALARY_Q_RE = re.compile(
    r"\bgaji\w*|\bsalary\b|\bpenghasilan\b|\bpendapatan\b|\bincome\b|"
    r"prospek\s+kerja|peluang\s+kerja|kerja\s+apa|jenjang\s+kar[ie]r|\bkarir\w*|"
    r"\bberapa\b[^?]{0,25}\b(?:dapat|dpt|hasil)\b",
    re.IGNORECASE)
SALARY_NOTE = (
    "[System: the lead asked about salary/career outcome — a hot buying signal, not a job "
    "application. Answer THIS turn with the concrete range from the knowledge base (always "
    "framed 'kisaran/tergantung', never a guarantee). NEVER meet a salary question with a "
    "hold-line or a request for their phone number — answer it, then move the sale forward.]"
)

# The lead has stopped engaging as a buyer — trolling, insults, off-topic spam, or plain
# gibberish. Threads 5091 (reels + "repost akun gue" + incoherence) and 5096 ("Ari kmu
# mabok??") each got 12 more pitches. One graceful, non-salesy reply, then stop pushing.
_DISENGAGE_RE = re.compile(
    r"\b(mabo?k|mabuk|goblo?k|tolol|bego|anjg|anjay|ngaco|garing|apaan\s*si|"
    r"gaje|halu|spam+|bot\s*(?:ya|kah|nih)|kepo|iseng|becanda|bercanda|"
    r"repost|folback|followback|follback|endorse|paid\s*promote)\b",
    re.IGNORECASE)
DISENGAGEMENT_NOTE = (
    "[System: the lead is not engaging as a buyer (trolling, joking, off-topic, or asking for "
    "a follow/repost/endorsement). Do NOT pitch or ask a sales question. Reply once, briefly "
    "and warmly, staying human; if there's nothing to sell here, it's fine to just close "
    "politely. Never chase.]"
)

# Discovery has run its course with nothing landing — past the same cap the stage gate uses,
# with no pain+gain captured. Thread 5039 asked 6+ discovery questions and never made an
# offer; the lead faded. Stop interrogating and present.
DISCOVERY_CAP_NOTE = (
    "[System: you've asked enough discovery questions and the lead isn't opening up. STOP "
    "asking open questions. Make a concrete move now: name the fitting product with its "
    "starting DP/instalment, mention the nearest intake, and offer ONE easy next step (a "
    "campus visit, the Demo Event, or booking a seat). One question max, and only a "
    "moving one (which schedule / shall I reserve), never another 'what are you looking for'.]"
)
# Explicit yes-words that accept a proposal. Deliberately EXCLUDES bare 'iya'/'ya': to an
# open question ('proyek apa?') those are filler, not acceptance (thread 5042 answered 'iya
# kak' eight times to open questions) — only unambiguous accept-words route to the
# advance-don't-rediscover note.
_YES_RE = re.compile(
    r"^(?:boleh+|mau+|ok(?:e|ee)?|okok|siap|sip|gas(?:s|keun)?|yu+k|ayo+|yaudah|gpp)"
    r"\b[\s\W]*(?:kak(?:ak)?|dong|aja)?[\s\W]*$", re.IGNORECASE)
# A fresh fear/doubt/objection in the lead's last message — must be handled BEFORE closing.
# Sim (p3-close): dossier had landed, so the closing trigger fired, but the lead had just
# said "takutnya aku ga bisa coding" — closing on a live fear reads as steamrolling and got
# escalated. Address the worry warmly first; the closing trigger waits for the next turn.
_OBJECTION_RE = re.compile(
    r"\b(takut\w*|khawatir|kuatir|ragu\w*|bingung|ga\s*bisa|gabisa|ga\s*ngerti|"
    r"gangerti|susah|sulit|mahal|kemahalan|berat|belum\s*(?:yakin|siap|sanggup)|"
    r"gimana\s*(?:kalau|kalo)|takutnya|worry|nggak\s*bisa|nggak\s*ngerti)\b",
    re.IGNORECASE)


def _turn_note(dialog: list, stored: object = None) -> str | None:
    """The one deterministic coaching note this turn needs, or None.

    Buying signal wins over bare-ack: 'boleh'/'mau' after the bot's own offer IS a
    bare-looking message, but it's an acceptance — advance-don't-rediscover is the right
    instruction (thread 5039: bot offered a campus visit, lead said 'Boleh', bot restarted
    discovery and the lead went quiet)."""
    ins = [m for m in dialog if m.direction == "in"]
    if not ins:
        return None
    last = (ins[-1].text or "").strip()
    # Priority order is deliberate. Disengagement first — never sell to a troll. Then salary
    # (a hot question that must be answered this turn, income context force-loaded for it).
    # Then buying signal (advance, don't re-discover). Then the discovery cap (stop asking,
    # present). Bare-ack note is the mildest, last.
    if _DISENGAGE_RE.search(last):
        return DISENGAGEMENT_NOTE
    if _SALARY_Q_RE.search(last):
        return SALARY_NOTE
    if _is_buying_signal(dialog):
        return BUYING_SIGNAL_NOTE
    # Discovery has landed but the lead hasn't committed — close, don't keep talking. BUT a
    # fresh fear/objection this turn must be handled first: closing over a live worry reads as
    # steamrolling (and trips the pitch gate). Let the normal handle_objection path take this
    # turn; the closing trigger fires again once the worry is answered.
    if (stored is not None and stored.has_discovery()
            and getattr(stored, "readiness", "") != "ready"
            and not _OBJECTION_RE.search(last)):
        return CLOSING_NOTE
    if (len(ins) >= DISCOVERY_TURN_CAP and stored is not None
            and not stored.has_discovery()):
        return DISCOVERY_CAP_NOTE
    if len(ins) >= 2 and _BARE_ACK_RE.match(last) \
            and _BARE_ACK_RE.match((ins[-2].text or "").strip()):
        return BARE_ACK_NOTE
    return None


def _bot_just_offered(dialog: list) -> bool:
    last_out = next((m for m in reversed(dialog) if m.direction == "out"), None)
    return last_out is not None and "?" in (last_out.text or "")


def _is_buying_signal(dialog: list) -> bool:
    """The lead's last message asks to move forward — an explicit intent word, a payment
    question, or a yes-word right after the bot's own offer. Doubles as the pitch gate's
    bypass: a lead who's asking to proceed has earned the close even with an empty dossier."""
    ins = [m for m in dialog if m.direction == "in"]
    if not ins:
        return False
    last = (ins[-1].text or "").strip()
    return bool(
        BUYING_SIGNAL_RE.search(last) or PAYMENT_INTENT_RE.search(last)
        or (_YES_RE.match(last) and _bot_just_offered(dialog)))


def _consecutive_bare_acks(dialog: list) -> int:
    """How many of the lead's MOST RECENT messages in a row are bare acks — the counter the
    hard either-or fires on. Counts back from the last inbound; the first non-ack stops it."""
    n = 0
    for m in reversed([m for m in dialog if m.direction == "in"]):
        if _BARE_ACK_RE.match((m.text or "").strip()):
            n += 1
        else:
            break
    return n

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
            # The first contact is classified by CODE and never free-generated — see opener.py
            # for the incident history this closes. Silent/junk entries ship a pure template;
            # typed entries ship a fixed frame with one bounded LLM slot; only when the slot
            # path declines (broker down, unsafe slot) does the full pipeline take over.
            # Gated on the lead writing in the branch's own script: the templates and frames
            # are Bahasa-only, so a Cyrillic opener (thread 452's Russian-speaking lead) goes
            # straight to the full pipeline, which follows the lead's language.
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
                logger.info("v3 branch=%d thread=%d move=%s tier=templated first=True",
                            self.branch_id, thread_id, decision.move)
                return decision.to_legacy(stored)
            if not ctx.over_budget and fc.entry in (Entry.AD_TYPED, Entry.ORGANIC):
                skeleton = await self._skeleton_opener(engine, ctx, fc, thread_id)
                if skeleton is not None:
                    self.last_decision = skeleton
                    logger.info(
                        "v3 branch=%d thread=%d move=%s tier=skeleton first=True",
                        self.branch_id, thread_id, skeleton.move)
                    return skeleton.to_legacy(stored)
        if not is_first_reply and _consecutive_bare_acks(ctx.dialog) >= _BARE_ACK_HARD_CAP:
            # The lead has stalled on bare acks (thread 5042: "iya kk" ×12). More open
            # questions won't land — force a binary either-or deterministically, same as the
            # opener templates. Zero LLM: the model was ignoring the prose note every turn.
            decision = TurnDecision(
                reply=BARE_ACK_EITHER_OR, move="handle_objection", stage=Stage.QUALIFYING)
            self.last_decision = decision
            logger.info("v3 branch=%d thread=%d tier=bare_ack_either_or", self.branch_id,
                        thread_id)
            return decision.to_legacy(stored)
        if ctx.over_budget:
            # prepare() was told to let the zero-cost template branch through; everything
            # from here on calls the broker, so the original budget gate applies now.
            logger.warning("branch=%d over daily LLM budget — %s skipped",
                           self.branch_id, workflow)
            return None
        # The first LLM turn — a plain first reply, OR the turn right after the templated
        # opener (every prior outbound is the template): with the opener no longer generated,
        # the highest-stakes generation moved to turn 2, but routing's is_first_reply=False
        # sent it to the cheap tier with an empty dossier steering every other branch to FAST.
        first_llm_turn = is_first_reply or all(t == AD_TAP_OPENER for t in outs)
        capability = pick_capability(stored, is_first_reply=first_llm_turn)
        # A salary/outcome question this turn force-loads the income section (facts_market's
        # job_outcome gate), so the model has the range in context instead of stalling on a
        # hold-line (thread 5049). It's a live question, not a stored objection, so it's added
        # for this turn's context only.
        asked_salary = bool(last_in and _SALARY_Q_RE.search(last_in.text or ""))
        categories = stored.open_objection_categories()
        if asked_salary:
            categories = categories | {"job_outcome"}
        context = await engine.kb_context(
            ctx, thread_id, light=False, objection_categories=categories)
        # The ad-entry hint asserts "they did not type it and did not ask you anything" —
        # true ONLY when the opening message really was the untouched button prefill. IG's
        # composer is editable: a lead can clear it and type a real question (thread 4972),
        # and the metadata still says ad_clicktomsg — injecting the hint then contradicts the
        # answer-first rule on the very message it matters most for.
        first_in = next((m for m in ctx.dialog if m.direction == "in"), None)
        pure_prefill_entry = bool(
            first_in and AD_TEMPLATE_RE.match((first_in.text or "").strip()))
        src = ctx.thread.lead_source
        if src == "ad_clicktomsg" and not pure_prefill_entry:
            # The lead typed/edited their own first message — the pure-tap hint would lie
            # ("they did not ask you anything"), but silence left the model with no entry
            # context at all (thread 5097). The typed-ad variant keeps the product anchor.
            entry_hint = AD_TYPED_ENTRY_HINT
        else:
            entry_hint = source_hint(src)
        if entry_hint is None and not src and not ctx.thread.ad_id:
            # A walk-in with no ad/story signal at all — the deep-discovery entry. Injected
            # every turn like the other entry hints; harmless once the dossier fills (its own
            # text defers to answer-first and to what the lead has already said).
            entry_hint = ORGANIC_ENTRY_HINT
        messages = build_messages_v3(
            context, ctx.dialog, lang, stored,
            coaching_notes=await self.coaching.active_manager_notes(),
            source_block=entry_hint,
            name_block=lead_name_hint(lead.display_name if lead is not None else None),
            manager_note=lead.manager_note if lead is not None else None,
            now_block=await engine._now_block(),  # noqa: SLF001 — branch-local clock, engine owns it
            is_first_reply=is_first_reply,
        )
        if messages[-1]["role"] == "assistant":
            # A re-triggered tick can reach here with the bot's own last message trailing.
            # Mistral hard-rejects an assistant-trailing array outright (code 3230; 285 such
            # errors in 24h when this was missing), and other providers silently treat it as a
            # continuation, which isn't the intent either. Nudge a fresh turn instead.
            messages.append({"role": "user", "content": _ASSISTANT_LAST_NUDGE})
        note = _turn_note(ctx.dialog, stored)
        if note:
            messages.append({"role": "user", "content": note})

        decision, _meta = await generate(
            engine, ctx, messages, thread_id, workflow=workflow,
            capability=capability, branch_id=self.branch_id)
        if decision is None:
            return None
        # The gates must see what the lead revealed THIS turn, not just prior turns. The main
        # call runs on chat:fast for a routine turn and fills decision.dossier unreliably, and
        # the discovery backstop used to run AFTER the gates (for next turn's save) — so on the
        # very turn a lead finished revealing a pain+goal, has_discovery() still read False and
        # the pitch gate escalated a legitimate close to a hold-line (sim p4-close/final). Run
        # the backstop BEFORE vetting when discovery hasn't landed yet, so the close/present
        # move is judged against a current dossier. The same merged dossier is then saved.
        merged = merge_dossier(stored, decision.dossier)
        if not merged.has_discovery():
            extra = await extract_discovery(
                self.llm, ctx.dialog, merged, lang, self.branch_id, thread_id,
                budget=ctx.budget)
            merged = merge_dossier(merged, extra)
        decision = await self._vet(
            engine, ctx, messages, thread_id, decision,
            workflow=workflow, capability=capability, context=context, lang=lang,
            last_inbound=(last_in.text if last_in is not None else "") or "",
            lead_typed_a_question=_typed_a_question(last_in), stored=merged,
            inbound_count=sum(1 for m in ctx.dialog if m.direction == "in"),
            lead_ready_signal=_is_buying_signal(ctx.dialog))
        await self.dossiers.save(lead.id if lead is not None else None, merged)
        self.last_decision = decision
        logger.info("v3 branch=%d thread=%d move=%s tier=%s first=%s",
                    self.branch_id, thread_id, decision.move, capability, is_first_reply)
        return decision.to_legacy(merged)

    async def _vet(  # noqa: PLR0913
        self, engine: DecisionEngine, ctx, messages: list[dict], thread_id: int,  # noqa: ANN001
        decision: TurnDecision, *, workflow: str, capability: str, context: str, lang: str,
        last_inbound: str, lead_typed_a_question: bool = False, stored: object = None,
        inbound_count: int = 0, lead_ready_signal: bool = False,
    ) -> TurnDecision:
        """Four gates, deliberately asymmetric.

        The money gate fails CLOSED, because quoting a price the school never set is a promise
        it has to honour. The critic fails OPEN, because an unreviewed real answer beats a stub
        — v2 had this the wrong way round and converted broker hiccups into lost leads.

        The answer gate and the pitch gate sit in between: both read the move the model
        DECLARED rather than its prose, so no other instruction can argue with them. v2
        enforced "no pitch before discovery" in code; the v3 rebuild only asked for it in
        prose, and thread 452 showed that wasn't enough on its own.

        Each fires independently, but the common case spends at most ONE rewrite, so a turn
        stays capped at three calls."""
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
                return _escalate(fixed or decision, MONEY_ESCALATION_REASON)
            if stored is not None and premature_pitch(
                fixed.move, stored, lead_typed_a_question, fixed.reply,
                inbound_count=inbound_count,
            lead_ready_signal=lead_ready_signal,
            ):
                # Asymmetry with the critic path closed: a money rewrite that dropped the bad
                # figure could still be an uninvited pitch, and used to ship unchecked.
                logger.error("v3 money rewrite pitched uninvited branch=%d thread=%d",
                             self.branch_id, thread_id)
                return _escalate(fixed, PITCH_ESCALATION_REASON)
            return fixed

        if lead_typed_a_question and decision.move != "answer_question":
            # Checked against the move the model DECLARED, not against its prose: cheap, exact,
            # and impossible for another instruction to argue with. Two live threads showed why
            # a prompt rule alone isn't enough — the same input got answered on one and
            # deflected on the next. Only fires when the lead TYPED the question; a prefilled
            # ad button is a tap, and opening a tap with a warm question is correct.
            logger.info("answer gate branch=%d thread=%d: lead asked, move was %s",
                        self.branch_id, thread_id, decision.move)
            answered = await self._regenerate(
                engine, ctx, messages, thread_id, workflow=workflow,
                correction=ANSWER_FIRST_CORRECTION)
            if answered is not None:
                # Same lesson as the critic path (thread 5010): a correction that demands
                # "give the actual answer FIRST" is exactly the instruction that talks the
                # model into a figure — and this rewrite used to ship with no check at all.
                # The original draft passed money_issues above; its rewrite must too. (No
                # premature_pitch re-check needed: lead_typed_a_question is True on this
                # branch, which is that gate's own bypass.)
                rewrite_issues = money_issues(answered.reply, context)
                if rewrite_issues:
                    logger.error(
                        "answer gate rewrite added an ungrounded claim branch=%d thread=%d: %s",
                        self.branch_id, thread_id, "; ".join(rewrite_issues))
                    return _escalate(answered, MONEY_ESCALATION_REASON)
                return answered  # one rewrite only; a second rewrite is what v2 did

        if stored is not None and premature_pitch(
            decision.move, stored, lead_typed_a_question, decision.reply,
            inbound_count=inbound_count,
            lead_ready_signal=lead_ready_signal,
        ):
            # v2 enforced "no pitch before pain+gain" in code (_stage_for). The v3 rebuild only
            # asked for it in prose, and thread 452 showed that wasn't enough: two turns after a
            # context clear, dossier empty, Stepan pitched Vibe Coding anyway.
            logger.info("pitch gate branch=%d thread=%d: move=%s with no discovery yet",
                       self.branch_id, thread_id, decision.move)
            discovered = await self._regenerate(
                engine, ctx, messages, thread_id, workflow=workflow,
                correction=PITCH_CORRECTION)
            if discovered is None or premature_pitch(
                discovered.move, stored, lead_typed_a_question, discovered.reply,
                inbound_count=inbound_count,
            lead_ready_signal=lead_ready_signal,
            ):
                # thread 5005, thread 5019: the rewrite ignored PITCH_CORRECTION and re-quoted
                # the same price on an empty-dossier turn twice in a row, even on SMART — and
                # `_escalate` below replaces `.reply` with the safe hold-line rather than
                # shipping that second offending draft (it used to ship it with only a flag
                # attached, which protected the CRM record but not the lead).
                logger.error("pitch gate unfixable branch=%d thread=%d — escalating",
                             self.branch_id, thread_id)
                return _escalate(discovered or decision, PITCH_ESCALATION_REASON)
            return discovered

        if capability != SMART:
            return decision  # routine turn — not worth a second call
        # The critic judges raw text and can't tell a tapped ad prefill from a typed question
        # — thread 5095: it rejected a correct warm opener for "not answering" the prefill's
        # jadwal/durasi/biaya wording, and its OWN rewrite added the pitch the pitch gate then
        # had to escalate. When the answer-first machinery already decided nothing was asked
        # (a tap, not typing), don't show the critic the pseudo-question at all.
        critic_inbound = last_inbound
        if not lead_typed_a_question and AD_TEMPLATE_RE.match((last_inbound or "").strip()):
            critic_inbound = ""
        verdict = await critic.review(
            self.llm, reply=decision.reply, context=context, last_inbound=critic_inbound,
            lang=lang, branch_id=self.branch_id, thread_id=thread_id, budget=ctx.budget)
        if verdict.sells:
            return decision
        logger.info("v3 critic branch=%d thread=%d rejected: %s",
                    self.branch_id, thread_id, verdict.why)
        rewritten = await self._regenerate(
            engine, ctx, messages, thread_id, workflow=workflow,
            correction=critic.CRITIC_CORRECTION.format(why=verdict.why, fix=verdict.fix))
        # The critic itself is NOT asked again — a second rejection is what sent v2 to a stub
        # and switched the lead's bot off. But "chase a better sell" is exactly the kind of
        # instruction that can talk the model into volunteering a price (thread 5010: the
        # ORIGINAL draft passed the pitch gate clean — no price yet, empty dossier — the
        # critic rejected it as under-selling, and its OWN rewrite added the price, shipped
        # with no check at all since this path pre-dates the money/pitch gates above it). Those
        # two checks are deterministic, not another LLM call, so re-running them here doesn't
        # risk the stub-loop the comment above is about.
        final = rewritten or decision
        rewrite_issues = money_issues(final.reply, context)
        if rewrite_issues:
            logger.error("v3 critic rewrite added an ungrounded claim branch=%d thread=%d: %s",
                         self.branch_id, thread_id, "; ".join(rewrite_issues))
            return _escalate(final, MONEY_ESCALATION_REASON)
        if stored is not None and premature_pitch(
            final.move, stored, lead_typed_a_question, final.reply,
            inbound_count=inbound_count,
            lead_ready_signal=lead_ready_signal,
        ):
            logger.error("v3 critic rewrite pitched uninvited branch=%d thread=%d",
                         self.branch_id, thread_id)
            return _escalate(final, PITCH_ESCALATION_REASON)
        return final

    async def _skeleton_opener(
        self, engine: DecisionEngine, ctx, fc, thread_id: int,  # noqa: ANN001
    ) -> TurnDecision | None:
        """First reply to a TYPED entry: fixed frame + one bounded LLM slot (see opener.py).

        Returns None whenever the slot can't be trusted — broker error, empty text, a money
        figure nobody asked for, or an ungrounded claim — and the caller falls through to the
        full gated pipeline, so this path can only ever be as risky as the old one."""
        context = await engine.kb_context(ctx, thread_id, light=False)
        prompt = SLOT_SYSTEM.format(kb=context[:12000], typed=fc.typed_text[:500])
        try:
            raw, meta = await self.llm.chat(
                [{"role": "user", "content": prompt}], capability=SMART, max_tokens=220,
                workflow="opener", thread_id=thread_id, branch_id=self.branch_id)
        except Exception as exc:  # noqa: BLE001 — transport-level; full pipeline takes over
            logger.warning("skeleton opener failed branch=%d thread=%d: %s",
                           self.branch_id, thread_id, exc)
            return None
        if ctx.budget is not None:
            await ctx.budget.record(float(meta.get("cost_usd") or 0.0))
        slot = (raw or "").strip().strip('"')
        if len(slot) < 5:
            return None
        if quotes_price(slot) and not PRICE_QUESTION_RE.search(fc.typed_text):
            return None  # volunteered figure — the full pipeline's gates own that case
        if money_issues(slot, context):
            return None  # ungrounded figure/link — never shippable from any path
        reply = compose_typed_opener(
            fc.entry, slot,
            clean_first_name(ctx.lead.display_name if ctx.lead is not None else None))
        move = "answer_question" if fc.entry is Entry.AD_TYPED else "discover_situation"
        return TurnDecision(reply=reply, move=move, stage=Stage.QUALIFYING)

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



def _typed_a_question(last_in: object) -> bool:
    """The lead asked something IN THEIR OWN WORDS.

    An ad's prefilled button text reads like a question ("Boleh info jadwal, durasi, dan
    biaya?") but the lead never typed it — they tapped an ad. Answering a tap with a price list
    is what the old opener did; the right move there is a warm question.

    IG's `is_ad_referral` metadata marks the message the ad's click-through landed on, but that
    composer text is EDITABLE — a lead can clear the prefill and type a real, specific question
    before sending (thread 4972: "saya ingin tahu detail program SMM dan biaya kursusnya" —
    is_ad_referral=True, but a genuine ask). So the flag alone can't settle it; only the TEXT
    can. AD_TEMPLATE_RE is the actual determinant for both ad-tap and pre-flag messages."""
    if last_in is None:
        return False
    text = (getattr(last_in, "text", "") or "").strip()
    if not text or AD_TEMPLATE_RE.match(text):
        return False
    return is_answerable_question(text)


def _awaiting_media(dialog: list) -> bool:
    """The newest inbound is a voice/image the broker hasn't transcribed yet — hold the turn so
    the reply answers the CONTENT, not the placeholder."""
    newest = dialog[-1] if dialog else None
    return (newest is not None and newest.direction == "in"
            and (newest.text or "").strip() in (VOICE_PENDING_PH, IMAGE_PENDING_PH))
