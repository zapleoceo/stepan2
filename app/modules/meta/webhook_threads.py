"""Reconcile the webhook's sender id with the conversation id the poll keys threads on.

The two paths describe the same chat differently. The poll reads /{page-id}/conversations and
stores `conversation.id` as ChannelThread.external_thread_id. The webhook delivers only the
sender's PSID (Messenger) or IGSID (Instagram Direct) and never the conversation id. Storing
the sender id as-is would give every lead who writes while both paths are live TWO threads —
two histories, two 24h windows, and a bot answering the half of the conversation it can see.

So the webhook is translated INTO the poll's key, never the other way round: the poll is the
slow reconcile that has to keep working untouched.
"""
from __future__ import annotations

import logging
from typing import Protocol

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

_log = logging.getLogger(__name__)


class ConversationResolver(Protocol):
    async def find_conversation_id(self, user_id: str) -> str | None:
        ...


async def resolve_thread_id(
    session: AsyncSession,
    branch_id: int,
    channel_id: int,
    sender_id: str,
    resolver: ConversationResolver | None,
) -> str | None:
    """external_thread_id for this sender, or None when it cannot be established.

    None is a deliberate outcome, not a failure to paper over: inventing a key (the raw PSID)
    would be exactly the duplicate-thread bug this module exists to prevent. The poll still
    reaches that conversation on its next pass, so the message is delayed, never lost.
    """
    known = await _known_thread(session, branch_id, channel_id, sender_id)
    if known:
        return known
    if resolver is None:
        return None
    try:
        found = await resolver.find_conversation_id(sender_id)
    except Exception:  # noqa: BLE001 — a Graph blip must not kill the whole webhook batch
        _log.warning("webhook: conversation lookup failed channel=%s sender=%s",
                     channel_id, sender_id, exc_info=True)
        return None
    if not found:
        _log.warning(
            "webhook: no conversation for sender=%s on channel=%s — leaving it to the poll",
            sender_id, channel_id)
    return found or None


async def _known_thread(
    session: AsyncSession, branch_id: int, channel_id: int, sender_id: str
) -> str | None:
    """The conversation id we already hold for this person on this channel.

    IngestService stamps Lead.ig_user_id with the message's sender id, so an existing thread
    already carries the join we need — and answering from the DB keeps the steady state at
    zero extra Graph calls, which matters because Meta throttles a Page account-wide."""
    row = (await session.execute(
        text(
            "SELECT ct.external_thread_id FROM channel_thread ct"
            " JOIN lead l ON l.id = ct.lead_id"
            " WHERE ct.channel_id = :ch AND l.branch_id = :b AND l.ig_user_id = :sid"
            " ORDER BY ct.id LIMIT 1"
        ),
        {"ch": channel_id, "b": branch_id, "sid": sender_id},
    )).first()
    return str(row[0]) if row and row[0] else None
