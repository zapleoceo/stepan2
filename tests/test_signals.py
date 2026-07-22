"""Inbound signals — what a message IS, independent of how the bot answers it.

These outlived the situational-nudge cascade because other modules genuinely need them:
ingest must not treat a business auto-responder as a lead, the comment triage decides whether
a public comment is worth answering, the digest separates ad prefill from the lead's own
words, and the follow-up timer honours an explicit "ask me in two weeks".
"""
from __future__ import annotations

import pytest

from app.modules.conversation.signals import (
    AD_TEMPLATE_RE,
    BUYING_SIGNAL_RE,
    DISCOVERY_TURN_CAP,
    PAYMENT_INTENT_RE,
    PRICE_QUESTION_RE,
    SOFT_NO_RE,
    is_answerable_question,
    is_auto_reply,
    postpone_days,
)


@pytest.mark.parametrize("text", [
    "Terima kasih telah menghubungi kami. Kami akan membalas pesan Anda segera.",
    "Terima kasih telah menghubungi kami. Kami akan segera membalas.",
])
def test_a_business_auto_responder_is_not_a_lead(text: str) -> None:
    """thread 2503: the bot spent a turn asking a robot about its career goals."""
    assert is_auto_reply(text)


@pytest.mark.parametrize("text", ["halo kak", "mau tanya dong", "berapa harganya?"])
def test_a_real_person_is_not_mistaken_for_a_robot(text: str) -> None:
    assert not is_auto_reply(text)


def test_the_ads_prefilled_opener_is_recognised_as_a_button_tap() -> None:
    """It is the ad's copy, never the lead's own words — the digest and the dossier both
    depend on telling them apart."""
    assert AD_TEMPLATE_RE.match("Halo, saya ingin tahu detail program dan biaya kursusnya")
    # The exact prefill behind 71% of live openers on 2026-07-22.
    assert AD_TEMPLATE_RE.match("Halo! Tertarik kursus. Boleh info jadwal, durasi, dan biaya?")


def test_a_typed_question_is_answerable() -> None:
    assert is_answerable_question("apa aja materinya kak?")
    assert is_answerable_question("berapa lama durasinya")


def test_a_bare_acknowledgement_is_not_a_question() -> None:
    assert not is_answerable_question("iya")
    assert not is_answerable_question("oke deh makasih")


def test_money_and_intent_signals_are_distinguishable() -> None:
    assert PRICE_QUESTION_RE.search("berapa biayanya kak?")
    assert PAYMENT_INTENT_RE.search("no rek nya berapa ya")
    assert BUYING_SIGNAL_RE.search("mau daftar kak")


def test_a_polite_indonesian_no_is_recognised() -> None:
    """Indonesian rarely refuses outright; the follow-up timer has to read the soft form."""
    assert SOFT_NO_RE.search("saya pikir-pikir dulu ya")
    assert SOFT_NO_RE.search("nanti dulu ya kak")


def test_an_explicit_postponement_is_converted_to_days() -> None:
    assert postpone_days("nanti 2 minggu lagi") == 14
    assert postpone_days("bulan depan aja kak") is not None


def test_a_vague_later_names_no_date() -> None:
    assert postpone_days("nanti aja") is None


def test_the_discovery_cap_is_a_real_bound() -> None:
    """Live threads showed an unbounded discovery turning into an interrogation."""
    assert 2 <= DISCOVERY_TURN_CAP <= 6
