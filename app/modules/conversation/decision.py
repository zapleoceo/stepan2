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
import re
from dataclasses import dataclass, field

from app.domain.enums import Stage

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
        about the lead, not just what this one turn added — and since 2026-07-26 the stage, the
        readiness and the course are READ off it rather than asked of the selling model."""
        ready = merged.readiness == "ready"
        return Decision(
            reply=self.reply,
            stage=_stage_from(merged, ready=ready),
            product_slug=merged.product_slug or self.product_slug,
            ready=ready or self.ready,
            needs_manager=self.needs_human,
            manager_question=self.human_reason,
            kb_gap=self.human_reason,
            ready_subtype="deal" if (ready or self.ready) else None,
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


def _stage_from(d: LeadDossier, *, ready: bool) -> Stage:
    """Where this conversation stands, read off what is known rather than asked of the seller.

    The selling model used to return a stage, and _stage_for (delivery.py) overrode it in six
    branches — a human-led lead, ready with a phone, ready without one, needs_manager, a
    PRESENTING with no captured need, a soft no misread as DORMANT. DORMANT and NURTURING it
    never owned at all: the outbox, the follow-up sweep and reactivation set those. What was
    genuinely left is these three lines, and they need no model to decide them.

    _stage_for still runs on top; this only supplies the proposal it used to get."""
    if ready:
        return Stage.READY
    if d.open_objections():
        return Stage.OBJECTION
    if d.has_discovery():
        return Stage.PRESENTING
    return Stage.QUALIFYING


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
    data = _unwrap_tool_envelope(data)
    reply = data.get("reply")
    if not isinstance(reply, str):
        raise ValueError("decision missing a string 'reply'")

    text = clean_reply(reply)
    lang = str(data.get("reply_language") or "").lower().strip()
    # stage / product_slug / ready are still READ when present — a reply generated just before
    # the 2026-07-26 deploy, or the occasional model that volunteers them anyway, loses
    # nothing — but nothing asks for them any more, and to_legacy prefers the dossier's answer.
    return TurnDecision(
        reply=text,
        move=_free_move(data.get("move")),
        stage=_coerce_stage(data.get("stage")) if data.get("stage") else Stage.QUALIFYING,
        # Reading the lead is discovery.py's job now; the prices are read off the reply itself
        # (guard.canonical_prices), so the author no longer has to list what it just wrote.
        # Both a full `dossier` and an explicit `prices_quoted` are still accepted, so a reply
        # generated just before the 2026-07-25 deploy loses nothing.
        dossier=_dossier(data.get("dossier"),
                         prices=str_list(data.get("prices_quoted")) or _prices_in(text)),
        product_slug=str(data.get("product_slug") or "").strip() or None,
        ready=bool(data.get("ready", False)),
        # Kept for replies still in flight from the old schema — the live path takes the number
        # from leads.phone.extract_phone, which mines the lead's own inbound at ingest.
        phone=str(data.get("phone") or "").strip() or None,
        needs_human=bool(data.get("needs_human", False)),
        human_reason=str(data.get("human_reason") or "").strip()[:300] or None,
        reply_language=lang if lang.isalpha() and 2 <= len(lang) <= 5 else None,
    )


def _prices_in(reply: str) -> list[str]:
    """Figures quoted in the reply, read off the text instead of asked for. A price already
    given is a commitment the next turn must not contradict, so it still has to be recorded —
    but the model that wrote the sentence is the least efficient way to find out."""
    from .guard import canonical_prices  # noqa: PLC0415 — guard imports nothing from here

    return [f"{value:,}".replace(",", ".") for value in sorted(canonical_prices(reply or ""))]


# ── internal helpers ──────────────────────────────────────────────────────────

# Anthropic served via the broker's forced-tool JSON mode intermittently wraps the whole
# decision in the tool-call envelope ({"parameters": {...}} — measured live on chat:sales,
# ~half of turns). The content inside is exactly our schema, so unwrap rather than fail the
# turn. One level only, and only when the envelope itself carries no 'reply'.
_TOOL_ENVELOPE_KEYS = ("parameters", "arguments", "input")


def _unwrap_tool_envelope(data: dict) -> dict:
    if "reply" in data:
        return data
    for key in _TOOL_ENVELOPE_KEYS:
        inner = data.get(key)
        if isinstance(inner, dict) and isinstance(inner.get("reply"), str):
            logger.info("decision: unwrapped %r tool envelope", key)
            return inner
    return data


_FREE_MOVE_RE = re.compile(r"[^a-z0-9_]+")


def _free_move(value: object) -> str:
    """Free mode keeps whatever label the model chose (sanitized to a short snake_case slug);
    the gates that read the enumerated moves are off there, so this is telemetry only."""
    move = _FREE_MOVE_RE.sub("_", str(value or "").strip().lower()).strip("_")[:40]
    return move or "free_move"


def _dossier(value: object, prices: list[str] | None = None) -> LeadDossier:
    """What the selling model reported about the lead this turn.

    Since 2026-07-25 that is only the prices it quoted — reading the person is discovery.py's
    job. A full `dossier` object is still parsed when present so a reply generated just before
    the deploy (or by a follow-up path yet to be migrated) loses nothing."""
    if not isinstance(value, dict):
        return LeadDossier(prices_quoted=prices or [])
    return LeadDossier(
        role=_text(value.get("role")),
        job_to_be_done=_text(value.get("job_to_be_done")),
        pains=_str_list(value.get("pains")),
        desired_state=_str_list(value.get("desired_state")),
        readiness=_text(value.get("readiness")),
        prices_quoted=_str_list(value.get("prices_quoted")) or (prices or []),
        payment_preference=_text(value.get("payment_preference")),
        budget_signal=_text(value.get("budget_signal")),
        objections=_objections(value.get("objections")),
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
