"""Worker side of the Meta webhook: normalized events → the ordinary ingest write path.

The webhook is the PRIMARY inbound path for official connectors; the poll stays on as the
slow reconcile that recovers anything Meta never delivered. Both therefore end in exactly the
same place — IngestService, keyed on the same external_thread_id and the same native message
id — so a message arriving twice is a no-op, not a second thread and a second reply.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Channel
from app.adapters.db.session import session_scope
from app.domain.enums import ChannelKind
from app.modules.leads.ingest import IngestService
from app.modules.settings.service import get_settings
from app.ports.channel import InboundMessage
from app.ports.notify import NotifierPort

from .webhook_parse import WebhookMessage
from .webhook_threads import resolve_thread_id

_log = logging.getLogger(__name__)

NotifierFactory = Callable[[object], NotifierPort | None]


async def ingest_webhook_messages(
    branch_id: int,
    events: list[dict[str, Any]],
    *,
    notifier_factory: NotifierFactory | None = None,
) -> int:
    """Persist a webhook batch. Returns rows stored. One transaction per Page, so a Page whose
    channel is misconfigured cannot roll back the messages of a healthy one."""
    by_page: dict[str, list[WebhookMessage]] = defaultdict(list)
    for raw in events:
        parsed = WebhookMessage.from_dict(raw)
        by_page[parsed.page_id].append(parsed)
    stored = 0
    for page_id, messages in by_page.items():
        stored += await _ingest_page(branch_id, page_id, messages, notifier_factory)
    return stored


async def _ingest_page(
    branch_id: int,
    page_id: str,
    messages: list[WebhookMessage],
    notifier_factory: NotifierFactory | None,
) -> int:
    try:
        async with session_scope() as session:
            channel = await _channel_for_page(session, branch_id, page_id)
            if channel is None:
                # Meta keeps delivering to a URL long after the operator switched the channel
                # off; that is a legitimate state, not an error worth a stack trace.
                _log.info("webhook: branch=%s has no active Meta channel for page=%s — dropped",
                          branch_id, page_id)
                return 0
            inbound = await _to_inbound(session, branch_id, channel, messages)
            if not inbound:
                return 0
            cfg = await get_settings(session, branch_id)
            notifier = notifier_factory(cfg) if notifier_factory else None
            svc = IngestService(session, branch_id, notifier=notifier)
            return len(await svc.ingest(channel.id or 0, inbound))
    except Exception:
        # Fail the batch loudly but locally: the poll re-reads the same conversation within
        # its cadence, so a lost webhook degrades latency, never correctness.
        _log.exception("webhook ingest failed branch=%s page=%s", branch_id, page_id)
        return 0


async def _to_inbound(
    session: AsyncSession, branch_id: int, channel: Channel, messages: list[WebhookMessage],
) -> list[InboundMessage]:
    resolver = await _resolver(session, channel)
    out: list[InboundMessage] = []
    for msg in messages:
        thread_id = await resolve_thread_id(
            session, branch_id, channel.id or 0, msg.sender_id, resolver)
        if thread_id is None:
            continue  # unkeyable → the poll picks it up; see webhook_threads.resolve_thread_id
        out.append(
            InboundMessage(
                external_thread_id=thread_id,
                sender_id=msg.sender_id,
                text=msg.text,
                occurred_at=msg.occurred_at,
                # The native id Meta assigns the message. S3 makes the poll carry the same
                # value, so whichever path arrives second is deduped by external id alone.
                external_id=msg.mid,
                lead_ig_user_id=msg.sender_id,
                ad_id=msg.ad_id,
                ad_media_id=msg.ad_media_id,
                ad_preview_url=msg.ad_preview_url,
                lead_source=msg.lead_source,
                media_url=msg.media_url,
                media_kind=msg.media_kind,
                link_url=msg.link_url,
            )
        )
    return out


async def _resolver(session: AsyncSession, channel: Channel) -> Any:
    """The live Graph port, used only to translate an UNKNOWN sender into a conversation id.

    Built lazily and tolerantly: a channel with no usable token still ingests every sender we
    already have a thread for (the DB half of the reconciliation needs no network at all)."""
    from app.worker import wiring  # noqa: PLC0415 — worker wiring imports modules; break the cycle

    try:
        return await wiring.build_channel_port(session, channel)
    except (NotImplementedError, KeyError, RuntimeError) as exc:
        _log.warning("webhook: no Graph port for channel %s: %s", channel.id, exc)
        return None


async def _channel_for_page(
    session: AsyncSession, branch_id: int, page_id: str
) -> Channel | None:
    """The branch's active Meta Business channel for this Page id.

    Scoped by branch_id on purpose: branch_id comes from the webhook URL and the signature
    proves only that the sender holds THAT branch's app secret. Without the scope a valid
    signature for one tenant could write into another tenant's channel."""
    if not page_id:
        return None
    rows = await session.execute(
        select(Channel).where(
            Channel.branch_id == branch_id,
            Channel.kind == ChannelKind.META_BUSINESS,
            Channel.account_id == page_id,
            Channel.is_active.is_(True),  # type: ignore[attr-defined]
        )
    )
    return rows.scalars().first()
