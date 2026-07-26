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
    impossible_capability_offers,
    invented_service_offers,
    is_hedged_salary_reference,
    media_delivery_offers,
    profile_inspection_claims,
    quotes_price,
    stale_dates,
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

# The first message to a SILENT ad tap must carry no figure. See free_mode's
# AD_TAP_FIRST_TURN_NOTE for the measurement: 36.3% answered without a number against 16.1%
# with one, over 819 threads that all started from the same place. The note says so, but the
# prefill sitting right in front of the model asks about cost, and the model keeps reading that
# as a question — so this is enforced, the same way the bubble cap is.
AD_TAP_PRICE_CORRECTION = (
    "[System: this is the first message to someone who only pressed an ad button — they have "
    "not told you anything, and the cost line they 'asked' is Meta's prefill, not their words. "
    "Your draft quotes a figure. Rewrite it with NO number of any kind: no price, no DP, no "
    "instalment, no discount, no 'starting from'. Keep the warmth and keep it short. Say what "
    "the course gives them in one line, and ask one easy thing. The price is the right answer "
    "to their next message, not to a button press.]"
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
    """A price figure in a NUDGE to someone money was never discussed with — volunteered, since
    a follow-up is never an answer to a fresh question (thread 4849). Used only by followup.py;
    live replies leave price timing to the model.

    Silence about money is what makes a figure uninvited, not the lead's readiness. Gating on
    `readiness != "ready"` alone muted the number for every lead who had asked "berapa", got
    the answer, and then went quiet — the exact person for whom a payment plan is the most
    useful thing left to say. So the exemption is: if THEY raised money, repeating a figure is
    a reminder rather than a new pitch.

    `prices_quoted` is emphatically not that signal, and reading it as one (2026-07-26, same
    day) reversed the rule inside a morning. It records the figures WE sent — decision._prices_in
    reads them off the bot's own reply — so one quote made every later nudge exempt for good.
    Thread 5393 is what that looks like: a spec sheet at 12:17 filled prices_quoted, and an
    hour later an unprompted Demo Event pitch with its own price sailed through to someone who
    had not said a word. Both remaining signals come from the lead's own words (discovery.py)."""
    if not quotes_price(reply):
        return False
    if dossier.readiness == "ready":
        return False
    return not (dossier.budget_signal or dossier.payment_preference)


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
    # A batch date that has already run is as wrong as an invented price — the lead waits for
    # a session nobody will hold. The KB annotator marks expired dates so the model shouldn't
    # reach one, but a card left un-updated is a standing trap (live: "batch berikutnya 19
    # Juli" offered on 25 July), so the send is blocked too.
    issues.extend(stale_dates(reply))
    # Offering to send a video/file/module the bot cannot send: the lead says yes and gets a
    # refusal one turn later (live, 25 July — 6 such offers in 24h). An offer we retract is
    # worse than no offer, so it never ships.
    issues.extend(
        f"cannot be sent in this chat (text only): {m}" for m in media_delivery_offers(reply))
    # Offering a call or a voice note: the bot has neither, so the lead is promised a channel
    # that will never open. The check has existed since v2 and was wired into the comments path
    # and the audit — but never into the reply gate, so live DMs have never been checked by it.
    issues.extend(
        f"a channel the bot does not have: {m}" for m in impossible_capability_offers(reply))
    # "I had a look at your profile / your posts are great": invented, and invented as flattery,
    # which is the worst place to be caught. See guard._PROFILE_CLAIM_RE and thread 5333 — once
    # such a claim is in the transcript the next turn treats it as fact and elaborates on it.
    issues.extend(
        f"claims to have seen something it cannot see: {m}"
        for m in profile_inspection_claims(reply))
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
