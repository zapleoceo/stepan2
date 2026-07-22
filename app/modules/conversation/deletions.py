"""DeletionService — carry out requested IG unsends (S1 deletions loop).

A manager asks to retract an outgoing message; we revoke it in IG FIRST, and only
delete the local row on success — so the UI never claims a retraction that didn't
happen. A failed revoke keeps the flag (warn + retry next tick). Removing the last
outgoing message rewinds last_out_at so the reply/followup state stays honest."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import ChannelThread, Message

logger = logging.getLogger(__name__)

# A revoke that hasn't succeeded within this window never will (IG won't unsend very old
# messages, and a message already deleted in the app stays 403/failed) — give up and clear
# the flag so the poison never piles up and starves the tick (real incident: a months-old
# backlog retried every minute got the whole delete action throttled by IG).
_MAX_REVOKE_AGE = timedelta(hours=6)
# One IG revoke can hang 40-90s; bound it so a single stuck call can't eat the job timeout.
_REVOKE_TIMEOUT_S = 25


class Revoker(Protocol):
    async def revoke(self, external_thread_id: str, external_message_id: str) -> bool: ...


class DeletionService:
    """Process delete_requested outgoing messages for one branch via a channel revoker."""

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        self.session = session
        self.branch_id = branch_id

    async def pending(self, channel_id: int, limit: int | None = None) -> list[Message]:
        # limit is for the worker's thread-discovery scan (it only acts on a few threads/tick);
        # process() calls it WITHOUT a limit because it must revoke every pending msg of a thread.
        q = (
            select(Message)
            .where(
                Message.branch_id == self.branch_id,
                Message.channel_id == channel_id,
                Message.delete_requested.is_(True),  # type: ignore[union-attr]
                Message.direction == "out",
            )
            .order_by(Message.occurred_at.asc())  # type: ignore[union-attr]
        )
        if limit is not None:
            q = q.limit(limit)
        return list((await self.session.exec(q)).all())

    async def process(self, channel_id: int, external_thread_id: str, revoker: Revoker) -> int:
        """Revoke each pending message in IG; delete locally only on success."""
        done = 0
        cutoff = datetime.now(UTC).replace(tzinfo=None) - _MAX_REVOKE_AGE
        for msg in await self.pending(channel_id):
            if msg.occurred_at is not None and msg.occurred_at < cutoff:
                msg.delete_requested = False  # too old to unsend — stop retrying, drop the flag
                self.session.add(msg)
                await self.session.flush()
                logger.info("unsend giving up (too old) branch=%d msg=%d",
                            self.branch_id, msg.id)
                continue
            try:
                ok = await asyncio.wait_for(
                    revoker.revoke(external_thread_id, msg.external_id),
                    timeout=_REVOKE_TIMEOUT_S,
                )
            except TimeoutError:
                ok = False
            if not ok:
                logger.warning(
                    "unsend failed branch=%d msg=%d — still in IG, will retry",
                    self.branch_id, msg.id,
                )
                continue
            await self._delete_local(msg)
            done += 1
        if done:
            logger.info("unsent branch=%d channel=%d: %d messages",
                        self.branch_id, channel_id, done)
        return done

    async def _delete_local(self, msg: Message) -> None:
        """Tombstone, not a hard delete.

        The row is the only evidence the message was OURS. Deleting it meant the inbox poll —
        which runs every two minutes and had already seen the message — found no match in the
        content dedup, filed it as a manager's manual reply, and handed the whole thread to a
        human with the bot muted (thread 4954). Keeping a revoked row costs nothing: it is
        hidden from the chat, excluded from the model's dialog, and still answers the one
        question the dedup asks."""
        thread_id = msg.thread_id
        msg.revoked_at = datetime.now(UTC).replace(tzinfo=None)
        msg.delete_requested = False
        self.session.add(msg)
        await self.session.flush()
        # rewind BOTH watermarks to the newest remaining message per direction (or NULL).
        # last_out_at gates the reply loop; last_in_at drives the sidebar's activity sort
        # and the reply window — leaving it stale after a delete left the thread list in the
        # wrong order (the real bug: deleting the newest message didn't re-sort the list).
        newest_out = (
            await self.session.execute(
                select(func.max(Message.occurred_at)).where(
                    Message.thread_id == thread_id, Message.direction == "out",
                    Message.revoked_at.is_(None),  # type: ignore[union-attr]
                )
            )
        ).scalar_one_or_none()
        newest_in = (
            await self.session.execute(
                select(func.max(Message.occurred_at)).where(
                    Message.thread_id == thread_id, Message.direction == "in",
                    Message.revoked_at.is_(None),  # type: ignore[union-attr]
                )
            )
        ).scalar_one_or_none()
        thread = await self.session.get(ChannelThread, thread_id)
        if thread is not None:
            thread.last_out_at = newest_out
            thread.last_in_at = newest_in
            self.session.add(thread)
            await self.session.flush()
