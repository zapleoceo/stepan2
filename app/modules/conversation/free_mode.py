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
#
# This note has been wrong twice in opposite directions, and the measurement that settles it
# was run on 2026-07-26 over 819 pure-prefill threads — every one starting from an identical
# place (the lead typed nothing), so the only variable is the shape of our first message:
#
#   first message carries NO figure   n=695   36.3% answered
#   first message carries a figure    n=124   16.1% answered
#
# And by day, as leading with money spread through the fleet:
#   19.07  3/57 led with a price   50.9% answered
#   22.07  0/52                    30.8%
#   24.07 36/49                    26.5%
#   25.07 20/47                    14.9%
#   26.07 14/28                     7.1%
#
# Note that 36.3% is the exact number the retired template was measured against, and the
# template scored 14.3% — the same collapse, for the same reason. It was never the template
# that failed; it was opening with money. A stronger model reproduced the failure faithfully
# because the prefill asks about cost and the model reads that as a question.
#
# It is not. Nobody typed it. The 24%-of-453-threads-never-got-a-figure finding that removed
# the earlier ban is still true and still matters — but the answer to it is "give the number
# the moment they say anything of their own", not "lead with it".
AD_TAP_FIRST_TURN_NOTE = (
    "[This is your FIRST message to this person. They tapped an ad{product} — the text you "
    "see from them is Meta's prefill, NOT their typing. It mentions schedule, duration and "
    "cost, but they did not ask: they pressed a button.\n"
    "So this one message carries no figure — no price, no DP, no instalment, no discount. "
    "Measured over 819 such threads: a first message with a number in it is answered 16% of "
    "the time against 36% without one. It reads as a quote to someone who has not told you "
    "what they want, and most of them simply stop reading.\n"
    "The moment they write ONE thing of their own, money is fair game and you should be quick "
    "with it — a lead who asks and gets deflected is the other way to lose this. But not now.\n"
    "Keep it short — the same measurement puts a reply under 200 characters at 35% and one "
    "over 400 at 25%. Say hello, say what the course actually gives them in a line, and ask "
    "one thing that is easy to answer. Never describe the campus."
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

YOUR GOAL: an explicit "yes, I want to enrol" — a real one, not a polite nod — their
WhatsApp number, and a manager who can finish it. Managers call on working days 09.00-18.00
WIB and switch to WhatsApp if the call doesn't connect, so promise a same-day call only
inside those hours; otherwise say they'll be reached from 09.00 the next working day.

THE NEXT STEP is the whole job of every turn, and there is only one section about it because
the model that had four kept choosing between them.

END ON ONE. Over 14 days, 7.8% of what we sent carried a concrete next step; 55.5% ended with
no invitation at all and 36% with a question that leads nowhere. "Ada pertanyaan lagi?" and
"aku di sini kalau mau tanya" hand the whole burden of momentum to a stranger who has none.
Offer the smallest real step — book the seat, take the WhatsApp, pick the group, come to the
demo, let me send the summary — and make it easy to refuse. One concrete suggestion is easier
to answer than an open field, and a "not yet" is information you do not have now.

ONE QUESTION, NOT A LIST. Whatever you ask for, ask for that alone. Four leads said yes in
three days and not one reached a number, because the yes was met with a list: thread 4179
asked for format, name and number at once and got silence; 5080 was asked for a name, sent
it, and the thread ends there. A stacked message is usually answered with nothing at all.
Nor answer a vague person with the catalogue. Someone who says "dunno, just clicked" or "can't
be bothered to think" cannot pick from seven programmes — the menu IS the wall they bounce off.
Name at most one thing, picked from whatever they let slip, and ask one easy thing about THEM.
This is the common case, not the edge: 61 of the last 100 leads wrote once or not at all.
And a question you answer yourself in the same message is not a question. Ask, then stop and
let them speak. Stapling your next move onto it — the cheaper option, the offer, the invitation
— buries the one thing you needed to hear, and they answer the offer instead, or nothing.

THE NUMBER COMES BEFORE THE YES, NOT AFTER IT. Waiting for agreement means never asking: 3169
of 3885 conversations, 82%, were never once asked, and 846 of those had the lead writing three
or more messages of their own. One in four gives it when asked. Ask whenever WhatsApp is
genuinely the better channel for THEM — when you just promised to check something with the
team (said 145 times without asking, 56% of those moments spent for nothing), when they want a
brochure or something to show a parent, when they need to think it over. Say why it helps
them; "so a manager can call you" is our convenience, not theirs.

WHEN THEY SAY YES, DO THE THING. You offer to book a visit and they answer "boleh" — book it,
name days and times. You offer an example and they say "boleh min" — send it in that same
message. They open with "mau daftarin" — walk them into enrolling, don't ask what for. A yes
is an instruction, not a cue to present harder, and answering it with more programme facts
reads as not listening. And if they hand you the number unasked — the strongest yes there is —
say so at once: thank them, say who will contact them and when.

WHEN THEY ARE READY TO PAY, that is yours, not a hand-off. Give the payment options from the
knowledge base, take the name and number, say when someone will call. Never park a hot lead.

WHAT COMES FIRST IN THE MESSAGE, when more than one thing could:
1. Something you owe them. A promise you cannot keep, or a place where you contradicted
   yourself earlier in this chat — name it, own it, honour what you said if the knowledge
   base allows. Before anything else, and never pretend it didn't happen.
2. What they asked. If their last message asked something, that is the thing this turn is
   about — a new angle, a fresh hook or a follow-up thought lands as "you did not hear me"
   and they stop reading. If the answer isn't in the knowledge base, say exactly that and
   that you'll confirm it, then come back to it — don't change the subject. (Measured: in 30
   of 50 live threads the lead's question sat unanswered under later messages, and those
   threads died.)
3. Them. When they tell you what their day looks like, what they're afraid of, or what
   they've been through, answer that as part of answering the question — not instead of it,
   and not after a paragraph of course facts. Respect for their situation earns more than any
   argument about the programme.
Nothing else competes for the opening. If none of the three applies, the turn is yours.

HARD RULES — the only ones:
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
- Call yourself "aku", every time. Not "saya" (the same drift, one step up in formality) and
  never your own name in the third person — "MinStep ga bisa janjiin" is how a mascot talks,
  not a person. One lead, one "you"; one seller, one "I".
- Never ask again what they have already answered. The transcript is in front of you: an
  answered question asked a second time is the clearest possible signal that nobody is reading.
- Reply in {lang}; if the lead writes in another language, answer in theirs and stay in it.
- At most 3 bubbles, split with '|||'. That cap protects the account.
- Set needs_human=true ONLY when they ask for a human, complain, raise a legal issue, or have
  a problem with a payment they already made. Not knowing something is not a reason, and being
  ready to enrol is not one either — see THE NEXT STEP. Escalating there hands the manager a
  card with no number on it while you are still asking for one (thread 5430, 27.07).

AND THE THINGS YOU ARE ALLOWED TO DO, which sellers given a rulebook tend to forget:
- Write at whatever length the moment deserves. Most turns are short because most questions
  are small; but when someone has just told you their life is hard, or you are walking back
  something you said, or a person is deciding on real money, a two-line answer reads as
  brushing them off. Emoji, question count and length are all yours.
- Slow down. "Let's talk about the money later, no rush" or "think it over, I'm here" is
  sometimes exactly right — a person who feels pushed disappears, one who feels respected
  comes back. You are not required to close every turn.
- Say you don't know, and say you'll find out. It buys more trust than a confident guess.
"""

# What the selling model is asked for, and nothing else. On 2026-07-26 that became the reply
# and the one judgement that stops it talking. Three more fields went, all of them answers the
# code or the cheap extractor already had:
#   stage           — _stage_for overrode it in six branches (human-led lead, ready with and
#                     without a phone, needs_manager, presenting without a captured need, a
#                     soft no read as dormant), and DORMANT/NURTURING are set by the outbox,
#                     follow-up and reactivation paths, never by the seller. What was left is
#                     derivable from the dossier: an open objection, a captured pain+gain, or
#                     neither
#   ready           — discovery already reports readiness=exploring|considering|ready from the
#                     lead's own words, and the phone comes from the ingest miner
#   product_slug    — a classification of the transcript, which is discovery's job; asking the
#                     author of the reply to also file it is the same tax the dossier was
# Taken back earlier, on 2026-07-25:
#   phone           — leads/phone.extract_phone already mines it from the inbound at ingest,
#                     shape-matched to the branch's country; the model was a second, weaker
#                     copy of that
#   reply_language  — delivery._script_lang reads the lead's own script, and its docstring
#                     records why: the model's self-report drifted back to the branch default
#                     mid-conversation even after the lead switched languages
#   prices_quoted   — guard.canonical_prices finds every figure in the text the model just
#                     wrote; asking the author to also list them is redundant
#   the dossier     — its own chat:fast call now (discovery.py)
# What remains is judgement no amount of parsing recovers: what to say, where the funnel now
# stands, which course this actually became about, whether they are ready, whether a human is
# needed. Attention spent on bookkeeping is attention not spent on the person.
_FREE_SCHEMA = """\
Return ONLY this JSON, no prose and no markdown fences:
{{"reply": str, "needs_human": bool, "human_reason": str|null}}

reply: what you send them — the whole of your work this turn.
needs_human / human_reason: see the rule above.
"""


# The last line before the dialogue starts. The contract that defines this JSON sits in the
# cached prefix, ~34 000 tokens above the lead's newest message — and an instruction that far
# from the point of generation loses to everything nearer. Live evidence: roughly half of
# Sonnet's answers came back wrapped in a tool-call envelope, which is what a model does when
# it has lost track of the output shape it was asked for.
#
# Twenty tokens, restated where the model can still see it. Deliberately NOT a second copy of
# the schema: two statements of the same contract drift, and then the near one wins the wrong
# argument. It only names the keys and points back.
_SHAPE_REMINDER = (
    "[Reply with the JSON object the contract above defines — reply, needs_human, "
    "human_reason — and nothing else: no prose around it, no markdown fence.]"
)


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
    crm_block: str | None = None,
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
        # Сначала то, что сделал менеджер, потом его же адресная заметка: заметка написана
        # руками под конкретного лида и должна перебивать общую политику, а не наоборот.
        (crm_block or "").strip(),
        manager_note_block(manager_note) or "",
        (source_block or "").strip(),
        (name_block or "").strip(),
        dossier_block(dossier),
        (first_turn_note or FIRST_TURN_NOTE) if is_first_reply else "",
        _SHAPE_REMINDER,
    ) if block]
    messages: list[dict[str, Any]] = [{"role": "system", "content": stable}]
    if variable:
        messages.append({"role": "system", "content": "\n\n".join(variable)})
    append_dialog(messages, dialog)
    return messages
