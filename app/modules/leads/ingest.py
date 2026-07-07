"""IngestService — turn InboundMessages into leads, threads and deduped Messages.

The single write path for inbound traffic: resolve identity, dedup by external id,
persist the message, advance the thread's reply window. Branch-scoped throughout.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import MediaAsset, Message, StageEvent
from app.domain.enums import HUMAN_LED_STAGES, Stage
from app.modules.ads import AdMappingService
from app.ports.channel import InboundMessage

from .identity import IdentityService
from .phone import extract_phone
from .repository import MessageRepo

logger = logging.getLogger(__name__)

WINDOW = timedelta(hours=24)  # private-channel reply window (e.g. MBS 24h)
# Our own send recorded at send-time vs the same message polled back with IG's own
# timestamp can drift by a few seconds plus poll latency; a lead/manager never re-sends
# the identical full text within minutes, so this wide window drops only the echo.
_OUT_ECHO_WINDOW = timedelta(minutes=5)


class IngestService:
    """Inbound ingestion for one branch — idempotent on (channel_id, external_id)."""

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        self.session = session
        self.branch_id = branch_id
        self.identity = IdentityService(session, branch_id)
        self.messages = MessageRepo(session, branch_id)

    async def ingest(
        self, channel_id: int, messages: list[InboundMessage]
    ) -> list[Message]:
        """Persist each new inbound; skip duplicates. Returns the rows it created."""
        created: list[Message] = []
        await self._advance_read_receipts(channel_id, messages)
        for inbound in messages:
            external_id = inbound.external_id or _external_id(inbound)
            if await self.messages.by_external(channel_id, external_id) is not None:
                continue  # already ingested — idempotent (incl. rows OutboxSender recorded)
            if inbound.external_id and await self.messages.by_external(
                channel_id, _external_id(inbound)
            ) is not None:
                continue  # legacy row stored under the synthetic id — don't duplicate
            if inbound.direction == "out":
                row = await self._store_outgoing(channel_id, external_id, inbound)
                if row is not None:
                    created.append(row)
                continue
            phone = extract_phone(inbound.text)  # merge key when the lead shares a number
            lead, thread = await self.identity.resolve_or_create(
                inbound.external_thread_id, channel_id,
                display_name=inbound.sender_name,
                phone=phone,
                ig_user_id=inbound.lead_ig_user_id or inbound.sender_id,
                ig_username=inbound.sender_username,
                avatar_url=inbound.sender_avatar,
            )
            row = await self._store(lead, thread, channel_id, external_id, inbound)
            if row is not None:
                created.append(row)
        return created

    async def _advance_read_receipts(
        self, channel_id: int, messages: list[InboundMessage]
    ) -> None:
        """Advance each known thread's lead_seen_at BEFORE message dedup.

        The receipt rides on every polled item, but the old update lived in _store and
        only ran when a NEW inbound row was written — a lead who READS our replies
        without answering produces no new rows, so their receipt froze at their last
        message (live: thread 452 showed 'read' in the IG app, nothing in our UI).
        Threads not yet in the DB are covered by _store after identity resolution."""
        latest: dict[str, datetime] = {}
        for m in messages:
            if m.lead_seen_at is None:
                continue
            prev = latest.get(m.external_thread_id)
            if prev is None or m.lead_seen_at > prev:
                latest[m.external_thread_id] = m.lead_seen_at
        for ext_id, seen in latest.items():
            thread = await self.identity.threads.by_external(channel_id, ext_id)
            if thread is not None and (
                thread.lead_seen_at is None or seen > thread.lead_seen_at
            ):
                thread.lead_seen_at = seen
                self.session.add(thread)

    async def _store_outgoing(
        self, channel_id: int, external_id: str, inbound: InboundMessage
    ) -> Message | None:
        """Record OUR message seen in the channel (manual reply from the IG app).

        Moves last_out_at so the bot never answers over a human. Skipped when the
        thread is unknown (inbound-only business — we never open conversations)."""
        thread = await self.identity.threads.by_external(
            channel_id, inbound.external_thread_id
        )
        if thread is None:
            return None
        # OutboxSender already recorded every message the bot/manager sent through our
        # queue, tagging it with the send-API's item id. The inbox poll re-surfaces that
        # same message under a DIFFERENT item id, so external-id dedup misses it and we'd
        # store our own send twice (one bubble showed up 2x in the chat + the LLM context).
        # A genuine manual reply typed in the IG app carries novel text, so a wide content
        # window here drops only the poll-back echo, never a real human message. Media items
        # carry the same placeholder text ('🖼 media') regardless of which photo/video it is,
        # so skip this content dedup for them — same guard as _store's lead-side path,
        # otherwise a manager sending two different photos back-to-back would drop the second.
        if inbound.media_url is None and await self.messages.duplicate_by_content(
            thread.id, "out", inbound.text, inbound.occurred_at, window=_OUT_ECHO_WINDOW
        ):
            return None
        msg = await self.messages.add(
            Message(
                branch_id=self.branch_id,
                thread_id=thread.id,
                channel_id=channel_id,
                external_id=external_id,
                direction="out",
                sent_by="manager",
                text=inbound.text,
                occurred_at=inbound.occurred_at,
            )
        )
        if inbound.media_url:
            # Manager-sent media (photo/video from the IG app) — same stub-and-backfill path
            # as lead-sent media (see _store below); ingest can't download inline, the
            # backfill worker fills the bytes later. Previously only the lead-side branch
            # did this, so a manager's own media rendered as a bare '🖼 media' placeholder
            # forever instead of the actual image/video.
            msg.media_pending = True
            self.session.add(MediaAsset(
                branch_id=self.branch_id, message_id=msg.id,
                kind=inbound.media_kind or "image", url=inbound.media_url,
            ))
        if thread.last_out_at is None or inbound.occurred_at > thread.last_out_at:
            thread.last_out_at = inbound.occurred_at
        return msg

    async def _store(
        self, lead, thread, channel_id: int, external_id: str, inbound: InboundMessage
    ) -> Message | None:
        if inbound.media_url is None and await self.messages.duplicate_by_content(
            thread.id, "in", inbound.text, inbound.occurred_at
        ):
            return None  # same text already in thread within 2s (pending→main id drift)
        if inbound.media_url is None and await self.messages.echo_of_our_own(
            thread.id, inbound.text, inbound.occurred_at
        ):
            return None  # IG echoed our own outgoing message back as if the lead sent it
        msg = await self.messages.add(
            Message(
                branch_id=self.branch_id,
                thread_id=thread.id,
                channel_id=channel_id,
                external_id=external_id,
                direction="in",
                sent_by="lead",
                text=inbound.text,
                occurred_at=inbound.occurred_at,
                link_url=inbound.link_url,
                preview_url=inbound.preview_url,
            )
        )
        if inbound.media_url:
            # ingest can't download inline; stash a stub the backfill worker fills later
            msg.media_pending = True
            self.session.add(MediaAsset(
                branch_id=self.branch_id, message_id=msg.id,
                kind=inbound.media_kind or "image", url=inbound.media_url,
            ))
        if inbound.lead_seen_at and (
            thread.lead_seen_at is None or inbound.lead_seen_at > thread.lead_seen_at
        ):
            thread.lead_seen_at = inbound.lead_seen_at
        if thread.last_in_at is None or inbound.occurred_at > thread.last_in_at:
            thread.last_in_at = inbound.occurred_at
            thread.window_until = inbound.occurred_at + WINDOW
            await self._reset_followup_cycle(thread)
            self._revive_bot(lead, thread)
        if inbound.product_hint and thread.product_slug is None:
            thread.product_slug = inbound.product_hint
            thread.product_source = "ad"
        if thread.lead_source is None:
            # IG doesn't always tag the referral type even when it DOES send an ad_id (live
            # case, thread 2158) — an ad_id is unambiguous evidence of a click-to-message ad,
            # so fall back to it rather than silently missing the entry-point prompt hint
            # (source_hint) that acknowledges the ad instead of a generic "how can I help".
            if inbound.lead_source:
                thread.lead_source = inbound.lead_source
            elif inbound.ad_id:
                thread.lead_source = "ad_clicktomsg"
        if inbound.ad_id and thread.ad_id is None:
            thread.ad_id = inbound.ad_id
        if inbound.ad_media_id and thread.ad_media_id is None:
            thread.ad_media_id = inbound.ad_media_id
        if inbound.ad_preview_url:
            thread.ad_preview_url = inbound.ad_preview_url  # always refresh (CDN URL)
        if thread.product_slug is None and thread.ad_id:
            mapped = await AdMappingService(
                self.session, self.branch_id).product_for_ad(thread.ad_id)
            if mapped:
                thread.product_slug = mapped
                thread.product_source = "ad"
        return msg

    async def _reset_followup_cycle(self, thread) -> None:
        """Fresh inbound restarts the follow-up cycle and cancels a queued nudge."""
        thread.followups_sent = 0
        thread.next_followup_at = None
        await self.session.execute(
            text(
                "UPDATE outbox SET status='skipped' WHERE thread_id=:tid"
                " AND status='pending' AND source='followup'"
            ),
            {"tid": thread.id},
        )

    def _revive_bot(self, lead, thread) -> None:
        """Fresh inbound re-enables the bot — except when a human leads the stage.

        Dormant leads wake up into qualifying (S1 semantics) with a journal entry."""
        if lead.is_blocked or lead.stage in HUMAN_LED_STAGES:
            return
        if lead.stage == Stage.DORMANT:
            self.session.add(StageEvent(
                branch_id=self.branch_id, lead_id=lead.id, thread_id=thread.id,
                from_stage=str(lead.stage), to_stage=str(Stage.QUALIFYING),
                actor="system", reason="lead revived by fresh inbound",
            ))
            lead.stage = Stage.QUALIFYING
            logger.info("branch=%d lead=%d revived dormant → qualifying",
                        self.branch_id, lead.id)
        if not lead.agent_enabled:
            lead.agent_enabled = True
        self.session.add(lead)


def _external_id(inbound: InboundMessage) -> str:
    """Stable per-message id — InboundMessage carries no native id, so derive one."""
    return f"{inbound.external_thread_id}:{inbound.occurred_at.isoformat()}:{inbound.sender_id}"
