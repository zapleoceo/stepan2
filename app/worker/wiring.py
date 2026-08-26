"""Worker wiring — the cross-tenant seams the scheduled tasks orchestrate over.

The worker is the only platform-level (un-scoped) actor: it lists ACTIVE branches and
their ACTIVE channels, then hands each to a branch-scoped use-case. Channel-transport
construction (per-channel secrets) is isolated here so the task bodies stay pure
orchestration and the wiring can be swapped/faked in one place."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import True_, case, func, select
from sqlalchemy.orm import aliased
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import (
    Branch,
    Channel,
    ChannelSession,
    ChannelThread,
    Lead,
    Outbox,
)
from app.config import settings
from app.connectors.registry import mute_reply_kinds, spec_for
from app.domain.enums import BOT_SILENT_STAGES, SessionStatus
from app.ports.channel import ChannelPort

_log = logging.getLogger(__name__)


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


# A thread the bot never got the chance to answer (agent was off, or a huge sync-only
# backlog) can sit "awaiting reply" for months. Flipping agent_enabled_global back on
# must never mass-blast a year-old backlog with a bot reply out of nowhere — real
# incident: turning branch 1 live re-surfaced threads with last_in_at back to 2025-08-26
# and started auto-replying to all of them at once. Bound the sweep to genuinely live
# conversations; anything older needs a deliberate, reviewed catch-up, not an automatic one.
_AWAITING_REPLY_MAX_AGE = timedelta(days=settings().awaiting_reply_max_age_days)

# Real incident: re-enabling a branch surfaced ~300 threads at once; reply_pending tried
# to decide() every one of them in a single tick, blew past ARQ's 120s job_timeout, and
# got killed + retried — the retry re-picked up threads whose advisory lock had already
# been released mid-flight, producing near-duplicate replies sent seconds apart (thread
# 1057 and ~20 others, all revoked afterward). A tick must always finish comfortably
# inside the timeout; a capped batch plus the next tick 60s later drains a large backlog
# just as fast, without ever risking a kill-and-retry duplicate.
_REPLY_BATCH_CAP = settings().reply_batch_cap


_newest = aliased(ChannelThread)


async def threads_awaiting_reply(
    session: AsyncSession, branch_id: int, limit: int | None = None,
) -> list[int]:
    """Thread ids with a fresh inbound the bot still owns (lead spoke last, not silent),
    oldest-waiting-first, capped at `limit` (defaults to _REPLY_BATCH_CAP).

    Per-lead agent_enabled gates manager takeovers; the NOT-EXISTS pending guard stops
    a second generation while a queued reply waits out its human-typing delay.

    'sending' counts as un-sent too: a reply queued then picked up by send_outbox sits in
    'sending' while its (slow, under-load) IG call runs, and last_out_at only updates on
    completion — so a guard on 'pending' alone let a second generation fire in that window
    and shipped a near-duplicate (thread 4579, 2026-07-19, server load 69)."""
    pending = (
        select(Outbox.id)
        .where(Outbox.thread_id == ChannelThread.id,
               Outbox.status.in_(("pending", "sending")))  # type: ignore[attr-defined]
        .exists()
    )
    cutoff = datetime.now(UTC).replace(tzinfo=None) - _AWAITING_REPLY_MAX_AGE
    # Коннектор, которому на этом филиале запрещено отвечать, — то же самое, что выключенный
    # канал строкой ниже, и по той же причине: текст будет сочинён, оплачен брокеру и отвергнут
    # на отправке. Настройку называет сам коннектор (ConnectorSpec.replies_setting), значение
    # берётся у филиала — приём при этом продолжает работать, молчат только ответы.
    from app.modules.settings.service import (  # noqa: PLC0415
        get_channel_settings,
        get_settings,
    )
    muted = mute_reply_kinds(await get_settings(session, branch_id))
    # …и КАНАЛЫ, переведённые в режим чтения. Тот же довод, но на ступень ниже: у
    # филиала бывает три вотсапа, и замолчать должен тот, где сидит живой человек, а
    # не весь вид коннектора. Настройки поканальные и кэшируются на 30 секунд, так что
    # это несколько попаданий в кэш, а не запрос на тред.
    silent: list[int] = []
    for ch in await active_channels(session, branch_id):
        if ch.id is not None and not (
            await get_channel_settings(session, branch_id, ch.id)
        ).replies_enabled:
            silent.append(ch.id)
    rows = await session.exec(
        select(ChannelThread.id)
        .join(Lead, Lead.id == ChannelThread.lead_id)  # type: ignore[arg-type]
        # inactive channel = undeliverable: generating a reply for it burns tokens into a
        # queue the sender can't drain (2026-07-13: 27 rows for the switched-off Meta channel)
        .join(Channel, Channel.id == ChannelThread.channel_id)  # type: ignore[arg-type]
        .where(
            Lead.branch_id == branch_id,
            Channel.is_active.is_(True),  # type: ignore[attr-defined]
            Channel.kind.not_in(muted) if muted else True_(),  # type: ignore[attr-defined]
            Channel.id.not_in(silent) if silent else True_(),  # type: ignore[attr-defined]
            # A manager's number used to be excluded here as well. It no longer is, and that
            # is the point: 295 of the 302 leads on those numbers have no other channel, so
            # the exclusion made "hand the lead back to Stepan" a switch that could never do
            # anything. What stops the bot there is the lead's own state — ingest moves them
            # to MANAGER with the switch off the moment the thread appears, and only a manager
            # turning it back on lets a line out (Lead.agent_enabled, two conditions below).
            #
            # Answer where the person is ACTUALLY waiting. A consolidated lead has several
            # threads, and replying into an older one answers a conversation they left —
            # while the message they just sent sits unanswered somewhere else.
            #
            # This is also how a read-only channel silences the lead ENTIRELY rather than
            # just itself: if their newest message came to a manager's WhatsApp, the only
            # eligible thread is one the filter above excludes, so nothing is generated. The
            # manager has them; a second answer from us on Instagram would be two voices
            # from one school. (Owner's rule, 2026-08-07.)
            ChannelThread.last_in_at == (
                # aliased: correlating ChannelThread with itself leaves the subquery with no
                # FROM clause of its own, and SQLAlchemy refuses rather than guessing.
                select(func.max(_newest.last_in_at))
                .where(_newest.lead_id == Lead.id)
                .correlate(Lead)
                .scalar_subquery()
            ),
            Lead.agent_enabled.is_(True),  # type: ignore[attr-defined]
            Lead.is_blocked.is_(False),  # type: ignore[attr-defined]
            Lead.stage.not_in(BOT_SILENT_STAGES),  # type: ignore[attr-defined]
            ChannelThread.last_in_at.is_not(None),  # type: ignore[attr-defined]
            ChannelThread.last_in_at >= cutoff,  # type: ignore[operator]
            (ChannelThread.last_out_at.is_(None))  # type: ignore[attr-defined]
            | (ChannelThread.last_out_at < ChannelThread.last_in_at),  # type: ignore[operator]
            ~pending,
        )
        .order_by(ChannelThread.last_in_at.asc())  # type: ignore[union-attr]
        .limit(limit if limit is not None else _REPLY_BATCH_CAP)
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


# A big backlog draining at once risks the same ARQ-timeout-then-retry hazard as
# threads_awaiting_reply — but here a retry-induced duplicate means an actual SECOND
# send to IG, not just a second LLM call. Cap per tick; the hourly/daily send cap
# already limits real throughput to far below this, so it only bites during a burst.
_SEND_BATCH_CAP = settings().send_batch_cap


async def threads_with_pending_outbox(session: AsyncSession, branch_id: int) -> list[int]:
    """Thread ids with a queued (pending) outbox line — a thread with a real REPLY
    (agent/manager) waiting goes first, a thread with ONLY a proactive touch (follow-up or
    reactivation) queued goes last. send_outbox drains threads in this order, so when the
    hourly/daily send cap is tight, a reply to something the lead just said is never crowded
    out by a proactive nudge. Reactivation counts as proactive (NOT a reply): a mass dormant
    harvest must never delay an answer to an active lead. Oldest-queued-first tiebreaker."""
    has_reply = func.max(
        case((Outbox.source.notin_(("followup", "reactivation")), 1), else_=0))
    earliest = func.min(Outbox.id)
    rows = await session.exec(
        select(Outbox.thread_id)
        # exclude inactive channels: their rows are unsendable, and being the oldest they
        # monopolised every _SEND_BATCH_CAP slot — live channels' rows never made the batch
        # and the whole outbox looked frozen (2026-07-13, the switched-off Meta channel)
        .join(ChannelThread, ChannelThread.id == Outbox.thread_id)  # type: ignore[arg-type]
        .join(Channel, Channel.id == ChannelThread.channel_id)  # type: ignore[arg-type]
        .where(
            Outbox.branch_id == branch_id,
            Outbox.status == "pending",
            Channel.is_active.is_(True),  # type: ignore[attr-defined]
        )
        .group_by(Outbox.thread_id)
        .order_by(has_reply.desc(), earliest)
        .limit(_SEND_BATCH_CAP)
    )
    return list(rows.scalars().all())


# A row is committed as 'sending' the instant before the network send (OutboxSender claims it
# so no other tick can re-send it). A worker restart — every deploy — between that commit and
# the send result strands the row in 'sending' forever. OutboxSender._sweep_stale_claims was
# built to recover exactly this, but it only runs for a thread that STILL has a PENDING row
# (that's the sole path into send_next); a thread whose ONLY row is the stuck claim is never
# visited, so it hangs indefinitely AND blocks regeneration — threads_awaiting_reply's ~pending
# guard counts 'sending' as "a reply is in flight", so the lead is never answered. Live
# 2026-07-20: threads 4655/4661/4689 sat mute for hours; 45 rows stranded back to 07-17. So
# sweep branch-wide each send tick, independent of whether any pending row exists.
_STALE_SENDING_MIN = 10


async def sweep_stale_sending(session: AsyncSession, branch_id: int, now: datetime) -> int:
    """Recover outbox rows orphaned in 'sending' (claimed more than _STALE_SENDING_MIN ago),
    branch-wide. Marked 'canceled', NOT 'failed': a crashed-mid-send is a silent system artifact
    (outcome unknown, never resent — a duplicate is the worse failure), so it must NOT surface in
    the chat as a red '✗ send failed · retry?' bubble (fetch_pending shows 'failed', not
    'canceled') — that retry button would risk a double-send, and the thread already self-heals
    (canceled ∉ ~pending, so it re-enters threads_awaiting_reply and a FRESH reply is generated).
    A REAL send failure (Meta window, IG error) is marked 'failed' by OutboxSender and stays
    visible/retryable. Returns rows swept."""
    from sqlalchemy import update  # noqa: PLC0415
    res = await session.execute(
        update(Outbox)
        .where(Outbox.branch_id == branch_id, Outbox.status == "sending",
               Outbox.sent_at < now - timedelta(minutes=_STALE_SENDING_MIN))
        .values(status="canceled", error="crashed mid-send — outcome unknown, not retried"))
    return res.rowcount or 0


async def sweep_undeliverable(session: AsyncSession, branch_id: int) -> int:
    """Retire pending rows whose channel is switched off — they can never be sent.

    threads_with_pending_outbox filters inactive channels out of the send batch on purpose
    (2026-07-13: their rows were the oldest, so they took every slot and the whole outbox
    looked frozen). Correct, but nothing then retired them, so they sat 'pending' for ever
    and the admin queue showed permanent "4 messages waiting" for a channel nobody had used
    in weeks — a counter that never moves is a counter nobody reads, and it hid whether any
    REAL send was stuck behind it (live 30.07.2026, thread 2569 on the disabled Meta channel).

    'skipped', not 'failed': nothing failed and nothing is retryable — the channel is off by
    a deliberate operator decision. Turning the channel back on does not resurrect these; a
    fresh reply is generated instead, which is what we want after weeks of silence."""
    from sqlalchemy import update  # noqa: PLC0415
    inactive = (
        select(ChannelThread.id)
        .join(Channel, Channel.id == ChannelThread.channel_id)  # type: ignore[arg-type]
        .where(Channel.is_active.is_(False))  # type: ignore[attr-defined]
    )
    res = await session.execute(
        update(Outbox)
        .where(Outbox.branch_id == branch_id, Outbox.status == "pending",
               Outbox.thread_id.in_(inactive))  # type: ignore[attr-defined]
        .values(status="skipped", error="channel switched off — undeliverable, not retried"))
    return res.rowcount or 0


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


async def build_channel_port(session: AsyncSession, channel: Channel) -> ChannelPort:
    """Resolve a live ChannelPort from the channel's stored credentials.

    A one-line dispatch on purpose: which secrets a connector needs, and how it turns them
    into a transport, is the connector's own business (app/connectors/<kind>.py). This was a
    50-line if-chain here, which is why adding a connector meant editing the worker.

    Callers guard with try/except and skip a channel that isn't ready (logged)."""
    spec = spec_for(channel.kind)
    if spec is None:
        raise KeyError(f"no connector registered for channel kind {channel.kind}")
    return await spec.build_port(session, channel)
