"""Shadow AI classifier for the lead's current turn — a cheap-model alternative to the six
"meaning" regexes in situations.py (SOFT_NO_RE, POSTPONE_RE, PAID_SHOCK_RE, TRUST_DOUBT_RE,
LOW_BUDGET_RE, NO_TIME_RE, BUYING_SIGNAL_RE). Those detect an infinite variety of phrasings by
pattern-matching phrasings already seen in a past incident — an AI classifier generalizes to
new wording natively. This module ONLY logs disagreement between the regex cascade's pick and
the classifier's pick; it never changes what gets sent (see reply.py call site). Once shadow
data shows near-zero meaningful disagreement, the classifier can replace the regex cascade for
these six categories — see project review 2026-07-22."""
from __future__ import annotations

import json
import logging

from app.ports.llm import LLMPort

logger = logging.getLogger(__name__)

TURN_TYPES = (
    "soft_no", "postpone", "paid_shock", "trust_doubt", "low_budget", "no_time",
    "buying_signal", "none",
)

_SYSTEM = (
    "Classify the lead's LATEST message in an Indonesian IG-DM sales chat into EXACTLY ONE "
    "category, judging MEANING not exact wording (Indonesian is often indirect/face-saving):\n"
    "- soft_no: a polite decline/hesitation/objection with no specific reason named "
    "('nanti dulu', 'mikir dulu', 'belum tertarik', 'ga usah', a flat 'no' in any wording)\n"
    "- postpone: a BARE decision-postpone with NO reason given (just 'later'/'thinking about "
    "it', not tied to money/time/trust)\n"
    "- paid_shock: surprise/pushback specifically that the course COSTS money ('wait, it's "
    "paid?!')\n"
    "- trust_doubt: doubts this is legitimate/real/a scam\n"
    "- low_budget: no money / can't afford / broke / unemployed\n"
    "- no_time: too busy / no time / schedule conflict\n"
    "- buying_signal: wants to enroll/pay/proceed now\n"
    "- none: none of the above (a plain question, smalltalk, already-answered menu reply, "
    "an ad click, etc.)\n"
    'Return ONLY this JSON: {"turn_type": one of the categories above}'
)


async def classify_turn(
    llm: LLMPort, *, last_txt: str, branch_id: int, thread_id: int,
    bill: bool = False, budget: object = None,
) -> str | None:
    """One cheap classification call. Returns a TURN_TYPES value, or None on any failure —
    shadow-only, so a failure here must never affect the real reply path."""
    if not (last_txt or "").strip():
        return "none"
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"LEAD'S LATEST MESSAGE:\n{last_txt.strip()[:500]}"},
    ]
    try:
        raw, meta = await llm.chat(
            messages, capability="chat:fast", require_json_schema=True,
            workflow="turn_classifier", thread_id=thread_id, branch_id=branch_id)
        if not bill:
            meta.pop("cost_usd", None)
        elif budget is not None:
            await budget.record(float(meta.get("cost_usd") or 0.0))
        data = json.loads(raw)
        turn_type = str(data.get("turn_type") or "").strip().lower()
        return turn_type if turn_type in TURN_TYPES else "none"
    except Exception as exc:  # noqa: BLE001 — shadow-only, never break the real reply
        logger.warning("turn classifier failed branch=%d thread=%d: %s", branch_id, thread_id, exc)
        return None


# Maps each nudge constant's identity to the category it represents, for comparing the regex
# cascade's pick against the classifier's — built lazily (import-time circular-import safe).
def _nudge_category_map() -> dict[int, str]:
    from . import situations as s  # noqa: PLC0415
    return {
        id(s.OBJECTION_HANDLE_NUDGE): "soft_no",
        id(s.SOFT_NO_NUDGE): "soft_no",
        id(s.SOFT_NO_WITH_QUESTION_NUDGE): "soft_no",
        id(s.POSTPONE_NUDGE): "postpone",
        id(s.PAID_SHOCK_NUDGE): "paid_shock",
        id(s.TRUST_DOUBT_NUDGE): "trust_doubt",
        id(s.LOW_BUDGET_NUDGE): "low_budget",
        id(s.ANSWER_FIRST_TIGHT_BUDGET_NUDGE): "low_budget",
        id(s.NO_TIME_NUDGE): "no_time",
        id(s.BUYING_SIGNAL_NUDGE): "buying_signal",
    }


def regex_category_for(nudge: str | None) -> str:
    """The shadow-comparable category for whatever situations.pick_nudge selected — "none" for
    every nudge outside the six meaning-categories (ad-opener, discovery, etc.), since the
    classifier is only asked to judge those six; anything else is not a meaningful disagreement."""
    if nudge is None:
        return "none"
    return _nudge_category_map().get(id(nudge), "none")
