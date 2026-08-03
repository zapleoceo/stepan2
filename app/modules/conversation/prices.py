"""Money figures, read the way the branch's own language writes them.

The money gate is the one check in this system that fails closed: a figure the knowledge base
does not contain never ships, because a price we invent is a promise the school has to honour.
It was also, until 2026-08-03, Indonesian-only. The canonicaliser recognised a sum by an "Rp"
prefix or a `juta`/`ribu` magnitude word and nothing else, so on any other branch
`canonical_prices("The course is $1,500")` returned the empty set, the gate found nothing to
compare against the KB, and the fabricated figure went to the customer unchecked. Every guard
around it — the ad-tap opener strip, the follow-up pitch gate — read the same blind detector.

So the currency vocabulary is DATA, one row per language, and adding a locale is adding a row.
`id` is exactly the pattern that has run in production (same regexes), and anything we have no
row for falls back to `en`, which is deliberately a superset: it carries the Indonesian
magnitudes too, so an Indonesian figure written on an English branch is still caught.
Over-matching costs a rewrite; under-matching costs a price the school must honour.

The row is chosen by the BRANCH's language, not by the language the reply happens to be
written in. Currency is a property of the market, not of the sentence: a lead who writes to
branch 1 in Russian is still quoted rupiah out of an Indonesian knowledge base, and reading
that figure with another market's rules is how "Rp2,5 juta" becomes twenty-five million.
Callers name the argument `money_lang` for that reason.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MoneyLocale:
    """How one language writes a sum of money.

    `figure_re` finds a candidate for canonicalisation: an optional currency prefix, the
    digits, an optional magnitude word. `quote_re` answers the cheaper question — does this
    text name a concrete sum at all — and is deliberately narrower (it is what decides whether
    an opener is 'leading with money', where a false positive silences a good message).

    There is no decimal-separator setting. Which of '.' and ',' is the decimal point is not a
    property a caller can be trusted to get right (see _to_number) — it is readable off the
    digits themselves, and reading it off the digits is what makes the same figure parse to the
    same number no matter which row is asked.
    """

    figure_re: re.Pattern[str]
    quote_re: re.Pattern[str]
    magnitudes: dict[str, int]


_ID_MAGNITUDES = {"juta": 1_000_000, "jt": 1_000_000, "ribu": 1_000, "rb": 1_000, "k": 1_000}

# Indonesian: verbatim the two patterns that have run on branch 1 since the gate was written.
# Note "k" is a magnitude for canonicalisation but NOT a price signal in quote_re — "500k" was
# never treated as leading with money, and this file does not get to change that.
_ID = MoneyLocale(
    figure_re=re.compile(r"(rp\.?\s*)?(\d[\d.,]*)\s*(juta|jt|ribu|rb|k)?\b", re.IGNORECASE),
    quote_re=re.compile(r"\brp\.?\s?\d[\d.,]*|\d[\d.,]*\s?(?:ribu|juta|rb\b)", re.IGNORECASE),
    magnitudes=_ID_MAGNITUDES,
)

# The fallback row, and the one every non-Indonesian branch gets today. Bare "m" is left out
# on purpose: "6 m" is six metres far more often than six million, and a magnitude with no
# currency in front of it is enough to make a bare number a price.
#
# The \b matters more than it looks: without it "rm" matches inside "form", and "form 500"
# becomes a price of 500 — which would block a perfectly good reply and, worse, read as
# "leading with money" on a first message. Symbols need no boundary; they are not letters.
#
# PHP is NOT here, and that is the point of this comment. It is the Philippine peso's ISO code
# and it is also the language this school teaches: on branch 10 (Philippines, lang=en) the live
# knowledge base says "PHP/Laravel" and "PHP/CodeIgniter's OOP" — checked in production, no
# peso figure anywhere; every branch quotes rupiah. With `php` in this list "we cover PHP 8.3"
# canonicalised to a price of 8.3, the money gate found it absent from the KB, and a correct
# answer about a backend course became the hold-line plus a hand-off. A three-letter code that
# collides with a product we sell has to earn its place, and this one cannot. The peso is
# reachable by its symbol, which collides with nothing; a branch that must write the code
# instead gets its own row, with the version-number problem solved there.
_EN_CURRENCY = r"(?:\b(?:rp|idr|usd|eur|gbp|sgd|myr|rm|vnd|aud)|[$€£₫₱])"
_EN_MAGNITUDES = {
    "million": 1_000_000, "millions": 1_000_000, "mio": 1_000_000, "mn": 1_000_000,
    "thousand": 1_000, "thousands": 1_000, "k": 1_000, **_ID_MAGNITUDES,
}
_EN = MoneyLocale(
    figure_re=re.compile(
        rf"({_EN_CURRENCY}\.?\s*)?(\d[\d.,]*)\s*"
        r"(millions|million|thousands|thousand|juta|ribu|mio|mn|jt|rb|k)?\b",
        re.IGNORECASE),
    quote_re=re.compile(
        rf"{_EN_CURRENCY}\.?\s?\d[\d.,]*"
        r"|\d[\d.,]*\s?(?:millions|million|thousands|thousand|juta|ribu|mio|mn|rb\b)",
        re.IGNORECASE),
    magnitudes=_EN_MAGNITUDES,
)

_LOCALES: dict[str, MoneyLocale] = {"id": _ID, "en": _EN}
_FALLBACK = _EN

# A digit run long enough to be a sum even with nothing around it — used only on the KB side,
# where extra numbers only make the subset check safer.
_BARE_NUMBER_RE = re.compile(r"\d[\d.,]{3,}")


def locale_for(money_lang: str | None) -> MoneyLocale:
    """The row for a MARKET's language — the branch's, never a single reply's."""
    return _LOCALES.get((money_lang or "").lower(), _FALLBACK)


