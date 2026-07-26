"""Pure prompt assembly — no I/O, no branch_id, no hardcoded language.

`build_messages` turns the branch's persona+KB block, optional coaching notes,
and the thread dialog into the chat `messages` array. The model is told to
answer in `lang`; nothing here is tied to a specific language."""
from __future__ import annotations

import re
from datetime import datetime

from app.adapters.db.models import Message

# How the lead first reached us — shapes the opener. An ad-click lead is warm and already
# picked an offer, so re-asking "what brings you here" wastes the intent; a story-reply is
# a lighter, more casual entry. Organic/unknown gets no hint (no assumptions).
#
# These blocks ride EVERY turn of the thread, not just the first, so each one must describe the
# ENTRY — a past event — and never the present state. The ad hint used to end "…they did not
# ask you anything, so there is nothing to answer yet": true on turn one, false and actively
# harmful on turn ten, where it contradicted the contract's first hard rule (an unanswered
# question outranks everything) on the model's own authority. 1010 ad threads in 30 days went
# past the prefill into real conversation carrying that sentence on every single turn.
_SOURCE_HINTS = {
    "ad_clicktomsg": (
        "ENTRY: this chat opened when the lead TAPPED one of our paid ads. The FIRST message in "
        "the transcript is the ad's own prefilled text, not their words — it tells you which "
        "product drew them and nothing else about them. Everything they have written SINCE is "
        "their own and is answered like any other message."
    ),
    "story": (
        "ENTRY: the lead replied to one of our Instagram stories — a light, casual opening."
    ),
}

# Injected for a lead who walked into the DM themselves — no ad, no story, no product signal.
# Without this the organic entry got NO hint at all, and the model treated it like an ad lead
# minus the product: one shallow question, then drift toward the flagship course. An organic
# lead is the one segment where deep discovery IS the opener: nothing about them is known, and
# whatever brought them here is fresh enough that they typed first.
# The ad lead who TYPED their own first message (cleared/edited the composer prefill) —
# neither the pure-tap hint (its "they did not ask you anything" would be false) nor no hint
# at all (thread 5097: with zero entry context the model pitched twice on an empty dossier).
# The one thing the entry still reveals is the product they tapped.
AD_TYPED_ENTRY_HINT = (
    "ENTRY: the lead arrived from one of our paid ads and TYPED their own first message — "
    "everything they wrote is their real words, and the answer-first rule fully applies. The "
    "ad they tapped tells you which product drew them; anchor your discovery to it (what they "
    "want that skill for, why now) instead of a generic 'what are you looking for'."
)


def ad_typed_entry_hint(product_title: str | None) -> str:
    """AD_TYPED_ENTRY_HINT with the tapped product named — same anchor, typed-entry wording."""
    if not product_title:
        return AD_TYPED_ENTRY_HINT
    return AD_TYPED_ENTRY_HINT + _AD_PRODUCT_ANCHOR.format(product=product_title)

ORGANIC_ENTRY_HINT = (
    "ENTRY: the lead reached out to the DM on their own — no ad, no story reply, no product "
    "signal came with them, so no product direction may be assumed from the entry. Their own "
    "words are the only source you have about what they came for."
)


# The ad's product, restated on every turn. Both ad hints say "the ad tells you which product
# drew them" without ever naming it: the title is passed to ad_tap_note on the FIRST turn only,
# so from turn two the model has no idea which campaign the lead came from. Thread 5361 is what
# that costs — a fresh graduate tapped a Vibe Coding ad, asked six turns later what web work
# involves, and was offered Python Back-End (8 months) and Data Analyst (9 months) while the
# 4-month course he had actually clicked went unmentioned.
_AD_PRODUCT_ANCHOR = (
    " The ad they tapped was for {product} — that is the one thing they chose, so it stays the "
    "default course for this conversation unless they say otherwise; if you steer them "
    "elsewhere, do it because THEY pointed there, not by forgetting where they came in."
)


def source_hint(lead_source: str | None, product_title: str | None = None) -> str | None:
    """One-line entry-point instruction for the prompt, or None for organic/unknown leads.
    `product_title` appends the tapped ad's product so the anchor survives past turn one."""
    hint = _SOURCE_HINTS.get(lead_source or "")
    if hint and product_title and lead_source == "ad_clicktomsg":
        return hint + _AD_PRODUCT_ANCHOR.format(product=product_title)
    return hint


# IG display names are often the raw @handle ('vibecoding_id', 'user8842') — a digit,
# underscore, dot or @ is the tell. Greeting a lead by a handle reads as a bot.
_HANDLE_TELL = re.compile(r"[0-9_@.]")


def clean_first_name(display_name: str | None) -> str | None:
    """A clean given name to address the lead by, or None for a handle/garbage."""
    name = (display_name or "").strip()
    if not name or _HANDLE_TELL.search(name):
        return None
    first = name.split()[0]
    if not (2 <= len(first) <= 20) or not first.isalpha():
        return None
    return first


def lead_name_hint(display_name: str | None) -> str | None:
    """Deterministic: a clean given name to address the lead by, or None for a handle."""
    first = clean_first_name(display_name)
    if first is None:
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

