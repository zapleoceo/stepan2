"""Worker wiring — the cross-tenant seams the scheduled tasks orchestrate over.

The worker is the only platform-level (un-scoped) actor: it lists ACTIVE branches and
their ACTIVE channels, then hands each to a branch-scoped use-case. Channel-transport
construction (per-channel secrets) is isolated here so the task bodies stay pure
orchestration and the wiring can be swapped/faked in one place."""
from __future__ import annotations

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.channels import REGISTRY
from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Outbox
from app.domain.enums import BOT_SILENT_STAGES
from app.ports.channel import ChannelPort


async def active_branches(session: AsyncSession) -> list[Branch]:
    """Every active tenant — the worker's unit of work (it is platform-level, un-scoped)."""
    rows = await session.exec(select(Branch).where(Branch.is_active.is_(True)))  # type: ignore[attr-defined]
    return list(rows.scalars().all())


async def active_channels(session: AsyncSession, branch_id: int) -> list[Channel]:
    """A branch's active channels — the ingest/send fan-out for that tenant."""
    rows = await session.exec(
        select(Channel).where(Channel.branch_id == branch_id, Channel.is_active.is_(True))  # type: ignore[attr-defined]
    )
    return list(rows.scalars().all())


async def threads_awaiting_reply(session: AsyncSession, branch_id: int) -> list[int]:
    """Thread ids with a fresh inbound the bot still owns (lead spoke last, not silent)."""
    rows = await session.exec(
        select(ChannelThread.id)
        .join(Lead, Lead.id == ChannelThread.lead_id)  # type: ignore[arg-type]
        .where(
            Lead.branch_id == branch_id,
            Lead.stage.not_in(BOT_SILENT_STAGES),  # type: ignore[attr-defined]
            ChannelThread.last_in_at.is_not(None),  # type: ignore[attr-defined]
            (ChannelThread.last_out_at.is_(None))  # type: ignore[attr-defined]
            | (ChannelThread.last_out_at < ChannelThread.last_in_at),  # type: ignore[operator]
        )
    )
    return [tid for tid in rows.scalars().all() if tid is not None]


async def threads_with_pending_outbox(session: AsyncSession, branch_id: int) -> list[int]:
    """Distinct thread ids that have at least one queued (pending) outbox line."""
    rows = await session.exec(
        select(Outbox.thread_id)
        .where(Outbox.branch_id == branch_id, Outbox.status == "pending")
        .distinct()
    )
    return list(rows.scalars().all())


def build_channel_port(channel: Channel) -> ChannelPort:
    """Resolve a live ChannelPort for a channel.

    Transport credentials live in ChannelSession (Fernet-encrypted) and their per-kind
    shape is defined when a channel is connected via the admin UI — not at import time.
    Until that wiring exists the registry class is known but cannot be instantiated, so
    callers must guard with a try/except and skip the channel (logged, never crash)."""
    if channel.kind not in REGISTRY:
        raise KeyError(f"no adapter for channel kind {channel.kind}")
    raise NotImplementedError(
        f"transport wiring for {channel.kind} channel {channel.id} is not configured yet"
    )
