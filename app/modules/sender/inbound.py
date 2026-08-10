"""Store what the CRM sender hands us, from whichever direction it arrives.

One function for both routes on purpose. The callback and the catch-up sweep carry the same
message under the same `external_id`, and if each wrote its own way the two would disagree
about what counts as a duplicate — which is the one thing this key exists to settle.

Deduplication is the unique index, not a prior SELECT. Two callbacks racing (a retry landing
next to the original, a sweep running while a callback arrives) both pass a check-then-insert
and both write; only the index actually stops them.
"""
from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import SenderInbound

logger = logging.getLogger(__name__)

CALLBACK = "callback"
CATCHUP = "catchup"

# Their field name → ours. Their model is wider; everything absent from here is noise we
# deliberately do not store rather than carry a copy of somebody else's schema.
_FIELDS = {
    "id": "sender_message_id",
    "external_id": "external_id",
    "from": "phone",
    "from_name": "from_name",
    "message": "text",
    "attachment": "attachment",
    "chanel": "channel_name",
    "project_id": "project_id",
    "branch_id": "branch_ref",
    "conversation_id": "conversation_id",
    "chat_id": "chat_id",
    "user_id": "sender_user_id",
}


def to_row(payload: dict, *, arrived_via: str) -> SenderInbound | None:
    """Their payload → our row, or None when it carries no deduplication key.

    Without `external_id` there is no way to tell a retry from a new message, and answering a
    lead twice is worse than missing one — so such a payload is rejected here and logged by
    the caller rather than stored under an invented id.
    """
    external_id = str(payload.get("external_id") or "").strip()
    if not external_id:
        return None
    row = SenderInbound(external_id=external_id, arrived_via=arrived_via)
    for theirs, ours in _FIELDS.items():
        if theirs == "external_id":
            continue
        value = payload.get(theirs)
        if value not in (None, ""):
            setattr(row, ours, str(value))
    direction = str(payload.get("type_send") or "in").strip().lower()
    row.direction = "out" if direction == "out" else "in"
    return row


async def store(session: AsyncSession, row: SenderInbound) -> bool:
    """True if this message is new, False if we already had it.

    A savepoint, so a collision does not poison the caller's transaction: the callback answers
    200 to a duplicate on purpose, and it cannot do that from a session the failed INSERT has
    already marked rollback-only.
    """
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        logger.info("sender inbound already held: external_id=%s", row.external_id)
        return False
    return True
