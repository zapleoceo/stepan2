"""Phone normalization — pure, no LLM, no I/O. Cross-channel merge key for leads."""
from __future__ import annotations

import re

_MIN_DIGITS = 9
_NON_DIGIT = re.compile(r"\D+")
_RUN = re.compile(r"[\d ()\-+.]{9,}")


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
