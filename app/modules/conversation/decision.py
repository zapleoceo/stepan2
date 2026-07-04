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
    manager_question: str | None = None
    kb_gap: str | None = None
    ready_subtype: str | None = None  # 'deal' | 'openhouse' when ready
    reply_language: str | None = None  # lead's language code when they wrote in another
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
        """At least one pain or gain captured — the minimum to present against a need."""
        return bool(self.pains or self.gains)


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
    return Decision(
        reply=clean_reply(reply),
        stage=stage,
        product_slug=data.get("product_slug") or None,
        ready=bool(data.get("ready", False)),
        needs_manager=bool(data.get("needs_manager", False)),
        manager_question=data.get("manager_question") or None,
        kb_gap=data.get("kb_gap") or None,
        ready_subtype=subtype if subtype in ("deal", "openhouse") else None,
        reply_language=lang if lang.isalpha() and 2 <= len(lang) <= 5 else None,
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
