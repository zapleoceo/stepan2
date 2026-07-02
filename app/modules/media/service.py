"""MediaService — store + backfill IG media the ingest path can't carry (S1 media_backfill).

Ingest sees a media item with empty text and flags the message (media_pending=True);
this branch-scoped service later downloads the bytes via a channel transport and
attaches a MediaAsset, clearing the flag. A download failure leaves the flag set so
the next tick retries — nothing is lost and the loop never crashes."""
from __future__ import annotations

import logging
from typing import Protocol

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import MediaAsset, Message

logger = logging.getLogger(__name__)


class MediaDownloader(Protocol):
    async def download_media(self, url: str) -> bytes: ...


class MediaService:
    """Persist and backfill media assets for one branch."""

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        self.session = session
        self.branch_id = branch_id

    async def store(
        self,
        message_id: int | None,
        kind: str,
        mime: str | None,
        url: str | None,
        data: bytes | None,
    ) -> MediaAsset:
        asset = MediaAsset(
            branch_id=self.branch_id, message_id=message_id, kind=kind,
            mime=mime, url=url, data=data,
        )
        self.session.add(asset)
        await self.session.flush()
        return asset

    async def pending(self, channel_id: int, limit: int) -> list[Message]:
        """Messages of this channel still awaiting a media download (capped batch)."""
        q = (
            select(Message)
            .where(
                Message.branch_id == self.branch_id,
                Message.channel_id == channel_id,
                Message.media_pending.is_(True),  # type: ignore[union-attr]
            )
            .limit(limit)
        )
        return list((await self.session.exec(q)).all())

    async def backfill(self, channel_id: int, downloader: MediaDownloader, limit: int) -> int:
        """Download media for pending messages of a channel; returns assets attached."""
        done = 0
        for msg in await self.pending(channel_id, limit):
            url = self._media_url(msg)
            if not url:
                msg.media_pending = False  # nothing to fetch — don't retry forever
                self.session.add(msg)
                await self.session.flush()
                continue
            try:
                data = await downloader.download_media(url)
            except Exception as exc:  # noqa: BLE001 — keep flag, retry next tick
                logger.warning(
                    "media download failed branch=%d msg=%d: %s",
                    self.branch_id, msg.id, exc)
                continue
            await self.store(msg.id, self._kind(msg), None, url, data)
            msg.media_pending = False
            self.session.add(msg)
            await self.session.flush()
            done += 1
        if done:
            logger.info("media backfill branch=%d channel=%d: %d assets",
                        self.branch_id, channel_id, done)
        return done

    def _media_url(self, msg: Message) -> str | None:
        # ingest stores the CDN url as the message text for a media item (empty caption)
        text = (msg.text or "").strip()
        return text if text.startswith("http") else None

    def _kind(self, msg: Message) -> str:
        low = (msg.text or "").lower()
        if ".mp4" in low or "video" in low:
            return "video"
        if ".mp3" in low or ".m4a" in low or "audio" in low or "voice" in low:
            return "audio"
        return "image"
