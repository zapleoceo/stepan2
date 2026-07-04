"""Worker wiring — the cross-tenant seams the scheduled tasks orchestrate over.

The worker is the only platform-level (un-scoped) actor: it lists ACTIVE branches and
their ACTIVE channels, then hands each to a branch-scoped use-case. Channel-transport
construction (per-channel secrets) is isolated here so the task bodies stay pure
orchestration and the wiring can be swapped/faked in one place."""
from __future__ import annotations

import json

from sqlalchemy import case, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.channels import REGISTRY
from app.adapters.channels.instagram import InstagramAdapter
from app.adapters.channels.meta_business import MetaBusinessAdapter
from app.adapters.channels.transports import (
    EvolutionTransport,
    GraphTransportHTTP,
    InstagrapiTransport,
)
from app.adapters.channels.whatsapp import WhatsAppAdapter
from app.adapters.crypto import decrypt
from app.adapters.db.models import (
    Branch,
    Channel,
    ChannelSession,
    ChannelThread,
    Lead,
    Outbox,
)
from app.config import settings
from app.domain.enums import BOT_SILENT_STAGES, ChannelKind, SessionStatus
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
    """Thread ids with a fresh inbound the bot still owns (lead spoke last, not silent).

    Per-lead agent_enabled gates manager takeovers; the NOT-EXISTS pending guard stops
    a second generation while a queued reply waits out its human-typing delay."""
    pending = (
        select(Outbox.id)
        .where(Outbox.thread_id == ChannelThread.id, Outbox.status == "pending")
        .exists()
    )
    rows = await session.exec(
        select(ChannelThread.id)
        .join(Lead, Lead.id == ChannelThread.lead_id)  # type: ignore[arg-type]
        .where(
            Lead.branch_id == branch_id,
            Lead.agent_enabled.is_(True),  # type: ignore[attr-defined]
            Lead.is_blocked.is_(False),  # type: ignore[attr-defined]
            Lead.stage.not_in(BOT_SILENT_STAGES),  # type: ignore[attr-defined]
            ChannelThread.last_in_at.is_not(None),  # type: ignore[attr-defined]
            (ChannelThread.last_out_at.is_(None))  # type: ignore[attr-defined]
            | (ChannelThread.last_out_at < ChannelThread.last_in_at),  # type: ignore[operator]
            ~pending,
        )
    )
    return [tid for tid in rows.scalars().all() if tid is not None]


async def try_lock_thread(session: AsyncSession, thread_id: int) -> bool:
    """Postgres advisory xact lock scoped to thread_id — released automatically when the
    caller's transaction ends (commit/rollback), no explicit unlock needed. Closes the gap
    the NOT-EXISTS pending guard leaves open: two overlapping reply_pending ticks can both
    pass that guard before either commits its outbox row, so both call the LLM for the same
    thread. No-op (always True) off Postgres — sqlite tests aren't concurrent."""
    if not settings().database_url.startswith("postgresql"):
        return True
    row = await session.exec(select(func.pg_try_advisory_xact_lock(thread_id)))  # type: ignore[arg-type]
    return bool(row.scalar_one())


async def threads_with_pending_outbox(session: AsyncSession, branch_id: int) -> list[int]:
    """Thread ids with a queued (pending) outbox line — a thread with a real REPLY
    (agent/manager) waiting goes first, a thread with ONLY a follow-up queued goes last.
    send_outbox drains threads in this order, so when the hourly/daily send cap is tight,
    a reply to something the lead just said is never crowded out by a proactive nudge.
    Oldest-queued-first as the tiebreaker within each tier."""
    has_reply = func.max(case((Outbox.source != "followup", 1), else_=0))
    earliest = func.min(Outbox.id)
    rows = await session.exec(
        select(Outbox.thread_id)
        .where(Outbox.branch_id == branch_id, Outbox.status == "pending")
        .group_by(Outbox.thread_id)
        .order_by(has_reply.desc(), earliest)
    )
    return list(rows.scalars().all())


async def mark_session_status(
    session: AsyncSession, channel_id: int, status: SessionStatus
) -> bool:
    """Flip the channel's ACTIVE session to a new status (e.g. CHALLENGE on checkpoint).

    build_channel_port only loads ACTIVE sessions, so a non-ACTIVE status freezes the
    channel across every loop until a re-login restores it. Returns True if it flipped."""
    rows = await session.exec(
        select(ChannelSession).where(
            ChannelSession.channel_id == channel_id,
            ChannelSession.status == SessionStatus.ACTIVE,
        )
    )
    row = rows.scalars().first()
    if row is None:
        return False
    row.status = status
    session.add(row)
    await session.flush()
    return True


async def _active_session_settings(session: AsyncSession, channel_id: int) -> dict | None:
    """Decrypt the channel's active session secret (instagrapi dump) — or None."""
    rows = await session.exec(
        select(ChannelSession).where(
            ChannelSession.channel_id == channel_id,
            ChannelSession.status == SessionStatus.ACTIVE,
        )
    )
    row = rows.scalars().first()
    return json.loads(decrypt(row.secret_enc)) if row else None


async def build_channel_port(session: AsyncSession, channel: Channel) -> ChannelPort:
    """Resolve a live ChannelPort from the channel's Fernet-encrypted ChannelSession.

    Instagram is wired (instagrapi via the stored session dump + geo-matched proxy);
    WhatsApp/MetaBusiness raise NotImplementedError until their sessions are connected.
    Callers guard with try/except and skip a channel that isn't ready (logged)."""
    if channel.kind not in REGISTRY:
        raise KeyError(f"no adapter for channel kind {channel.kind}")
    if channel.kind == ChannelKind.INSTAGRAM:
        dump = await _active_session_settings(session, channel.id or 0)
        if dump is None:
            raise RuntimeError(f"no active session for channel {channel.id}")
        proxy = dump.pop("proxy", None) or settings().ig_proxy  # per-channel proxy first
        branch = await session.get(Branch, channel.branch_id)
        transport = InstagrapiTransport(
            username=channel.handle or "", session_settings=dump, proxy=proxy,
            lang=branch.lang if branch else "", tz_offset_h=branch.tz_offset_h if branch else None)
        return InstagramAdapter(transport, handle=channel.handle or "")
    if channel.kind == ChannelKind.META_BUSINESS:
        dump = await _active_session_settings(session, channel.id or 0)
        if dump is None:
            raise RuntimeError(f"no active token for Meta Business channel {channel.id}")
        transport = GraphTransportHTTP(
            base_url=dump.get("base_url", "https://graph.instagram.com/v21.0"),
            account_id=dump.get("account_id") or channel.account_id or "",
            token=dump["token"],
        )
        return MetaBusinessAdapter(
            transport, account_id=dump.get("account_id") or channel.account_id or ""
        )
    if channel.kind == ChannelKind.WHATSAPP:
        dump = await _active_session_settings(session, channel.id or 0)
        if dump is None:
            raise RuntimeError(f"no WhatsApp config for channel {channel.id}")
        transport = EvolutionTransport(
            base_url=dump["base_url"],
            instance=dump["instance"],
            api_key=dump["api_key"],
        )
        return WhatsAppAdapter(transport, instance=dump["instance"])
    raise NotImplementedError(
        f"transport wiring for {channel.kind} channel {channel.id} is not configured yet"
    )
