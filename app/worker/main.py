"""ARQ worker entrypoint — thin scheduled tasks over the branch use-cases.

Each task is pure orchestration: open a session, walk ACTIVE tenants, and delegate to a
branch-scoped use-case (IngestService / ReplyService / OutboxSender). All domain logic
lives in the modules. Importing this module touches no Redis and no DB — the worker is
profile-gated and started only by `arq app.worker.main.WorkerSettings`."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from arq import cron, func
from arq.connections import RedisSettings
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Channel
from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM, BrokerUnavailable
from app.adapters.notify.telegram import TelegramNotifier
from app.config import settings
from app.domain.enums import ChannelKind, SessionStatus
from app.modules.conversation.followup import FollowupService
from app.modules.conversation.outbox import OutboxSender
from app.modules.conversation.reactivation import ReactivationService
from app.modules.conversation.reply import ReplyService
from app.modules.conversation.repository import ThreadRepo
from app.modules.knowledge.service import KnowledgeService
from app.modules.knowledge.source import effective_kb_branch
from app.modules.leads.ingest import IngestService
from app.modules.settings.service import get_channel_settings, get_settings
from app.ports.notify import NotifierPort

from . import breaker, wiring

logger = logging.getLogger(__name__)

# Anti-ban: the cron fires at a fixed second; poll IG a random moment into the minute
# instead so the private-API calls don't hit on a machine-regular tick (S1 jitter).
_INGEST_JITTER_S = settings().ingest_jitter_s


async def ingest_active_channels(ctx: dict[str, Any]) -> int:
    """Dispatcher: fan out one ingest job per branch so a slow poll on one branch never
    stalls the others (see _fan_out_per_branch)."""
    return await _fan_out_per_branch(ctx, "ingest_branch")


async def ingest_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """Pull new inbound for every active channel of ONE branch. Returns rows stored.

    Each channel ingests in its OWN transaction: a slow poll that overruns the cron can
    overlap the next run, and two runs racing past the dedup check would hit the
    (channel_id, external_id) unique constraint — that must abort only the racing
    channel, not the whole cycle. The constraint itself is the backstop that makes the
    concurrent insert harmless."""
    await asyncio.sleep(random.uniform(0, _INGEST_JITTER_S))  # noqa: S311 — jitter, not crypto
    async with session_scope() as session:
        channels = await wiring.active_channels(session, branch_id)
    stored = 0
    for channel in channels:
        stored += await _ingest_channel(branch_id, channel.id)
    return stored


async def _ingest_channel(branch_id: int, channel_id: int) -> int:
    """Ingest one channel in its own transaction; a concurrent-run race is a no-op."""
    try:
        async with session_scope() as session:
            channel = await session.get(Channel, channel_id)
            if channel is None or not channel.is_active:
                return 0
            try:
                port = await wiring.build_channel_port(session, channel)
            except (NotImplementedError, KeyError, RuntimeError) as exc:
                logger.warning("skip ingest channel %s: %s", channel_id, exc)
                return 0
            if not await _healthy(session, branch_id, channel, port):
                return 0  # checkpoint/expired session — frozen until re-login
            try:
                inbound = await port.fetch_inbound()
            except RuntimeError as exc:
                # e.g. own IG id unresolvable — the transport fails the poll on purpose
                # rather than misclassify our own messages as inbound. Skip THIS channel
                # only; other channels/branches keep polling.
                logger.warning("skip ingest channel %s: fetch failed: %s", channel_id, exc)
                return 0
            cfg = await get_settings(session, branch_id)
            svc = IngestService(session, branch_id, notifier=_build_notifier(cfg))
            return len(await svc.ingest(channel_id, inbound))
    except IntegrityError:
        logger.info("ingest channel %s: rows already stored by a concurrent run", channel_id)
        return 0
    except Exception:
        logger.exception("ingest failed channel %s", channel_id)
        return 0


# Adaptive polling: the base ingest runs every 2 min (anti-ban footprint fixed). During a
# LIVE back-and-forth the lead should not wait a whole 2-min tick for each of their replies to
# be seen, so an interleaved poll on the OFF minutes picks up ONLY channels that currently have
# an active conversation — a thread with a lead message in the last few minutes that the bot
# hasn't answered yet. An idle account is never polled extra, so the private-API footprint only
# rises when a real human would also be checking more often (a live chat), which reads as more
# human, not less. Bounded window keeps a stale thread from holding a channel in fast-poll.
_ACTIVE_CONVO_WINDOW_MIN = 6


async def ingest_active_conversations(ctx: dict[str, Any]) -> int:
    """Off-minute dispatcher: interleave an extra ingest for branches with a live conversation."""
    return await _fan_out_per_branch(ctx, "ingest_active_branch", gate_platform=True)


async def ingest_active_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """Poll ONLY channels with an active (recent, unanswered) conversation — skip idle ones so
    the baseline anti-ban cadence is untouched for accounts nobody is chatting with right now."""
    await asyncio.sleep(random.uniform(0, _INGEST_JITTER_S))  # noqa: S311 — jitter, not crypto
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=_ACTIVE_CONVO_WINDOW_MIN)
    async with session_scope() as session:
        channels = await wiring.active_channels(session, branch_id)
        active_ids = await _channels_with_live_convo(
            session, [c.id for c in channels], cutoff)
    stored = 0
    for channel in channels:
        if channel.id in active_ids:
            stored += await _ingest_channel(branch_id, channel.id)
    return stored


async def _channels_with_live_convo(
    session: AsyncSession, channel_ids: list[int], cutoff: datetime,
) -> set[int]:
    """Channel ids that have a thread with a lead message since `cutoff` the bot hasn't caught
    up to (last_in newer than last_out) — the live-conversation signal. `cutoff` is a naive
    UTC datetime to match channel_thread's `timestamp without time zone` columns."""
    if not channel_ids:
        return set()
    from sqlalchemy import bindparam, text  # noqa: PLC0415
    stmt = text(
        "SELECT DISTINCT channel_id FROM channel_thread"
        " WHERE channel_id IN :ids"
        "   AND last_in_at IS NOT NULL"
        "   AND last_in_at > :cutoff"
        "   AND (last_out_at IS NULL OR last_in_at > last_out_at)"
    ).bindparams(bindparam("ids", expanding=True))
    rows = (await session.execute(stmt, {"ids": channel_ids, "cutoff": cutoff})).all()
    return {r[0] for r in rows}


