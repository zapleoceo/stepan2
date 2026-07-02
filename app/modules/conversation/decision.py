"""The model's structured answer — what to say and where the lead now stands.

`parse_decision` is strict on the contract but tolerant of ```json fences the model
sometimes wraps around the object."""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.domain.enums import Stage

from .sanitize import clean_reply


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

    try:
        stage = Stage(data["stage"])
    except KeyError as exc:
        raise ValueError("decision missing 'stage'") from exc
    except ValueError as exc:
        raise ValueError(f"unknown stage: {data['stage']!r}") from exc

    try:
        reply = data["reply"]
    except KeyError as exc:
        raise ValueError("decision missing 'reply'") from exc
    if not isinstance(reply, str):
        raise ValueError("'reply' must be a string")

    subtype = str(data.get("ready_subtype") or "").lower().strip()
    return Decision(
        reply=clean_reply(reply),
        stage=stage,
        product_slug=data.get("product_slug") or None,
        ready=bool(data.get("ready", False)),
        needs_manager=bool(data.get("needs_manager", False)),
        manager_question=data.get("manager_question") or None,
        kb_gap=data.get("kb_gap") or None,
        ready_subtype=subtype if subtype in ("deal", "openhouse") else None,
    )
