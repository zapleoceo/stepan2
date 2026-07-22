"""The v3 money gate — the only deterministic check that still blocks a send.

v2 had 21 regex checks and not one of them asked whether the reply sells; failing any of them
swapped the answer for a stub (25% reply rate) or a numbered menu. What remains here is only
what costs real money or real trust: a price the KB doesn't contain, a link that doesn't
exist, an invented income claim.
"""
from __future__ import annotations

from app.modules.conversation.guard_v3 import MONEY_CORRECTION, money_issues

_KB = ("Vibe Coding: durasi 6 bulan · harga Rp 13.360.000, DP Rp 500.000, "
       "cicilan Rp 2.226.000 per bulan. Info: https://itstep.id")


def test_a_grounded_price_passes() -> None:
    assert money_issues("Investasinya Rp 13.360.000 kak, DP-nya Rp 500.000", _KB) == []


def test_an_invented_price_is_blocked() -> None:
    """The single most expensive mistake this bot can make — a price the school must honour."""
    issues = money_issues("Investasinya Rp 26.000.000 kak", _KB)
    assert len(issues) == 1
    assert "26.000.000" in issues[0]


def test_a_price_quoted_with_an_empty_knowledge_base_is_blocked() -> None:
    assert money_issues("Harganya Rp 7.000.000", "") != []


def test_magnitude_wording_is_matched_against_the_same_figure() -> None:
    """'Rp 2,5 juta' and '2.500.000' are the same promise."""
    assert money_issues("DP-nya 500 ribu kak", _KB) == []


def test_a_reply_with_no_money_at_all_is_never_blocked() -> None:
    assert money_issues("Halo kak, kelasnya seru banget lho", "") == []
    assert money_issues("Kelasnya 6 bulan, seminggu 2 kali", "") == []


def test_an_ungrounded_link_is_blocked() -> None:
    issues = money_issues("Cek di https://itstep-jakarta.example.com ya kak", _KB)
    assert any("link" in i for i in issues)


def test_a_grounded_link_passes() -> None:
    assert money_issues("Infonya di https://itstep.id kak", _KB) == []


def test_an_invented_income_claim_is_blocked() -> None:
    """A promise about earnings is a trust liability, not a sales flourish."""
    assert money_issues("Alumni kami rata-rata dapat Rp 8.000.000 per bulan", _KB) != []


def test_instalment_wording_is_not_mistaken_for_an_income_claim() -> None:
    assert money_issues("Cicilannya Rp 2.226.000 per bulan kak", _KB) == []


def test_every_issue_is_reported_not_just_the_first() -> None:
    issues = money_issues("Rp 99.000.000, cek https://scam.example.com", _KB)
    assert len(issues) >= 2


def test_the_correction_demands_a_rewrite_never_a_retreat() -> None:
    """v2's corrections let the model fall back to 'I'll check with the team', which is how it
    learned to go quiet on answerable questions."""
    text = MONEY_CORRECTION.format(issues="x")
    assert "do not go silent" in text and "do not hand the lead off" in text