async def _healthy(session: AsyncSession, branch_id: int, channel: Channel, port) -> bool:
    """Gate a channel on its live session status; a CHALLENGE freezes it + alerts once.

    Freezing = flip the ChannelSession out of ACTIVE so build_channel_port skips this
    channel in every loop (ingest/reply/send/unsend) until a re-login restores it."""
    try:
        status = await port.session_status()
    except Exception:
        logger.exception("session_status failed channel %s", channel.id)
        return True  # transient probe error — don't freeze on a flaky check
    if status == SessionStatus.ACTIVE:
        return True
    flipped = await wiring.mark_session_status(session, channel.id, status)
    logger.error(
        "ACCOUNT CHECKPOINT branch=%d channel=%s status=%s — frozen until re-login",
        branch_id, channel.id, status,
    )
    if flipped:
        cfg = await get_settings(session, branch_id)
        notifier = _build_notifier(cfg)
        if notifier is not None:
            try:
                await notifier.send(  # channel-level alert → group General, no per-lead topic
                    text=(f"⚠️ IG channel {channel.handle or channel.id} needs re-login\n"
                          f"IG-канал {channel.handle or channel.id} требует ре-логина"),
                )
            except Exception:
                logger.warning("checkpoint alert failed channel %s", channel.id)
    return False


def _build_notifier(branch_cfg: object) -> NotifierPort | None:
    """Build a TelegramNotifier for the branch; return None if config is incomplete."""
    bot_token = settings().tg_bot_token
    tg_group = getattr(branch_cfg, "tg_group_id", "")
    if not bot_token or not tg_group:
        return None
    try:
        return TelegramNotifier(bot_token=bot_token, group_chat_id=int(tg_group))
    except (ValueError, TypeError) as exc:
        logger.warning("cannot build TelegramNotifier: %s", exc)
        return None


async def _platform_agent_on(session: AsyncSession) -> bool:
    """Whole-platform kill switch (app_setting branch_id IS NULL). Default ON."""
    from sqlalchemy import text  # noqa: PLC0415

    row = (await session.execute(
        text("SELECT value FROM app_setting"
             " WHERE branch_id IS NULL AND key='agent_enabled_platform'"))).first()
    return (row[0] or "true").strip().lower() in ("true", "1", "yes") if row else True


async def _fan_out_per_branch(
    ctx: dict[str, Any], job_name: str, *, gate_platform: bool = False,
    gate_broker: bool = False,
) -> int:
    """Dispatch one arq job per active branch, so branches run CONCURRENTLY (bounded by
    worker_max_jobs) and INDEPENDENTLY — a slow or failing branch holds a single job slot
    and times out on its own without delaying or aborting the tick for other branches. This
    generalises the reply_pending→generate_one_reply fan-out to every cron task.

    The `{job_name}:{branch_id}` job id dedups: a branch whose previous job is still running
    when the next tick fires does not stack a second job (same idempotency trick as the
    reply:{thread_id} id). gate_platform short-circuits the whole fan-out when the kill switch
    is OFF, so an operator flipping it mid-incident stops new work immediately."""
    if gate_broker and (redis := ctx.get("redis")) is not None and await breaker.is_open(redis):
        # Proactive work (follow-ups) — skip entirely while the broker looks down; it is not
        # urgent and the reply canary will clear the guard within a tick when the broker heals.
        # (The reply path does NOT use this: it sends a canary instead, see reply_pending_branch.)
        logger.warning("broker guard open — skip %s for all branches", job_name)
        return 0
    async with session_scope() as session:
        if gate_platform and not await _platform_agent_on(session):
            logger.info("platform agent OFF — skip %s for all branches", job_name)
            return 0
        branches = await wiring.active_branches(session)
    redis = ctx["redis"]
    enqueued = 0
    for branch in branches:
        assert branch.id is not None
        job = await redis.enqueue_job(job_name, branch.id, _job_id=f"{job_name}:{branch.id}")
        if job is not None:  # None → this branch's job is already in flight; skip
            enqueued += 1
    return enqueued


async def reply_pending(ctx: dict[str, Any]) -> int:
    """Top dispatcher: fan out one reply_pending_branch job per branch (branch-independent),
    gated by the platform kill switch."""
    return await _fan_out_per_branch(ctx, "reply_pending_branch", gate_platform=True)


async def reply_pending_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """Per-branch dispatcher: enqueue one generate_one_reply job per awaiting thread of THIS
    branch. Returns enqueued.

    Does NO broker work itself — it just finds threads (lead spoke last, bot-owned, no pending
    reply) and hands each to its own ARQ job. Deduped by _job_id=reply:{thread_id}, so a thread
    already being generated is not double-enqueued; concurrency is bounded by worker_max_jobs.
    This replaces the old batch loop where one slow generation blocked the other threads in the
    tick and a >budget generation got killed and retried (double-billed).

    Quiet hours do NOT apply here — they throttle proactive follow-ups, never a reply to
    something the lead already said. A lead who writes at 3am still gets answered."""
    redis = ctx["redis"]
    cap = settings().reply_dispatch_cap
    try:
        async with session_scope() as session:
            cfg = await get_settings(session, branch_id)
            if not cfg.agent_enabled:
                logger.info("branch %s: agent disabled — skip reply_pending", branch_id)
                return 0
            thread_ids = await wiring.threads_awaiting_reply(session, branch_id, limit=cap)
    except Exception:
        logger.exception("reply_pending: branch=%s dispatch failed, skipping", branch_id)
        return 0
    if thread_ids and await breaker.is_open(redis):
        # The broker looked down last tick. Don't stampede it with the whole backlog — send
        # ONE canary. Its call either succeeds (clearing the guard, so the next tick runs the
        # full fleet) or fails (re-tripping). The rest wait one tick, not a fixed cooldown.
        logger.warning("branch %s: broker guard open — 1 canary reply (%d awaiting)",
                       branch_id, len(thread_ids))
        thread_ids = thread_ids[:1]
    enqueued = 0
    for thread_id in thread_ids:
        job = await redis.enqueue_job(
            "generate_one_reply", branch_id, thread_id,
            _job_id=f"reply:{thread_id}")  # None → a job for this thread is already in flight
        if job is not None:
            enqueued += 1
    return enqueued


_REPLY_INFLIGHT = "reply:inflight"  # redis sorted-set: marker -> start-time, for the slot cap


