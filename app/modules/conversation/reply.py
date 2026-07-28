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
import re
from dataclasses import replace

from app.adapters.channels.ig_parse import IMAGE_PENDING_PH, VOICE_PENDING_PH
from app.adapters.db.models import Lead

from .decision import Decision, TurnDecision, generate
from .delivery import ReplyDelivery, _script_lang
from .discovery import extract_discovery
from .dossier import merge_dossier
from .engine import _ASSISTANT_LAST_NUDGE, TEMPLATED_META, DecisionEngine
from .free_mode import ad_tap_note, build_messages_free
from .guard import quotes_price
from .money_gate import (
    AD_TAP_PRICE_CORRECTION,
    MONEY_CORRECTION,
    MONEY_ESCALATION_REASON,
    money_issues,
)
from .opener import JUNK_OPENER, Entry
from .opener import classify as classify_entry
from .prompt import (
    ORGANIC_ENTRY_HINT,
    ad_typed_entry_hint,
    lead_name_hint,
    source_hint,
)
from .repository import DossierRepo
from .routing import SALES, SMART
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

# Threads opened before 2026-07-25 carry a retired ad-tap template as their only outbound;
# the turn after it is still the first REAL generation and belongs on the strong chain.
_LEGACY_TAP_PREFIX = "Halo, aku MinStep dari IT STEP Academy"


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
        if _goodbye_loop(ctx.dialog, ctx.lead.display_name if ctx.lead is not None else None):
            logger.info("reply branch=%d thread=%d: both sides have said goodbye — staying quiet",
                        self.branch_id, thread_id)
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
        # The tapped ad's product — needed by the opener note on the first turn and by the
        # entry hint's anchor on every turn after it, so one lookup serves both.
        ad_product = await self._product_title(ctx.thread.product_slug)
        first_note: str | None = None
        if is_first_reply and script_lang is None:
            # Only JUNK still ships a template: an emoji or a garbled string carries nothing
            # to react to, so a fixed clarifying line is as good as a written one and costs
            # nothing. Every other first contact — including a SILENT ad tap — is written by
            # the model. The tap used to get a template that opened with the DP figure; over
            # 30 days it was answered 14.3% of the time against 36.3% for a written reply,
            # and quoting money before the lead says a word contradicts the contract itself.
            # Gated on the lead writing in the branch's own script: the template is
            # Bahasa-only, so a Cyrillic first contact goes straight to the model.
            fc = classify_entry(ctx.dialog, ctx.thread.lead_source, ctx.thread.ad_id)
            if fc.entry is Entry.JUNK:
                decision = TurnDecision(reply=JUNK_OPENER)
                self.last_decision = decision
                self._last_llm_meta = TEMPLATED_META
                logger.info("reply branch=%d thread=%d tier=templated first=True",
                            self.branch_id, thread_id)
                return decision.to_legacy(stored)
            if fc.entry is Entry.AD_SILENT or (fc.entry is Entry.STORY and not fc.typed_text):
                # Nothing of the lead's own to answer — the note tells the model that, names
                # the tapped product, and holds back the price until they ask.
                first_note = ad_tap_note(ad_product)
        if ctx.over_budget:
            # prepare() was told to let the zero-cost template branch through; everything
            # from here on calls the broker, so the original budget gate applies now.
            logger.warning("branch=%d over daily LLM budget — %s skipped",
                           self.branch_id, workflow)
            return None

        # Every live reply rides the sales chain. The dossier-based tiering that used to sit
        # here demoted engaged leads to the cheapest model whenever the dossier was thin —
        # which was almost always. See routing.py for the measurement and thread 4681.
        capability = SALES
        context = await engine.free_kb_context()
        messages = build_messages_free(
            context, ctx.dialog, lang, stored,
            coaching_notes=await self.coaching.active_manager_notes(),
            # On a silent tap the first-turn note already explains the prefill, names the
            # product and says what to do about it. The entry hint says the same three things
            # in its own words — ~560 characters of duplication in the one message that the
            # measurement says must be SHORT. It resumes from turn two, where the note is gone.
            source_block=None if first_note else _entry_hint(ctx, ad_product),
            name_block=lead_name_hint(lead.display_name if lead is not None else None),
            manager_note=lead.manager_note if lead is not None else None,
            now_block=await engine._now_block(ctx.thread),  # noqa: SLF001 — engine owns the clock
            is_first_reply=is_first_reply,
            first_turn_note=first_note,
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
        if first_note is not None and quotes_price(decision.reply):
            decision = await self._strip_first_turn_price(
                engine, ctx, messages, thread_id, decision, workflow=workflow)
        merged = merge_dossier(stored, decision.dossier)
        # Reading the lead is its own call now, and it runs every turn — not only when the
        # dossier looks empty. The selling model used to own this and filled it for ~5% of
        # leads; a call with no reply to write has no competing task (see discovery.py).
        extra = await extract_discovery(
            self.llm, ctx.dialog, merged, lang, self.branch_id, thread_id, budget=ctx.budget,
            product_slugs=await self._product_slugs())
        merged = merge_dossier(merged, extra)
        if lead is not None and not stored.has_intent() and merged.has_intent():
            self._report_qualified(lead)
        decision = await self._vet(
            engine, ctx, messages, thread_id, decision, workflow=workflow, context=context)
        await self.dossiers.save(lead.id if lead is not None else None, merged)
        self.last_decision = decision
        logger.info("reply branch=%d thread=%d tier=%s first=%s",
                    self.branch_id, thread_id, capability, is_first_reply)
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
            decision, meta = await generate(
                engine, ctx, messages, thread_id, workflow=workflow,
                capability=SMART, branch_id=self.branch_id)
        else:
            if decision is None and capability == SALES:
                logger.warning(
                    "reply: unparseable chat:sales decision branch=%d thread=%d — retry on smart",
                    self.branch_id, thread_id)
                decision, meta = await generate(
                    engine, ctx, messages, thread_id, workflow=workflow,
                    capability=SMART, branch_id=self.branch_id)
        # enqueue_reply stamps this on every bubble — the only place the broker line reaches
        # the chat. Assigning it at the single exit is what keeps the fallback chains honest:
        # the chip must name the model that actually wrote the text the lead sees.
        self._last_llm_meta = meta
        return decision, meta

    async def _strip_first_turn_price(  # noqa: PLR0913
        self, engine: DecisionEngine, ctx, messages: list[dict], thread_id: int,  # noqa: ANN001
        decision: TurnDecision, *, workflow: str,
    ) -> TurnDecision:
        """One rewrite when the opening message to a silent ad tap quotes a figure.

        Measured over 819 pure-prefill threads: a first message with a number in it is
        answered 16.1% of the time against 36.3% without one (see AD_TAP_FIRST_TURN_NOTE for
        the day-by-day collapse). The note forbids it, but Meta's prefill mentions cost and the
        model keeps reading a button press as a question — so it is enforced here.

        Fails OPEN: if the rewrite is unusable we ship the original. A priced opener is a weak
        opener, not a false one, and silence is worse than either."""
        logger.info("ad-tap opener quoted a price branch=%d thread=%d — rewriting",
                    self.branch_id, thread_id)
        try:
            fixed, meta = await generate(
                engine, ctx,
                [*messages, {"role": "user", "content": AD_TAP_PRICE_CORRECTION}],
                thread_id, workflow=workflow, capability=SALES, branch_id=self.branch_id)
        except Exception as exc:  # noqa: BLE001 — an opener still beats no opener
            logger.warning("ad-tap price rewrite failed branch=%d thread=%d: %s",
                           self.branch_id, thread_id, exc)
            return decision
        if fixed is None or not fixed.reply.strip() or quotes_price(fixed.reply):
            return decision
        self._last_llm_meta = meta
        return fixed

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
            fixed, meta = await generate(
                engine, ctx,
                [*messages, {"role": "user",
                             "content": MONEY_CORRECTION.format(issues="; ".join(issues))}],
                thread_id, workflow=workflow, capability=SALES, branch_id=self.branch_id)
            self._last_llm_meta = meta  # the rewrite is what ships — its cost is the turn's
        except Exception as exc:  # noqa: BLE001 — a failed rewrite means the hold-line ships
            logger.warning("money rewrite failed branch=%d thread=%d: %s",
                           self.branch_id, thread_id, exc)
            fixed = None
        if fixed is None or money_issues(fixed.reply, context):
            logger.error("money gate unfixable branch=%d thread=%d — escalating",
                         self.branch_id, thread_id)
            return _escalate(fixed or decision, MONEY_ESCALATION_REASON)
        return fixed

    def _report_qualified(self, lead: Lead) -> None:
        """Fire QualifiedLead the first turn this lead becomes one — a button-press has turned
        into a conversation with an intent behind it.

        This is the only one of our three Meta events with the volume to steer a campaign:
        ~200 a week against ~14 hand-offs, where Meta wants ~50 a week per ad set to leave the
        learning phase. Idempotent by event_id, and guarded by the has_intent() transition so
        one lead reports once however many turns follow.

        Never allowed to affect the reply — and since 2026-07-27 not allowed to delay it
        either: queued for the post-commit drain rather than awaited here, because the outbox
        sender cannot see the queued reply until this transaction commits."""
        self.queue_capi(
            f"qualified-{self.branch_id}-{lead.id}", lead.phone_e164, "QualifiedLead")

    async def _product_slugs(self) -> list[str]:
        """The branch's active course slugs — the closed set the extractor may pick from, so a
        hallucinated slug can never reach the thread."""
        from sqlalchemy import select  # noqa: PLC0415

        from app.adapters.db.models import Product  # noqa: PLC0415
        rows = (await self.session.execute(
            select(Product.slug).where(
                Product.branch_id == self.branch_id,
                Product.is_active == True))).all()  # noqa: E712 — SQLAlchemy needs the comparison
        return [r[0] for r in rows if r[0]]

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


def _entry_hint(ctx, product_title: str | None = None) -> str | None:  # noqa: ANN001
    """The one entry-context hint this thread earns, with the tapped ad's product named.

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
        return ad_typed_entry_hint(product_title)
    hint = source_hint(src, product_title)
    if hint is None and not src and not ctx.thread.ad_id:
        return ORGANIC_ENTRY_HINT
    return hint


# A closing with nothing in it: thanks, ok, see you, a lone emoji. Built as an allow-list
# rather than a pattern of whole messages, because real farewells carry filler — "Oke sip, udah
# clear. Makasih Min, saya tunggu besok pagi ya" is a goodbye, and matching only bare words
# missed every live example. A message counts as empty when NOTHING is left after removing
# pleasantries, forms of address and filler; anything substantive that survives ("harganya",
# "daftarin", a phone number) keeps the turn alive.
_CLOSING_WORDS = frozenset("""
    oke ok oke oke okey okay okeh sip siap baik iya ya yaa yaudah udah sudah clear jelas
    makasih makasi thanks thank you terima kasih sama samasama dadah bye semangat noted
    mantap aamiin amin sampai jumpa ketemu besok pagi nanti tunggu ditunggu menunggu saya aku
    gue min mimin kak kakak ya deh dong nya lah kok aja juga banyak sekali
    selamat malam siang sore dan hari
""".split())
_WORD_RE = re.compile(r"[a-zA-ZÀ-ɏ]+")


def _is_closing_only(text: str, extra: frozenset[str] = frozenset()) -> bool:
    """True when the message carries no content beyond pleasantries — including one that is
    only emoji or punctuation, which is how the loop ends up looking after a few rounds.

    `extra` carries the lead's own name. Without it the check fails on exactly the farewells we
    write: "Sampai besok, Kak Rani" is a goodbye, but "Rani" is not a pleasantry, so the guard
    saw content and answered again — which is how a sim conversation still reached seventeen
    turns after the guard was in place."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    words = [w.lower() for w in _WORD_RE.findall(stripped)]
    if not words:                       # a lone 🙏 or "..." — nothing said at all
        return True
    return all(w in _CLOSING_WORDS or w in extra for w in words)


def _repeats(a: str, b: str, threshold: float = 0.55) -> bool:
    """Two messages saying substantially the same thing.

    Overlap is measured against the SHORTER message, not against the union: farewells vary in
    padding more than in content, and Jaccard punishes that padding twice. "Udah ah Kak,
    makasih. Nanti gue WA aja kalo perlu" and "Iya Kak, makasih. Nanti gue tunggu WA nya ya"
    share six words of ten — obviously the same message — but score only 0.40 by union.

    The three-word floor keeps "oke" from matching "oke"; two bare acknowledgements are not
    evidence of anything, and the pleasantry check below is what handles those.

    Measuring against the shorter message has one failure mode, and it silenced a live thread
    on 28.07.2026: a SHORT new message whose words happen to sit inside a LONG previous one
    scores near 1.0 while saying something entirely new. Thread 5540 asked "belajar kursus it
    gitu ka" (5 words) right after "Ka btw memang bisa sambil online? Soalnya saya di jogja
    sambil belajar kursus skill IT juga" (15) — four words of five, 0.80, and the bot went
    quiet for five hours on a lead who was still asking questions.

    Two messages that say the same thing are also of comparable size. Requiring that first
    costs nothing on real repetition (the farewell pair above is 10 words against 11) and
    removes the whole containment artefact."""
    wa = {w.lower() for w in _WORD_RE.findall(a or "")}
    wb = {w.lower() for w in _WORD_RE.findall(b or "")}
    if len(wa) < 3 or len(wb) < 3:
        return False
    if min(len(wa), len(wb)) / max(len(wa), len(wb)) < 0.5:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= threshold


def _both_looping(dialog: list) -> bool:
    """Neither side has said anything new for two exchanges.

    CORROBORATOR, never a trigger — `_goodbye_loop` calls this only after establishing that the
    lead's last message is pleasantries and nothing else. It was a standalone condition for one
    day and silenced a live thread; shape is good evidence about OUR side idling and bad
    evidence about whether the lead is done.

    The vocabulary approach below catches a farewell made of pleasantries, and it kept missing
    by one word — "Udah ah Kak, makasih. Nanti gue WA aja kalo perlu. Bye!" is unmistakably a
    goodbye, but `ah`, `kalo` and `perlu` are not on any list, and no list will ever be
    finished. Three rounds of widening it is the definition of a patch.

    So this looks at shape instead of words: when the lead's last two messages are near
    duplicates of each other AND ours are too, the conversation is idling regardless of what
    language it idles in. A lead who introduces anything — a question, a number, a new
    objection — breaks the similarity immediately, which is exactly the property a word list
    cannot have."""
    ins = [m.text or "" for m in dialog if m.direction == "in"]
    outs = [m.text or "" for m in dialog if m.direction != "in"]
    if len(ins) < 2 or len(outs) < 2:
        return False
    return _repeats(ins[-1], ins[-2]) and _repeats(outs[-1], outs[-2])


def _goodbye_loop(dialog: list, lead_name: str | None = None) -> bool:
    """The conversation is over and both sides know it — the lead's last message carries no
    content and our previous message was already a farewell.

    The contract asks the model to let a finished conversation finish, and the model does not:
    each turn looks new to it, so it answers "one last short time" again and again. Two sim
    runs ended this way — thirteen turns of "sampai jumpa" in one, seventeen in another, the
    bot eventually reduced to sending a lone 🙏 over and over. In production every one of those
    is a real message against the account's send budget, to someone who said goodbye long ago.

    Silence is the correct answer here, and it is the one thing a prompt cannot reliably
    produce. Requires BOTH sides to be closing: a bare "oke" after a real answer of ours is a
    lead still listening, and that turn is not dropped."""
    ins = [m for m in dialog if m.direction == "in"]
    outs = [m for m in dialog if m.direction != "in"]
    if not ins or not outs:
        return False
    extra = frozenset(w.lower() for w in _WORD_RE.findall(lead_name or ""))
    # The veto, and it comes first: if the lead said anything at all, we answer. Shape-based
    # repetition used to short-circuit this check and decide on its own — that is what silenced
    # thread 5540 for five hours while the lead kept asking questions. The two errors are not
    # symmetric. Answering one turn too many after a goodbye costs one message; going quiet on
    # a live lead is unrecoverable and raises no alert, so nothing may outrank this test.
    if not _is_closing_only(ins[-1].text, extra):
        return False
    return _is_closing_only(outs[-1].text, extra) or _both_looping(dialog)


def _awaiting_media(dialog: list) -> bool:
    """The newest inbound is a voice/image the broker hasn't transcribed yet — hold the turn so
    the reply answers the CONTENT, not the placeholder."""
    newest = dialog[-1] if dialog else None
    return (newest is not None and newest.direction == "in"
            and (newest.text or "").strip() in (VOICE_PENDING_PH, IMAGE_PENDING_PH))
