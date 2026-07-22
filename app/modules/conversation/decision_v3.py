"""The v3 model answer: one reply, the move it chose, and its updated read of the lead.

v2 asked the model for 18 flat fields and then rebuilt the lead's state from raw history each
turn. v3 asks for the reply plus a dossier delta, so state accumulates instead of being
re-derived — and the chosen move is returned explicitly, which makes the sales step a logged
fact rather than something inferred from the text afterwards.

Adapting back to the legacy Decision keeps the whole downstream (enqueue, stage events,
hand-off, outbox) engine-agnostic: v3 replaces how a reply is produced and judged, not the
plumbing that delivers it."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.domain.enums import Stage

from .decision import Decision, coerce_stage, str_list, strip_fences
from .dossier import LeadDossier, Objection
from .prompt_v3 import MOVES
from .sanitize import clean_reply

logger = logging.getLogger(__name__)

_MAX_OBJECTIONS = 8


@dataclass(frozen=True)
class DecisionV3:
    """What the model decided this turn."""

    reply: str
    move: str
    stage: Stage
    dossier: LeadDossier = field(default_factory=LeadDossier)
    product_slug: str | None = None
    ready: bool = False
    phone: str | None = None
    needs_human: bool = False
    human_reason: str | None = None
    reply_language: str | None = None

    def to_legacy(self, merged: LeadDossier) -> Decision:
        """A Decision the existing pipeline can carry, populated from the merged dossier.

        `merged` (not self.dossier) is passed in so the legacy fields reflect everything known
        about the lead, not just what this one turn added."""
        return Decision(
            reply=self.reply,
            stage=self.stage,
            product_slug=self.product_slug,
            ready=self.ready,
            needs_manager=self.needs_human,
            manager_question=self.human_reason,
            kb_gap=self.human_reason,
            ready_subtype="deal" if self.ready else None,
            lead_type=_lead_type_of(merged),
            audience=_audience_of(merged),
            reply_language=self.reply_language,
            phone=self.phone,
            jobs=[merged.job_to_be_done] if merged.job_to_be_done else [],
            pains=list(merged.pains),
            gains=list(merged.desired_state),
            discovery_complete=merged.has_discovery(),
            open_objections=merged.open_objections(),
            hard_stop=merged.refusal == "blunt",
        )


async def generate(  # noqa: PLR0913
    engine: object, ctx: object, messages: list[dict], thread_id: int, *,
    workflow: str, capability: str, branch_id: int,
) -> tuple[DecisionV3 | None, dict]:
    """One generation, with a single escalation when the cheap model returns broken JSON.

    Two attempts is the ceiling everywhere — replies and follow-ups alike. A third rewrite is
    what v2 did, and it is what produced answers written to conflicting corrections.
    `engine` is anything with .run(); typed loosely to keep this free of an import cycle."""
    from .routing import FAST, SMART  # noqa: PLC0415 — routing imports enums, not this module

    raw, meta = await engine.run(ctx, messages, thread_id,
                                 workflow=workflow, capability=capability)
    try:
        return parse_decision_v3(raw), meta
    except ValueError:
        if capability != FAST:
            logger.warning("%s: unparseable decision branch=%d thread=%d — skip",
                           workflow, branch_id, thread_id)
            return None, meta
    logger.warning("%s: unparseable cheap decision branch=%d thread=%d — retry on smart",
                   workflow, branch_id, thread_id)
    raw, meta = await engine.run(ctx, messages, thread_id,
                                 workflow=workflow, capability=SMART)
    try:
        return parse_decision_v3(raw), meta
    except ValueError:
        logger.warning("%s: unparseable on both tiers branch=%d thread=%d — skip",
                       workflow, branch_id, thread_id)
        return None, meta


def parse_decision_v3(raw_json: str) -> DecisionV3:
    """Parse the model's JSON; raises ValueError on a broken contract."""
    try:
        data = json.loads(strip_fences(raw_json))
    except json.JSONDecodeError as exc:
        raise ValueError(f"decision is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("decision JSON must be an object")
    reply = data.get("reply")
    if not isinstance(reply, str):
        raise ValueError("decision missing a string 'reply'")

    lang = str(data.get("reply_language") or "").lower().strip()
    return DecisionV3(
        reply=clean_reply(reply),
        move=_move(data.get("move")),
        stage=coerce_stage(data.get("stage")),
        dossier=_dossier(data.get("dossier")),
        product_slug=str(data.get("product_slug") or "").strip() or None,
        ready=bool(data.get("ready", False)),
        phone=str(data.get("phone") or "").strip() or None,
        needs_human=bool(data.get("needs_human", False)),
        human_reason=str(data.get("human_reason") or "").strip()[:300] or None,
        reply_language=lang if lang.isalpha() and 2 <= len(lang) <= 5 else None,
    )


# ── internal helpers ──────────────────────────────────────────────────────────

def _move(value: object) -> str:
    """An off-contract move must never abort a good reply — the text is what reaches the lead.
    Fall back to the neutral move and log, so a drifting model is visible in the logs."""
    move = str(value or "").strip().lower()
    if move in MOVES:
        return move
    logger.info("decision_v3: unknown move %r → give_value", value)
    return "give_value"


def _dossier(value: object) -> LeadDossier:
    """The turn's delta. A malformed dossier costs this turn's learning, never the reply."""
    if not isinstance(value, dict):
        return LeadDossier()
    return LeadDossier(
        role=_text(value.get("role")),
        job_to_be_done=_text(value.get("job_to_be_done")),
        pains=str_list(value.get("pains")),
        desired_state=str_list(value.get("desired_state")),
        decides_with=_text(value.get("decides_with")),
        readiness=_text(value.get("readiness")),
        prices_quoted=str_list(value.get("prices_quoted")),
        payment_preference=_text(value.get("payment_preference")),
        budget_signal=_text(value.get("budget_signal")),
        objections=_objections(value.get("objections")),
        products_named=str_list(value.get("products_named")),
        cases_used=str_list(value.get("cases_used")),
        arguments_used=str_list(value.get("arguments_used")),
        refusal=_text(value.get("refusal")),
    )


def _objections(value: object) -> list[Objection]:
    if not isinstance(value, list):
        return []
    out: list[Objection] = []
    for item in value:
        if isinstance(item, dict):
            text = _text(item.get("text"), lower=False)[:160]
            if text:
                out.append(Objection(text, _text(item.get("status")) or "open",
                                     _text(item.get("handled_by"), lower=False)[:160]))
        elif isinstance(item, str) and item.strip():
            out.append(Objection(item.strip()[:160]))
    return out[:_MAX_OBJECTIONS]


def _text(value: object, lower: bool = True) -> str:
    text = str(value or "").strip()
    return text.lower() if lower else text


def _lead_type_of(d: LeadDossier) -> str | None:
    """The legacy intent segment, derived from the dossier rather than asked for separately —
    one fewer field for the model to keep consistent with itself."""
    if d.refusal == "blunt":
        return "non_target"
    if d.readiness == "ready":
        return "hot"
    if d.budget_signal and d.readiness != "ready":
        return "no_budget"
    if d.readiness == "considering" or d.has_discovery():
        return "warm"
    return "cold" if d.readiness == "exploring" else None


def _audience_of(d: LeadDossier) -> str | None:
    if d.role == "school":
        return "student"
    return "adult" if d.role in ("student", "working", "jobseeking", "parent") else None