async def generate_one_reply(ctx: dict[str, Any], branch_id: int, thread_id: int) -> bool:
    """One thread's decide+enqueue, as its OWN ARQ job so it can poll the broker to completion
    on its own timeout (settings.reply_job_timeout_s) without a shared tick budget killing it.

    Idempotent by construction: the reply:{thread_id} _job_id stops a second in-flight job, the
    advisory lock guards concurrent runs, and the NOT-EXISTS pending-outbox guard stops a second
    generation once a reply is queued. Kill-switch re-checked here in case it flipped OFF between
    dispatch and execution."""
    redis = ctx["redis"]
    now = time.time()
    marker = f"{thread_id}:{now}"
    # Cap concurrent SLOW reply jobs below worker_max_jobs so a burst (a 300-thread re-enable
    # while the broker is slow) can't fill every worker slot and starve ingest/send. A sorted
    # set keyed by start-time is leak-proof: a crashed job's marker ages out of the count.
    await redis.zremrangebyscore(_REPLY_INFLIGHT, 0, now - settings().reply_job_timeout_s)
    await redis.zadd(_REPLY_INFLIGHT, {marker: now})
    llm = BrokerLLM()  # created up front so the failure log can always report its call count
    try:
        if await redis.zcard(_REPLY_INFLIGHT) > settings().reply_max_concurrency:
            return False  # over cap → leave slots for other tasks; re-dispatched next tick
        async with session_scope() as session:
            if not await _platform_agent_on(session):
                return False
            if not await wiring.try_lock_thread(session, thread_id):
                return False  # another job owns this thread right now
            cfg = await get_settings(session, branch_id)
            kb = await effective_kb_branch(session, branch_id)  # shared-KB link, if any
            reply = ReplyService(
                session, branch_id, llm, KnowledgeService(session, kb, llm),
                branch_settings=cfg, notifier=_build_notifier(cfg),
                broker_budget_s=settings().reply_broker_budget_s,
            )
            started = time.time()
            decision = await reply.decide(thread_id)
            if getattr(llm, "calls", 0) > 0:
                # The broker answered (decide made a call and didn't raise gateway-down) — clear
                # the guard so the whole fleet resumes next tick. Recovery reopens us, not a
                # timer: a key rotation that heals the broker restores full speed immediately.
                await breaker.clear(redis)
            if decision is None:
                logger.info(
                    "reply branch=%d thread=%d held in %.1fs (%d broker calls, no reply)",
                    branch_id, thread_id, time.time() - started, getattr(llm, "calls", 0))
                return False
            queued = await reply.enqueue_reply(thread_id, decision) is not None
            logger.info(
                "reply branch=%d thread=%d %s in %.1fs (%d broker calls incl. retries)",
                branch_id, thread_id, "queued" if queued else "skipped",
                time.time() - started, getattr(llm, "calls", 0))
            return queued
    except BrokerUnavailable as exc:
        # The gateway is down, not this thread's fault — trip the guard so the next tick sends
        # ONE canary instead of the whole backlog. Cleared the instant any call succeeds.
        await breaker.trip(redis)
        logger.warning("reply branch=%d thread=%d: broker down (%s) — guard tripped",
                       branch_id, thread_id, exc)
        return False
    except Exception:
        logger.exception("reply failed branch=%d thread=%d after %d broker calls",
                         branch_id, thread_id, getattr(llm, "calls", 0))
        return False
    finally:
        await redis.zrem(_REPLY_INFLIGHT, marker)


async def schedule_followups(ctx: dict[str, Any]) -> int:
    """Set follow-up timers for cold threads and queue proactive messages.

    Runs every 10 minutes (between ingest and reply). Quiet hours do NOT block queueing —
    only the send (OutboxSender.send_next) holds a follow-up until quiet hours end, so a
    nudge queued at 23:50 is ready to go out the instant quiet hours lift instead of
    losing a whole cron cycle. Only fires when followup_enabled=true in branch settings.

    Each due THREAD runs in its OWN transaction (mirrors reply_pending/_reply_thread) —
    a branch can have hundreds of due threads, and a slow/degraded broker can make the
    whole cron job hit its ARQ timeout mid-list. Before this split, the entire branch's
    due-thread loop shared one open transaction that only committed at the very end, so a
    timeout kill silently discarded every follow-up already generated earlier in that same
    cycle — broker calls could log ok=True and still never reach the outbox (2026-07-07)."""
    return await _fan_out_per_branch(ctx, "schedule_followups_branch", gate_platform=True,
                                     gate_broker=True)


async def schedule_followups_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """One branch's follow-up harvest. followup_enabled and the schedule are per-connector
    now (resolved inside FollowupService.due_threads), so the branch-level enabled check is
    gone — a branch runs if ANY of its channels wants follow-ups."""
    queued = 0
    llm = BrokerLLM()
    try:
        async with session_scope() as session:
            branch_cfg = await get_settings(session, branch_id)
            kb = await effective_kb_branch(session, branch_id)
            knowledge = KnowledgeService(session, kb, llm)
            svc = FollowupService(session, branch_id, llm, knowledge, branch_cfg,
                                  notifier=_build_notifier(branch_cfg))
            due = await svc.due_threads(datetime.now(UTC).replace(tzinfo=None))
    except Exception:
        logger.exception(
            "schedule_followups: branch=%s bookkeeping failed, skipping", branch_id)
        return 0
    for thread_id, product_slug, sent_so_far in due:
        if await _queue_one_followup(branch_id, thread_id, product_slug, sent_so_far, llm):
            queued += 1  # timers are armed by OutboxSender after bot sends
    return queued


async def _queue_one_followup(
    branch_id: int, thread_id: int, product_slug: str | None, sent_so_far: int,
    llm: BrokerLLM,
) -> bool:
    """One thread's follow-up generate+queue in its own transaction; isolates failures per
    thread so a poison thread (bad LLM JSON, broker error) or a later job-timeout can't
    roll back follow-ups already committed for earlier threads this cycle."""
    try:
        async with session_scope() as session:
            branch_cfg = await get_settings(session, branch_id)
            kb = await effective_kb_branch(session, branch_id)
            knowledge = KnowledgeService(session, kb, llm)
            svc = FollowupService(session, branch_id, llm, knowledge, branch_cfg,
                                  notifier=_build_notifier(branch_cfg))
            return await svc.queue_one(thread_id, product_slug, sent_so_far)
    except Exception:
        logger.exception("followup failed branch=%d thread=%d", branch_id, thread_id)
        return False


async def reactivate_dormant(ctx: dict[str, Any]) -> int:
    """Dispatcher: fan out one dormant-reactivation harvest per branch (opt-in, gated)."""
    return await _fan_out_per_branch(ctx, "reactivate_dormant_branch", gate_platform=True,
                                     gate_broker=True)


async def reactivate_dormant_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """One branch's dormant harvest — a single personalized touch per due lead, per-thread tx."""
    queued = 0
    llm = BrokerLLM()
    try:
        async with session_scope() as session:
            branch_cfg = await get_settings(session, branch_id)
            if not branch_cfg.reactivation_enabled:
                return 0
            kb = await effective_kb_branch(session, branch_id)
            svc = ReactivationService(session, branch_id, llm,
                                      KnowledgeService(session, kb, llm), branch_cfg)
            due = await svc.due(datetime.now(UTC).replace(tzinfo=None))
    except Exception:
        logger.exception("reactivate_dormant: branch=%s bookkeeping failed, skipping", branch_id)
        return 0
    for thread_id, _slug, lead_id in due:
        if await _reactivate_one(branch_id, thread_id, lead_id, llm):
            queued += 1
    return queued


