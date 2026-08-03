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
    empty_handed_refusal,
    fabricated_alumni_claim,
    fabricated_income_figure,
    fabricated_result_claim,
    impossible_capability_offers,
    invented_service_offers,
    is_hedged_salary_reference,
    media_delivery_offers,
    profile_inspection_claims,
    quotes_price,
    review_content_claims,
    stale_dates,
    ungrounded_urls,
    vague_start_window,
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

_MONEY_CORRECTION = (
    "[System: your draft states a figure/link OR offers a service/material that is NOT in the "
    "knowledge base: {issues}. Rewrite the SAME message keeping its intent and warmth, but "
    "state only figures and links that appear in the knowledge base above, and offer ONLY what "
    "the school actually provides{catalogue}. Do NOT invent a consultation, session, or a "
    "document you'll prepare. If you don't have the fact, say what you do know and offer to "
    "confirm with the team — do not go silent and do not hand the lead off.]"
)
# Which of our offers is free and which is not is IT STEP Jakarta's catalogue, not a fact
# about selling — naming a "Demo Event" to a branch that has never held one teaches the model
# an offer that does not exist, and the rewrite it produces invents around it. The knowledge
# base already carries every branch's real catalogue; only the Indonesian branches get the
# reminder they have been trained against.
_ID_CATALOGUE_CLAUSE = (
    " — the only free thing you may offer is a campus visit; the Demo Event is a paid offer")


def money_correction(lang: str = "id") -> str:
    """The correction handed back to the model when the gate trips, with the tenant-specific
    offer reminder only where it is true."""
    catalogue = _ID_CATALOGUE_CLAUSE if (lang or "").lower() == "id" else ""
    return _MONEY_CORRECTION.replace("{catalogue}", catalogue)


def uninvited_price(reply: str, dossier: object, *, ad_promised_price: bool = False,
                    lang: str = "id") -> bool:
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
    had not said a word. Both remaining signals come from the lead's own words (discovery.py).

    `ad_promised_price` settles the one place two measurements pulled apart. Meta's prefill
    reads "Boleh info jadwal, durasi, dan biaya?", and leading the FIRST message with a figure
    halves the reply rate (16.1% against 36.3% over 819 threads) — so the opener carries none.
    But if the nudge is silenced too, that lead never gets a number at all, which is the other
    measured loss: only 24% of 453 price-ad threads ever saw one. Thread 5293 lived it — an ad
    that asks about cost, four of our messages, no figure in two days.

    So the ad may buy a price ONCE: not in the opener, and not again once we have answered —
    only while the question the ad put in their mouth is still hanging."""
    if not quotes_price(reply, lang):
        return False
    if dossier.readiness == "ready":
        return False
    if ad_promised_price and not dossier.prices_quoted:
        return False
    return not (dossier.budget_signal or dossier.payment_preference)


def money_issues(reply: str, context: str, lang: str = "id") -> list[str]:
    """Ungrounded money/link claims AND invented services in the draft — the fail-closed set.
    Empty means it is safe to send. (Named 'money' for history; it now also gates a promised
    service/material that isn't part of the offering — same must-not-ship severity.)

    `lang` is the language the reply is written in, and it decides how a sum is recognised.
    Default 'id' so an unlanguaged caller keeps branch 1's exact behaviour; every live path
    passes the conversation's own language, because a gate that cannot see "$1,500" is not a
    gate at all."""
    issues: list[str] = []
    for url in ungrounded_urls(reply, context):
        issues.append(f"link not in the knowledge base: {url}")
    # Price/income grounding runs per bubble so a hedged salary RANGE (a market reference, not
    # a course price) can be exempted — its numbers can't exact-match the KB and shouldn't
    # (thread 5049). Everything else in that bubble is still checked normally.
    for bubble in (reply or "").split("|||"):
        if is_hedged_salary_reference(bubble):
            continue
        issues.extend(_ungrounded_prices(bubble, context, lang))
    issues.extend(fabricated_income_figure(reply))
    # A result stated as a percentage is never in the knowledge base and never true of us.
    # Thread 4799 produced one twice — first as an outside brand, then as "our alumni".
    issues.extend(
        f"an invented result percentage: {m}" for m in fabricated_result_claim(reply))
    # …and a named person claimed as ours. Thread 2367 was told "Contoh alumni kami, Pieter
    # Levels" — someone with no connection to the school, and checkable in one search. The KB
    # forbids this in prose twice; only the gate can actually stop it.
    issues.extend(
        f"a named person claimed as our alumnus, not in the knowledge base: {m}"
        for m in fabricated_alumni_claim(reply, context))
    issues.extend(
        f"service/material not in the offering (invented): {m}"
        for m in invented_service_offers(reply))
    # A batch date that has already run is as wrong as an invented price — the lead waits for
    # a session nobody will hold. The KB annotator marks expired dates so the model shouldn't
    # reach one, but a card left un-updated is a standing trap (live: "batch berikutnya 19
    # Juli" offered on 25 July), so the send is blocked too.
    issues.extend(stale_dates(reply))
    # …and a start given as a month rather than a date, which stale_dates cannot see because
    # there is no day in it to expire. Two threads on two products lost to this wording.
    issues.extend(
        f"a course start given as a window, not a date: {m}" for m in vague_start_window(reply))
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
    # Same shape, one step removed: asserting what our REVIEWS say. Pointing at the address on
    # Google Maps is fine and verifiable; "lots of students there share their experience" is a
    # claim about a page nobody here has read, and the lead opens it while still in the chat.
    # Banned in facts_policy on 27.07 and produced anyway on the next sim run.
    issues.extend(
        f"a claim about what our reviews say, which nobody has read: {m}"
        for m in review_content_claims(reply))
    # Обратная ошибка к предыдущей: не выдумал доказательство, а не дал никакого. База требует
    # называть ближайший реальный кейс вместо «у нас нет» — прозой это правило не держится
    # (тред 5440: пять ответов подряд «нет отзывов» при полной базе поимённых выпускников).
    issues.extend(
        f"says it has no proof and offers nothing verifiable instead: {m}"
        for m in empty_handed_refusal(reply))
    return issues


def _ungrounded_prices(reply: str, context: str, lang: str = "id") -> list[str]:
    """Every money figure quoted must appear in the knowledge base.

    v2 split this across three mechanisms (a no-prices-at-all check, a subset check, and an
    LLM verify) that could each let a wrong figure through on their own. One rule: if the
    number isn't in the KB, it isn't real. Quoting a price that doesn't exist is the single
    most expensive mistake this bot can make — it is a promise the school has to honour."""
    quoted = canonical_prices(reply or "", lang=lang)
    if not quoted:
        return []
    grounded = canonical_prices(context or "", liberal=True, lang=lang)
    invented = sorted(quoted - grounded)
    return [f"price figure not in the knowledge base: {value:,}".replace(",", ".")
            for value in invented]
