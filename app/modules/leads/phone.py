"""Phone normalization — pure, no LLM, no I/O. Cross-channel merge key for leads."""
from __future__ import annotations

import re

_MIN_DIGITS = 9
_NON_DIGIT = re.compile(r"\D+")
_RUN = re.compile(r"[\d ()\-+.]{9,}")

# Indonesian mobile shape in free text: +62/62/0 prefix (optional separator), 8x mobile,
# long digit tail. Prices (Rp 1.200.000) never start with this prefix → no false matches.
_SHAPED = re.compile(r"(?:\+?62|0)[\s.\-]?8[1-9][\d ()\-.]{6,}")


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

    `country_code` (the branch's own, default Indonesia "62") is the trunk prefix applied to
    a local `0…` number, so a non-Indonesian branch no longer stamps its leads as +62. The
    shape detector is tuned for Indonesian mobiles; a number that doesn't match it returns
    None — never mis-prefixed — which keeps the cross-branch merge safe until per-country
    shapes are added."""
    if not text:
        return None
    cc = (country_code or "62").strip() or "62"
    match = _SHAPED.search(text)
    digits = normalize_phone(match.group(0)) if match else None
    if not digits:
        return None
    if digits.startswith("0"):
        digits = cc + digits[1:]
    elif digits.startswith("8"):
        digits = cc + digits
    return "+" + digits if digits.startswith(cc) else None