async def _reactivate_one(
    branch_id: int, thread_id: int, lead_id: int, llm: BrokerLLM,
) -> bool:
    """One dormant lead's reactivation in its own transaction (isolates per-lead failures)."""
    try:
        async with session_scope() as session:
            branch_cfg = await get_settings(session, branch_id)
            kb = await effective_kb_branch(session, branch_id)
            svc = ReactivationService(session, branch_id, llm,
                                      KnowledgeService(session, kb, llm), branch_cfg)
            return await svc.reactivate_one(thread_id, lead_id)
    except Exception:
        logger.exception("reactivation failed branch=%d thread=%d", branch_id, thread_id)
        return False


async def learning_audit(ctx: dict[str, Any]) -> int:
    """Dispatcher: weekly learning-audit report per branch (propose-only, TG report)."""
    return await _fan_out_per_branch(ctx, "learning_audit_branch", gate_platform=True)


async def learning_audit_branch(ctx: dict[str, Any], branch_id: int) -> int:
    from app.modules.learning.audit import LearningAudit  # noqa: PLC0415
    try:
        async with session_scope() as session:
            cfg = await get_settings(session, branch_id)
            if not getattr(cfg, "learning_audit_enabled", False):
                return 0
            await LearningAudit(session, branch_id, _build_notifier(cfg)).run()
            return 1
    except Exception:
        logger.exception("learning_audit failed branch=%s", branch_id)
        return 0


async def send_outbox(ctx: dict[str, Any]) -> int:
    """Drain one pending outbox line per thread through its channel. Returns rows attempted.

    Per-thread transaction: an already-delivered IG send is committed before the next
    thread runs, so a later failure can never roll back a 'sent' row into a re-send."""
    # The emergency kill-switch must stop the ACTUAL IG writes, not just generation — gate the
    # fan-out so an operator flipping it OFF mid-incident stops draining the outbox everywhere.
    return await _fan_out_per_branch(ctx, "send_outbox_branch", gate_platform=True)


async def send_outbox_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """Drain pending outbox lines for ONE branch, each thread in its own transaction so an
    already-delivered send is committed before the next runs (a later failure can't roll a
    'sent' row back into a re-send). sending_enabled is per-connector, but is read here as a
    branch cap first — the per-thread OutboxSender re-resolves it per channel."""
    attempted = 0
    async with session_scope() as session:
        swept = await wiring.sweep_stale_sending(
            session, branch_id, datetime.now(UTC).replace(tzinfo=None))
        if swept:
            logger.warning(
                "branch=%d: swept %d outbox rows orphaned in 'sending' → failed "
                "(they re-enter awaiting for a fresh reply)", branch_id, swept)
        channels = {c.id: c for c in await wiring.active_channels(session, branch_id)}
        thread_ids = await wiring.threads_with_pending_outbox(session, branch_id)
    for thread_id in thread_ids:
        try:
            async with session_scope() as session:
                attempted += await _send_thread(session, branch_id, thread_id, channels)
        except Exception:
            logger.exception("send failed branch=%d thread=%d", branch_id, thread_id)
    return attempted


async def _send_thread(
    session: AsyncSession,
    branch_id: int,
    thread_id: int,
    channels: dict[int | None, Channel],
) -> int:
    """Send the next due line of one thread via its channel; skip when wiring is absent.

    Same advisory lock as reply_pending, same reason: a slow send (network, IG throttling)
    can push this tick past the next cron firing, and two overlapping ticks both reading the
    same 'pending' row with no lock means TWO real sends to the lead (confirmed live: thread
    1730 got the same reply delivered twice, ~15s apart, each tick unaware of the other)."""
    if not await wiring.try_lock_thread(session, thread_id):
        return 0  # another tick already owns this thread's outbox right now
    thread = await ThreadRepo(session, branch_id).by_id(thread_id)
    if thread is None:
        return 0
    channel = channels.get(thread.channel_id)
    if channel is None:
        return 0
    try:
        port = await wiring.build_channel_port(session, channel)
    except (NotImplementedError, KeyError, RuntimeError) as exc:
        logger.warning("skip send thread %s: %s", thread_id, exc)
        return 0
    sender = OutboxSender(session, branch_id, port)
    return 1 if await sender.send_next(thread_id) is not None else 0


# IG's private-API unsend is slow (observed 40-90s under IG-side throttling); each single
# revoke is bounded by DeletionService's own asyncio.wait_for, but a thread with many
# pending messages can still take long, so batching many threads' revokes in one tick can
# overrun ARQ's worker_job_timeout_s. Revoking is idempotent-ish (a retried revoke of an
# already-gone message fails gracefully) and each thread now commits in its OWN transaction
# (a kill+retry can't roll back a committed unsend), but the per-TICK cap still bounds how
# many threads a single tick attempts so at least SOME progress commits every cycle.
_DELETION_THREAD_CAP = settings().deletion_thread_cap


async def _try_build_port(session: AsyncSession, channel: Channel, capability: str):  # noqa: ANN202
    """Build the channel port, or None (logged) when it can't be built or lacks `capability`
    — the shared skip path for the maintenance crons (deletions/profiles/media)."""
    try:
        port = await wiring.build_channel_port(session, channel)
    except (NotImplementedError, KeyError, RuntimeError) as exc:
        logger.warning("skip channel %s: %s", channel.id, exc)
        return None
    return port if hasattr(port, capability) else None


async def process_deletions(ctx: dict[str, Any]) -> int:
    """Carry out requested IG unsends: revoke in IG first, delete locally on success.

    Gated by the platform kill-switch (an unsend is a real outbound IG write). Each thread
    runs in its OWN transaction (like reply_pending/_reply_thread): before, the whole
    nested loop shared one transaction across all branches doing 40-90s IG revokes each — a
    kill mid-loop (job-timeout) rolled back every already-committed local deletion and
    re-revoked on retry. The thread cap is now a per-TICK budget, not per-channel."""
    # Real IG writes → gate the fan-out on the kill switch (mirrors send_outbox).
    return await _fan_out_per_branch(ctx, "process_deletions_branch", gate_platform=True)


