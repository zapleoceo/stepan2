"""Phone normalization — pure, no LLM, no I/O. Cross-channel merge key for leads."""
from __future__ import annotations

import re

_MIN_DIGITS = 9
_NON_DIGIT = re.compile(r"\D+")
_RUN = re.compile(r"[\d ()\-+.]{9,}")

# Per-country mobile shape in free text: (+cc | cc | 0) prefix, the national mobile lead
# digit(s), then a long digit tail. A local price (Rp 1.200.000) never matches these →
# no false positives. The mobile lead digit differs per country, so the wrong-country
# shape simply doesn't match (returns None — never a mis-prefixed number).
#   62 Indonesia  → 8[1-9]…  (unchanged, proven on the live branch)
#   60 Malaysia   → 1[0-9]…  (01x-xxxx-xxxx)
#   63 Philippines→ 9…        (09xx-xxx-xxxx)
_SHAPE_BY_CC: dict[str, re.Pattern[str]] = {
    "62": re.compile(r"(?:\+?62|0)[\s.\-]?8[1-9][\d ()\-.]{6,}"),
    "60": re.compile(r"(?:\+?60|0)[\s.\-]?1[0-9][\d ()\-.]{6,}"),
    "63": re.compile(r"(?:\+?63|0)[\s.\-]?9[\d ()\-.]{8,}"),
}
_DEFAULT_CC = "62"


def normalize_phone(raw: str) -> str | None:
    """Extract a phone and return international digits-only; None if no >=9-digit run.

    Strips +, -, spaces, dots and brackets, then joins the digits — a grouped number
    (`+62 812-3456-7890`) yields the same key as its compact form, which is what the
    cross-channel merge relies on.
    """
    if not raw:
        return None
    run = max(_RUN.findall(raw), key=len, default="")
    digits = _NON_DIGIT.sub("", run)
    return digits if len(digits) >= _MIN_DIGITS else None


def extract_phone(text: str | None, country_code: str = "62") -> str | None:
    """Pull a mobile from free text, canonicalized to +<cc>…E.164; None if none.

    `0812…`, `62812…` and `+62 812…` all yield the same `+62…` key so the same person
    merges into one lead across channels. The canonical form is both stored on the lead
    and used for lookup — the only writer/reader of phone_e164.

    `country_code` (the branch's own, default Indonesia "62") picks the mobile SHAPE to
    look for and the trunk prefix applied to a local `0…` number, so a Malaysian/Philippine
    branch matches its own numbers and stamps them +60/+63, not +62. A country without a
    known shape (or a number that doesn't fit its shape) returns None — never mis-prefixed —
    keeping the cross-branch merge safe."""
    if not text:
        return None
    cc = (country_code or _DEFAULT_CC).strip() or _DEFAULT_CC
    shape = _SHAPE_BY_CC.get(cc)
    if shape is None:
        return None  # unknown country — don't guess a number out of free text
    match = shape.search(text)
    digits = normalize_phone(match.group(0)) if match else None
    if not digits:
        return None
    if digits.startswith("0"):
        digits = cc + digits[1:]
    elif not digits.startswith(cc):
        digits = cc + digits  # a bare local number (no trunk 0, no country code)
    return "+" + digits if digits.startswith(cc) else None
