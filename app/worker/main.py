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
from datetime import UTC, datetime
from typing import Any

from arq import cron, func
from arq.connections import RedisSettings
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Channel
from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.adapters.notify.telegram import TelegramNotifier
from app.config import settings
from app.domain.enums import SessionStatus
from app.modules.conversation.followup import FollowupService
from app.modules.conversation.outbox import OutboxSender
from app.modules.conversation.reply import ReplyService
from app.modules.conversation.repository import ThreadRepo
from app.modules.knowledge.service import KnowledgeService
from app.modules.knowledge.source import effective_kb_branch
from app.modules.leads.ingest import IngestService
from app.modules.settings.service import get_settings
from app.ports.notify import NotifierPort

from . import wiring

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
) -> int:
    """Dispatch one arq job per active branch, so branches run CONCURRENTLY (bounded by
    worker_max_jobs) and INDEPENDENTLY — a slow or failing branch holds a single job slot
    and times out on its own without delaying or aborting the tick for other branches. This
    generalises the reply_pending→generate_one_reply fan-out to every cron task.

    The `{job_name}:{branch_id}` job id dedups: a branch whose previous job is still running
    when the next tick fires does not stack a second job (same idempotency trick as the
    reply:{thread_id} id). gate_platform short-circuits the whole fan-out when the kill switch
    is OFF, so an operator flipping it mid-incident stops new work immediately."""
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
    try:
        if await redis.zcard(_REPLY_INFLIGHT) > settings().reply_max_concurrency:
            return False  # over cap → leave slots for other tasks; re-dispatched next tick
        llm = BrokerLLM()
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
            decision = await reply.decide(thread_id)
            if decision is None:
                return False
            return await reply.enqueue_reply(thread_id, decision) is not None
    except Exception:
        logger.exception("reply failed branch=%d thread=%d", branch_id, thread_id)
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
    return await _fan_out_per_branch(ctx, "schedule_followups_branch", gate_platform=True)


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
    from app.adapters.crm import CrmReader, CrmWebhook  # noqa: PLC0415
    from app.modules.crm import CrmSyncService  # noqa: PLC0415
    from app.modules.crm.pull import CrmPullService  # noqa: PLC0415
    synced = 0
    try:
        async with session_scope() as session:
            synced += await CrmSyncService(session, branch_id, CrmWebhook()).sync_pending()
    except Exception:
        logger.exception("crm push failed branch=%d", branch_id)
    try:
        async with session_scope() as session:
            await CrmPullService(session, branch_id, CrmReader()).sync_active()
    except Exception:
        logger.exception("crm pull failed branch=%d", branch_id)
    return synced


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
                done += await svc.backfill(
                    channel.id, port, limit=20, transcriber=BrokerLLM())  # type: ignore[arg-type]
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


async def reindex_knowledge(ctx: dict[str, Any]) -> int:
    """Watcher: reindex the RAG store for any branch whose KB changed since its last index.

    Each branch reindexes in its own transaction so one embedding failure doesn't drop the
    others. Returns the number of branches reindexed this tick."""
    return await _fan_out_per_branch(ctx, "reindex_knowledge_branch")


async def reindex_knowledge_branch(ctx: dict[str, Any], branch_id: int) -> int:
    """Reindex ONE branch's RAG store if its KB changed since the last index — its own
    transaction so a slow embedding run on one branch never blocks another's."""
    from app.modules.knowledge.reindex import branch_needs_reindex, reindex_branch  # noqa: PLC0415
    llm = BrokerLLM()
    try:
        async with session_scope() as session:
            if not await branch_needs_reindex(session, branch_id):
                return 0
            await reindex_branch(session, branch_id, llm)
            return 1
    except Exception:
        logger.exception("reindex failed branch=%d", branch_id)
        return 0


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


def _redis_settings() -> RedisSettings:
    """ARQ broker connection from the app's redis_url (parsed, never reconstructed)."""
    return RedisSettings.from_dsn(settings().redis_url)


async def _on_startup(ctx: dict) -> None:
    """Fail-fast on broken config before the worker starts pulling jobs off the queue."""
    settings().validate_runtime()


