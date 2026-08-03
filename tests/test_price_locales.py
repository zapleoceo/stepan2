"""The money gate could only see money written in Indonesian.

A figure was recognised by an "Rp" prefix or a `juta`/`ribu` magnitude and nothing else, so on
any branch that does not sell in rupiah the gate found no figures to compare against the
knowledge base, returned "nothing wrong here", and shipped whatever the model had invented. A
price the school did not set is the single most expensive mistake this bot can make — it is a
promise somebody has to honour — and the check that exists to stop it was a no-op everywhere
except Jakarta.

The first half of this file pins branch 1's parsing exactly as it has always behaved; the
second half is the leak.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.modules.conversation.guard import is_risky, price_claims_grounded  # noqa: E402
from app.modules.conversation.money_gate import money_issues, uninvited_price  # noqa: E402
from app.modules.conversation.prices import canonical_prices, quotes_price  # noqa: E402

_ID_KB = "Vibe Coding: Rp 13.360.000, DP Rp 500.000, cicilan Rp 2.226.000 per bulan"
_EN_KB = "Vibe Coding: $1,500 in total, $250 deposit, or 6 x $250 per month"


# ── branch 1: nothing moved ───────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Rp 1.882.955", {1_882_955}),
    ("Rp2,5 juta", {2_500_000}),
    ("1,67 juta", {1_670_000}),
    ("500 ribu", {500_000}),
    ("500rb", {500_000}),
    ("Kelasnya 6 bulan, seminggu 2 kali", set()),
])
def test_indonesian_figures_canonicalise_exactly_as_before(text: str, expected: set) -> None:
    """The decimal rules are the fiddly part: a magnitude word makes the number a decimal
    count of that unit, and ',' is the Indonesian decimal point."""
    assert canonical_prices(text) == expected


def test_a_bare_number_is_still_not_a_price_on_branch_one() -> None:
    """'16' in "16 sesi" must never be read as sixteen rupiah and matched against the KB."""
    assert canonical_prices("Programnya 37 sesi, 6 bulan") == set()


def test_the_indonesian_gate_still_passes_a_grounded_figure() -> None:
    assert money_issues("Investasinya Rp 13.360.000 kak, DP-nya Rp 500.000", _ID_KB) == []


def test_the_indonesian_gate_still_blocks_an_invented_figure() -> None:
    issues = money_issues("Investasinya Rp 26.000.000 kak", _ID_KB)
    assert issues and "26.000.000" in issues[0]


def test_the_price_quote_signal_is_unchanged_for_branch_one() -> None:
    """quotes_price decides whether an opener is 'leading with money' — a false positive
    there rewrites a perfectly good first message. '500k' was never a trigger and stays out."""
    assert quotes_price("Harganya Rp 500.000")
    assert quotes_price("sekitar 13 juta kak")
    assert not quotes_price("Bisa dicicil kok kak")
    assert not quotes_price("500k")


# ── every other branch: the leak ──────────────────────────────────────────────

def test_a_fabricated_dollar_price_used_to_ship_and_now_does_not() -> None:
    """The whole point of this step. Same draft, same KB, two languages: Indonesian catches
    it, English used to wave it through because it could not see a '$' at all."""
    draft = "The full course is $2,900 - shall I reserve you a seat?"
    assert money_issues(draft, _EN_KB, "en") != []
    assert money_issues(draft, _EN_KB) == []  # what the 'id' parser sees: no figure at all


def test_a_grounded_dollar_price_still_passes_on_an_english_branch() -> None:
    """A gate that blocks everything is as useless as one that blocks nothing — the figures
    that ARE in the knowledge base have to get through."""
    assert money_issues("It's $1,500 in total, or $250 a month over six months", _EN_KB,
                        "en") == []


@pytest.mark.parametrize("text,expected", [
    ("$1,500", {1500}),
    ("USD 2,900", {2900}),
    ("€1.5 million", {1_500_000}),
    ("250 thousand", {250_000}),
    ("Rp 13.360.000", {13_360_000}),  # the fallback stays a superset of the Indonesian shapes
])
def test_english_figures_canonicalise(text: str, expected: set) -> None:
    assert canonical_prices(text, lang="en") == expected


@pytest.mark.parametrize("text", [
    "Please fill in the form 500 characters or less",  # 'rm' hides inside 'form'
    "The course runs 6 months, twice a week",
    "We start on 12 August",
])
def test_ordinary_prose_is_not_read_as_a_price(text: str) -> None:
    """A false positive here is not harmless: it blocks a good reply and, on a first message,
    reads as leading with money — the thing measured to halve the reply rate."""
    assert canonical_prices(text, lang="en") == set()
    assert not quotes_price(text, "en")


def test_an_unlisted_language_gets_the_broad_parser_not_the_indonesian_one() -> None:
    """A Vietnamese branch is not covered by a table row yet, and the fallback must be the
    one that sees the most currencies — not the one that sees only rupiah."""
    assert canonical_prices("The deposit is $250", lang="vi") == {250}


def test_an_english_price_now_routes_the_reply_to_the_fabrication_verify() -> None:
    """is_risky is what sends a draft to the LLM grounding check. An English figure carried
    no Indonesian price word, so the whole verify never ran on a non-Indonesian branch."""
    assert is_risky("The course is $2,900", "en")
    assert not is_risky("The course is $2,900")  # the 'id' vocabulary, blind to it


def test_an_english_price_mixed_up_between_products_is_caught_by_the_public_gate() -> None:
    """price_claims_grounded is the comment path's cheap check; a public price mistake
    screenshots. A figure not in the KB must not read as grounded."""
    assert price_claims_grounded("The price is $1,500", _EN_KB, "en")
    assert not price_claims_grounded("The price is $9,900", _EN_KB, "en")


def test_the_dossier_records_a_dollar_figure_the_bot_just_quoted() -> None:
    """`prices_quoted` is read off the bot's own reply and shown back to it next turn — "the
    prices you already gave them". Blind to dollars, it stayed empty forever on an English
    branch, so the model had no record of its own commitment and the follow-up gate's
    ad-promised-price exemption never closed."""
    from app.modules.conversation.decision import parse_turn_decision  # noqa: PLC0415

    body = '{"reply": "The full course is $1,500.", "needs_human": false}'
    assert parse_turn_decision(body, lang="en").dossier.prices_quoted == ["1.500"]
    assert parse_turn_decision(body).dossier.prices_quoted == []


class _Dossier:
    readiness = "exploring"
    prices_quoted: list[str] = []
    budget_signal = ""
    payment_preference = ""


def test_an_uninvited_dollar_figure_in_a_nudge_is_caught() -> None:
    """A follow-up is never an answer to a fresh question, so a figure in one is always
    volunteered (thread 4849). The check read the Indonesian vocabulary, so on any other
    branch every nudge could quote a price at a lead who never mentioned money."""
    nudge = "Still thinking it over? The course is $1,500 and we start next month."
    assert uninvited_price(nudge, _Dossier(), lang="en")
    assert not uninvited_price(nudge, _Dossier())
