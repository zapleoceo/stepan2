"""A second, decoupled pass whose only job is filling `pains`/`desired_state`/`objections`.

The main v3 turn (decision.generate + contract._SCHEMA) already asks one model call to write
a warm, on-brand reply AND correctly populate a 21-field dossier at the same time. Measured
live on branch_id=1 (2026-07-23): of 1215 leads active in the last 7 days, only ~5% had ANY
dossier saved, and only ~2% had pains+desired_state both filled — the reply wins that
competition for attention essentially every time, so discovery gets left empty out of
generation pressure, not because the lead never said anything.

This module is the backstop, not a replacement: a SEPARATE chat:fast call, given only the
dialog and what pains/desired_state are already known, with the ONE job of reading what the
lead revealed. No reply to write, no stage to pick, no 21-field contract to juggle — a tiny
schema is the entire hypothesis being tested. It never blocks or gates the reply: any failure
(broker error, timeout, unparseable JSON) is swallowed and logged, same discipline as
critic.review — an unreachable extractor must never cost the lead their answer.
"""
from __future__ import annotations

import json
import logging

from app.adapters.db.models import Message
from app.ports.llm import LLMPort

from .decision import str_list, strip_fences
from .dossier import LeadDossier, Objection
from .prompt import _role_of
from .routing import FAST

logger = logging.getLogger(__name__)

_DIALOG_BUDGET = 20  # last N turns — discovery lives in recent talk, not the whole history

_SYSTEM = """\
You read one Instagram DM conversation between a lead and a sales rep at an IT school. Your \
ONLY job: extract what the LEAD has revealed about their pains and desired outcomes — in \
their own words/meaning, not what the rep suggested or offered to them. A bare "iya"/"ok" to \
the rep's question reveals nothing; do not invent a pain or a goal that was never actually \
said. Extract only what is genuinely new — do not repeat anything already listed as known \
below. If nothing new was revealed, return empty lists.

pains: what worries them, what's holding them back, what's not working now.
desired_state: what a good outcome looks like to them — the goal, not the product.
objections: any reason they gave for hesitating (price, time, trust, parents, ...), in their \
own words. Leave empty if none.

Return ONLY this JSON, no prose, no markdown fences:
{"pains": [str], "desired_state": [str], "objections": [str]}
"""


def _transcript(dialog: list[Message]) -> str:
    lines = []
    for m in dialog[-_DIALOG_BUDGET:]:
        text = (m.text or "").strip()
        if not text:
            continue
        speaker = "LEAD" if _role_of(m) == "user" else "REP"
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def _known_block(dossier: LeadDossier) -> str:
    lines = [f"- pains: {'; '.join(dossier.pains)}" if dossier.pains else "",
             f"- desired_state: {'; '.join(dossier.desired_state)}"
             if dossier.desired_state else ""]
    body = "\n".join(line for line in lines if line)
    if body:
        return f"ALREADY KNOWN (do not repeat these):\n{body}"
    return "ALREADY KNOWN: nothing yet."


async def extract_discovery(  # noqa: PLR0913
    llm: LLMPort, dialog: list[Message], dossier: LeadDossier, lang: str,
    branch_id: int, thread_id: int, budget: object = None,
) -> LeadDossier:
    """The dialog's discovery delta, extracted on chat:fast. Empty LeadDossier on any failure —
    the caller merges this straight into what's already known via merge_dossier, so a soft
    failure here simply means this turn adds nothing, never that the turn breaks."""
    transcript = _transcript(dialog)
    if not transcript:
        return LeadDossier()
    user = f"{_known_block(dossier)}\n\nCONVERSATION (lang: {lang}):\n{transcript}"
    try:
        raw, meta = await llm.chat(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            capability=FAST, require_json_schema=True,
            workflow="discovery", thread_id=thread_id, branch_id=branch_id)
        if budget is not None:
            await budget.record(float(meta.get("cost_usd") or 0.0))
        return _parse(raw)
    except Exception as exc:  # noqa: BLE001 — an unreachable extractor must not cost the reply
        logger.warning("discovery unavailable branch=%d thread=%d: %s — skipped",
                       branch_id, thread_id, exc)
        return LeadDossier()


def _parse(raw: str) -> LeadDossier:
    try:
        data = json.loads(strip_fences(raw))
    except (json.JSONDecodeError, TypeError):
        logger.warning("discovery: unparseable extraction — skipped")
        return LeadDossier()
    if not isinstance(data, dict):
        return LeadDossier()
    objections = [Objection(text) for text in str_list(data.get("objections"))]
    return LeadDossier(
        pains=str_list(data.get("pains")),
        desired_state=str_list(data.get("desired_state")),
        objections=objections,
    )
