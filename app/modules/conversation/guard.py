"""Reply guard — the verification layer that stops the bot from stating things the KB
doesn't support, AND from a handful of live conversation-quality failures that don't need
KB context to detect (multiple questions in one turn, offering a capability Stepan doesn't
have, contradicting its own channel).

Two tiers, cheapest first:
  1. deterministic (always on): any URL not present verbatim in the KB context is a
     fabrication (this alone would have blocked the fake `lab.itstep.id/...?access=...` in
     chat 1736); a claim of an already-sent file/screenshot/WA delivery is always false;
     more than one '?' in a turn means the lead got two questions and answered one (thread
     1729/1793); offering a voice note/call (thread 1330) or telling an Instagram lead to
     "go DM on Instagram" (thread 2092) are structurally impossible regardless of KB.
  2. selective LLM verify: only when the reply looks risky (a link, an offer, a resource
     hand-out, a price figure, or a specific alumni/success story), a cheap model lists
     claims unsupported by the KB context — including a story that isn't one of the
     product's curated Success Cases (chat 1827: "salah satu alumni kami..." with nothing
     to back it up if the lead asks to see it).

On an unfixable violation the caller regenerates once, then falls back to a safe
"let me confirm with the team" hand-off — never sends the fabrication.
"""
from __future__ import annotations

import json
import logging
import re

from app.ports.llm import LLMPort

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
# Bare official site / no-path is allowed even if not quoted in context; anything with a
# path or query (a specific resource) must be grounded in the KB text.
_BARE_OK = re.compile(r"^https?://(www\.)?itstep\.id/?$", re.IGNORECASE)

# Reply shapes worth an LLM grounding check — offers, resources, hand-outs, access.
_RISKY_RE = re.compile(
    r"\b(gratis|free|akses|access|link|lab|trial|reserve|reservein|slot|voucher|"
    r"kupon|promo|diskon|discount|beasiswa|scholarship|garansi|jaminan|refund|"
    r"sertifikat cisco|cyberops|template|tutorial|download|kirim(?:kan)? (?:link|file|akses)|"
    r"harga|biaya|tarif|cicilan|angsuran)\b",
    re.IGNORECASE)
# A concrete money figure (e.g. "Rp 297.000", "1.670.000/bulan", "500 ribu") — the exact
# shape of the chat-452 fabrication, which carried no "diskon/promo/gratis" trigger word.
_PRICE_RE = re.compile(
    r"\brp\.?\s?\d[\d.,]*|\d[\d.,]*\s?(?:ribu|juta|rb\b)", re.IGNORECASE)

# A claim that a file/screenshot/dataset has ALREADY been sent, or delivered specifically
# via WhatsApp — deterministically false regardless of KB content: Stepan is text-only (no
# image/file attach capability) and Instagram-only (no WhatsApp channel). A 50-thread live
# audit (2026-07-05) found leads left believing a screenshot/dataset had arrived when
# nothing was ever sent (threads 1408, 1721).
_DELIVERY_NOUN = r"(?:screenshot|foto|gambar|file|dokumen|dataset|dm|wa|whatsapp)"
_FALSE_DELIVERY_RE = re.compile(
    rf"\b{_DELIVERY_NOUN}\w*\b[^.!?\n]{{0,15}}\b(?:udah|sudah)\b[^.!?\n]{{0,20}}\bkirim(?:kan)?\b"
    rf"|\b(?:udah|sudah)\b[^.!?\n]{{0,40}}\bkirim(?:kan)?\b[^.!?\n]{{0,40}}\b{_DELIVERY_NOUN}\b",
    re.IGNORECASE)

