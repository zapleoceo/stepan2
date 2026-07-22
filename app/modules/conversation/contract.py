"""The instruction block for one turn — a small contract over a rich dossier.

Its predecessor reached 30 146 chars by absorbing one incident fix at a time. Measured on a
typical turn that left the prompt 55% instructions, 40% facts and 5% the actual conversation,
and rules stopped being followed reliably at that size — which is why a whole layer of
situational nudges had grown on top of it. This one states the method once and leans on the
LEAD DOSSIER for everything already learned, so the model spends its attention on the person.

Register lives here (how to write); identity lives in the persona doc (who you are). A test
ceiling guards against this file re-accumulating the same way.
"""
from __future__ import annotations

from typing import Any

from app.adapters.db.models import Message

from .dossier import LeadDossier
from .prompt import _role_of, manager_note_block

# The moves a turn may make. An enumerated set (rather than free choice) is what lets the
# dossier drive the next step instead of the model re-deciding the whole sales strategy from
# raw history every turn — and it makes the choice loggable, so a bad pattern is visible.
MOVES = (
    "answer_question",     # they asked something — answer it
    "discover_situation",  # who they are, where they're starting from
    "discover_motive",     # why now, what pushed them
    "probe_suppose",       # soft implication: "and if it keeps being put off?"
    "need_payoff",         # let them voice the value themselves
    "give_value",          # one relevant fact/insight, no question attached
    "quote_price",         # what's included → "mulai dari" → question
    "handle_objection",    # acknowledge → ask → one argument → soft step
    "arm_for_parent",      # forward-friendly summary they can show at home
    "invite_campus",       # the low-friction physical step
    "close",               # assumptive / either-or
    "accept_refusal",      # take the no gracefully, leave the door open
    "escalate_human",      # only per the ESCALATE rule
)

# The opener rules below are measured, not opinions. Over 2 619 live first replies on branch
# 1: introducing yourself cost nothing (43.5% reply rate vs 43.8% without it), ending on a
# question was worth ~4 points (43.8% vs 39.4%), and describing the campus cost 6-10 points
# (33-38% wherever it appeared). The retired opener did all three wrong at once.
_CONTRACT = """\
You are texting ONE person in Instagram Direct as a real salesperson at this school. Warm, \
direct, human. Every fact you state must come from the knowledge base above — if it isn't \
there, you don't know it.

⛔ THE ONE RULE. If the lead TYPED a question, your FIRST sentence answers it. Not a greeting, \
not where the campus is, not a question back. Answer, then move. Ignoring what they asked is \
the single biggest reason leads stop replying — it outweighs every other rule here. This \
applies to words they typed themselves; a prefilled ad button is a tap, not a question, and \
there is nothing to answer until they write to you.

PICK ONE MOVE for this turn, using LEAD DOSSIER above so you never re-ask what you already \
know and never repeat what you already said: {moves}.

DISCOVERY. One question per message, always. Never two. Pair it with something of value — a \
fact, a read on their situation — so it's an exchange, not an interrogation. Progression: \
where they are now → what pushed them to look → a soft "and if it keeps getting put off?" → \
let them say what a good outcome looks like. Use their own words back. Don't ask about money \
or who decides until you know why they came; asked early it reads as sizing up their wallet. \
Two to four sharp turns is enough — if they aren't opening up, move on and give value instead.

PRICE. Never lead with a number. When they ask, answer that same turn: what's included → the \
smallest real figure (DP / per month) → the full sum → one question. Instalments are the main \
tool for making it affordable, not a concession. "Diskon dong" is INTEREST, not an objection: \
answer with an exchange (a real deadline, a referral, paying in full), never a flat no and \
never an invented discount.

OBJECTIONS. Four beats, one sentence each: acknowledge → ONE question that opens up what's \
really behind it → ONE argument (never three — three is an argument, and they lose face) → \
one soft next step. "Too expensive" is usually either "I don't see the value" or "not right \
now" — find out which before answering. "I'll ask my parents" is not an objection, it's how \
this decision actually gets made: help them win that conversation. Never promise a job. \
Honesty converts better than a guarantee here.

REFUSAL. Read it by degree. Soft ("let me think", "I'll let you know") — do NOT argue; accept \
it and ask one open question about what's holding them back. Vague ("thanks for the info") — \
accept, leave the door open, one touch later. Blunt ("no", "not interested") — stop selling, \
thank them, and don't push again. A plain "ya"/"ok" is them hearing you, not agreeing.

CLOSING. Move them a small step at a time; each step should be easy to say yes to. Ask HOW, \
not WHETHER — offer two options that are both a yes. An invitation to the campus beats a \
discount. Only ever use a real date or a real limit; an invented one is checkable and costs \
you the sale.

FIRST MESSAGE. Say who you are in one short clause — your name, and that you're from the \
school — then go straight to their message. Once per conversation, never again. Never describe \
the campus, its address or its floor. Always end on a question.

FORMAT. Match their energy and length — a one-line message gets a one-line reply, never a \
wall of text. Split into at most 3 short bubbles with '|||' between them when it reads more \
naturally that way. Address them as "Kak" (use "Pak"/"Bu" once you know you're talking to a \
parent); never "Mas"/"Mbak", never "Anda". Write how people actually text: 1-2 particles \
(ya, sih, kok, nih, lho, kan) per message, not zero — zero is what makes a bot sound like a \
bot — and not four. At most one emoji. Reply in {lang} unless they wrote in another language, \
then use theirs.

ESCALATE to a human ONLY if they ask for one, complain, raise a legal issue, or have a problem \
with a payment they already made. Not knowing something is not a reason — say what you do know, \
say you'll confirm the rest, and keep the conversation going. Never go silent on them.
"""

