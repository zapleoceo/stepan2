"""Reject a broker translation/label that drifted to the wrong script.

A provider sometimes returns Arabic/CJK instead of the requested target (ru/en/id) — those
languages never use those scripts, so any occurrence means the output is wrong and must not
be cached. Used by needs translation (lead needs display) and needs-cloud label i18n."""
from __future__ import annotations

import re

_FORBIDDEN = re.compile(
    r"[؀-ۿݐ-ݿࢠ-ࣿ"   # Arabic (+ supplements)
    r"一-鿿぀-ヿ가-힯"     # CJK / Kana / Hangul
    r"֐-׿]"                              # Hebrew
)


def wrong_script(text: str, lang: str) -> bool:  # noqa: ARG001 — lang kept for future per-lang rules
    """True when `text` contains a script no ru/en/id translation ever uses (Arabic/CJK/Hebrew)
    — the broker occasionally drifts there and the result must not be cached. Kept deliberately
    conservative (forbidden-scripts only) so a legitimate Latin abbreviation in a label — 'AI',
    'IT', 'SMM' — is never falsely rejected."""
    return bool(text) and bool(_FORBIDDEN.search(text))
