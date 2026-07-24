"""The v3 money gate — the only deterministic check that still fails closed.

v2 had 21 regex checks, each an incident fossilised into code (thread 1736, 2864, 4045, 4220,
…), and not one of them asked whether the reply SELLS. Failing any of them didn't improve the
answer, it replaced it with a stub or a numbered menu — both of which drop the conversation
out of the sale. Measured on live data, the stub got a 25% reply rate against 47.7% for a
normal answer.

What survives here is only what costs real money or real trust if it's wrong: a price the
knowledge base doesn't contain, a link that doesn't exist, an invented income claim. Those
three are worth blocking a send over. Everything else — tone, question count, repetition,
sales quality — is judged by the critic, which fails OPEN.
"""
from __future__ import annotations

from .guard import (
    canonical_prices,
    fabricated_income_figure,
    invented_service_offers,
    is_hedged_salary_reference,
    quotes_price,
    ungrounded_urls,
)

# The correction handed to the model when the gate trips. It names the offence and demands a
# replacement — never a retreat to "I'll check with the team", which is what v2 did and what
# taught the bot to go quiet on answerable questions.
# Stamped on the one hand-off v3 raises by itself, so the chat log can tell a machine-forced
# escalation from a reason the model actually named.
MONEY_ESCALATION_REASON = (
    "Степан дважды назвал сумму или ссылку, которых нет в базе знаний — "
    "нужен ручной ответ менеджера с точной цифрой")

# Used only by followup.py: a nudge that volunteers a price gets one rewrite before being
# dropped — a follow-up is never an answer to a fresh question, so a figure in one is
# always uninvited.
PITCH_CORRECTION = (
    "[System: nobody asked about money this turn, and your draft volunteers a figure anyway. "
    "Rewrite the SAME message keeping its hook and warmth, but without any price — give the "
    "value first; the numbers come when they ask.]"
)

MONEY_CORRECTION = (
    "[System: your draft states a figure/link OR offers a service/material that is NOT in the "
    "knowledge base: {issues}. Rewrite the SAME message keeping its intent and warmth, but "
    "state only figures and links that appear in the knowledge base above, and offer ONLY what "
    "the school actually provides — the only free thing you may offer is a campus visit; the "
    "Demo Event is a paid offer. Do NOT invent a consultation, session, or a document you'll "
    "prepare. If you don't have the fact, say what you do know and offer to confirm with the "
    "team — do not go silent and do not hand the lead off.]"
)


def uninvited_price(reply: str, dossier: object) -> bool:
    """A price figure in a NUDGE with the lead not already `ready` — always volunteered,
    since a follow-up is never an answer to a fresh question (thread 4849). Used only by
    followup.py; live replies leave price timing to the model."""
    return quotes_price(reply) and dossier.readiness != "ready"


def money_issues(reply: str, context: str) -> list[str]:
    """Ungrounded money/link claims AND invented services in the draft — the fail-closed set.
    Empty means it is safe to send. (Named 'money' for history; it now also gates a promised
    service/material that isn't part of the offering — same must-not-ship severity.)"""
    issues: list[str] = []
    for url in ungrounded_urls(reply, context):
        issues.append(f"link not in the knowledge base: {url}")
    # Price/income grounding runs per bubble so a hedged salary RANGE (a market reference, not
    # a course price) can be exempted — its numbers can't exact-match the KB and shouldn't
    # (thread 5049). Everything else in that bubble is still checked normally.
    for bubble in (reply or "").split("|||"):
        if is_hedged_salary_reference(bubble):
            continue
        issues.extend(_ungrounded_prices(bubble, context))
    issues.extend(fabricated_income_figure(reply))
    issues.extend(
        f"service/material not in the offering (invented): {m}"
        for m in invented_service_offers(reply))
    return issues


def _ungrounded_prices(reply: str, context: str) -> list[str]:
    """Every money figure quoted must appear in the knowledge base.

    v2 split this across three mechanisms (a no-prices-at-all check, a subset check, and an
    LLM verify) that could each let a wrong figure through on their own. One rule: if the
    number isn't in the KB, it isn't real. Quoting a price that doesn't exist is the single
    most expensive mistake this bot can make — it is a promise the school has to honour."""
    quoted = canonical_prices(reply or "")
    if not quoted:
        return []
    grounded = canonical_prices(context or "", liberal=True)
    invented = sorted(quoted - grounded)
    return [f"price figure not in the knowledge base: {value:,}".replace(",", ".")
            for value in invented]
