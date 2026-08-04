"""Read a channel's stored credentials.

Lives here rather than in worker/wiring so a connector's port builder can reach it without
importing the worker (wiring imports the registry — the other direction would be a cycle)."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.crypto import decrypt
from app.adapters.db.models import ChannelSession
from app.domain.enums import SessionStatus


async def active_session_settings(session: AsyncSession, channel_id: int) -> dict | None:
    """Decrypt the channel's ACTIVE session secret — or None.

    Only ACTIVE: mark_session_status flips a checkpointed channel to CHALLENGE, and that
    freezes every loop for it until a re-login restores the row."""
    rows = await session.exec(
        select(ChannelSession).where(
            ChannelSession.channel_id == channel_id,
            ChannelSession.status == SessionStatus.ACTIVE,
        )
    )
    row = rows.scalars().first()
    return json.loads(decrypt(row.secret_enc)) if row else None
