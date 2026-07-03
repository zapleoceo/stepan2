"""ARQ worker entrypoint — thin scheduled tasks over the branch use-cases.

Each task is pure orchestration: open a session, walk ACTIVE tenants, and delegate to a
branch-scoped use-case (IngestService / ReplyService / OutboxSender). All domain logic
lives in the modules. Importing this module touches no Redis and no DB — the worker is
profile-gated and started only by `arq app.worker.main.WorkerSettings`."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from arq import cron
from arq.connections import RedisSettings
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
from app.modules.leads.ingest import IngestService
from app.modules.settings.service import get_settings
from app.ports.notify import NotifierPort

from . import wiring

logger = logging.getLogger(__name__)

# Anti-ban: the cron fires at a fixed second; poll IG a random moment into the minute
# instead so the private-API calls don't hit on a machine-regular tick (S1 jitter).
_INGEST_JITTER_S = 12.0


async def ingest_active_channels(ctx: dict[str, Any]) -> int:
    """Pull new inbound for every active channel of every active branch. Returns rows stored."""
    await asyncio.sleep(random.uniform(0, _INGEST_JITTER_S))  # noqa: S311 — jitter, not crypto
    stored = 0
    async with session_scope() as session:
        for branch in await wiring.active_branches(session):
            assert branch.id is not None
            ingest = IngestService(session, branch.id)
            for channel in await wiring.active_channels(session, branch.id):
                assert channel.id is not None
                try:
                    port = await wiring.build_channel_port(session, channel)
                except (NotImplementedError, KeyError, RuntimeError) as exc:
                    logger.warning("skip ingest channel %s: %s", channel.id, exc)
                    continue
                if not await _healthy(session, branch.id, channel, port):
                    continue  # checkpoint/expired session — frozen until re-login
                try:
                    inbound = await port.fetch_inbound()
                except Exception:
                    logger.exception("ingest fetch failed channel %s", channel.id)
                    continue
                stored += len(await ingest.ingest(channel.id, inbound))
    return stored


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
                await notifier.notify_manager(
                    branch_id=branch_id, lead_id=0, kind="channel_checkpoint",
                    summary_en=f"IG channel {channel.handle or channel.id} needs re-login",
                    summary_ru=f"IG-канал {channel.handle or channel.id} требует ре-логина",
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


async def reply_pending(ctx: dict[str, Any]) -> int:
    """Decide and enqueue the agent reply for every thread awaiting one. Returns enqueued.

    Each thread runs in its OWN transaction so a poison thread (bad LLM JSON, DB error)
    can't roll back replies already committed for other threads/branches this tick."""
    enqueued = 0
    llm = BrokerLLM()
    async with session_scope() as session:
        branches = await wiring.active_branches(session)
    for branch in branches:
        assert branch.id is not None
        async with session_scope() as session:
            cfg = await get_settings(session, branch.id)
            if not cfg.agent_enabled:
                logger.info("branch %s: agent disabled — skip reply_pending", branch.id)
                continue
            if cfg.is_quiet_hour():
                logger.info("branch %s: quiet hours — skip reply_pending", branch.id)
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
            cfg = await get_settings(session, branch_id)
            reply = ReplyService(
                session, branch_id, llm, KnowledgeService(session, branch_id),
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

    Runs every 10 minutes (between ingest and reply); quiet hours are respected.
    Only fires when followup_enabled=true in branch settings."""
    sent = 0
    llm = BrokerLLM()
    async with session_scope() as session:
        for branch in await wiring.active_branches(session):
            assert branch.id is not None
            branch_cfg = await get_settings(session, branch.id)
            if not branch_cfg.followup_enabled:
                continue
            if branch_cfg.is_quiet_hour():
                continue
            knowledge = KnowledgeService(session, branch.id)
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
    """Send the next due line of one thread via its channel; skip when wiring is absent."""
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
                for ext_thread in set(by_thread.values()):
                    done += await svc.process(channel_id, ext_thread, port)  # type: ignore[arg-type]
    return done


async def sync_crm(ctx: dict[str, Any]) -> int:
    """Push unsynced manager alerts to each branch's CRM webhook (crm_enabled gates)."""
    from app.adapters.crm import CrmWebhook  # noqa: PLC0415
    from app.modules.crm import CrmSyncService  # noqa: PLC0415
    synced = 0
    transport = CrmWebhook()
    async with session_scope() as session:
        for branch in await wiring.active_branches(session):
            assert branch.id is not None
            try:
                synced += await CrmSyncService(session, branch.id, transport).sync_pending()
            except Exception:
                logger.exception("crm sync failed branch=%d", branch.id)
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
                done += await svc.backfill(channel.id, port, limit=20)  # type: ignore[arg-type]
    return done


def _redis_settings() -> RedisSettings:
    """ARQ broker connection from the app's redis_url (parsed, never reconstructed)."""
    return RedisSettings.from_dsn(settings().redis_url)


class WorkerSettings:
    """ARQ worker config. Cron drives the three orchestration tasks on a steady cadence;
    they are staggered so each minute ingests, then replies, then sends in order."""

    functions = [
        ingest_active_channels, reply_pending, send_outbox, schedule_followups,
        process_deletions, sync_crm, refresh_profiles, backfill_media,
    ]
    cron_jobs = [
        cron(ingest_active_channels, second=0, run_at_startup=False),
        cron(reply_pending, second=20, run_at_startup=False),
        cron(send_outbox, second=40, run_at_startup=False),
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
    ]
    redis_settings = _redis_settings()
    max_jobs = 10
    job_timeout = 120
    keep_result = 3600
