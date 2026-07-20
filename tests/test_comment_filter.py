"""Triage for public comments — reply only to real questions/interest, hide spam/abuse,
skip noise. Public mistakes are costly (everyone sees them), so the classifier errs toward
skip."""
from __future__ import annotations

import pytest

from app.modules.comments.filter import classify_comment, is_warm


@pytest.mark.parametrize("text", [
    "berapa harganya kak?",
    "kelasnya offline atau online?",
    "mau daftar dong",
    "info dong kak",
    "saya tertarik banget pengen ikut",
    "gimana cara ikutnya?",
])
def test_questions_and_interest_get_a_reply(text: str) -> None:
    action, _ = classify_comment(text)
    assert action == "reply"


@pytest.mark.parametrize("text", [
    "follow akun aku ya @toko",
    "cek dm ada promo slot gacor maxwin",
    "https://bit.ly/cuan",
    "open order jastip dong",
])
def test_spam_is_hidden(text: str) -> None:
    action, reason = classify_comment(text)
    assert action == "hide" and reason == "spam"


@pytest.mark.parametrize("text", ["anjing bot", "penipu nih", "goblok"])
def test_abuse_is_hidden(text: str) -> None:
    action, reason = classify_comment(text)
    assert action == "hide" and reason == "abuse"


@pytest.mark.parametrize("text", [
    "🔥🔥🔥", "first", "mantap", "keren banget", "aamiin", "@budi @ani", "...", "up",
])
def test_noise_is_skipped_not_replied(text: str) -> None:
    action, _ = classify_comment(text)
    assert action == "skip"


def test_plain_statement_without_ask_is_skipped() -> None:
    action, reason = classify_comment("kemarin aku lewat kampusnya")
    assert action == "skip" and reason == "no_question"


def test_warm_intent_is_dm_worthy() -> None:
    assert is_warm("mau daftar dong")
    assert is_warm("saya minat, info caranya")
    assert not is_warm("kelasnya jam berapa?")  # a plain factual question isn't warm-to-DM