# Alumni/success-story narrative — a specific-sounding "one of our alumni did X" claim.
# Policy (2026-07-06): illustrative stories are fine, but ONLY when they're the exact cases
# already curated in a product's "Success cases" section (real named public figures + links,
# or the Director's own real projects) — never improvised on the fly with no case behind it.
# Chat 1827 is the live example: "salah satu alumni kami yang berhasil..." with zero name,
# link, or specific detail — if the lead asks to see it, there is nothing to show. This
# doesn't block generalized TRUE archetype language ("banyak peserta kami mulai dari nol,
# ada yang jadi developer...") — the LLM verify step judges that distinction using the
# actual Success Cases / Stories content in context.
_STORY_RE = re.compile(
    r"\b(alumni kami|lulusan kami|peserta kami|salah satu (peserta|siswa|alumni|mentor|"
    r"murid))\b", re.IGNORECASE)


def false_delivery_claims(reply: str) -> list[str]:
    """Claims of an already-sent file/screenshot/WA delivery — always fabricated (Stepan
    cannot attach files and has no WhatsApp channel), so this needs no KB context at all."""
    return [m.group(0) for m in _FALSE_DELIVERY_RE.finditer(reply or "")]


# Quoted example scripts in the reply (rare, but the KB itself has «...»-quoted sample
# lines) can carry a "?" that isn't a real question TO the lead — strip before counting.
_QUOTE_STRIP_RE = re.compile(r"«[^»]*»")


def multiple_questions(reply: str) -> list[str]:
    """More than one '?' in a single turn (counted across every '|||' bubble, since the
    lead experiences a multi-bubble reply as ONE turn) — a hard live pattern (thread 1729:
    "pernah ngerasa gak dapet engagement? Atau bingung bikin konten...?" two distinct
    questions joined by 'atau' in one message; thread 1793: two separate questions split
    across two bubbles of the same turn) that leaves one of the two unanswered. The KB's
    own MESSAGE FORMULA already says "ONE engaging question" — this is the deterministic
    backstop for that rule, the same pattern as every other guard check here."""
    text = _QUOTE_STRIP_RE.sub("", reply or "")
    count = text.count("?")
    if count >= 2:
        return [f"{count} question marks in a single turn — ask exactly ONE question"]
    return []


def truncate_to_one_question(reply: str) -> str:
    """Deterministic last resort for a draft still asking 2+ questions after a regen: keep
    everything through the FIRST real question mark, drop the rest. A double question is a
    style slip, not a fabrication risk — trimming it is safe and always available, unlike
    the SAFE_FALLBACK hand-off, which wastes a manager's attention on a lead who asked a
    perfectly answerable question (live case: threads 2159/2160, "ceritakan lebih detail
    tentang kursusnya" got a full hand-off because the regen also happened to double up)."""
    # Same length as the original so the found index lines up with the un-stripped string —
    # a quoted KB "?" must not count, but blanking it (not deleting it) keeps positions valid.
    masked = _QUOTE_STRIP_RE.sub(lambda m: "�" * len(m.group(0)), reply or "")
    idx = masked.find("?")
    if idx == -1:
        return reply
    return reply[: idx + 1].rstrip()


# Stepan is a TEXT-ONLY Instagram DM bot — no voice notes, no calls, no video. Offering one
# is a capability that doesn't exist, whether phrased as a future offer (thread 1330: "aku
# bisa jelasin lewat voice note") or (already covered by false_delivery_claims) as done.
_IMPOSSIBLE_CAPABILITY_RE = re.compile(
    r"\b(voice note|rekaman suara|video call|telpon (?:langsung|kamu|kakak)|"
    r"nelpon (?:langsung|kamu|kakak)|jelasin(?:in)? (?:lewat|via) (?:telepon|telpon|call))\b",
    re.IGNORECASE)


def impossible_capability_offers(reply: str) -> list[str]:
    """Offers of a capability Stepan structurally doesn't have (voice/video/calls) — always
    false regardless of KB content, same tier as false_delivery_claims."""
    return [m.group(0) for m in _IMPOSSIBLE_CAPABILITY_RE.finditer(reply or "")]


