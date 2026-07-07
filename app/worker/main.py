"""ARQ worker entrypoint — thin scheduled tasks over the branch use-cases.

Each task is pure orchestration: open a session, walk ACTIVE tenants, and delegate to a
branch-scoped use-case (IngestService / ReplyService / OutboxSender). All domain logic
lives in the modules. Importing this module touches no Redis and no DB — the worker is
profile-gated and started only by `arq app.worker.main.WorkerSettings`."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime
from typing import Any

from arq import cron
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
    """Pull new inbound for every active channel of every active branch. Returns rows stored.

    Each channel ingests in its OWN transaction: a slow poll that overruns the cron can
    overlap the next run, and two runs racing past the dedup check would hit the
    (channel_id, external_id) unique constraint — that must abort only the racing
    channel, not the whole cycle. The constraint itself is the backstop that makes the
    concurrent insert harmless."""
    await asyncio.sleep(random.uniform(0, _INGEST_JITTER_S))  # noqa: S311 — jitter, not crypto
    async with session_scope() as session:
        work = [
            (branch.id, channel.id)
            for branch in await wiring.active_branches(session)
            for channel in await wiring.active_channels(session, branch.id)
        ]
    stored = 0
    for branch_id, channel_id in work:
        stored += await _ingest_channel(branch_id, channel_id)
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
            return len(await IngestService(session, branch_id).ingest(channel_id, inbound))
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


async def reply_pending(ctx: dict[str, Any]) -> int:
    """Decide and enqueue the agent reply for every thread awaiting one. Returns enqueued.

    Quiet hours do NOT apply here — they throttle proactive follow-ups (see
    schedule_followups), never a reply to something the lead already said. A lead who
    writes at 3am still gets answered; only the BOT-initiated nudge waits for daytime.

    Each thread runs in its OWN transaction so a poison thread (bad LLM JSON, DB error)
    can't roll back replies already committed for other threads/branches this tick."""
    enqueued = 0
    llm = BrokerLLM()
    async with session_scope() as session:
        if not await _platform_agent_on(session):
            logger.info("platform agent OFF — skip reply_pending for all branches")
            return 0
        branches = await wiring.active_branches(session)
    for branch in branches:
        assert branch.id is not None
        async with session_scope() as session:
            cfg = await get_settings(session, branch.id)
            if not cfg.agent_enabled:
                logger.info("branch %s: agent disabled — skip reply_pending", branch.id)
                continue
            thread_ids = await wiring.threads_awaiting_reply(session, branch.id)
        for thread_id in thread_ids:
            if await _reply_thread(branch.id, thread_id, llm):
                enqueued += 1
    return enqueued


async def _reply_thread(branch_id: int, thread_id: int, llm: BrokerLLM) -> bool:
    """One thread's decide+enqueue in its own transaction; isolate failures per thread."""
    try:
        async with session_scope() as session:
            if not await wiring.try_lock_thread(session, thread_id):
                return False  # another tick already owns this thread right now
            cfg = await get_settings(session, branch_id)
            kb = await effective_kb_branch(session, branch_id)  # shared-KB link, if any
            reply = ReplyService(
                session, branch_id, llm, KnowledgeService(session, kb, llm),
                branch_settings=cfg, notifier=_build_notifier(cfg),
            )
            decision = await reply.decide(thread_id)
            if decision is None:
                return False
            return await reply.enqueue_reply(thread_id, decision) is not None
    except Exception:
        logger.exception("reply failed branch=%d thread=%d", branch_id, thread_id)
        return False


async def schedule_followups(ctx: dict[str, Any]) -> int:
    """Set follow-up timers for cold threads and queue proactive messages.

    Runs every 10 minutes (between ingest and reply). Quiet hours do NOT block queueing —
    only the send (OutboxSender.send_next) holds a follow-up until quiet hours end, so a
    nudge queued at 23:50 is ready to go out the instant quiet hours lift instead of
    losing a whole cron cycle. Only fires when followup_enabled=true in branch settings."""
    sent = 0
    llm = BrokerLLM()
    async with session_scope() as session:
        if not await _platform_agent_on(session):
            return 0
        for branch in await wiring.active_branches(session):
            assert branch.id is not None
            branch_cfg = await get_settings(session, branch.id)
            if not branch_cfg.followup_enabled:
                continue
            kb = branch.kb_source_branch_id or branch.id  # object in hand → no extra query
            knowledge = KnowledgeService(session, kb, llm)
            svc = FollowupService(session, branch.id, llm, knowledge, branch_cfg)
            sent += await svc.run()  # timers are armed by OutboxSender after bot sends
    return sent


