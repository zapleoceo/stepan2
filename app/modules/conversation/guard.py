"""Reply guard — the verification layer that stops the bot from stating things the KB
doesn't support (fabricated links, free-lab access, invented rates/certs/dates).

Two tiers, cheapest first:
  1. deterministic: any URL not present verbatim in the KB context is a fabrication
     (this alone would have blocked the fake `lab.itstep.id/...?access=...` in chat 1736).
  2. selective LLM verify: only when the reply looks risky (a link, an offer, a resource
     hand-out), a cheap model lists claims unsupported by the KB context.

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
    r"sertifikat cisco|cyberops|template|tutorial|download|kirim(?:kan)? (?:link|file|akses))\b",
    re.IGNORECASE)

# Bahasa hand-off when a clean reply can't be produced — never invents, defers to a human.
SAFE_FALLBACK = (
    "Untuk yang satu ini aku mau pastikan dulu ke tim biar infonya akurat ya Kak 🙏 "
    "Nanti aku kabari secepatnya. Sementara itu, ada hal lain yang bisa aku bantu?")

_VERIFY_SYSTEM = (
    "You check a sales bot's draft reply for fabrication. You get the KNOWLEDGE BASE the "
    "bot may use, then the DRAFT. List every CONCRETE factual claim in the draft that is "
    "NOT supported by the knowledge base: invented links, free/discount/trial offers, lab "
    "or resource access, prices, dates, certifications, guarantees, statistics. Ignore "
    "generic rapport, questions, and paraphrases of KB facts. Reply JSON only: "
    '{"unsupported": ["<short quote or description>", ...]}. Empty list if all grounded.')


def _grounded_url(url: str, context: str) -> bool:
    u = url.rstrip(".,);’'\"")
    return bool(_BARE_OK.match(u)) or u.lower() in context.lower()


def ungrounded_urls(reply: str, context: str) -> list[str]:
    """URLs in the reply not backed by the KB context — the highest-confidence fabrication."""
    return [u for u in _URL_RE.findall(reply or "") if not _grounded_url(u, context)]


def is_risky(reply: str) -> bool:
    """Cheap gate: does the reply look like it might hand out an offer/resource/link?"""
    return bool(_URL_RE.search(reply or "") or _RISKY_RE.search(reply or ""))


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
        raw, meta = await llm.chat(
            messages, capability="chat:fast", require_json_schema=True,
            workflow="guard", thread_id=thread_id, branch_id=branch_id)
        if not bill:
            meta.pop("cost_usd", None)  # sandbox verify shouldn't distort cost meta
        data = json.loads(
            raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        items = data.get("unsupported") or []
        return [str(x) for x in items][:8]
    except Exception as exc:  # noqa: BLE001 — a failed verify must not block the reply
        logger.warning("guard verify failed branch=%d thread=%d: %s", branch_id, thread_id, exc)
        return []


CORRECTION = (
    "[System: your previous draft contained claims NOT in the knowledge base: {issues}. "
    "Rewrite the reply WITHOUT any of them. Never invent links, lab/resource access, free "
    "trials, discounts, rates, certifications, dates, or statistics. If you don't have a "
    "fact, say you'll confirm it with the team. Return the JSON as usual.]")