# Chat 2092: the bot told an Instagram lead to "langsung aja DM aku di Instagram" — but this
# conversation IS the Instagram DM. Stepan has exactly one channel; redirecting a lead who
# is already there to "go DM on Instagram" is always a self-contradiction, never a real
# instruction — no KB context needed to know that.
_WRONG_CHANNEL_RE = re.compile(
    r"\bdm\b[^.!?\n]{0,40}\binstagram\b|\binstagram\b[^.!?\n]{0,40}\bdm\b"
    r"|\bchat(?:kan)?\b[^.!?\n]{0,20}\bdi instagram\b",
    re.IGNORECASE)


def wrong_channel_claims(reply: str) -> list[str]:
    """Telling an Instagram-DM lead to go message on Instagram — always wrong, this IS
    Instagram."""
    return [m.group(0) for m in _WRONG_CHANNEL_RE.finditer(reply or "")]


# A price/availability question ("ini gratis ga kak?", "berapa?") escalated to
# needs_manager when the retrieved KB context ALREADY has a price figure for the product
# being discussed is not a real KB gap — the contract's own rule says either answer it or
# defer with a discovery question, never hand off (thread 2285: lead asked "ini gratis ga
# kak?" right after the bot itself named "Skill Booster"; the Cybersecurity Skill Booster
# price - Rp 700.000/600.000 - was right there in context, and the bot silently muted
# itself instead of using it).
_PRICE_QUESTION_RE = re.compile(
    r"\b(gratis|free|berapa|harga|biaya|tarif|cicilan|angsuran|murah|mahal)\b",
    re.IGNORECASE)


def premature_manager_handoff(last_inbound: str, context: str) -> bool:
    """True when the lead's last message is a price/availability question AND the KB
    context already contains a price figure — a needs_manager decision on THIS turn would
    be escalating something the model already had the answer for."""
    if not _PRICE_QUESTION_RE.search(last_inbound or ""):
        return False
    return bool(_PRICE_RE.search(context or ""))


MANAGER_HANDOFF_CORRECTION = (
    "[System: you set needs_manager=true for a price/availability question, but a price "
    "figure for this product is already in the knowledge base context above - this is NOT a "
    "real KB gap, do not hand it off to a human. Either answer the price directly, or if "
    "discovery genuinely isn't done yet, acknowledge and defer with ONE discovery question "
    "per your own early-price rule - never silently escalate a fact you already have. Set "
    "needs_manager=false. Return the JSON as usual.]"
)


# Bahasa hand-off when a clean reply can't be produced — never invents, defers to a human.
SAFE_FALLBACK = (
    "Untuk yang satu ini aku mau pastikan dulu ke tim biar infonya akurat ya Kak 🙏 "
    "Nanti aku kabari secepatnya. Sementara itu, ada hal lain yang bisa aku bantu?")

_VERIFY_SYSTEM = (
    "You check a sales bot's draft reply for fabrication. You get the KNOWLEDGE BASE the "
    "bot may use, then the DRAFT. List every CONCRETE factual claim in the draft that is "
    "NOT supported by the knowledge base: invented links, free/discount/trial offers, lab "
    "or resource access, prices, dates, certifications, guarantees, statistics. "
    "ALUMNI/SUCCESS-STORY CLAIMS: a specific-sounding story ('salah satu alumni kami yang...', "
    "a named or implied individual with a concrete outcome) is a fabrication UNLESS that exact "
    "case (name, outcome, or link) appears in the knowledge base's Success Cases / Stories "
    "content. A GENERALIZED true statement ('banyak peserta kami mulai dari nol, ada yang jadi "
    "developer, ada yang freelance') is fine even without a specific case — only flag a "
    "SPECIFIC unsourced story. Ignore generic rapport, questions, and paraphrases of KB facts. "
    "Output ONE unsupported claim per line (a short quote or description), nothing else — no "
    "numbering, no JSON, no prose. If everything is grounded, reply with the single word CLEAN.")

_CLEAN_TOKENS = frozenset({"clean", "none", "ok", "grounded", "[]", "-", "n/a", "kosong"})
# a leading list marker only: "- ", "* ", "• ", "1. ", "2) " — not digits inside the claim
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+")