async def send_outbox(ctx: dict[str, Any]) -> int:
    """Drain one pending outbox line per thread through its channel. Returns rows attempted.

    Per-thread transaction: an already-delivered IG send is committed before the next
    thread runs, so a later failure can never roll back a 'sent' row into a re-send."""
    attempted = 0
    async with session_scope() as session:
        branches = await wiring.active_branches(session)
    for branch in branches:
        assert branch.id is not None
        async with session_scope() as session:
            cfg = await get_settings(session, branch.id)
            if not cfg.sending_enabled:  # queue keeps accumulating, nothing goes out
                continue
            channels = {c.id: c for c in await wiring.active_channels(session, branch.id)}
            thread_ids = await wiring.threads_with_pending_outbox(session, branch.id)
        for thread_id in thread_ids:
            try:
                async with session_scope() as session:
                    attempted += await _send_thread(session, branch.id, thread_id, channels)
            except Exception:
                logger.exception("send failed branch=%d thread=%d", branch.id, thread_id)
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


# IG's private-API unsend call has been observed taking 40-90s (likely IG-side
# throttling after a send burst) with no internal timeout of its own — batching many
# threads' worth of revokes in one tick reliably blows past ARQ's 120s job_timeout.
# Revoking is idempotent-ish (a retried revoke of an already-gone message just fails
# gracefully) so a kill+retry here is not the correctness hazard reply/send batching
# was, but it wastes the whole tick on nothing — cap it so at least SOME progress
# commits every cycle instead of none.
_DELETION_THREAD_CAP = settings().deletion_thread_cap


async def process_deletions(ctx: dict[str, Any]) -> int:
    """Carry out requested IG unsends: revoke in IG first, delete locally on success."""
    from app.modules.conversation.deletions import DeletionService  # noqa: PLC0415
    done = 0
    async with session_scope() as session:
        for branch in await wiring.active_branches(session):
            assert branch.id is not None
            channels = {c.id: c for c in await wiring.active_channels(session, branch.id)}
            svc = DeletionService(session, branch.id)
            for channel_id, channel in channels.items():
                pending = await svc.pending(channel_id)
                if not pending:
                    continue
                try:
                    port = await wiring.build_channel_port(session, channel)
                except (NotImplementedError, KeyError, RuntimeError) as exc:
                    logger.warning("skip unsend channel=%s: %s", channel_id, exc)
                    continue
                if not hasattr(port, "revoke"):
                    continue  # channel doesn't support unsend
                by_thread: dict[int, str] = {}
                for msg in pending:
                    if msg.thread_id not in by_thread:
                        thread = await ThreadRepo(session, branch.id).by_id(msg.thread_id)
                        if thread is not None:
                            by_thread[msg.thread_id] = thread.external_thread_id
                threads_this_tick = list(set(by_thread.values()))[:_DELETION_THREAD_CAP]
                for ext_thread in threads_this_tick:
                    done += await svc.process(channel_id, ext_thread, port)  # type: ignore[arg-type]
    return done


async def sync_crm(ctx: dict[str, Any]) -> int:
    """CRM sync, both directions: push unsynced manager alerts out (crm_enabled), and
    pull lead state in to stand down leads a manager already owns (crm_read_enabled)."""
    from app.adapters.crm import CrmReader, CrmWebhook  # noqa: PLC0415
    from app.modules.crm import CrmSyncService  # noqa: PLC0415
    from app.modules.crm.pull import CrmPullService  # noqa: PLC0415
    synced = 0
    transport = CrmWebhook()
    reader = CrmReader()
    async with session_scope() as session:
        for branch in await wiring.active_branches(session):
            assert branch.id is not None
            try:
                synced += await CrmSyncService(session, branch.id, transport).sync_pending()
            except Exception:
                logger.exception("crm push failed branch=%d", branch.id)
            try:
                await CrmPullService(session, branch.id, reader).sync_active()
            except Exception:
                logger.exception("crm pull failed branch=%d", branch.id)
    return synced


