"""Free reply mode — the model sells its own way; the code only guards the money.

The scripted contract encodes HOW to sell (13 moves, discovery ladder, price script, nine
turn-notes) and grew out of incidents on weak models. Free mode is the opposite bet: give a
STRONG model (the broker's Sonnet-first chat:sales chain) the full fact surface, the goal, and
the few rules that protect real money — and let it decide everything else.

The prompt is built for the broker's prompt cache: message[0] is a byte-stable system prefix
(full KB + this contract — identical across turns AND across leads of the same branch/language),
everything per-lead lives in a second, small system message after it. Any conditional insertion
into message[0] breaks the cache and triples the Sonnet bill — keep it stable.
"""
from __future__ import annotations

from typing import Any

from app.adapters.db.models import Message

from .dossier import LeadDossier
from .prompt import _role_of, manager_note_block

# Injected only on the turn it applies to. As a standing contract section the model had to
# decide for itself whether "first message" described this turn, and on thread 4956 it didn't —
# a bare "Iya ka" was answered with no introduction at all. is_first_reply is already known in
# code, so it goes in as a fact rather than as a condition. The opener rules are measured over
# 2 619 live first replies: introducing yourself cost nothing, ending on a question was worth
# ~4 points of reply rate, describing the campus cost 6-10.
FIRST_TURN_NOTE = (
    "[This is your FIRST message to this person. Open by saying who you are in one short "
    "clause — your name, and that you're from the school — then go straight to what they "
    "wrote. Never describe the campus, its address or its floor. End on a question.]"
)

# The lead tapped an ad; the visible first message is the ad's prefill, not their typing.
# The old fixed template opened with the DP figure and was answered 14.3% of the time against
# 36.3% for a written reply — so this note replaced it, and it carried a flat ban on quoting
# any price. That ban went too far: the prefill Meta ships reads "Boleh info jadwal, durasi,
# dan biaya?", so the person believes they asked about money, and a 9-day audit found only
# 24% of 453 such threads ever got a figure — the single largest measured loss in the funnel.
# The template failed because it led with money before saying anything else, not because a
# price is forbidden. State what the prefill is and let the model decide when the number lands.
AD_TAP_FIRST_TURN_NOTE = (
    "[This is your FIRST message to this person. They tapped an ad{product} — the text you "
    "see from them is Meta's prefill, not their typing, so nothing about them is known yet. "
    "But note what that prefill says: it asks about schedule, duration and cost, and from "
    "their side it looks like they asked. Do not stonewall it — a person who taps a price ad "
    "and gets only questions back usually leaves. Whether you give the figure straight away, "
    "or a starting price plus one question, or ask first because their goal changes which "
    "product applies, is your call. Never describe the campus. Keep it to 1-2 short bubbles.]"
)


def ad_tap_note(product_title: str | None) -> str:
    """The first-turn note for a silent ad tap, naming the product when the ad maps to one."""
    return AD_TAP_FIRST_TURN_NOTE.format(
        product=f" for {product_title}" if product_title else "")

# The branch language as a person would name it. "Reply in id" is an instruction about a
# string; "Reply in Bahasa Indonesia" is an instruction about a language. Same length.
_LANG_NAMES = {"id": "Bahasa Indonesia", "ms": "Bahasa Melayu", "en": "English",
               "ru": "Russian", "uk": "Ukrainian", "vi": "Vietnamese"}


def language_name(lang: str) -> str:
    return _LANG_NAMES.get((lang or "").lower(), lang)


