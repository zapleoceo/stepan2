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

# A lead this deep into a conversation represents real invested effort — losing a near-closed
# deal to a cheap-model slip costs more than it would have earlier. Not stage-gated: a lead can
# sit in 'qualifying' for many turns of genuine back-and-forth without ever hitting a smart_stage.
# Was 6 — together with the forever-sticky regen threshold it routed 95% of live replies to
# smart (measured 2026-07-12: 558 smart vs 29 fast/24h), defeating the hybrid split. 10 keeps
# the protection for genuinely long threads while returning the mid-length majority to fast.
_DEEP_CONVERSATION_TURNS = 10
# Once guard has repeatedly had to regenerate replies for THIS lead, that's direct evidence the
# cheap model struggles with this specific conversation — a per-lead signal no stage or regex
# can see, since it comes from the LEAD's own history, not this turn's text. Was 1 — but a
# single regen over a whole lead history is noise (any lead who ever tripped one stayed smart
# forever); two+ is a pattern.
_GUARD_REGEN_STICKY_THRESHOLD = 2


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
    followup_attempt: int = 0,
    inbound_count: int = 0,
    guard_regen_count: int = 0,
) -> str:
    """`SMART` or `FAST` for this turn. mode != 'hybrid' → always SMART (feature off).

    smart_stages = the operator-tunable set of stages that keep the strong model.
    followup_attempt = how many nudges already sent in this thread (0 = first nudge).
    inbound_count = how many lead messages so far in this thread — a per-lead depth signal
    independent of funnel stage (a long 'qualifying' back-and-forth is still real investment).
    guard_regen_count = how many times guard has ALREADY had to regenerate a reply for this
    LEAD (across their whole history, not just this thread) — a per-lead reliability signal:
    once the cheap model has stumbled on this specific lead, keep it on smart going forward
    rather than re-rolling the same risk every turn."""
    if mode != "hybrid":
        return SMART
    if workflow == "followup":
        # The first nudge is genuinely low-stakes (cheap model is fine). From the 2nd nudge
        # on, the cheap model was observed repeating an earlier opener near-verbatim (chat
        # 1830: re-greeted the lead and re-asked the same discovery question 2 nudges in) —
        # varying the angle across attempts needs the stronger model's instruction-following.
        return SMART if followup_attempt >= 1 else FAST
    stage_val = stage.value if isinstance(stage, Stage) else str(stage or "").lower()
    if stage_val in smart_stages or lead_type == "hot":
        return SMART
    if guard_regen_count >= _GUARD_REGEN_STICKY_THRESHOLD:
        return SMART  # this lead has already burned a regen once — don't gamble again
    if inbound_count >= _DEEP_CONVERSATION_TURNS:
        return SMART  # deep conversation, real effort invested, regardless of the stage label
    text = last_inbound or ""
    if _BUY_RE.search(text) or _PHONE_RE.search(text):
        return SMART  # buying signal arrived early — don't gamble the close on the cheap model
    return FAST
