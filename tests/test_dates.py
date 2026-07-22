"""Dates are resolved before the model sees them, so it never does the arithmetic.

The live failure: on 23 July, with the knowledge base saying "Sabtu, 8 Agustus 2026", Stepan
invited a lead to the Demo Event "Sabtu ini" — 25 July, a fortnight early, at an empty campus.
"""
from __future__ import annotations

from datetime import date

from app.modules.conversation.dates import EXPIRED_NOTE, annotate_dates

_TODAY = date(2026, 7, 23)  # a Thursday


def test_a_future_date_carries_its_weekday_and_distance() -> None:
    """The exact live case."""
    out = annotate_dates("Demo Event berikutnya: Sabtu, 8 Agustus 2026", _TODAY)
    assert "[hari Sabtu, 16 hari lagi]" in out


def test_the_weekday_is_computed_not_copied() -> None:
    """A card claiming the wrong weekday must not propagate it."""
    out = annotate_dates("acara 10 Agustus 2026", _TODAY)
    assert "hari Senin" in out


def test_today_and_tomorrow_read_naturally() -> None:
    assert "hari ini" in annotate_dates("mulai 23 Juli 2026", _TODAY)
    assert "besok" in annotate_dates("mulai 24 Juli 2026", _TODAY)


def test_a_past_date_is_labelled_so_it_is_never_offered() -> None:
    """Nothing else in the prompt has to remember that this batch already ran."""
    out = annotate_dates("batch 11 Juli 2026 sudah jalan", _TODAY)
    assert EXPIRED_NOTE in out
    assert "hari lagi" not in out


def test_a_date_without_a_year_means_the_next_time_it_comes_round() -> None:
    """An undated '8 Agustus' read in September points at next year, not eight months ago."""
    assert "hari lagi" in annotate_dates("acara 8 Agustus", _TODAY)
    assert EXPIRED_NOTE not in annotate_dates("acara 8 Agustus", date(2026, 9, 1))


def test_every_date_in_a_card_is_resolved() -> None:
    out = annotate_dates("intake 1 September 2026 atau 1 Oktober 2026", _TODAY)
    assert out.count("hari lagi") == 2


def test_annotating_twice_changes_nothing() -> None:
    """A memoized context is reused across a turn's rewrites — brackets must not accumulate."""
    once = annotate_dates("Sabtu, 8 Agustus 2026", _TODAY)
    assert annotate_dates(once, _TODAY) == once


def test_an_impossible_date_is_left_alone() -> None:
    assert annotate_dates("31 Februari 2026", _TODAY) == "31 Februari 2026"


def test_text_without_dates_is_untouched() -> None:
    text = "Vibe Coding 37 sesi, Rp 13.000.000, bisa dicicil 4x."
    assert annotate_dates(text, _TODAY) == text


def test_empty_input_is_safe() -> None:
    assert annotate_dates("", _TODAY) == ""
