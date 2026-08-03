"""Money figures, read the way the branch's own language writes them.

The money gate is the one check in this system that fails closed: a figure the knowledge base
does not contain never ships, because a price we invent is a promise the school has to honour.
It was also, until 2026-08-03, Indonesian-only. The canonicaliser recognised a sum by an "Rp"
prefix or a `juta`/`ribu` magnitude word and nothing else, so on any other branch
`canonical_prices("The course is $1,500")` returned the empty set, the gate found nothing to
compare against the KB, and the fabricated figure went to the customer unchecked. Every guard
around it — the ad-tap opener strip, the follow-up pitch gate — read the same blind detector.

So the currency vocabulary is DATA, one row per language, and adding a locale is adding a row.
`id` is exactly the pattern that has run in production (same regexes, same decimal rules), and
anything we have no row for falls back to `en`, which is deliberately a superset: it carries
the Indonesian magnitudes too, so an Indonesian figure written on an English branch is still
caught. Over-matching costs a rewrite; under-matching costs a price the school must honour.
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
    `decimal_comma` says which separator is the decimal point: Indonesian writes 2,5 juta.
    """

    figure_re: re.Pattern[str]
    quote_re: re.Pattern[str]
    magnitudes: dict[str, int]
    decimal_comma: bool


_ID_MAGNITUDES = {"juta": 1_000_000, "jt": 1_000_000, "ribu": 1_000, "rb": 1_000, "k": 1_000}

# Indonesian: verbatim the two patterns that have run on branch 1 since the gate was written.
# Note "k" is a magnitude for canonicalisation but NOT a price signal in quote_re — "500k" was
# never treated as leading with money, and this file does not get to change that.
_ID = MoneyLocale(
    figure_re=re.compile(r"(rp\.?\s*)?(\d[\d.,]*)\s*(juta|jt|ribu|rb|k)?\b", re.IGNORECASE),
    quote_re=re.compile(r"\brp\.?\s?\d[\d.,]*|\d[\d.,]*\s?(?:ribu|juta|rb\b)", re.IGNORECASE),
    magnitudes=_ID_MAGNITUDES,
    decimal_comma=True,
)

# The fallback row, and the one every non-Indonesian branch gets today. Bare "m" is left out
# on purpose: "6 m" is six metres far more often than six million, and a magnitude with no
# currency in front of it is enough to make a bare number a price.
#
# The \b matters more than it looks: without it "rm" matches inside "form", and "form 500"
# becomes a price of 500 — which would block a perfectly good reply and, worse, read as
# "leading with money" on a first message. Symbols need no boundary; they are not letters.
_EN_CURRENCY = r"(?:\b(?:rp|idr|usd|eur|gbp|sgd|myr|rm|vnd|php|aud)|[$€£₫])"
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
    decimal_comma=False,
)

_LOCALES: dict[str, MoneyLocale] = {"id": _ID, "en": _EN}
_FALLBACK = _EN

# A digit run long enough to be a sum even with nothing around it — used only on the KB side,
# where extra numbers only make the subset check safer.
_BARE_NUMBER_RE = re.compile(r"\d[\d.,]{3,}")


def locale_for(lang: str | None) -> MoneyLocale:
    return _LOCALES.get((lang or "").lower(), _FALLBACK)


def _parse_money(num: str, mag: str, loc: MoneyLocale) -> int | None:
    """A magnitude word makes the number a DECIMAL count of that unit — '2,5 juta' → 2.5 →
    2_500_000, '1,67 juta' → 1_670_000, '500 ribu' → 500_000 (Indonesian ',' = decimal; in
    English it is '.', and '1.5 million' means the same thing). With NO magnitude word,
    separators are thousands groupers — '1.882.955' → 1_882_955."""
    digits_only = re.sub(r"[^\d]", "", num)
    if not digits_only:
        return None
    if not mag:
        return int(digits_only)
    s = num.replace(",", ".") if loc.decimal_comma else num.replace(",", "")
    parts = s.split(".")
    if len(parts) > 1:
        s = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return int(round(float(s) * loc.magnitudes[mag]))
    except ValueError:
        return int(digits_only) * loc.magnitudes[mag]


def canonical_prices(text: str, *, liberal: bool = False, lang: str = "id") -> set[int]:
    """Every money figure in `text` as a canonical integer — 'Rp 1.882.955' → 1882955,
    'Rp2,5 juta' → 2500000, '$1,500' → 1500 — so a reply figure can be matched against the KB
    regardless of formatting. Strict side: only figures carrying a currency prefix OR a
    magnitude word count (a bare '16' isn't a price). liberal=True (the KB side) also takes
    bare digit runs ('500,000 IDR'): extra KB numbers only make the subset check safer, while
    the REPLY side stays strict money shapes."""
    loc = locale_for(lang)
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


def quotes_price(reply: str, lang: str = "id") -> bool:
    """A concrete money figure appears in the text — the same shape the money gate verifies
    against the KB. Used by the pitch gate as a content-based backstop: the model can mislabel
    its own move (thread 4972 shipped a full price quote tagged `answer_question`), but it
    cannot hide the figure itself."""
    return bool(locale_for(lang).quote_re.search(reply or ""))
