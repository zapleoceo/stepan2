"""The weekly audit's two counting rules, pinned.

The report is propose-only, so its only product is a number the owner acts on. Both bugs below
shipped in the first live report: they did not break anything, they pointed the owner at the
wrong week's worst class.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from datetime import date, timedelta  # noqa: E402

import pytest  # noqa: E402

from app.modules.learning.audit import _CHECKS, _real_questions  # noqa: E402

_DATE_CHECK = next(fn for name, fn in _CHECKS if name == "протухшая дата")
_QUESTION_CHECK = next(fn for name, fn in _CHECKS if name == "2+ вопроса")


def test_a_date_is_judged_on_the_day_it_was_sent() -> None:
    """stale_dates defaults to TODAY. Feeding it a week-old message meant every correct
    announcement of a now-past event counted as a violation — 85 of them in the first report,
    and not one had been wrong when it shipped."""
    event = date.today() - timedelta(days=2)
    months = ("Januari", "Februari", "Maret", "April", "Mei", "Juni",
              "Juli", "Agustus", "September", "Oktober", "November", "Desember")
    body = f"Demo Event-nya {event.day} {months[event.month - 1]} ya Kak!"
    sent_before = event - timedelta(days=3)
    assert not _DATE_CHECK(body, sent_before)  # correct when it was sent
    assert _DATE_CHECK(body, date.today())  # the same words sent today ARE stale


@pytest.mark.parametrize("body", [
    "Kakak tanya soal biaya SMM ya? Programnya 2 minggu, Rp 1.882.955.",
    "Kakak datang dari iklan Vibe Coding kan? Ini program 4 bulan.",
])
def test_a_softened_statement_is_not_a_question(body: str) -> None:
    """A sentence-final ya/kan confirms context, it does not ask for an answer. Counting raw
    '?' inflated the worst class by 28% (235 flagged, 184 earned it)."""
    assert _real_questions(body) <= 1
    assert not _QUESTION_CHECK(body, date.today())


def test_two_things_asked_at_once_still_counts() -> None:
    body = ("Boleh tau lagi cari skill buat apa? Terus Kakak lebih nyaman kelas malam "
            "atau siang?")
    assert _real_questions(body) == 2
    assert _QUESTION_CHECK(body, date.today())
