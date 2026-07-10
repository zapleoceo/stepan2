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


def _canonical(digits: str, cc: str) -> str | None:
    """digits (no separators) → '+<cc>…' E.164 for country `cc`, or None if it can't be
    stamped for that country. Single source of truth for the trunk-0 / bare-local / already-
    prefixed cases, shared by extract_phone (free-text mining) and to_e164 (a known number)."""
    if digits.startswith("0"):
        digits = cc + digits[1:]
    elif not digits.startswith(cc):
        digits = cc + digits  # a bare local number (no trunk 0, no country code)
    return "+" + digits if digits.startswith(cc) else None


def to_e164(raw: str | None, country_code: str = _DEFAULT_CC) -> str | None:
    """Canonicalize an ALREADY-IDENTIFIED phone string (e.g. one the LLM pulled out) to
    '+<cc>…' E.164 for the branch's country. Unlike extract_phone it does not shape-match
    free text — the caller already decided this is a phone — but it shares the SAME country
    stamping so a chat-typed 01x/09x number on a MY/PH branch becomes +60/+63, not +62.
    Returns None if the digit count is implausible (a stray number isn't a phone)."""
    if not raw:
        return None
    cc = (country_code or _DEFAULT_CC).strip() or _DEFAULT_CC
    had_plus = raw.strip().startswith("+")
    digits = _NON_DIGIT.sub("", raw)
    if not (8 <= len(digits) <= 15):  # E.164 caps at 15; below 8 isn't a real number
        return None
    if had_plus:  # an explicit international number — trust it as-is
        return "+" + digits
    return _canonical(digits, cc)


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
    return _canonical(digits, cc)
