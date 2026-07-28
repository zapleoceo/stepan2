"""Backfill lead.dossier from the legacy lead.needs, closing the v2/v3 seam.

Both columns hold the same fact — what we know about a lead — and for weeks every reader had
to remember to ask for both. Twice on 2026-07-26 someone did not: the chat panel and the
needs cloud each read `needs` alone, found the column nobody had written since the cutover,
and showed an empty box. Neither looked like a fault; both looked like "no data yet".

After this runs, `dossier` is the only place the answer lives, and every reader is simplified
to it in the same change.

The shape conversion mirrors dossier.from_needs(): the first job becomes job_to_be_done, any
further jobs join the desired state, gains become the desired state, and every stored
objection was by definition still open. Written out longhand rather than imported, because a
migration must keep describing the database as it was on the day it ran, however the
application code moves afterwards.

Revision ID: dossbf00001
Revises: crmwon00002
Create Date: 2026-07-28 17:00:00
"""
from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text

revision = "dossbf00001"
down_revision = "crmwon00002"
branch_labels = None
depends_on = None

_MAX_PER_LIST = 6
_MAX_OBJECTIONS = 8


def _clean(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text_ = str(item).strip()
        if text_ and text_ not in out:
            out.append(text_)
    return out[:_MAX_PER_LIST]


def _to_dossier(raw: str) -> str | None:
    try:
        d = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    jobs = _clean(d.get("jobs"))
    pains = _clean(d.get("pains"))
    desired = _clean(_clean(d.get("gains")) + jobs[1:])
    objections = [
        {"text": t, "status": "open", "handled_by": "", "category": ""}
        for t in _clean(d.get("objections"))[:_MAX_OBJECTIONS]
    ]
    if not (jobs or pains or desired or objections):
        return None  # an empty v2 record carries nothing worth writing
    return json.dumps({
        "role": "", "job_to_be_done": jobs[0] if jobs else "",
        "pains": pains, "desired_state": desired,
        "decides_with": "", "readiness": "", "product_slug": "",
        "prices_quoted": [], "payment_preference": "", "budget_signal": "",
        "objections": objections, "products_named": [], "cases_used": [],
        "arguments_used": [], "refusal": "none",
    }, ensure_ascii=False)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(text(
        "SELECT id, needs FROM lead WHERE dossier IS NULL AND needs IS NOT NULL")).fetchall()
    for lead_id, needs in rows:
        converted = _to_dossier(needs or "")
        if converted is not None:
            bind.execute(text("UPDATE lead SET dossier = :d WHERE id = :i"),
                         {"d": converted, "i": lead_id})


def downgrade() -> None:
    """Nothing to undo: `needs` is left untouched, so the old column is still the fallback a
    rolled-back build would read. The column is dropped in a later release, once this one has
    proven itself in production."""
