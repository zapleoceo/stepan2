"""Pick the LLM capability per turn — the cost lever behind the reply pipeline.

`chat:smart` is the strong-but-scarce model; `chat:fast` is the cheap, effectively
unlimited one. The hybrid policy keeps `smart` for the moments where a subtly worse
decision costs a sale, and routes the cheap majority to `fast`. Reversible per branch via
a single baked-in policy (2026-07-19) — see pick_capability.

A broken `fast` decision is caught downstream (reply.py) and retried once on `smart`, so
`fast` never silently drops a reply — this router only decides the FIRST attempt."""
from __future__ import annotations

import re

from app.domain.enums import Stage

SMART = "chat:smart"
FAST = "chat:fast"

# Every ACTIVE sales stage runs on the strong model (owner directive 2026-07-20: sales quality
# end-to-end over the fast-lane savings). The cheap model's shallow discovery and weak close-
# momentum in mid-`qualifying` was the main drag on reply quality — a warm lead being worked
# deserves the strong model on every turn, not only at the money moments. Only 'new' (pre-first-
# word), followups' first nudge, and non_target wrap-ups still ride the cheap lane.
_DEFAULT_SMART_STAGES = frozenset(
    {"qualifying", "nurturing", "presenting", "objection", "ready"})
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


def pick_capability(
    *, workflow: str, stage: Stage | str | None, lead_type: str | None,
    last_inbound: str,
    followup_attempt: int = 0,
    inbound_count: int = 0,
    guard_regen_count: int = 0,
) -> str:
    """`SMART` (DeepSeek, warm-cached) or `FAST` (free pool) for this turn.

    Policy is baked in (2026-07-19, owner-approved — the old reply_routing/smart_stages
    settings are gone): the strong model handles every SALES-DECISIVE moment; the free pool
    handles only genuinely low-stakes chatter, to save cost. Decisive = the first reply to a
    new lead (the opener decides ~76% of ghosts), any money stage, a hot lead, a buying/phone
    signal, a price question, a menu choice, a soft-no/budget objection, a deep thread, or a
    lead the cheap model already stumbled on. Everything else (neutral mid-discovery, plain
    acknowledgements, off-topic) rides the cheap lane.

    followup_attempt = nudges already sent (0 = first). inbound_count = lead messages so far.
    guard_regen_count = times guard regenerated for this LEAD across their whole history."""
    if workflow == "followup":
        # The first nudge is low-stakes (cheap). From the 2nd on, varying the angle without
        # repeating an earlier opener needs the strong model's instruction-following (chat 1830).
        return SMART if followup_attempt >= 1 else FAST
    # The FIRST reply to a brand-new lead is the single highest-leverage message — the opener
    # is where ~76% of leads ghost (funnel audit 2026-07-19). Never gamble it on the free pool.
    if inbound_count <= 1:
        return SMART
    stage_val = stage.value if isinstance(stage, Stage) else str(stage or "").lower()
    if stage_val in _DEFAULT_SMART_STAGES or lead_type == "hot":
        return SMART
    if guard_regen_count >= _GUARD_REGEN_STICKY_THRESHOLD:
        return SMART  # this lead has already burned a regen once — don't gamble again
    if inbound_count >= _DEEP_CONVERSATION_TURNS:
        return SMART  # deep conversation, real effort invested, regardless of the stage label
    text = last_inbound or ""
    if _BUY_RE.search(text) or _PHONE_RE.search(text):
        return SMART  # buying signal arrived early — don't gamble the close on the cheap model
    # Conversion moments the cheap model fumbles: a price question (framed answer decides the
    # sale), a numbered-menu choice (value+step, not a re-ask), and the objection turn (soft-no
    # / budget) — OBJECTION_HANDLE is the most instruction-heavy nudge in the system.
    from .situations import (  # noqa: PLC0415 (avoid import cycle)
        LOW_BUDGET_RE,
        MENU_REPLY_RE,
        PRICE_QUESTION_RE,
        SOFT_NO_RE,
    )
    if (SOFT_NO_RE.search(text) or LOW_BUDGET_RE.search(text)
            or PRICE_QUESTION_RE.search(text) or MENU_REPLY_RE.match(text.strip())):
        return SMART
    return FAST
