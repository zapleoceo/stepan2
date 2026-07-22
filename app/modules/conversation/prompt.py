"""Pure prompt assembly — no I/O, no branch_id, no hardcoded language.

`build_messages` turns the branch's persona+KB block, optional coaching notes,
and the thread dialog into the chat `messages` array. The model is told to
answer in `lang`; nothing here is tied to a specific language."""
from __future__ import annotations

import re
from datetime import datetime

from app.adapters.db.models import Message

_COACHING_HEADER = "MANDATORY RULES (from manager — follow strictly):"

# How the lead first reached us — shapes the opener. An ad-click lead is warm and already
# picked an offer, so re-asking "what brings you here" wastes the intent; a story-reply is
# a lighter, more casual entry. Organic/unknown gets no hint (no assumptions).
_SOURCE_HINTS = {
    "ad_clicktomsg": (
        "ENTRY: the lead started this chat by tapping one of our paid ads and its prefilled "
        "message — a click showing topic interest, NOT a request to be pitched. Don't ask what "
        "brought them here and don't present the product yet; acknowledge warmly and open with "
        "ONE discovery question about their goal/motivation. Details come after a need surfaces."
    ),
    "story": (
        "ENTRY: the lead replied to our Instagram story — a light, casual entry. Warm up and "
        "build rapport before steering toward an offer."
    ),
}


def source_hint(lead_source: str | None) -> str | None:
    """One-line entry-point instruction for the prompt, or None for organic/unknown leads."""
    return _SOURCE_HINTS.get(lead_source or "")


# IG display names are often the raw @handle ('vibecoding_id', 'user8842') — a digit,
# underscore, dot or @ is the tell. Greeting a lead by a handle reads as a bot.
_HANDLE_TELL = re.compile(r"[0-9_@.]")


def lead_name_hint(display_name: str | None) -> str | None:
    """Deterministic: a clean given name to address the lead by, or None for a handle."""
    name = (display_name or "").strip()
    if not name or _HANDLE_TELL.search(name):
        return None
    first = name.split()[0]
    if not (2 <= len(first) <= 20) or not first.isalpha():
        return None
    return (
        f"LEAD NAME: the lead's name is {first}. Address them by it naturally and sparingly, "
        "like a real salesperson — never force it into every message."
    )


def _role_of(message: Message) -> str:
    return "user" if message.direction == "in" else "assistant"


_MANAGER_NOTE_HEADER = "MANAGER NOTE ON THIS LEAD (follow strictly, overrides your own read):"


def manager_note_block(note: str | None) -> str | None:
    """A manager's PER-LEAD override, unlike CoachingNote (branch-wide rules for every
    lead). The live gap this closes: a manager manually moves a lead back out of READY
    because it isn't actually ready, but nothing stops the model from marking ready=true
    again on the very next turn. A manager writes free text here and it's injected every
    turn until cleared."""
    text = (note or "").strip()
    return f"{_MANAGER_NOTE_HEADER}\n{text}" if text else None


def now_hint(now_local: datetime) -> str:
    """A branch-local 'today is …' line injected into the prompt so the model can reason about
    what's already passed, and never offers a session date in the past."""
    return (
        "CURRENT DATE & TIME (branch-local): "
        f"{now_local:%A, %d %B %Y, %H:%M}. "
        "Any class/batch date at or before this moment has ALREADY passed — never offer a "
        "session in the past, and never invent or guess a date: state one ONLY if the KB "
        "lists it and it's still in the future. If the KB has no confirmed upcoming date, say "
        "the next batch isn't scheduled yet and offer to confirm the schedule with the team."
    )