_GROUP_LEN = 3  # a thousands group is exactly three digits, in every locale that has them
_SEPARATORS = re.compile(r"[.,]")


def _scaled(groups: list[str], factor: int) -> int:
    """Everything but the last group, then a decimal point, then the last — times `factor`,
    rounded to the nearest integer, ties to even.

    Integer arithmetic rather than float because this gate fails closed and therefore must not
    raise: `float("9" * 400 + ".5")` is infinity, and int(round(inf)) is an OverflowError that
    propagates out of the gate and kills the turn. The knowledge base is long enough to carry a
    digit run like that. Ties-to-even is what round() did before, kept so the reading of a real
    figure does not move."""
    fraction = groups[-1]
    unit = 10 ** len(fraction)
    whole, rest = divmod(int("".join(groups[:-1]) + fraction) * factor, unit)
    if 2 * rest > unit or (2 * rest == unit and whole % 2):
        whole += 1
    return whole


def _parse_money(num: str, mag: str, loc: MoneyLocale) -> int | None:
    """One figure's digits, read without being told which separator is the decimal point.

    That used to be a per-locale flag, and a flag is exactly the wrong shape for it: the same
    rupiah string then parsed to two different numbers depending on which row was asked (a
    lead who typed one Cyrillic word moved branch 1 to the fallback row and "Rp2,5 juta"
    became 25_000_000), and the English row's answer — separators are ALWAYS groupers — ate
    the cents off "$1,500.00" and returned 150_000. The digits answer both questions.

    WITH a magnitude word the number is a COUNT of that unit and the last separator is a
    decimal point: '2,5 juta' → 2.5, '1.5 million' → 1.5, '3.250 juta' → 3.25 (measured: 79 of
    branch 1's live installment quotes are written '4× Rp 3.250.000' and one 'Rp 3.250 juta' —
    reading that one as three thousand two hundred and fifty MILLION would hold a correct
    reply). Nobody writes a count of millions with thousands grouping.

    WITHOUT one, separators are groupers — except that a thousands group is exactly three
    digits, so a trailing group of one or two cannot be one and is the cents: '1.882.955' →
    1_882_955, '$1,500.00' → 1500. Four or more keeps the old all-digits reading rather than
    inventing a new one."""
    groups = [g for g in _SEPARATORS.split(num.strip(".,")) if g]
    if not groups or not all(g.isdigit() for g in groups):
        return None
    if mag:
        factor = loc.magnitudes[mag]
        return int(groups[0]) * factor if len(groups) == 1 else _scaled(groups, factor)
    if len(groups) > 1 and len(groups[-1]) < _GROUP_LEN:
        return _scaled(groups, 1)
    return int("".join(groups))


def canonical_prices(text: str, *, liberal: bool = False, money_lang: str = "id") -> set[int]:
    """Every money figure in `text` as a canonical integer — 'Rp 1.882.955' → 1882955,
    'Rp2,5 juta' → 2500000, '$1,500' → 1500 — so a reply figure can be matched against the KB
    regardless of formatting. Strict side: only figures carrying a currency prefix OR a
    magnitude word count (a bare '16' isn't a price). liberal=True (the KB side) also takes
    bare digit runs ('500,000 IDR'): extra KB numbers only make the subset check safer, while
    the REPLY side stays strict money shapes."""
    loc = locale_for(money_lang)
    out: set[int] = set()
    for m in loc.figure_re.finditer(text or ""):
        mag = (m.group(3) or "").lower()
        if not m.group(1) and not mag:
            continue  # bare number, no currency and no magnitude word — not a price
        val = _parse_money(m.group(2), mag, loc)
        if val is not None:
            out.add(val)
    if liberal:
        for m in _BARE_NUMBER_RE.finditer(text or ""):
            digits = re.sub(r"[^\d]", "", m.group(0))
            if digits:
                out.add(int(digits))
    return out


def quotes_price(reply: str, money_lang: str = "id") -> bool:
    """A concrete money figure appears in the text — the same shape the money gate verifies
    against the KB. Used by the pitch gate as a content-based backstop: the model can mislabel
    its own move (thread 4972 shipped a full price quote tagged `answer_question`), but it
    cannot hide the figure itself."""
    return bool(locale_for(money_lang).quote_re.search(reply or ""))
