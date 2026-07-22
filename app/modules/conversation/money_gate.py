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

from .guard import canonical_prices, fabricated_income_figure, ungrounded_urls

# The correction handed to the model when the gate trips. It names the offence and demands a
# replacement — never a retreat to "I'll check with the team", which is what v2 did and what
# taught the bot to go quiet on answerable questions.
# Stamped on the one hand-off v3 raises by itself, so the chat log can tell a machine-forced
# escalation from a reason the model actually named.
MONEY_ESCALATION_REASON = (
    "Степан дважды назвал сумму или ссылку, которых нет в базе знаний — "
    "нужен ручной ответ менеджера с точной цифрой")

MONEY_CORRECTION = (
    "[System: your draft states something about money or a link that is NOT in the knowledge "
    "base: {issues}. Rewrite the SAME message keeping its intent and warmth, but state only "
    "figures and links that appear in the knowledge base above. If you don't have the exact "
    "number, say what you do know and offer to confirm the precise figure — do not go silent "
    "and do not hand the lead off.]"
)


def money_issues(reply: str, context: str) -> list[str]:
    """Ungrounded money/link claims in the draft. Empty means it is safe to send."""
    issues: list[str] = []
    for url in ungrounded_urls(reply, context):
        issues.append(f"link not in the knowledge base: {url}")
    issues.extend(_ungrounded_prices(reply, context))
    issues.extend(fabricated_income_figure(reply))
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
