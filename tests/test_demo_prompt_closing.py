"""The landing chat is tuned to close inside the conversation, not to consult politely.

Why this file exists: the demo prompt was originally written in DM logic, where backing off
politely is right because a follow-up can recover the lead later. This page has neither — no
DB, no contact, no scheduled message (see _routes_demo module docstring and _demo_lead). The
only conversion event is the contact capture that notifies the owner, so "ease off and stop
selling" silently meant "lose them". These assertions pin the intent, not the wording; they
guard against a future edit quietly restoring the DM-shaped rules.
"""
from __future__ import annotations

from app.api._routes_demo import _SYSTEM
from app.modules.persona.service import SEED_PERSONAS

_S = _SYSTEM.lower()


def test_the_stated_goal_is_getting_a_contact_in_this_conversation() -> None:
    assert "one goal" in _S
    assert "gone forever" in _S


def test_the_prompt_forbids_claiming_anything_was_sent() -> None:
    """The live failure of 28.07.2026: a visitor asked for material by email and was told
    "готово, скинул на почту". Nothing was sent — there is no email code anywhere in this
    application, and the demo chat has no outbound channel at all. The visitor went to check
    an empty inbox, and no notification reached the owner either.

    The prompt caused it. It said in one breath that the page has no way back to the visitor,
    and in the next to ask them "where should I send the specifics" — the model resolved the
    contradiction by pretending to deliver."""
    assert "you cannot send them anything" in _S
    assert "never say you sent something" in _S
    assert "where should i send it" in _S          # named explicitly as the banned close
    assert "everything you want them to have, you give them here" in _S


def test_the_close_hands_over_to_a_human_rather_than_promising_delivery() -> None:
    assert "reaches a human on the team" in _S
    assert "leave the best way to reach you" in _S


def test_an_unsupported_channel_is_disclosed_before_pitching() -> None:
    """A visitor said they sell on TikTok and was told "great channel" and pitched to.
    TikTok is not connected — only Instagram, WhatsApp and Messenger are."""
    assert "channel honesty" in _S
    assert "tiktok is on the roadmap and not connected yet" in _S


def test_the_contact_is_asked_for_alone() -> None:
    assert "one question, not a list" in _S


def test_contact_is_asked_early_not_only_at_the_end() -> None:
    assert "ask for the contact early" in _S
    assert "at the first sign of interest" in _S


def test_a_soft_no_no_longer_terminates_the_sale() -> None:
    """It used to say: acknowledge, leave the door open, STOP selling — with nothing left to
    recover the lead. Now it must convert the hesitation into one honest answer + an offer."""
    assert "a soft no is not the end" in _S
    assert "stop selling — no extra pitch" not in _S


def test_a_hard_no_still_stops_everything() -> None:
    """Persistence is bounded: pushing past an explicit no is what makes a bot obnoxious."""
    assert "a hard no is a hard no" in _S
    assert "stop selling completely" in _S


def test_persistence_has_an_explicit_ceiling() -> None:
    assert "never ask a third time" in _S
    assert "never pressure" in _S
    assert "manufacture urgency" in _S


def test_small_or_starting_out_is_not_treated_as_a_poor_fit() -> None:
    """Writing off the small ones is expensive when the traffic is paid — and the free tier
    exists precisely for them."""
    assert "is not a poor fit" in _S
    assert "assume they're a real buyer" in _S


def test_discovery_no_longer_gates_the_pitch() -> None:
    assert "one question, then sell" in _S
    assert "don't pitch before you understand the pain" not in _S


def test_price_is_answered_straight_not_held_hostage() -> None:
    assert "never hold the price hostage" in _S


def test_the_honesty_guards_are_untouched() -> None:
    """Loosening the sales rules must never loosen these: a fabricated number costs the deal
    on the first call, and naming the vertical the product grew out of is a privacy leak."""
    assert "never invent numbers, case studies, client names or guarantees" in _S
    assert "never break character" in _S
    assert "never mention or imply any specific real client" in _S


def test_persona_snapshot_tracks_the_runtime_prompt() -> None:
    """The library entry is a browsable copy of the live prompt; a stale copy misleads."""
    demo = next(p for p in SEED_PERSONAS if p["slug"] == "website-demo")
    body = demo["content"].lower()
    assert demo["version"] == "1.2"
    assert "no follow-up on this page" in body
    assert "never ask a third time" in body
    # 1.2: the library copy carried the same "ask where to send it" close that produced the
    # fabricated email, so it had to move with the runtime prompt rather than sit stale.
    assert "you cannot send anything" in body
    assert "never say you sent something" in body
    assert "tiktok is planned and not connected" in body
