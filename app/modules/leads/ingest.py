"""IngestService — turn InboundMessages into leads, threads and deduped Messages.

The single write path for inbound traffic: resolve identity, dedup by external id,
persist the message, advance the thread's reply window. Branch-scoped throughout.
"""
from __future__ import annotations

from datetime import timedelta

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Message
from app.ports.channel import InboundMessage

from .identity import IdentityService
from .repository import MessageRepo

WINDOW = timedelta(hours=24)  # private-channel reply window (e.g. MBS 24h)


class IngestService:
    """Inbound ingestion for one branch — idempotent on (channel_id, external_id)."""

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        self.session = session
        self.branch_id = branch_id
        self.identity = IdentityService(session, branch_id)
        self.messages = MessageRepo(session, branch_id)

    async def ingest(
        self, channel_id: int, messages: list[InboundMessage]
    ) -> list[Message]:
        """Persist each new inbound; skip duplicates. Returns the rows it created."""
        created: list[Message] = []
        for inbound in messages:
            external_id = _external_id(inbound)
            if await self.messages.by_external(channel_id, external_id) is not None:
                continue  # already ingested — idempotent
            _lead, thread = await self.identity.resolve_or_create(
                inbound.external_thread_id, channel_id, None, None
            )
            created.append(await self._store(thread, channel_id, external_id, inbound))
        return created

    async def _store(
        self, thread, channel_id: int, external_id: str, inbound: InboundMessage
    ) -> Message:
        msg = await self.messages.add(
            Message(
                branch_id=self.branch_id,
                thread_id=thread.id,
                channel_id=channel_id,
                external_id=external_id,
                direction="in",
                sent_by="lead",
                text=inbound.text,
                occurred_at=inbound.occurred_at,
            )
        )
        thread.last_in_at = inbound.occurred_at
        thread.window_until = inbound.occurred_at + WINDOW
        if inbound.product_hint and thread.product_slug is None:
            thread.product_slug = inbound.product_hint
        return msg


def _external_id(inbound: InboundMessage) -> str:
    """Stable per-message id — InboundMessage carries no native id, so derive one."""
    return f"{inbound.external_thread_id}:{inbound.occurred_at.isoformat()}:{inbound.sender_id}"
