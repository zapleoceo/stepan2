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

from .guard import canonical_prices, fabricated_income_figure, quotes_price, ungrounded_urls

# The correction handed to the model when the gate trips. It names the offence and demands a
# replacement — never a retreat to "I'll check with the team", which is what v2 did and what
# taught the bot to go quiet on answerable questions.
# Stamped on the one hand-off v3 raises by itself, so the chat log can tell a machine-forced
# escalation from a reason the model actually named.
MONEY_ESCALATION_REASON = (
    "Степан дважды назвал сумму или ссылку, которых нет в базе знаний — "
    "нужен ручной ответ менеджера с точной цифрой")

PITCH_CORRECTION = (
    "[System: you don't know this lead's pain or goal yet, and your draft pitched a product "
    "or asked for a commitment anyway. Rewrite the SAME message as one honest discovery move "
    "instead — a question about their situation or motive. Save the pitch for once you "
    "actually know why they're here.]"
)

MONEY_CORRECTION = (
    "[System: your draft states something about money or a link that is NOT in the knowledge "
    "base: {issues}. Rewrite the SAME message keeping its intent and warmth, but state only "
    "figures and links that appear in the knowledge base above. If you don't have the exact "
    "number, say what you do know and offer to confirm the precise figure — do not go silent "
    "and do not hand the lead off.]"
)


# Moves that pitch — name a product, quote a price, or ask for a commitment — before
# discovery is done. v2 enforced "no presenting without a pain and a gain" in CODE
# (_stage_for rolled the stage back); the v3 rebuild only asked for it in prose (CLOSING:
# "save it until you know why they came"), and thread 452 showed prose alone isn't enough —
# two turns after a context clear, with the dossier empty, Stepan pitched Vibe Coding anyway.
_PITCH_MOVES = frozenset({"give_value", "quote_price", "invite_campus", "close"})


def premature_pitch(
    move: str, dossier: object, lead_asked_directly: bool, reply: str = "",
) -> bool:
    """True when the model pitched before earning the right to.

    Never fires when the lead asked outright (answer-first already covers that turn) or once
    discovery has actually landed a pain and a desired outcome.

    Checks the DECLARED move first, but that alone isn't airtight: thread 4972 shipped a full
    price quote on a first turn with an empty dossier, self-labelled `answer_question` — a
    move outside `_PITCH_MOVES`, so the move check alone let it through. A price figure in the
    reply is pitch content regardless of what the model called the move, so it's checked too."""
    if lead_asked_directly or dossier.has_discovery():
        return False
    return move in _PITCH_MOVES or quotes_price(reply)


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