async def process_deletions_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """Carry out requested IG unsends for ONE branch: revoke in IG first, delete locally on
    success. Each thread runs in its OWN transaction; the thread cap is now a PER-BRANCH
    per-tick budget (was shared across all branches, which coupled them — a busy branch could
    starve another's unsends)."""
    from app.modules.conversation.deletions import DeletionService  # noqa: PLC0415
    done = 0
    budget = _DELETION_THREAD_CAP  # threads unsent per tick for THIS branch
    try:
        async with session_scope() as session:
            work: list[tuple[Channel, str]] = []
            for channel in await wiring.active_channels(session, branch_id):
                if len(work) >= budget:
                    break  # only `budget` threads act this tick — don't scan/resolve more
                # cap the discovery scan and stop resolving once we have enough threads;
                # a large unsent backlog used to scan every row + do an N+1 by_id per thread
                # just to act on a handful (the rest drain over the next ticks).
                pending = await DeletionService(session, branch_id).pending(
                    channel.id, limit=budget * 4)
                seen: dict[int, str] = {}
                for msg in pending:
                    if len(seen) >= budget:
                        break
                    if msg.thread_id not in seen:
                        thread = await ThreadRepo(session, branch_id).by_id(msg.thread_id)
                        if thread is not None:
                            seen[msg.thread_id] = thread.external_thread_id
                for ext in dict.fromkeys(seen.values()):
                    work.append((channel, ext))
    except Exception:
        logger.exception("process_deletions: branch=%s bookkeeping failed", branch_id)
        return 0
    for channel, ext_thread in work:
        if budget <= 0:
            break
        done += await _process_one_deletion(branch_id, channel, ext_thread)
        budget -= 1
    return done


async def _process_one_deletion(branch_id: int, channel: Channel, ext_thread: str) -> int:
    """One thread's unsend in its own transaction — a failure or job-timeout kill can't
    roll back unsends already committed for other threads this tick."""
    from app.modules.conversation.deletions import DeletionService  # noqa: PLC0415
    try:
        async with session_scope() as session:
            port = await _try_build_port(session, channel, "revoke")
            if port is None:
                return 0  # can't build / channel doesn't support unsend
            return await DeletionService(session, branch_id).process(
                channel.id, ext_thread, port)  # type: ignore[arg-type]
    except Exception:
        logger.exception(
            "unsend failed branch=%d channel=%s thread=%s", branch_id, channel.id, ext_thread)
        return 0


async def sync_crm(ctx: dict[str, Any]) -> int:
    """CRM sync, both directions: push unsynced manager alerts out (crm_enabled), and
    pull lead state in to stand down leads a manager already owns (crm_read_enabled).

    Each branch runs in its OWN transaction so one branch's DB error can't roll back
    another branch's already-flushed CRM push rows."""
    return await _fan_out_per_branch(ctx, "sync_crm_branch")


async def sync_crm_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """One branch's CRM sync, both directions: push unsynced manager alerts out (crm_enabled),
    and pull lead state in to stand down leads a manager already owns (crm_read_enabled). Push
    and pull are separately try-wrapped so one direction's error can't lose the other."""
    from app.adapters.crm import CrmWebhook  # noqa: PLC0415
    from app.modules.crm import CrmSyncService  # noqa: PLC0415
    from app.modules.crm.gate import build_crm_reader  # noqa: PLC0415
    from app.modules.crm.pull import CrmPullService  # noqa: PLC0415
    synced = 0
    try:
        async with session_scope() as session:
            synced += await CrmSyncService(session, branch_id, CrmWebhook()).sync_pending()
    except Exception:
        logger.exception("crm push failed branch=%d", branch_id)
    try:
        async with session_scope() as session:
            cfg = await get_settings(session, branch_id)
            reader = build_crm_reader(cfg)  # REST contract or the CRM's own MCP server
            await CrmPullService(session, branch_id, reader).sync_active()
    except Exception:
        logger.exception("crm pull failed branch=%d", branch_id)
    return synced


async def crm_rescue(ctx: dict[str, Any]) -> int:
    """Hourly (work hours only, enforced in the service): pick up leads the CRM's phone
    calls couldn't reach and have Stepan continue them in chat. Capped to a trickle per
    run; every send still passes the outbox caps and the CRM gate."""
    from app.modules.crm.rescue import CrmRescueService  # noqa: PLC0415
    total = 0
    async with session_scope() as session:
        branches = await wiring.active_branches(session)
    for branch in branches:
        assert branch.id is not None
        try:
            async with session_scope() as session:
                total += await CrmRescueService(session, branch.id, BrokerLLM()).run()
        except Exception:
            logger.exception("crm rescue failed branch=%d", branch.id)
    return total


async def ingest_comments(ctx: dict[str, Any]) -> int:
    """Hourly: fan out comment ingest+reply, one job per branch. Gated by the platform
    kill-switch — it posts PUBLIC replies, so an operator stopping the bot stops this too."""
    return await _fan_out_per_branch(ctx, "ingest_comments_branch",
                                     gate_platform=True, gate_broker=True)


async def ingest_comments_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """One branch: for each IG channel with comment replies ON, pull new comments under our
    posts and answer the ones worth it (within the comment caps). Returns replies posted.

    Its own transaction; per-channel try so one channel's failure can't abort the branch.
    Anti-ban jitter up front — the cron fires on a fixed second, so offset the private-API
    walk off the machine tick (same reason as ingest_branch)."""
    from app.modules.comments.service import CommentService  # noqa: PLC0415

    await asyncio.sleep(random.uniform(0, _INGEST_JITTER_S))  # noqa: S311 — jitter, not crypto
    posted = 0
    async with session_scope() as session:
        channels = await wiring.active_channels(session, branch_id)
    for channel in channels:
        if channel.kind != ChannelKind.INSTAGRAM:
            continue
        try:
            async with session_scope() as session:
                ch_cfg = await get_channel_settings(session, branch_id, channel.id)
                if not (ch_cfg.agent_enabled and ch_cfg.comment_replies_enabled):
                    continue
                kb = await effective_kb_branch(session, branch_id)
                knowledge = KnowledgeService(session, kb, BrokerLLM())
                svc = CommentService(session, branch_id, BrokerLLM(), knowledge, ch_cfg)
                port = await wiring.build_channel_port(session, channel)
                await svc.ingest(channel, port)
                posted += await svc.process(channel, port)
        except (NotImplementedError, KeyError, RuntimeError) as exc:
            logger.warning("comment ingest skip branch=%d channel=%s: %s",
                           branch_id, channel.id, exc)
        except Exception:
            logger.exception("comment ingest failed branch=%d channel=%s",
                             branch_id, channel.id)
    return posted


async def sync_ads(ctx: dict[str, Any]) -> int:
    """Fan out the Meta ad map + insight sync, one job per branch.

    Not gated by the platform kill-switch: this is read-only reporting, it sends nothing to
    a lead, so an operator stopping the bot mid-incident still wants their spend numbers."""
    return await _fan_out_per_branch(ctx, "sync_ads_branch")


