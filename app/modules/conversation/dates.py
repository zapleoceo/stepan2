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
break; it is a fact it can only restate.

The annotation lands inside the CACHED PREFIX — messages[0], byte-identical for every lead of
a branch — so it had to stop being written in Bahasa Indonesia for everyone. Parsing still
accepts both month spellings whatever the branch speaks (cards and replies are routinely in
different languages); only the words we write back are per-locale, and `id` is exactly what
production has been reading."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_MONTHS = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12,
}
# The cards are written in English ("Next group: August 9, 2026") while the replies are in
# Indonesian — annotating only the Indonesian spelling left every card date unannotated, so
# an expired batch reached the lead as "batch berikutnya 19 Juli" six days after it ran.
_MONTHS_EN = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
# "19 juli 2026" (Indonesian, day first) or "July 19, 2026" (English, month first). Both
# spellings are always parsed, whatever the branch speaks: the cards are written in one
# language and the replies in another, and a date missed here is an expired batch offered live.
_DATE_RE = re.compile(
    r"\b(?P<day>\d{1,2})\s+(?P<month>" + "|".join(_MONTHS) + r")\b(?:\s+(?P<year>\d{4}))?"
    r"|\b(?P<month_en>" + "|".join(_MONTHS_EN) + r")\s+(?P<day_en>\d{1,2})"
    r"(?:,?\s+(?P<year_en>\d{4}))?",
    re.IGNORECASE)


@dataclass(frozen=True)
class DateLocale:
    """The words an annotation is written in. Parsing is shared; only the OUTPUT is per
    language, because this text goes into the cached prefix the model reads."""

    weekdays: tuple[str, ...]  # Monday-indexed, matching date.weekday()
    expired: str
    today: str                 # formatted with {weekday}
    tomorrow: str              # formatted with {weekday}
    ahead: str                 # formatted with {weekday} and {days}
    # Regex source matching a bracket this locale plausibly wrote, looser than what we emit
    # (the model copies our markers into its own text and does not copy them exactly, and
    # sanitize.clean_reply is the last chance to keep one out of a lead's message) — but not
    # so loose that it eats a bracket the model wrote for the LEAD. strip_date_annotations
    # runs on EVERY branch's outgoing bubbles, so the English clause is a live regex on
    # branch 1 too: "Promo [today only] Kak" must survive it. Anchoring on the weekday +
    # comma our own format always emits is what keeps it off ordinary prose.
    loose: str


_ID = DateLocale(
    weekdays=("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"),
    expired="SUDAH LEWAT — jangan tawarkan",
    today="hari {weekday}, hari ini",
    tomorrow="hari {weekday}, besok",
    ahead="hari {weekday}, {days} hari lagi",
    loose=r"[^\]]*hari[^\]]*",
)
_EN_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_EN = DateLocale(
    weekdays=_EN_WEEKDAYS,
    expired="ALREADY PAST — do not offer",
    today="{weekday}, today",
    tomorrow="{weekday}, tomorrow",
    ahead="{weekday}, in {days} days",
    loose=r"[^\]]*\b(?:" + "|".join(_EN_WEEKDAYS) + r"),[^\]]*",
)
_LOCALES: dict[str, DateLocale] = {"id": _ID, "en": _EN}
_FALLBACK = _EN

# Kept as a module constant because the tests name it, and what they pin is the Indonesian
# branch whose expired batch produced the whole rule.
EXPIRED_NOTE = _ID.expired

_ANNOTATED = re.compile(
    r"\s*\[(?:"
    + "|".join(alt for loc in _LOCALES.values()
               for alt in (re.escape(loc.expired), loc.loose))
    + r")\]")


def locale_for(lang: str | None) -> DateLocale:
    return _LOCALES.get((lang or "").lower(), _FALLBACK)


def strip_date_annotations(text: str) -> str:
    """Remove the markers this module adds — for outgoing text, which must never carry them.

    Strips EVERY locale's markers, not just the branch's own: a thread whose language changed
    mid-conversation can carry a context annotated one way and a reply written the other."""
    return _ANNOTATED.sub("", text or "")


def annotate_dates(text: str, today: date, lang: str = "id") -> str:
    """Every date in `text` followed by what day it falls on and how far off it is.

    Idempotent: re-annotating an already-annotated context leaves it unchanged, so a memoized
    context reused across a turn's rewrites doesn't accumulate brackets.

    This text lands inside the CACHED PREFIX — messages[0], identical for every lead of a
    branch — so it must be written in the branch's own language, not in Bahasa Indonesia on a
    branch that has never spoken it. Default 'id' keeps branch 1 byte-identical."""
    if not text:
        return text
    loc = locale_for(lang)

    def _replace(m: re.Match[str]) -> str:
        when = _resolve(m, today)
        if when is None:
            return m.group(0)
        return f"{m.group(0)} [{_describe(when, today, loc)}]"

    return _DATE_RE.sub(_replace, _ANNOTATED.sub("", text))


def _resolve(m: re.Match[str], today: date) -> date | None:
    """The date a match refers to. A card that omits the year means the next time it comes
    round, so an undated '8 Agustus' in September points at next year, not eight months ago."""
    if m.group("month_en"):
        day, month = int(m.group("day_en")), _MONTHS_EN[m.group("month_en").lower()]
        year = m.group("year_en")
    else:
        day, month = int(m.group("day")), _MONTHS[m.group("month").lower()]
        year = m.group("year")
    try:
        if year:
            return date(int(year), month, day)
        candidate = date(today.year, month, day)
        return candidate if candidate >= today else date(today.year + 1, month, day)
    except ValueError:
        return None  # 31 Februari and friends — leave the text alone


def _describe(when: date, today: date, loc: DateLocale) -> str:
    days = (when - today).days
    if days < 0:
        return loc.expired
    weekday = loc.weekdays[when.weekday()]
    if days == 0:
        return loc.today.format(weekday=weekday)
    if days == 1:
        return loc.tomorrow.format(weekday=weekday)
    return loc.ahead.format(weekday=weekday, days=days)
