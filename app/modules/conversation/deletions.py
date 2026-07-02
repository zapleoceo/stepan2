"""DeletionService — carry out requested IG unsends (S1 deletions loop).

A manager asks to retract an outgoing message; we revoke it in IG FIRST, and only
delete the local row on success — so the UI never claims a retraction that didn't
happen. A failed revoke keeps the flag (warn + retry next tick). Removing the last
outgoing message rewinds last_out_at so the reply/followup state stays honest."""
from __future__ import annotations

import logging
from typing import Protocol

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import ChannelThread, Message

logger = logging.getLogger(__name__)


class Revoker(Protocol):
    async def revoke(self, external_thread_id: str, external_message_id: str) -> bool: ...


class DeletionService:
    """Process delete_requested outgoing messages for one branch via a channel revoker."""

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        self.session = session
        self.branch_id = branch_id

    async def pending(self, channel_id: int) -> list[Message]:
        q = select(Message).where(
            Message.branch_id == self.branch_id,
            Message.channel_id == channel_id,
            Message.delete_requested.is_(True),  # type: ignore[union-attr]
            Message.direction == "out",
        )
        return list((await self.session.exec(q)).all())

    async def process(self, channel_id: int, external_thread_id: str, revoker: Revoker) -> int:
        """Revoke each pending message in IG; delete locally only on success."""
        done = 0
        for msg in await self.pending(channel_id):
            if not await revoker.revoke(external_thread_id, msg.external_id):
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
        thread_id = msg.thread_id
        await self.session.delete(msg)
        await self.session.flush()
        # rewind last_out_at to the newest remaining outgoing message (or NULL)
        newest = (
            await self.session.execute(
                select(func.max(Message.occurred_at)).where(
                    Message.thread_id == thread_id, Message.direction == "out"
                )
            )
        ).scalar_one_or_none()
        thread = await self.session.get(ChannelThread, thread_id)
        if thread is not None:
            thread.last_out_at = newest
            self.session.add(thread)
            await self.session.flush()
