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
- Every fact, price, schedule, link, discount and promise must come from the knowledge base
  above. If it isn't there, you don't know it — say what you DO know and offer to confirm
  the rest. Never invent anything.
- Reply in {lang}; if the lead writes in another language, answer in theirs and stay in it.
- Write like a human in a chat: short messages, at most one question per message, at most 3
  bubbles split by '|||' (usually 1-2), at most one emoji. Match their length and energy.
- Set needs_human=true ONLY when they ask for a human, complain, raise a legal issue, or
  have a problem with a payment they already made. Not knowing something is not a reason —
  and never go silent.
"""

# Same fields as the scripted schema — the pipeline downstream (stage events, hand-off, CRM
# push, follow-ups) reads them — but `move` is the model's own label, not an enumerated set.
_FREE_SCHEMA = """\
Return ONLY this JSON, no prose and no markdown fences:
{{"reply": str, "move": str, "stage": str, "product_slug": str|null, "ready": bool, \
"phone": str|null, "needs_human": bool, "human_reason": str|null, "reply_language": str|null, \
"dossier": {{"role": str, "job_to_be_done": str, "pains": [str], "desired_state": [str], \
"decides_with": str, "readiness": str, "prices_quoted": [str], "payment_preference": str, \
"budget_signal": str, \
"objections": [{{"text": str, "status": str, "handled_by": str, "category": str}}], \
"products_named": [str], "cases_used": [str], "arguments_used": [str], "refusal": str}}}}

move: a short snake_case label YOU choose for what you did this turn (e.g. build_rapport,
  quote_price, close) — free-form, for the log.
stage: new|nurturing|qualifying|presenting|objection|dormant. Not 'ready' — that's the flag.
ready: true only when they gave a contact AND want to enrol or reserve now.
phone: their number exactly as they typed it, the turn they share it; else null.
reply_language: ISO code when you replied in something other than {lang}, else null.
dossier: your updated read of this person. Carry forward what's in LEAD DOSSIER above and
  add what this turn revealed.
  role: school|student|working|jobseeking|parent. decides_with: self|parents|family.
  readiness: exploring|considering|ready. refusal: none|soft|vague|blunt.
  objections: everything raised so far; status 'open' or 'handled' with how you handled it.
  category: price|time|trust|job_outcome|self_study_free|parent_approval, else empty.
  prices_quoted / products_named / cases_used / arguments_used: what you have ALREADY used
  with this lead, so you don't serve the same thing twice. Append, never drop.
  Record what the LEAD revealed, not what you suggested. Leave a field empty when unknown.
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
) -> list[dict[str, Any]]:
    """Stable cached prefix first, then one small per-lead system block, then the dialog.

    messages[0] must stay byte-identical between turns and between leads (same branch +
    language) — it is the broker's prompt-cache anchor. A test pins this invariant."""
    stable = knowledge.rstrip() + "\n\n" + free_contract(lang)
    variable = [block for block in (
        (now_block or "").strip(),
        _notes_block(coaching_notes),
        manager_note_block(manager_note) or "",
        (source_block or "").strip(),
        (name_block or "").strip(),
        dossier_block(dossier),
        FIRST_TURN_NOTE if is_first_reply else "",
    ) if block]
    messages: list[dict[str, Any]] = [{"role": "system", "content": stable}]
    if variable:
        messages.append({"role": "system", "content": "\n\n".join(variable)})
    append_dialog(messages, dialog)
    return messages
