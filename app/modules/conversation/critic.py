"""The v3 critic — asks whether the reply SELLS, and fails OPEN.

Two inversions from v2.

What it judges. v2's quality layer was 21 checks for things that must not appear; nothing
asked whether the answer moved the sale. So a reply could pass every check and still be the
canned campus boilerplate that, measured live, dropped the response rate from 47.7% to 35.8%.
This critic scores the three things that decide whether a lead writes back.

How it fails. v2's critic returned ok=False on any broker hiccup or malformed JSON, and a
second failure switched the lead's bot off for good. Broker instability converted directly
into lost conversations. Here an error means the draft SHIPS: an unreviewed real answer beats
a stub every time, and the miss is logged instead of being charged to the lead.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.ports.llm import LLMPort

from .decision import strip_fences

logger = logging.getLogger(__name__)

_KB_BUDGET = 8_000

_SYSTEM = """\
You review one draft message from a salesperson at an IT school, written to a lead in \
Instagram Direct. You are not a compliance checker — you are a sales floor manager who wants \
this lead to write back.

Judge only three things:
1. ANSWERS — if the lead asked something, does the FIRST line actually answer it? A greeting, \
a campus description, or a question back instead of the answer is a failure. This matters more \
than the other two combined.
2. MOVES — does it take the conversation somewhere: a real question, a relevant fact, a next \
step? Generic filler ("ada yang bisa dibantu lagi?") is a failure.
3. SOUNDS HUMAN — like a person texting, not a brochure. Wrong length for the lead's message, \
no particles, stiff formal register, or several questions at once are failures.

Be reluctant to fail a draft. A slightly plain but honest reply is FINE and should pass — the \
lead getting a real answer beats them getting nothing. Fail it only when a real salesperson \
would wince.

Return ONLY this JSON: {"sells": bool, "why": str, "fix": str}
why: one short sentence, only when sells is false.
fix: what to do differently, only when sells is false.
"""


@dataclass(frozen=True)
class Verdict:
    sells: bool
    why: str = ""
    fix: str = ""
    errored: bool = False


CRITIC_CORRECTION = (
    "[System: a sales reviewer flagged your draft: {why} {fix} Rewrite the SAME message, "
    "keeping every fact grounded in the knowledge base. If the lead asked something, your "
    "first line must answer it. Do not hand off and do not fall back to a generic line.]"
)


async def review(  # noqa: PLR0913
    llm: LLMPort, *, reply: str, context: str, last_inbound: str, lang: str,
    branch_id: int, thread_id: int, budget: object = None, bill: bool = True,
) -> Verdict:
    """Judge one draft. Any failure to reach a verdict passes the draft through."""
    user = (
        f"KNOWLEDGE BASE:\n{context[:_KB_BUDGET]}\n\n"
        f"LEAD'S LAST MESSAGE:\n{last_inbound or '(they only tapped an ad, no words yet)'}\n\n"
        f"EXPECTED REPLY LANGUAGE: {lang}\n\n"
        f"DRAFT:\n{reply}")
    try:
        raw, meta = await llm.chat(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            capability="chat:smart", require_json_schema=True,
            workflow="critic", thread_id=thread_id, branch_id=branch_id)
        if not bill:
            meta.pop("cost_usd", None)
        elif budget is not None:
            await budget.record(float(meta.get("cost_usd") or 0.0))
        return _parse(raw)
    except Exception as exc:  # noqa: BLE001 — an unreachable reviewer must not cost the lead
        logger.warning("critic unavailable branch=%d thread=%d: %s — shipping the draft",
                       branch_id, thread_id, exc)
        return Verdict(sells=True, errored=True)


def _parse(raw: str) -> Verdict:
    """A verdict we cannot read is not a rejection — same reasoning as an unreachable critic."""
    try:
        data = json.loads(strip_fences(raw))
    except (json.JSONDecodeError, TypeError):
        logger.warning("critic: unparseable verdict — shipping the draft")
        return Verdict(sells=True, errored=True)
    if not isinstance(data, dict) or "sells" not in data:
        logger.warning("critic: verdict missing 'sells' — shipping the draft")
        return Verdict(sells=True, errored=True)
    return Verdict(
        sells=bool(data.get("sells")),
        why=str(data.get("why") or "").strip()[:300],
        fix=str(data.get("fix") or "").strip()[:300],
    )
