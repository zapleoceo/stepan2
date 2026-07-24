"""The model's answer for one turn — parsing, and the shape the pipeline carries.

Two dataclasses live here. `Decision` is what delivery consumes (stage, product, ready, phone,
hand-off) and is deliberately unchanged, so nothing downstream had to move when the decision
procedure was rebuilt. `TurnDecision` is what the model actually returns now: a reply, the one
move it chose, and a dossier delta — state that ACCUMULATES instead of being re-derived from
raw history every turn.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.domain.enums import Stage

from .contract import MOVES
from .dossier import LeadDossier, Objection
from .sanitize import clean_reply

logger = logging.getLogger(__name__)

# Intent segment (temperature) the model classifies once it has signal — for routing +
# reporting. 'student' is NOT here: being school-age is an audience, orthogonal to intent
# (a student can be hot/warm/cold), so it lives on _AUDIENCES instead.
_LEAD_TYPES = frozenset(
    {"hot", "warm", "cold", "no_budget", "non_target", "unclear"})
# Audience axis — WHO the lead is, independent of how ready they are to buy.
_AUDIENCES = frozenset({"adult", "student"})


def _coerce_stage(value: object) -> Stage:
    """Model's stage → Stage. An LLM can emit anything ('greeting', a typo, nothing);
    an off-contract stage must NOT abort the reply — fall back to QUALIFYING (an active,
    non-silent stage) so the bot keeps talking. The reply itself is what matters."""
    try:
        return Stage(str(value).lower().strip())
    except ValueError:
        logger.warning("decision: unknown stage %r → QUALIFYING", value)
        return Stage.QUALIFYING


@dataclass(frozen=True)
class Decision:
    reply: str
    stage: Stage
    product_slug: str | None
    ready: bool
    needs_manager: bool
    # The model's own short explanation for why it's moving the funnel stage this turn
    # (null when the stage isn't changing) — logged to ThreadLog so the chat's chronology
    # shows WHY, the same way a manual stage move's reason popup does.
    stage_reason: str | None = None
    manager_question: str | None = None
    kb_gap: str | None = None
    ready_subtype: str | None = None  # 'deal' | 'openhouse' when ready
    lead_type: str | None = None  # intent segment (hot|warm|cold|no_budget|non_target|unclear)
    audience: str | None = None  # who they are (adult|student), orthogonal to lead_type
    reply_language: str | None = None  # lead's language code when they wrote in another
    # The lead's phone / WhatsApp number if they shared one in the chat (raw digits as written).
    # Persisted to lead.phone_e164, and a captured phone is what gates a real deal hand-off.
    phone: str | None = None
    # Discovered customer profile (Value Proposition Canvas): what the lead is trying to
    # achieve (jobs), their obstacles/fears (pains), and the outcomes they want (gains).
    jobs: list[str] = field(default_factory=list)
    pains: list[str] = field(default_factory=list)
    gains: list[str] = field(default_factory=list)
    discovery_complete: bool = False
    # Objections the lead has raised and NOT yet accepted a reframe for (budget/time/trust/
    # job-doubt/distance/confusion). The model re-reports the still-open set each turn; stored
    # on the lead (replace) so the next turn can't pitch over a live objection.
    open_objections: list[str] = field(default_factory=list)
    # Lead explicitly demanded we stop contacting them ("jangan chat lagi", "stop", threatens
    # to report spam). A normal "no thanks" is NOT this — only an explicit do-not-contact.
    hard_stop: bool = False

    def has_needs(self) -> bool:
        """A pain AND a gain captured — the emotional layer reached, not just a goal. See
        NeedsProfile.has_needs (app/modules/conversation/needs.py) for the full rationale."""
        return bool(self.pains and self.gains)


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text
    body = text[3:]
    if body[:4].lower() == "json":  # ```json … ```
        body = body[4:]
    return body.rsplit("```", 1)[0].strip()


def parse_decision(raw_json: str) -> Decision:
    """Parse the model's JSON into a Decision; raises ValueError on a broken contract."""
    try:
        data = json.loads(_strip_fences(raw_json))
    except json.JSONDecodeError as exc:
        raise ValueError(f"decision is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("decision JSON must be an object")

    stage = _coerce_stage(data.get("stage"))

    try:
        reply = data["reply"]
    except KeyError as exc:
        raise ValueError("decision missing 'reply'") from exc
    if not isinstance(reply, str):
        raise ValueError("'reply' must be a string")

    subtype = str(data.get("ready_subtype") or "").lower().strip()
    lang = str(data.get("reply_language") or "").lower().strip()
    ltype = str(data.get("lead_type") or "").lower().strip()
    aud = str(data.get("audience") or "").lower().strip()
    if ltype == "student":  # legacy/cached contract emitted student as a segment — remap it
        aud = aud or "student"
        ltype = ""
    return Decision(
        reply=clean_reply(reply),
        stage=stage,
        stage_reason=(str(data.get("stage_reason")).strip()[:300] or None)
        if data.get("stage_reason") else None,
        product_slug=data.get("product_slug") or None,
        ready=bool(data.get("ready", False)),
        needs_manager=bool(data.get("needs_manager", False)),
        manager_question=data.get("manager_question") or None,
        kb_gap=data.get("kb_gap") or None,
        ready_subtype=subtype if subtype in ("deal", "openhouse") else None,
        lead_type=ltype if ltype in _LEAD_TYPES else None,
        audience=aud if aud in _AUDIENCES else None,
        reply_language=lang if lang.isalpha() and 2 <= len(lang) <= 5 else None,
        phone=(str(data.get("phone")).strip() or None) if data.get("phone") else None,
        jobs=_str_list(data.get("jobs")),
        pains=_str_list(data.get("pains")),
        gains=_str_list(data.get("gains")),
        discovery_complete=bool(data.get("discovery_complete", False)),
        open_objections=_str_list(data.get("open_objections")),
        hard_stop=bool(data.get("hard_stop", False)),
    )


# Public aliases — the v3 parser needs exactly these semantics (fence tolerance, list
# cleaning, never-abort stage coercion); it imports them rather than reimplementing them.
strip_fences = _strip_fences


def _str_list(value: object, max_items: int = 6, max_len: int = 160) -> list[str]:
    """Clean a model-returned list into ≤max_items short non-empty strings."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        s = str(item).strip()[:max_len]
        if s:
            out.append(s)
        if len(out) >= max_items:
            break
    return out


str_list = _str_list


_MAX_OBJECTIONS = 8


@dataclass(frozen=True)
class TurnDecision:
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
) -> tuple[TurnDecision | None, dict]:
    """One generation, with a single escalation when the cheap model returns broken JSON.

    Two attempts is the ceiling everywhere — replies and follow-ups alike. A third rewrite is
    what v2 did, and it is what produced answers written to conflicting corrections.
    `engine` is anything with .run(); typed loosely to keep this free of an import cycle."""
    from .routing import FAST, SMART  # noqa: PLC0415 — routing imports enums, not this module

    raw, meta = await engine.run(ctx, messages, thread_id,
                                 workflow=workflow, capability=capability)
    try:
        return parse_turn_decision(raw), meta
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
        return parse_turn_decision(raw), meta
    except ValueError:
        logger.warning("%s: unparseable on both tiers branch=%d thread=%d — skip",
                       workflow, branch_id, thread_id)
        return None, meta


def parse_turn_decision(raw_json: str) -> TurnDecision:
    """Parse the model's JSON; raises ValueError on a broken contract."""
    try:
        data = json.loads(_strip_fences(raw_json))
    except json.JSONDecodeError as exc:
        raise ValueError(f"decision is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("decision JSON must be an object")
    reply = data.get("reply")
    if not isinstance(reply, str):
        raise ValueError("decision missing a string 'reply'")

    lang = str(data.get("reply_language") or "").lower().strip()
    return TurnDecision(
        reply=clean_reply(reply),
        move=_move(data.get("move")),
        stage=_coerce_stage(data.get("stage")),
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
    logger.info("decision: unknown move %r → give_value", value)
    return "give_value"


def _dossier(value: object) -> LeadDossier:
    """The turn's delta. A malformed dossier costs this turn's learning, never the reply."""
    if not isinstance(value, dict):
        return LeadDossier()
    return LeadDossier(
        role=_text(value.get("role")),
        job_to_be_done=_text(value.get("job_to_be_done")),
        pains=_str_list(value.get("pains")),
        desired_state=_str_list(value.get("desired_state")),
        decides_with=_text(value.get("decides_with")),
        readiness=_text(value.get("readiness")),
        prices_quoted=_str_list(value.get("prices_quoted")),
        payment_preference=_text(value.get("payment_preference")),
        budget_signal=_text(value.get("budget_signal")),
        objections=_objections(value.get("objections")),
        products_named=_str_list(value.get("products_named")),
        cases_used=_str_list(value.get("cases_used")),
        arguments_used=_str_list(value.get("arguments_used")),
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
                                     _text(item.get("handled_by"), lower=False)[:160],
                                     _text(item.get("category"))))
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