async def sync_ads_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """One branch's ad attribution: map new lead media → ad, then refresh the insight window.

    Cheap by construction — the map walk is skipped entirely when no lead media is unmapped
    (the steady state), and insights cover only ads our own leads came from. Map and insights
    run in SEPARATE transactions so a throttled insight pull can't roll back a map row that
    was already resolved: the map is immutable and expensive to rediscover, insights are
    re-pulled next tick anyway."""
    from app.modules.ads.sync import AdSyncService  # noqa: PLC0415
    from app.modules.settings.service import get_settings  # noqa: PLC0415
    mapped = 0
    try:
        async with session_scope() as session:
            cfg = await get_settings(session, branch_id)
            mapped = await AdSyncService(session, branch_id, cfg).sync_map()
    except Exception:
        logger.exception("ad map sync failed branch=%d", branch_id)
    try:
        async with session_scope() as session:
            cfg = await get_settings(session, branch_id)
            await AdSyncService(session, branch_id, cfg).sync_insights()
    except Exception:
        logger.exception("ad insight sync failed branch=%d", branch_id)
    return mapped


async def backfill_ads(ctx: dict[str, Any]) -> int:
    """Fan out the nightly spend-history backfill, one job per branch."""
    return await _fan_out_per_branch(ctx, "backfill_ads_branch")


async def backfill_ads_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """Claim one older chunk of Meta spend history for this branch.

    Separate from sync_ads on purpose. sync_ads must stay fast and frequent — a new lead's ad
    should be mapped within the tick, and its rolling window keeps today's spend current. The
    backfill is the opposite: slow, greedy, and only worth doing where it competes with
    nothing for the account's rate limit. Hence 02:40 UTC, and one chunk per night rather
    than a year in one go."""
    from app.modules.ads.sync import AdSyncService  # noqa: PLC0415
    from app.modules.settings.service import get_settings  # noqa: PLC0415
    try:
        async with session_scope() as session:
            cfg = await get_settings(session, branch_id)
            return await AdSyncService(session, branch_id, cfg).backfill_insights()
    except Exception:
        logger.exception("ad insight backfill failed branch=%d", branch_id)
        return 0


async def refresh_profiles(ctx: dict[str, Any]) -> int:
    """Refresh IG follower/following stats for stale active-funnel leads (TTL ~6h).

    Heavy private-API call (ban surface) — gated by the platform kill-switch, capped per
    branch, and each branch runs in its OWN transaction so one branch's failure can't roll
    back another's refreshed profiles. Runs every 30 minutes."""
    return await _fan_out_per_branch(ctx, "refresh_profiles_branch", gate_platform=True)


async def refresh_profiles_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """One branch's IG follower/following refresh for stale active-funnel leads (TTL ~6h).
    Heavy private-API call (ban surface); capped per branch, its own transaction."""
    from app.modules.leads.profiles import ProfileService  # noqa: PLC0415
    refreshed = 0
    try:
        async with session_scope() as session:
            svc = ProfileService(session, branch_id)
            for channel in await wiring.active_channels(session, branch_id):
                port = await _try_build_port(session, channel, "fetch_profile")
                if port is None:
                    continue  # can't build / channel kind has no profile stats
                refreshed += await svc.refresh(port, limit=20)  # type: ignore[arg-type]
    except Exception:
        logger.exception("refresh_profiles: branch=%s failed", branch_id)
    return refreshed


async def backfill_media(ctx: dict[str, Any]) -> int:
    """Download media flagged pending at ingest and attach a MediaAsset (capped batch).

    Gated by the platform kill-switch (hits the IG private API). Each branch runs in its
    OWN transaction so one branch's failure can't roll back another's downloads. Runs every
    few minutes; a download failure keeps the flag set so the next tick retries."""
    return await _fan_out_per_branch(ctx, "backfill_media_branch", gate_platform=True)


async def backfill_media_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """One branch's media backfill: download items flagged pending at ingest and attach a
    MediaAsset (capped batch). Hits the IG private API; a download failure keeps the flag set
    so the next tick retries."""
    from app.modules.media.service import MediaService  # noqa: PLC0415
    done = 0
    try:
        async with session_scope() as session:
            svc = MediaService(session, branch_id)
            for channel in await wiring.active_channels(session, branch_id):
                assert channel.id is not None
                if not await svc.pending(channel.id, limit=1):
                    continue  # nothing flagged — skip building the port
                port = await _try_build_port(session, channel, "download_media")
                if port is None:
                    continue  # can't build / channel kind can't download media
                broker = BrokerLLM()
                done += await svc.backfill(
                    channel.id, port, limit=20,
                    transcriber=broker, describer=broker, translator=broker)
    except Exception:
        logger.exception("backfill_media: branch=%s failed", branch_id)
    return done


_BROKER_LOG_RETENTION_DAYS = settings().broker_log_retention_days


async def prune_broker_log(ctx: dict[str, Any]) -> int:
    """Drop broker_log rows older than the retention window (keeps the table bounded)."""
    from datetime import timedelta  # noqa: PLC0415

    from sqlalchemy import text  # noqa: PLC0415
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=_BROKER_LOG_RETENTION_DAYS)
    async with session_scope() as session:
        res = await session.execute(
            text("DELETE FROM broker_log WHERE created_at < :c"), {"c": cutoff}
        )
    return res.rowcount or 0


_DIGEST_THREADS = 300


async def daily_digest(ctx: dict[str, Any]) -> int:
    """Ship the dialogue digest to whoever the branch put in `digest_tg_id`. Read-only
    analytics (no IG writes) → not kill-switch gated. Runs after aggregate_needs so the
    needs cloud in the file is the freshly-classified one."""
    return await _fan_out_per_branch(ctx, "daily_digest_branch")


