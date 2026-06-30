"""ARQ worker entrypoint — thin scheduled tasks over the branch use-cases.

Each task is pure orchestration: open a session, walk ACTIVE tenants, and delegate to a
branch-scoped use-case (IngestService / ReplyService / OutboxSender). All domain logic
lives in the modules. Importing this module touches no Redis and no DB — the worker is
profile-gated and started only by `arq app.worker.main.WorkerSettings`."""
from __future__ import annotations

import logging
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Channel
from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.adapters.notify.telegram import TelegramNotifier
from app.config import settings
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


async def ingest_active_channels(ctx: dict[str, Any]) -> int:
    """Pull new inbound for every active channel of every active branch. Returns rows stored."""
    stored = 0
    async with session_scope() as session:
        for branch in await wiring.active_branches(session):
            assert branch.id is not None
            ingest = IngestService(session, branch.id)
            for channel in await wiring.active_channels(session, branch.id):
                assert channel.id is not None
                try:
                    port = await wiring.build_channel_port(session, channel)
                    inbound = await port.fetch_inbound()
                except (NotImplementedError, KeyError, RuntimeError) as exc:
                    logger.warning("skip ingest channel %s: %s", channel.id, exc)
                    continue
                stored += len(await ingest.ingest(channel.id, inbound))
    return stored


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
    """Decide and enqueue the agent reply for every thread awaiting one. Returns enqueued."""
    enqueued = 0
    llm = BrokerLLM()
    async with session_scope() as session:
        for branch in await wiring.active_branches(session):
            assert branch.id is not None
            branch_cfg = await get_settings(session, branch.id)

            if not branch_cfg.agent_enabled:
                logger.info("branch %s: agent disabled — skip reply_pending", branch.id)
                continue

            if branch_cfg.is_quiet_hour():
                logger.info("branch %s: quiet hours — skip reply_pending", branch.id)
                continue

            knowledge = KnowledgeService(session, branch.id)
            notifier = _build_notifier(branch_cfg)
            reply = ReplyService(
                session, branch.id, llm, knowledge,
                branch_settings=branch_cfg, notifier=notifier,
            )
            for thread_id in await wiring.threads_awaiting_reply(session, branch.id):
                decision = await reply.decide(thread_id)
                if decision is None:
                    continue
                if await reply.enqueue_reply(thread_id, decision) is not None:
                    enqueued += 1
    return enqueued


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
            await svc.reset_timers()
            sent += await svc.run()
    return sent


async def send_outbox(ctx: dict[str, Any]) -> int:
    """Drain one pending outbox line per thread through its channel. Returns rows attempted."""
    attempted = 0
    async with session_scope() as session:
        for branch in await wiring.active_branches(session):
            assert branch.id is not None
            channels = {c.id: c for c in await wiring.active_channels(session, branch.id)}
            for thread_id in await wiring.threads_with_pending_outbox(session, branch.id):
                attempted += await _send_thread(session, branch.id, thread_id, channels)
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


def _redis_settings() -> RedisSettings:
    """ARQ broker connection from the app's redis_url (parsed, never reconstructed)."""
    return RedisSettings.from_dsn(settings().redis_url)


class WorkerSettings:
    """ARQ worker config. Cron drives the three orchestration tasks on a steady cadence;
    they are staggered so each minute ingests, then replies, then sends in order."""

    functions = [ingest_active_channels, reply_pending, send_outbox, schedule_followups]
    cron_jobs = [
        cron(ingest_active_channels, second=0, run_at_startup=False),
        cron(reply_pending, second=20, run_at_startup=False),
        cron(send_outbox, second=40, run_at_startup=False),
        # Follow-ups run every 10 minutes (minute divisible by 10, second=50)
        cron(schedule_followups, minute={0, 10, 20, 30, 40, 50}, second=50,
             run_at_startup=False),
    ]
    redis_settings = _redis_settings()
    max_jobs = 10
    job_timeout = 120
    keep_result = 3600