class WorkerSettings:
    """ARQ worker config. Cron drives the three orchestration tasks on a steady cadence;
    they are staggered so each minute ingests, then replies, then sends in order."""

    functions = [
        # Cron dispatchers: each fans out one per-branch job so branches run concurrently and
        # independently (a slow/failing branch can't stall or abort the tick for the others).
        ingest_active_channels, reply_pending, send_outbox, schedule_followups,
        process_deletions, sync_crm, refresh_profiles, backfill_media, prune_broker_log,
        reindex_knowledge, aggregate_needs,
        # Per-branch jobs the dispatchers enqueue — the actual work, one branch each.
        # keep_result=0 is MANDATORY here: each is enqueued with a STABLE _job_id
        # ({job_name}:{branch_id}) so a still-running job dedups the next tick's enqueue —
        # but arq's enqueue_job ALSO returns None while a stored RESULT exists, so the
        # worker's default keep_result=3600 made every per-branch job re-enqueueable only
        # ONCE PER HOUR (its result blocked re-dispatch for the full hour), silently
        # throttling reply/send/ingest/followups to 1 run/hour/branch instead of per-tick
        # (prod incident 2026-07-10: 20+ threads stuck "awaiting reply", reply_pending
        # reporting 0 enqueued every minute). keep_result=0 frees the id the instant the
        # job finishes, same as generate_one_reply.
        func(ingest_branch, keep_result=0),
        func(reply_pending_branch, keep_result=0),
        func(send_outbox_branch, keep_result=0),
        func(schedule_followups_branch, keep_result=0),
        func(process_deletions_branch, keep_result=0),
        func(sync_crm_branch, keep_result=0),
        func(refresh_profiles_branch, keep_result=0),
        func(backfill_media_branch, keep_result=0),
        func(reindex_knowledge_branch, keep_result=0),
        func(aggregate_needs_branch, keep_result=0),
        # Per-reply job: its OWN long timeout (waits out a slow broker); no result kept so the
        # reply:{thread_id} dedup frees the instant it finishes → the thread can be re-dispatched
        # for its NEXT message; no ARQ retry (a broker timeout re-dispatches next tick instead of
        # immediately re-hitting the same slow broker and double-billing).
        func(generate_one_reply, timeout=settings().reply_job_timeout_s, keep_result=0,
             max_tries=1),
    ]
    cron_jobs = [
        # Ingest every 2 min: an IG poll costs several private-API calls each with a
        # 2-5s anti-ban delay, so a cycle can run ~50s. Every-minute polling risked
        # overlap (→ constraint races) and hammered IG; 2 min stays well clear and is
        # gentler on the account. Reply/send stay per-minute (DB/queue, cheap).
        cron(ingest_active_channels, minute=set(range(0, 60, 2)), second=0,
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
        # CRM push every 5 minutes (only branches with crm_enabled + webhook URL)
        cron(sync_crm, minute={5, 15, 25, 35, 45, 55}, second=10, run_at_startup=False),
        # Profile stats refresh every 30 minutes (heavy, TTL-gated, capped batch)
        cron(refresh_profiles, minute={0, 30}, second=15, run_at_startup=False),
        # Media backfill every 3 minutes (capped batch; no-op when nothing flagged)
        cron(backfill_media, minute=set(range(0, 60, 3)), second=25, run_at_startup=False),
        # Broker-log retention: prune old rows daily at 03:30 (broker_log_retention_days)
        cron(prune_broker_log, hour={3}, minute={30}, second=0, run_at_startup=False),
        # RAG reindex watcher every 5 min: rebuilds only branches whose KB changed
        cron(reindex_knowledge, minute={2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57},
             second=45, run_at_startup=False),
        # Needs-cloud aggregation once a day at 17:00 UTC = 00:00 WIB (midnight Jakarta), all
        # branches. Incremental + analytics-only, so it's cheap and safe to run platform-wide.
        cron(aggregate_needs, hour={17}, minute={0}, second=0, run_at_startup=False),
    ]
    on_startup = _on_startup
    redis_settings = _redis_settings()
    max_jobs = settings().worker_max_jobs
    job_timeout = settings().worker_job_timeout_s
    keep_result = 3600