def dossier_block(d: LeadDossier) -> str:
    """What is already known about this lead — the block that replaces re-deriving it from raw
    history every turn. Empty when nothing is known yet, so a first turn stays clean."""
    lines = [f"- {label}: {value}" for label, value in (
        ("who they are", d.role),
        ("what they want", d.job_to_be_done),
        ("what worries them", "; ".join(d.pains)),
        ("what a good outcome looks like", "; ".join(d.desired_state)),
        ("how ready they are", d.readiness),
        ("payment preference", d.payment_preference),
        ("budget signal", d.budget_signal),
    ) if value]
    open_objections = d.open_objections()
    if open_objections:
        # Stated, not ordered: "handle before anything else" forced the old objection back to
        # the top of the turn even when the lead had moved on to a fresh question.
        lines.append("- raised and not yet resolved: " + "; ".join(open_objections))
    handled = [f"{o.text} → {o.handled_by}" for o in d.objections
               if o.status == "handled" and o.handled_by]
    if handled:
        lines.append("- already answered (don't re-argue): " + "; ".join(handled))
    # Only prices survive here. "products named / stories told / arguments made" were the
    # model restating its own transcript back to itself — the dialogue below already shows
    # what it said. A quoted figure is different: it is a commitment, and knowing it was
    # already given changes whether repeating it is a reminder or a new offer.
    if d.prices_quoted:
        lines.append("- prices you already gave them: " + ", ".join(d.prices_quoted))
    if d.refusal != "none":
        lines.append(f"- they have said no, degree: {d.refusal}")
    return "LEAD DOSSIER (what you already know — never re-ask it):\n" + "\n".join(lines) \
        if lines else ""


def append_dialog(messages: list[dict[str, Any]], dialog: list[Message]) -> None:
    """Append the dialog turns, merging consecutive same-role messages (some providers
    hard-reject same-role runs)."""
    for m in dialog:
        content = (m.text or "").strip()
        if not content:
            continue
        role = _role_of(m)
        if messages and messages[-1]["role"] == role and role != "system":
            messages[-1]["content"] += "\n" + content
        else:
            messages.append({"role": role, "content": content})


def _notes_block(notes: list[str] | None) -> str:
    if not notes:
        return ""
    body = "\n".join(f"- {n}" for n in notes)
    return f"MANAGER RULES for every lead (follow strictly):\n{body}"

# The goal is stated as the funnel Dima runs: explicit agreement → phone → manager (CRM).
# Manager process facts (call 09-18 WIB, WhatsApp fallback) are owner-confirmed 2026-07-24.
_FREE_CONTRACT = """\
You are texting ONE person in Instagram Direct as a real salesperson at this school. The
persona above is who you are; the knowledge base above is everything that is true. HOW to
sell is yours to decide — read this person, pick your own approach, pace, arguments and
words. There is no script and no fixed sequence: answer what they ask, learn what you need,
sell the way this particular conversation calls for.

YOUR GOAL, in order:
1. Bring them to an EXPLICIT agreement to join a course — a real "yes, I want to enrol",
   not a polite nod.
2. Then ask for their phone/WhatsApp number so a manager can call them, register them and
   give the payment details. Managers call on working days 09.00-18.00 WIB, and switch to
   WhatsApp if the call doesn't go through — promise a same-day call only inside those
   hours; otherwise say they'll be contacted from 09.00 the next working day.
3. If they are ready to pay right now, give the payment options from the knowledge base
   yourself — never park a hot lead to wait for a manager.

HARD RULES — the only ones:
- An unanswered question outranks everything you wanted to say. If their last message asked
  something, that is the only thing worth writing about this turn — a new angle, a fresh hook
  or a follow-up thought lands as "you did not hear me" and they stop reading. If the answer
  isn't in the knowledge base, say exactly that and that you'll confirm it, then come back to
  it — don't change the subject. (Measured: in 30 of 50 live threads the lead's question sat
  unanswered under later messages, and those threads died.)
- Every fact, price, schedule, link, discount and promise must come from the knowledge base
  above. If it isn't there, you don't know it — say what you DO know and offer to confirm
  the rest. Never invent anything.
- Dates: quote them EXACTLY as written above. Never work out or add a weekday, and never
  restate a date in your own words — if the knowledge base says "9 August", write "9 August",
  not "Saturday, 9 August". A weekday you computed yourself is a fact nobody checked, and
  getting it wrong sends someone to the campus on the wrong day. Same for a start date you
  were not given: say the schedule isn't fixed yet and offer to confirm it with the team,
  rather than implying classes begin right after payment.
- We are a private course centre, not a university: never call our place a "kampus" and never
  imply a degree. It is "tempat kami" / "tempat kursus" / "lokasi kami", at Menara Sudirman.
  (KBBI reserves "kampus" for higher education; claiming it is both false and, paired with
  "Academy", the profile Indonesian media call a "kampus bodong".)
- Address them as Kak/Kakak and keep it that way for the whole chat. Not "kamu" (too
  familiar), not "Anda" (formal, corporate). Drifting between forms mid-conversation reads
  as two different people writing.
- Reply in {lang}; if the lead writes in another language, answer in theirs and stay in it.
- Write like a human in a chat, not like a brochure: short messages, at most 3 bubbles split
  by '|||'. How many questions, how much emoji, how long — you judge, from their length and
  energy. The bubble cap is the only hard limit here; it protects the account, not the style.
- Set needs_human=true ONLY when they ask for a human, complain, raise a legal issue, or
  have a problem with a payment they already made. Not knowing something is not a reason —
  and never go silent.
"""

