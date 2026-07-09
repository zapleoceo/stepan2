"""The model's structured answer — what to say and where the lead now stands.

`parse_decision` is strict on the contract but tolerant of ```json fences the model
sometimes wraps around the object."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.domain.enums import Stage

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
        hard_stop=bool(data.get("hard_stop", False)),
    )


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