def _parse_unsupported(raw: str) -> list[str]:
    """Unsupported-claims list from the verifier's reply. Tolerates the new line-based format
    AND a legacy JSON body ({"unsupported": [...]}), so a stale guard_verify prompt in the DB
    keeps working through the transition."""
    s = (raw or "").strip()
    if not s:
        return []
    if s.startswith("{") or s.startswith("```"):  # legacy JSON shape
        body = s.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            items = json.loads(body).get("unsupported") or []
            return [str(x).strip() for x in items if str(x).strip()][:8]
        except (json.JSONDecodeError, AttributeError):
            pass  # not real JSON — fall through to line parsing
    out: list[str] = []
    for line in s.splitlines():
        claim = _LIST_MARKER_RE.sub("", line.strip()).strip()  # drop only a leading bullet/number
        if not claim:
            continue
        if claim.lower() in _CLEAN_TOKENS:  # explicit "all grounded" sentinel
            return []
        out.append(claim)
    return out[:8]


def _grounded_url(url: str, context: str) -> bool:
    u = url.rstrip(".,);’'\"")
    return bool(_BARE_OK.match(u)) or u.lower() in context.lower()


def ungrounded_urls(reply: str, context: str) -> list[str]:
    """URLs in the reply not backed by the KB context — the highest-confidence fabrication."""
    return [u for u in _URL_RE.findall(reply or "") if not _grounded_url(u, context)]


def is_risky(reply: str) -> bool:
    """Cheap gate: does the reply look like it might hand out an offer/resource/link,
    state a concrete price (chat-452 shape), or tell a specific alumni/success story
    (chat-1827 shape) that needs checking against the curated Success Cases content?"""
    text = reply or ""
    return bool(
        _URL_RE.search(text) or _RISKY_RE.search(text) or _PRICE_RE.search(text)
        or _STORY_RE.search(text))


async def verify_grounding(
    llm: LLMPort, reply: str, context: str, *, branch_id: int,
    thread_id: int, bill: bool = True, system: str | None = None,
) -> list[str]:
    """LLM grounding check on a risky reply; returns unsupported claims ([] = clean).
    `system` overrides the checker prompt (from the editable `guard_verify` KB doc)."""
    messages = [
        {"role": "system", "content": system or _VERIFY_SYSTEM},
        {"role": "user", "content": f"KNOWLEDGE BASE:\n{context[:12000]}\n\nDRAFT:\n{reply}"},
    ]
    try:
        # No require_json_schema: the verifier answers in plain lines, so the broker isn't
        # limited to JSON-mode providers (wider/cheaper pool, fewer timeouts). The parser
        # still accepts a legacy JSON body from a stale guard_verify prompt.
        raw, meta = await llm.chat(
            messages, capability="chat:fast",
            workflow="guard", thread_id=thread_id, branch_id=branch_id)
        if not bill:
            meta.pop("cost_usd", None)  # sandbox verify shouldn't distort cost meta
        return _parse_unsupported(raw)
    except Exception as exc:  # noqa: BLE001 — a failed verify must not block the reply
        logger.warning("guard verify failed branch=%d thread=%d: %s", branch_id, thread_id, exc)
        return []


CORRECTION = (
    "[System: your previous draft had these problems: {issues}. "
    "Rewrite the reply fixing ALL of them. Never invent links, lab/resource access, free "
    "trials, discounts, rates, certifications, dates, or statistics. Never claim you have "
    "ALREADY sent a file/screenshot/dataset or delivered anything via WhatsApp — you cannot "
    "attach files and have no WhatsApp channel. Never tell a specific alumni/success story "
    "that isn't one of the exact cases in the product's Success Cases section — use one of "
    "those verbatim, a generalized true statement, or skip the story. Ask EXACTLY ONE "
    "question per turn — never two questions, never 'X atau Y' phrased as a double question. "
    "Never offer a voice note, call, or video — you are a text-only Instagram DM bot. Never "
    "tell the lead to go DM you on Instagram — this conversation already IS Instagram. If "
    "you don't have a fact, say you'll confirm it with the team. Return the JSON as usual.]")
