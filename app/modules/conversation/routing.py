"""Pick the LLM capability per turn — the cost lever behind the reply pipeline.

`chat:smart` is the strong-but-scarce model; `chat:fast` is the cheap, effectively
unlimited one. The hybrid policy keeps `smart` for the moments where a subtly worse
decision costs a sale, and routes the cheap majority to `fast`. Reversible per branch via
the `reply_routing` setting (`off` → always `smart`, the pre-optimisation behaviour).

A broken `fast` decision is caught downstream (reply.py) and retried once on `smart`, so
`fast` never silently drops a reply — this router only decides the FIRST attempt."""
from __future__ import annotations

import re

from app.domain.enums import Stage

SMART = "chat:smart"
FAST = "chat:fast"

# Default stages where money is on the table — a weaker decision here loses a deal. Operator-
# tunable via the smart_stages setting; this is the fallback when the setting is empty/garbage.
_DEFAULT_SMART_STAGES = frozenset({"presenting", "objection", "ready"})
_ALL_STAGE_VALUES = frozenset(s.value for s in Stage)
# A hot buying signal can land while the lead is still nominally early (e.g. "gimana bayar"
# at qualifying). Catch it with a cheap regex and force smart regardless of the stage.
_BUY_RE = re.compile(
    r"(daftar|enroll|sign\s?up|bayar|pembayaran|transfer|"
    r"\bdp\b|\bdeal\b|ga+s+|mau\s?(ikut|gabung|daftar|bayar)|booking|reserve|payment)",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"\d[\d\s\-]{7,}\d")  # a phone-length digit run = ready-to-hand-off signal


def parse_smart_stages(raw: str | None) -> frozenset[str]:
    """Comma-list setting → validated set of stage names. Unknown tokens are dropped; an empty
    or all-invalid value falls back to the default (never let a typo route money turns to fast)."""
    vals = {t.strip().lower() for t in (raw or "").split(",") if t.strip()}
    valid = vals & _ALL_STAGE_VALUES
    return frozenset(valid) if valid else _DEFAULT_SMART_STAGES


def pick_capability(
    *, workflow: str, stage: Stage | str | None, lead_type: str | None,
    last_inbound: str, mode: str,
    smart_stages: frozenset[str] = _DEFAULT_SMART_STAGES,
) -> str:
    """`SMART` or `FAST` for this turn. mode != 'hybrid' → always SMART (feature off).

    smart_stages = the operator-tunable set of stages that keep the strong model."""
    if mode != "hybrid":
        return SMART
    if workflow == "followup":
        return FAST  # nudging a quiet lead — lowest-stakes traffic, always cheap
    stage_val = stage.value if isinstance(stage, Stage) else str(stage or "").lower()
    if stage_val in smart_stages or lead_type == "hot":
        return SMART
    text = last_inbound or ""
    if _BUY_RE.search(text) or _PHONE_RE.search(text):
        return SMART  # buying signal arrived early — don't gamble the close on the cheap model
    return FAST