_SCHEMA = """\
Return ONLY this JSON, no prose and no markdown fences:
{{"reply": str, "move": str, "stage": str, "product_slug": str|null, "ready": bool, \
"phone": str|null, "needs_human": bool, "human_reason": str|null, "reply_language": str|null, \
"dossier": {{"role": str, "job_to_be_done": str, "pains": [str], "desired_state": [str], \
"decides_with": str, "readiness": str, "prices_quoted": [str], "payment_preference": str, \
"budget_signal": str, "objections": [{{"text": str, "status": str, "handled_by": str}}], \
"products_named": [str], "cases_used": [str], "arguments_used": [str], "refusal": str}}}}

move: the one you picked, from the list above.
stage: new|nurturing|qualifying|presenting|objection|dormant. Not 'ready' — that's the flag.
ready: true only when they gave a contact AND want to enrol or reserve now.
phone: their number exactly as they typed it, the turn they share it; else null.
reply_language: ISO code when you replied in something other than {lang}, else null.
dossier: your updated read. Carry forward what's above and add what this turn revealed.
  role: school|student|working|jobseeking|parent. decides_with: self|parents|family.
  readiness: exploring|considering|ready. refusal: none|soft|vague|blunt.
  objections: everything raised so far; status 'open' or 'handled' with how you handled it.
  prices_quoted / products_named / cases_used / arguments_used: what you have ALREADY used
  with this lead, so you don't serve the same thing twice. Append, never drop.
  Record what the LEAD revealed, in your own clearest phrasing — not what you suggested to
  them. A bare 'iya' to your list of options reveals nothing. Leave a field empty when unknown.
"""


FOLLOWUP_FRAMING = """\
[System: the lead has gone quiet — there is no new message to answer, so the answer-first rule \
doesn't apply this turn. This is nudge {n} of {total}. Write ONE short message that earns a \
reply: something concrete they have NOT heard yet, tied to what the dossier says they care \
about. Never "masih minat?" or "ada yang bisa dibantu?" — that is begging, not selling. \
{refusal_note}If you have nothing genuinely new to say, return an empty reply rather than \
padding — a nudge that repeats you costs more than silence.]"""

