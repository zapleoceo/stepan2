"""Worker side of the Meta webhook: normalized events → the ordinary ingest write path.

The webhook is the PRIMARY inbound path for official connectors; the poll stays on as the
slow reconcile that recovers anything Meta never delivered. Both end in the same place —
IngestService, on the same external_thread_id — so an overlap is deduped, not duplicated.

How much of that dedup is real TODAY, precisely, because an earlier version of this docstring
claimed more than the code did and every media DM was stored twice for it:
  - identical text at the same second → dedup by content (IngestService.duplicate_by_content);
    this is why webhook_parse does not strip the text and truncates the ms timestamp;
  - a photo/share, which the webhook describes as '🖼 media'/'🔗 …' and the poll as an empty
    message → dedup by IngestService._is_contentless + MessageRepo.inbound_exists_at;
  - by native message id (mid) → NOT yet. The poll does not carry the mid until step S3. Until
    it merges, the two heuristics above are the whole of it.
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
    by_entry: dict[tuple[str, str], list[WebhookMessage]] = defaultdict(list)
    for raw in events:
        parsed = WebhookMessage.from_dict(raw)
        by_entry[(parsed.page_id, parsed.object_type)].append(parsed)
    stored = 0
    for (page_id, object_type), messages in by_entry.items():
        stored += await _ingest_page(
            branch_id, page_id, object_type, messages, notifier_factory)
    return stored


async def _ingest_page(
    branch_id: int,
    page_id: str,
    object_type: str,
    messages: list[WebhookMessage],
    notifier_factory: NotifierFactory | None,
) -> int:
    try:
        async with session_scope() as session:
            channel = await _channel_for_entry(session, branch_id, page_id, object_type)
            if channel is None:
                # WARNING, not INFO. Yes, this is a legitimate state after an operator switches
                # a connector off — but it is also what an entry.id we cannot map looks like,
                # and that is total, silent inbound loss on the primary path: the HTTP layer has
                # already answered 200 {"accepted": N}, Meta will not retry, and the poll keeps
                # delivering leads, so the operator sees traffic and concludes webhooks work.
                # Silent loss is the failure this whole step exists to remove; it must be
                # greppable in the container log the day it starts.
                _log.warning(
                    "META WEBHOOK DROPPED %d message(s): branch=%s has no active Meta channel "
                    "matching object=%s entry.id=%s — inbound is falling back to the poll",
                    len(messages), branch_id, object_type or "?", page_id)
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

    Built once per batch, before we know whether any sender needs it — building it can cost one
    Graph call (the page-token fetch), which the worker process then caches, so it is once per
    worker rather than once per message. Not lazy: making it lazy would thread a factory through
    resolve_thread_id for a saving that the process cache already delivers.

    Tolerant: a channel with no usable token still ingests every sender we already have a
    thread for — that DB half of the reconciliation needs no network at all."""
    from app.worker import wiring  # noqa: PLC0415 — worker wiring imports modules; break the cycle

    try:
        return await wiring.build_channel_port(session, channel)
    except (NotImplementedError, KeyError, RuntimeError) as exc:
        _log.warning("webhook: no Graph port for channel %s: %s", channel.id, exc)
        return None


async def _channel_for_entry(
    session: AsyncSession, branch_id: int, page_id: str, object_type: str
) -> Channel | None:
    """The branch's active Meta Business channel this `entry` belongs to.

    Scoped by branch_id on purpose: branch_id comes from the webhook URL and the signature
    proves only that the sender holds THAT branch's app secret — and app_secret_for() falls
    back to the shared product secret, so the signature alone does NOT identify a tenant.
    Without this scope a valid signature for one tenant could write into another's channel.

    `entry.id` is not one id. For object='page' it is the Facebook Page id, which is what
    _routes_connect._persist stores on Channel.account_id, so the direct match holds. For
    object='instagram' — Stepan's whole revenue path — Meta sends the Instagram
    professional-account id instead, a number we store nowhere, so the direct match CANNOT hit
    and every Instagram DM would be dropped. See _instagram_channel for how that is resolved.
    """
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
    direct = rows.scalars().first()
    if direct is not None or object_type != "instagram":
        return direct
    return await _instagram_channel(session, branch_id, page_id)


async def _instagram_channel(
    session: AsyncSession, branch_id: int, ig_account_id: str
) -> Channel | None:
    """Resolve an object='instagram' entry when its id matches no stored Page id.

    We hold the Page id, never the IG professional-account id Meta puts in `entry.id`, so the
    only join available is "this branch's one active Meta Business channel". Applied strictly:
    if the branch has none, or more than one, we refuse rather than guess — routing a DM into
    the wrong client's inbox is worse than a poll-cycle delay. Restricted to object='instagram'
    so an object='page' entry with an unknown Page id stays a hard drop, which is the mismatch
    that means someone reconnected a different Page.

    Storing the IG id at connect time (/{page-id}?fields=instagram_business_account) is the
    real fix and needs a Channel column — logged as debt in docs/tech-debt.md.
    """
    rows = await session.execute(
        select(Channel).where(
            Channel.branch_id == branch_id,
            Channel.kind == ChannelKind.META_BUSINESS,
            Channel.is_active.is_(True),  # type: ignore[attr-defined]
        )
    )
    channels = list(rows.scalars().all())
    if len(channels) != 1:
        _log.warning(
            "webhook: object=instagram entry.id=%s matches no channel on branch=%s and the "
            "branch has %d active Meta channels — refusing to guess",
            ig_account_id, branch_id, len(channels))
        return None
    _log.info(
        "webhook: object=instagram entry.id=%s is the IG account id, not the Page id — "
        "routed to the branch's only active Meta channel %s",
        ig_account_id, channels[0].id)
    return channels[0]