async def refresh_profiles(ctx: dict[str, Any]) -> int:
    """Refresh IG follower/following stats for stale active-funnel leads (TTL ~6h).

    Heavy private-API call, so capped per tick per branch to respect rate limits. Runs
    every 30 minutes. A per-lead fetch failure leaves that lead untouched."""
    from app.modules.leads.profiles import ProfileService  # noqa: PLC0415
    refreshed = 0
    async with session_scope() as session:
        for branch in await wiring.active_branches(session):
            assert branch.id is not None
            svc = ProfileService(session, branch.id)
            for channel in await wiring.active_channels(session, branch.id):
                try:
                    port = await wiring.build_channel_port(session, channel)
                except (NotImplementedError, KeyError, RuntimeError) as exc:
                    logger.warning("skip profiles channel %s: %s", channel.id, exc)
                    continue
                if not hasattr(port, "fetch_profile"):
                    continue  # channel kind has no profile stats
                refreshed += await svc.refresh(port, limit=20)  # type: ignore[arg-type]
    return refreshed


async def backfill_media(ctx: dict[str, Any]) -> int:
    """Download media flagged pending at ingest and attach a MediaAsset (capped batch).

    Runs every few minutes; a download failure keeps the flag set so the next tick
    retries. Safe no-op when nothing is flagged."""
    from app.modules.media.service import MediaService  # noqa: PLC0415
    done = 0
    async with session_scope() as session:
        for branch in await wiring.active_branches(session):
            assert branch.id is not None
            svc = MediaService(session, branch.id)
            for channel in await wiring.active_channels(session, branch.id):
                assert channel.id is not None
                if not await svc.pending(channel.id, limit=1):
                    continue  # nothing flagged — skip building the port
                try:
                    port = await wiring.build_channel_port(session, channel)
                except (NotImplementedError, KeyError, RuntimeError) as exc:
                    logger.warning("skip media channel %s: %s", channel.id, exc)
                    continue
                if not hasattr(port, "download_media"):
                    continue  # channel kind can't download media
                done += await svc.backfill(
                    channel.id, port, limit=20, transcriber=BrokerLLM())  # type: ignore[arg-type]
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
    from app.modules.knowledge.reindex import branch_needs_reindex, reindex_branch  # noqa: PLC0415

    llm = BrokerLLM()
    async with session_scope() as session:
        branch_ids = [b.id for b in await wiring.active_branches(session)]
    done = 0
    for branch_id in branch_ids:
        try:
            async with session_scope() as session:
                if not await branch_needs_reindex(session, branch_id):
                    continue
                await reindex_branch(session, branch_id, llm)
                done += 1
        except Exception:
            logger.exception("reindex failed branch=%d", branch_id)
    return done


def _redis_settings() -> RedisSettings:
    """ARQ broker connection from the app's redis_url (parsed, never reconstructed)."""
    return RedisSettings.from_dsn(settings().redis_url)


class WorkerSettings:
    """ARQ worker config. Cron drives the three orchestration tasks on a steady cadence;
    they are staggered so each minute ingests, then replies, then sends in order."""

    functions = [
        ingest_active_channels, reply_pending, send_outbox, schedule_followups,
        process_deletions, sync_crm, refresh_profiles, backfill_media, prune_broker_log,
        reindex_knowledge,
    ]
    cron_jobs = [
        # Ingest every 2 min: an IG poll costs several private-API calls each with a
        # 2-5s anti-ban delay, so a cycle can run ~50s. Every-minute polling risked
        # overlap (→ constraint races) and hammered IG; 2 min stays well clear and is
        # gentler on the account. Reply/send stay per-minute (DB/queue, cheap).
        cron(ingest_active_channels, minute=set(range(0, 60, 2)), second=0,
             run_at_startup=False),
        # Reply + send poll every 20s (not once/min) to cut internal latency. Neither adds
        # IG load: reply_pending is LLM/DB only, and send_outbox's real IG send rate is
        # bounded by scheduled_at + reply_delay + hourly/daily caps — polling more often
        # just shortens the wait for an already-due row, it never sends more. The 2-min
        # ingest cadence (the actual IG-polling, anti-ban-sensitive step) is unchanged.
        cron(reply_pending, second={0, 20, 40}, run_at_startup=False),
        cron(send_outbox, second={10, 30, 50}, run_at_startup=False),
        # Unsend requests every minute (second=30, between reply and send)
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
    ]
    redis_settings = _redis_settings()
    max_jobs = settings().worker_max_jobs
    job_timeout = settings().worker_job_timeout_s
    keep_result = 3600