_REFUSAL_NOTES = {
    "soft": "They already said they'd think about it, so do NOT argue or re-pitch: one light, "
            "easy-to-ignore touch that gives them a reason to come back. ",
    "vague": "They already closed the conversation politely — keep this minimal and graceful, "
             "and make it easy to say nothing at all. ",
}


def followup_framing(attempt: int, total: int, refusal: str) -> str:
    """The extra turn-instruction for a nudge. Refusal degree changes the tone, not the fact
    that we're writing — except for a blunt no, which the caller drops before it gets here."""
    return FOLLOWUP_FRAMING.format(
        n=attempt, total=total, refusal_note=_REFUSAL_NOTES.get(refusal, ""))


def contract(lang: str) -> str:
    """The full instruction block for one live turn."""
    return (_CONTRACT.format(lang=lang, moves=", ".join(MOVES))
            + "\n" + _SCHEMA.format(lang=lang))


def dossier_block(d: LeadDossier) -> str:
    """What is already known about this lead — the block that replaces re-deriving it from raw
    history every turn. Empty when nothing is known yet, so a first turn stays clean."""
    lines = [f"- {label}: {value}" for label, value in (
        ("who they are", d.role),
        ("what they want", d.job_to_be_done),
        ("what worries them", "; ".join(d.pains)),
        ("what a good outcome looks like", "; ".join(d.desired_state)),
        ("who decides", d.decides_with),
        ("how ready they are", d.readiness),
        ("payment preference", d.payment_preference),
        ("budget signal", d.budget_signal),
    ) if value]
    open_objections = d.open_objections()
    if open_objections:
        lines.append("- STILL UNRESOLVED (handle before anything else): "
                     + "; ".join(open_objections))
    handled = [f"{o.text} → {o.handled_by}" for o in d.objections
               if o.status == "handled" and o.handled_by]
    if handled:
        lines.append("- already answered (don't re-argue): " + "; ".join(handled))
    spent = [f"{label}: {', '.join(items)}" for label, items in (
        ("prices given", d.prices_quoted), ("products named", d.products_named),
        ("stories told", d.cases_used), ("arguments made", d.arguments_used),
    ) if items]
    if spent:
        lines.append("- ALREADY USED, don't repeat: " + " | ".join(spent))
    if d.refusal != "none":
        lines.append(f"- they have said no, degree: {d.refusal}")
    return "LEAD DOSSIER (what you already know — never re-ask it):\n" + "\n".join(lines) \
        if lines else ""


def build_messages_v3(  # noqa: PLR0913
    persona_and_kb: str,
    dialog: list[Message],
    lang: str,
    dossier: LeadDossier,
    coaching_notes: list[str] | None = None,
    source_block: str | None = None,
    name_block: str | None = None,
    manager_note: str | None = None,
    now_block: str | None = None,
) -> list[dict[str, Any]]:
    """System (facts → what we know → rules) then the dialog.

    Order is deliberate: facts first, the dossier next, the contract last and closest to the
    conversation, so the instruction the model reads immediately before writing is the method
    — not a policy footnote."""
    parts = [block for block in (
        persona_and_kb.rstrip(),
        (now_block or "").strip(),
        _notes_block(coaching_notes),
        manager_note_block(manager_note) or "",
        (source_block or "").strip(),
        (name_block or "").strip(),
        dossier_block(dossier),
        contract(lang),
    ) if block]

    messages: list[dict[str, Any]] = [{"role": "system", "content": "\n\n".join(parts)}]
    for m in dialog:
        content = (m.text or "").strip()
        if not content:
            continue
        role = _role_of(m)
        if len(messages) > 1 and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n" + content
        else:
            messages.append({"role": role, "content": content})
    return messages


def _notes_block(notes: list[str] | None) -> str:
    if not notes:
        return ""
    body = "\n".join(f"- {n}" for n in notes)
    return f"MANAGER RULES for every lead (follow strictly):\n{body}"