async def daily_digest_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """One branch's digest → Telegram, as a file (all dialogs blow past sendMessage's 4096).
    Recipient is a niche per-branch setting read directly, so it stays out of the settings
    schema/UI (same precedent as _platform_agent_on)."""
    from sqlalchemy import text  # noqa: PLC0415

    from app.modules.reports.daily_digest import build_digest  # noqa: PLC0415

    async with session_scope() as session:
        row = (await session.execute(text(
            "SELECT value FROM app_setting WHERE branch_id = :b AND key = 'digest_tg_id'"),
            {"b": branch_id})).first()
        chat_raw = (row[0] or "").strip() if row else ""
        if not chat_raw:
            return 0  # not configured for this branch — nothing to do
        markdown = await build_digest(session, branch_id, limit=_DIGEST_THREADS)
    token = settings().tg_bot_token
    if not token:
        logger.warning("daily_digest: branch=%d no bot token — skipped", branch_id)
        return 0
    try:
        chat_id = int(chat_raw)
    except ValueError:
        logger.warning("daily_digest: branch=%d digest_tg_id=%r is not an id", branch_id,
                       chat_raw)
        return 0
    day = datetime.now(UTC).date().isoformat()
    status = await TelegramNotifier(
        bot_token=token, group_chat_id=chat_id,
    ).send_document(
        filename=f"stepan-dialogs-branch{branch_id}-{day}.md",
        content=markdown,
        caption=f"Выгрузка диалогов за {day} — филиал {branch_id}, "
                f"последние {_DIGEST_THREADS} чатов.",
        chat_id=chat_id,
    )
    logger.info("daily_digest: branch=%d → chat=%s %s", branch_id, chat_id, status)
    return 1 if status == "ok" else 0


async def aggregate_needs(ctx: dict[str, Any]) -> int:
    """Nightly (midnight Jakarta) needs-cloud pass for ALL branches: incrementally classify
    leads whose needs changed onto the branch's stable taxonomy, then snapshot the day's
    aggregates for history. Analytics only (no IG writes) → not kill-switch gated; each branch
    in its own transaction so one branch's LLM/broker hiccup can't roll back another's."""
    return await _fan_out_per_branch(ctx, "aggregate_needs_branch")


async def aggregate_needs_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """One branch's nightly needs-cloud pass: incrementally classify leads whose needs changed
    onto the branch's stable taxonomy, cache label translations, then snapshot the day's
    aggregates. Analytics only; its own transaction so one branch's broker hiccup is isolated."""
    from app.modules.needs_cloud import (  # noqa: PLC0415
        classify_branch,
        translate_labels,
        write_snapshot,
    )
    llm = BrokerLLM()
    try:
        async with session_scope() as session:
            processed = await classify_branch(session, branch_id, llm)
            await translate_labels(session, branch_id, llm)  # cache en/id label translations
            await write_snapshot(session, branch_id)
            return processed
    except Exception:
        logger.exception("aggregate_needs failed branch=%d", branch_id)
        return 0


async def escalate_stale_alerts(ctx: dict[str, Any]) -> int:
    """Re-ping the manager on ready/handoff alerts left unworked past the SLA — one polite,
    tagged nudge per lead. Fans out per branch so a slow branch can't stall the others."""
    return await _fan_out_per_branch(ctx, "escalate_stale_alerts_branch", gate_platform=False)


async def escalate_stale_alerts_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """One branch's SLA re-ping pass. Independent of the agent kill switch: a ready lead needs
    a human whether or not the bot is answering new leads."""
    from app.modules.notifications.escalation import EscalationService  # noqa: PLC0415
    try:
        async with session_scope() as session:
            cfg = await get_settings(session, branch_id)
            return await EscalationService(session, branch_id, _build_notifier(cfg)).run()
    except Exception:
        logger.exception("escalate_stale_alerts: branch=%d failed", branch_id)
        return 0


def _redis_settings() -> RedisSettings:
    """ARQ broker connection from the app's redis_url (parsed, never reconstructed)."""
    return RedisSettings.from_dsn(settings().redis_url)