# Every field here is read by something downstream — routing, follow-up tone, stage events,
# hand-off, CRM. Five were dropped on 2026-07-25 after grepping for their readers and finding
# none: `move` (a label the model invented each turn for one log line), `decides_with`,
# `products_named`, `cases_used`, `arguments_used`. The last three were fed back as "don't
# repeat yourself", which the dialogue above already shows — the model was paying attention
# to restate its own transcript. Attention spent on the schema is attention not spent on the
# person, and the dossier was only being filled for ~5% of leads (see discovery.py).
_FREE_SCHEMA = """\
Return ONLY this JSON, no prose and no markdown fences:
{{"reply": str, "stage": str, "product_slug": str|null, "ready": bool, \
"phone": str|null, "needs_human": bool, "human_reason": str|null, "reply_language": str|null, \
"dossier": {{"role": str, "job_to_be_done": str, "pains": [str], "desired_state": [str], \
"readiness": str, "prices_quoted": [str], "payment_preference": str, "budget_signal": str, \
"objections": [{{"text": str, "status": str, "handled_by": str, "category": str}}], \
"refusal": str}}}}

stage: new|nurturing|qualifying|presenting|objection|dormant. Not 'ready' — that's the flag.
ready: true only when they gave a contact AND want to enrol or reserve now.
phone: their number exactly as they typed it, the turn they share it; else null.
reply_language: ISO code when you replied in something other than {lang}, else null.
dossier: what you now know about this PERSON — carry forward what's in LEAD DOSSIER above and
  add what this turn revealed. Record what they revealed, not what you offered; leave a field
  empty when you don't know.
  role: school|student|working|jobseeking|parent. readiness: exploring|considering|ready.
  refusal: none|soft|vague|blunt. objections: everything raised so far, status 'open' or
  'handled' with how you handled it; category: price|time|trust|job_outcome|self_study_free|
  parent_approval, else empty.
"""


def free_contract(lang: str) -> str:
    named = language_name(lang)
    return _FREE_CONTRACT.format(lang=named) + "\n" + _FREE_SCHEMA.format(lang=named)


def build_messages_free(  # noqa: PLR0913
    knowledge: str,
    dialog: list[Message],
    lang: str,
    dossier: LeadDossier,
    coaching_notes: list[str] | None = None,
    source_block: str | None = None,
    name_block: str | None = None,
    manager_note: str | None = None,
    now_block: str | None = None,
    is_first_reply: bool = False,
    first_turn_note: str | None = None,
) -> list[dict[str, Any]]:
    """Stable cached prefix first, then one small per-lead system block, then the dialog.

    messages[0] must stay byte-identical between turns and between leads (same branch +
    language) — it is the broker's prompt-cache anchor. A test pins this invariant.

    `first_turn_note` overrides the default opener note — a silent ad tap needs different
    instructions from a lead who wrote something (see ad_tap_note)."""
    stable = knowledge.rstrip() + "\n\n" + free_contract(lang)
    variable = [block for block in (
        (now_block or "").strip(),
        _notes_block(coaching_notes),
        manager_note_block(manager_note) or "",
        (source_block or "").strip(),
        (name_block or "").strip(),
        dossier_block(dossier),
        (first_turn_note or FIRST_TURN_NOTE) if is_first_reply else "",
    ) if block]
    messages: list[dict[str, Any]] = [{"role": "system", "content": stable}]
    if variable:
        messages.append({"role": "system", "content": "\n\n".join(variable)})
    append_dialog(messages, dialog)
    return messages
