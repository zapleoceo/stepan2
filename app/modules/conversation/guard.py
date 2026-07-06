"""Reply guard — the verification layer that stops the bot from stating things the KB
doesn't support (fabricated links, free-lab access, invented rates/certs/dates, invented
alumni/success stories).

Two tiers, cheapest first:
  1. deterministic: any URL not present verbatim in the KB context is a fabrication
     (this alone would have blocked the fake `lab.itstep.id/...?access=...` in chat 1736);
     a claim of an already-sent file/screenshot/WA delivery is always false too.
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
    "[System: your previous draft contained claims NOT in the knowledge base: {issues}. "
    "Rewrite the reply WITHOUT any of them. Never invent links, lab/resource access, free "
    "trials, discounts, rates, certifications, dates, or statistics. Never claim you have "
    "ALREADY sent a file/screenshot/dataset or delivered anything via WhatsApp — you cannot "
    "attach files and have no WhatsApp channel. Never tell a specific alumni/success story "
    "that isn't one of the exact cases in the product's Success Cases section — use one of "
    "those verbatim, a generalized true statement, or skip the story. If you don't have a "
    "fact, say you'll confirm it with the team. Return the JSON as usual.]")