async def _on_startup(ctx: dict) -> None:
    """Fail-fast on broken config before the worker starts pulling jobs off the queue."""
    # ARQ configures its own logger but leaves the app logger at the default WARNING, so every
    # logger.info() in the domain (reply timing, media backfill, stage moves) was invisible in
    # the worker. Surface app.* at INFO (third-party stays at WARNING — no httpx/sqlalchemy spam).
    logging.getLogger("app").setLevel(logging.INFO)
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # basicConfig turned the ROOT logger to INFO, which also unmuted httpx's per-poll "GET
    # /v1/jobs/{id}" spam — silence the noisy transport loggers back to WARNING.
    for noisy in ("httpx", "httpcore", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings().validate_runtime()


class WorkerSettings:
    """ARQ worker config. Cron drives the three orchestration tasks on a steady cadence;
    they are staggered so each minute ingests, then replies, then sends in order."""

    functions = [
        # Cron dispatchers: each fans out one per-branch job so branches run concurrently and
        # independently (a slow/failing branch can't stall or abort the tick for the others).
        ingest_active_channels, ingest_active_conversations, reply_pending, send_outbox,
        schedule_followups, reactivate_dormant, learning_audit, escalate_stale_alerts,
        process_deletions, sync_crm, refresh_profiles, backfill_media, prune_broker_log,
        daily_digest, crm_rescue, ingest_comments,
        aggregate_needs, sync_ads, backfill_ads,
        # Per-branch jobs the dispatchers enqueue — the actual work, one branch each. Each is
        # enqueued with a STABLE _job_id ({job_name}:{branch_id}) so a still-running job dedups
        # the next tick's enqueue; the worker-level keep_result=0 (see WorkerSettings) frees the
        # id the instant the job ends, so the dedup never outlives the run.
        ingest_branch, ingest_active_branch, reply_pending_branch, send_outbox_branch,
        schedule_followups_branch, reactivate_dormant_branch, learning_audit_branch,
        escalate_stale_alerts_branch,
        process_deletions_branch, sync_crm_branch, refresh_profiles_branch,
        backfill_media_branch, aggregate_needs_branch,
        daily_digest_branch, ingest_comments_branch,
        sync_ads_branch, backfill_ads_branch,
        # Per-reply job: its OWN long timeout (waits out a slow broker); no ARQ retry (a broker
        # timeout re-dispatches next tick rather than immediately re-hitting the same slow broker
        # and double-billing). keep_result=0 is the worker default — a killed/failed reply job
        # frees its reply:{thread_id} id next tick instead of blocking it (prod 2026-07-10).
        func(generate_one_reply, timeout=settings().reply_job_timeout_s, max_tries=1),
    ]
    cron_jobs = [
        # Ingest every 2 min: an IG poll costs several private-API calls each with a
        # 2-5s anti-ban delay, so a cycle can run ~50s. Every-minute polling risked
        # overlap (→ constraint races) and hammered IG; 2 min stays well clear and is
        # gentler on the account. Reply/send stay per-minute (DB/queue, cheap).
        cron(ingest_active_channels, minute=set(range(0, 60, 2)), second=0,
             run_at_startup=False),
        # Adaptive interleave: fill the OFF minutes (1,3,5,…) but only for channels with a
        # LIVE conversation (see ingest_active_branch) — a lead mid-chat is seen within ~1 min
        # instead of ~2, while idle accounts keep the exact 2-min footprint (anti-ban). The
        # (channel_id, external_id) unique constraint makes an overlap with the base poll a
        # harmless no-op, same as two base runs racing.
        cron(ingest_active_conversations, minute=set(range(1, 60, 2)), second=0,
             run_at_startup=False),
        # reply_pending polls once/min (not every 20s): a slow tick — broker degraded, or a
        # guard regen doubling the LLM calls — can outrun ARQ's job_timeout, get killed and
        # retried, and the retry re-picks a thread whose advisory lock already released →
        # duplicate LLM decision AND duplicate real IG send (live: thread 2161, 2026-07-07).
        # A 60s cadence leaves a full job_timeout of headroom before the next tick can overlap.
        # PHASE (not cadence): fire at second=45, just after the ~:00-:50 ingest cycle commits,
        # so a message ingested THIS cycle is decided the same minute instead of waiting ~60s
        # for the next tick — then send_outbox at :50 ships it ~5s later. Still exactly once/min,
        # so the kill-retry duplicate guard above is unchanged; only the ingest→reply handoff
        # shrinks. A slow ingest that commits after :45 simply falls to the next tick (no worse
        # than before). Measured 2026-07-10: median lead→reply 170s was dominated by cron-handoff
        # gaps, not the LLM (p50 ~1.7s) — this trims the reply half of that.
        # send_outbox stays every 20s: it's cheap (one due row per call) and never overlaps a
        # reply for the same thread (its own advisory lock), so latency there is worth keeping.
        cron(reply_pending, second={45}, run_at_startup=False),
        cron(send_outbox, second={10, 30, 50}, run_at_startup=False),
        # Unsend requests every minute (second=30; independent of the reply/send phase)
        cron(process_deletions, second=30, run_at_startup=False),
        # Follow-ups run every 10 minutes (minute divisible by 10, second=50)
        cron(schedule_followups, minute={0, 10, 20, 30, 40, 50}, second=50,
             run_at_startup=False),
        # Dormant reactivation: opt-in (reactivation_enabled), twice a day at Jakarta midday
        # (UTC+7: 04:00 UTC = 11:00 WIB, 08:00 UTC = 15:00 WIB) so a cold touch lands in
        # business hours; small batch, quiet hours still held by the outbox send layer.
        cron(reactivate_dormant, hour={4, 8}, minute={20}, second=30, run_at_startup=False),
        # Learning audit: Monday 02:00 UTC = 09:00 WIB — the week's self-review lands with
        # the owner's Monday morning coffee. Propose-only; per-branch opt-in flag.
        cron(learning_audit, weekday={0}, hour={2}, minute={0}, second=0,
             run_at_startup=False),
        # SLA re-ping: every 2 min, nudge the manager on a ready/handoff alert left unworked
        # past alert_reping_after_min (default 5). Fires once per alert, working-hours only.
        cron(escalate_stale_alerts, minute=set(range(0, 60, 2)), second=40,
             run_at_startup=False),
        # CRM push every 5 minutes (only branches with crm_enabled + webhook URL)
        cron(sync_crm, minute={5, 15, 25, 35, 45, 55}, second=10, run_at_startup=False),
        # Rescue of CRM missed-call leads: hourly, work hours only (service-enforced),
        # ≤2 leads per tick — a steady trickle through the 262-lead no-answer backlog.
        cron(crm_rescue, minute={42}, second=30, run_at_startup=False),
        # Profile stats refresh every 30 minutes (heavy, TTL-gated, capped batch)
        cron(refresh_profiles, minute={0, 30}, second=15, run_at_startup=False),
        # Media backfill every 3 minutes (capped batch; no-op when nothing flagged)
        cron(backfill_media, minute=set(range(0, 60, 3)), second=25, run_at_startup=False),
        # Broker-log retention: prune old rows daily at 03:30 (broker_log_retention_days)
        cron(prune_broker_log, hour={3}, minute={30}, second=0, run_at_startup=False),
        # Needs-cloud aggregation once a day at 17:00 UTC = 00:00 WIB (midnight Jakarta), all
        # branches. Incremental + analytics-only, so it's cheap and safe to run platform-wide.
        cron(aggregate_needs, hour={17}, minute={0}, second=0, run_at_startup=False),
        # 07:00 Jakarta (WIB = UTC+7) — the owner reads it with morning coffee. Lands 7h
        # after aggregate_needs (17:00 UTC = midnight Jakarta), so the needs cloud in the
        # file is that same night's freshly-classified one.
        cron(daily_digest, hour={0}, minute={0}, second=0, run_at_startup=False),
        # Comments under our own posts: once an hour (minute={17}, offset from the other
        # hourly jobs). Opt-in per channel (comment_replies_enabled) and platform-gated —
        # a public reply is higher-stakes than a DM, so the kill switch stops it too. IG
        # throttles comment automation hard, hence hourly + the low comment caps.
        cron(ingest_comments, minute={17}, second=30, run_at_startup=False),
        # Meta ad map + insights every 20 min. Deliberately slow: Meta throttles an ad account
        # account-wide after a burst (code 80004 — hit live while building this), and its own
        # attribution lags ~7 days, so a faster cadence would buy nothing but 429s. Costs zero
        # Graph calls when no new ad appeared and no lead media is unmapped.
        cron(sync_ads, minute={3, 23, 43}, second=20, run_at_startup=False),
        # Spend history backfill at 02:40 UTC (~09:40 Jakarta — after the night, before the
        # working day). One chunk per run: it walks a long time_range and would otherwise eat
        # the account throttle that sync_ads needs to discover newly launched ads. Stops by
        # itself once history reaches the floor, so on most nights this is a no-op.
        cron(backfill_ads, hour={2}, minute={40}, second=0, run_at_startup=False),
    ]
    on_startup = _on_startup
    redis_settings = _redis_settings()
    max_jobs = settings().worker_max_jobs
    job_timeout = settings().worker_job_timeout_s
    # keep_result=0: every job here is FIRE-AND-FORGET — nothing reads a job's result via
    # arq (grep: no .result() anywhere). A non-zero keep_result was actively harmful: arq's
    # enqueue_job returns None while a stored RESULT exists, so a completed/failed job with a
    # STABLE _job_id ({job}:{branch} or reply:{thread}) blocked its own re-dispatch for the
    # whole keep_result window. Worse, a reply job KILLED by its arq timeout (slow broker)
    # stores a JobExecutionFailed result under the WORKER default (the per-func keep_result=0
    # only governs SUCCESS), so a single slow generation stuck the thread "awaiting" for an
    # hour with no reply/send (prod 2026-07-10, threads 2566-2570). 0 frees every dedup id the
    # instant the job ends; in-flight dedup is unaffected (the arq:job/in-progress keys live
    # only while the job runs, independent of keep_result).
    keep_result = 0
