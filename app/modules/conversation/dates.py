"""Resolve the dates in the knowledge base before the model ever sees them.

A model asked to turn "Sabtu, 8 Agustus 2026" into conversational Indonesian reaches for a
relative phrase and gets the arithmetic wrong: on 23 July it offered the Demo Event "Sabtu ini"
— two weeks early, so a lead following that invitation arrives at an empty campus.

Telling it not to do that is the weak fix. Measured on this branch, a flat prohibition in the
knowledge base (Open House, banned in capitals in the section that applies to EVERY reply) cut
mentions by ~95% and still leaked 4-9 a day. And the dominant phrase isn't even a weekday:
"minggu ini" outnumbers everything else, so deleting the word "Sabtu" from a card would not
have helped.

So the arithmetic moves to where it is exact. Every date in the assembled context is annotated
in place with the weekday it actually falls on and how far away it is, and anything already
past is marked as such. The model no longer computes — it repeats. That is not a rule it can
break; it is a fact it can only restate."""
from __future__ import annotations

import re
from datetime import date

_MONTHS = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12,
}
# Monday-indexed, matching date.weekday().
_WEEKDAYS = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")

_DATE_RE = re.compile(
    r"\b(?P<day>\d{1,2})\s+(?P<month>" + "|".join(_MONTHS) + r")\b(?:\s+(?P<year>\d{4}))?",
    re.IGNORECASE)

EXPIRED_NOTE = "SUDAH LEWAT — jangan tawarkan"
_ANNOTATED = re.compile(r"\s*\[(?:" + re.escape(EXPIRED_NOTE) + r"|[^\]]*hari[^\]]*)\]")


def annotate_dates(text: str, today: date) -> str:
    """Every date in `text` followed by what day it falls on and how far off it is.

    Idempotent: re-annotating an already-annotated context leaves it unchanged, so a memoized
    context reused across a turn's rewrites doesn't accumulate brackets."""
    if not text:
        return text

    def _replace(m: re.Match[str]) -> str:
        when = _resolve(m, today)
        if when is None:
            return m.group(0)
        return f"{m.group(0)} [{_describe(when, today)}]"

    return _DATE_RE.sub(_replace, _ANNOTATED.sub("", text))


def _resolve(m: re.Match[str], today: date) -> date | None:
    """The date a match refers to. A card that omits the year means the next time it comes
    round, so an undated '8 Agustus' in September points at next year, not eight months ago."""
    day, month = int(m.group("day")), _MONTHS[m.group("month").lower()]
    year = m.group("year")
    try:
        if year:
            return date(int(year), month, day)
        candidate = date(today.year, month, day)
        return candidate if candidate >= today else date(today.year + 1, month, day)
    except ValueError:
        return None  # 31 Februari and friends — leave the text alone


def _describe(when: date, today: date) -> str:
    days = (when - today).days
    if days < 0:
        return EXPIRED_NOTE
    weekday = _WEEKDAYS[when.weekday()]
    if days == 0:
        return f"hari {weekday}, hari ini"
    if days == 1:
        return f"hari {weekday}, besok"
    return f"hari {weekday}, {days} hari lagi"
