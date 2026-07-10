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


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, *, mime: str = ...,
                         thread_id: int | None = ..., branch_id: int | None = ...) -> str: ...


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

    async def backfill(
        self, channel_id: int, downloader: MediaDownloader, limit: int,
        transcriber: Transcriber | None = None,
    ) -> int:
        """Download bytes for the media stub ingest attached to each pending message.

        Ingest records a MediaAsset stub (url set, data NULL) and flags the message; here
        we fill the stub's bytes. A stub without a live url just clears the flag; a
        download failure keeps the flag so the next tick retries — nothing is lost. For a
        voice message we also transcribe the audio (broker) into the message text, so the
        bot answers what was SAID, not '🎤 voice'."""
        done = 0
        for msg in await self.pending(channel_id, limit):
            stub = await self._pending_stub(msg.id)
            if stub is None or not stub.url:
                msg.media_pending = False  # nothing to fetch — don't retry forever
                self.session.add(msg)
                await self.session.flush()
                continue
            try:
                data = await downloader.download_media(stub.url)
            except ValueError as exc:
                # A permanent reject (e.g. the transport's size cap — a video too big to
                # buffer): clear the flag so we don't re-stream it every tick forever.
                logger.warning(
                    "media permanently skipped branch=%d msg=%d: %s",
                    self.branch_id, msg.id, exc)
                msg.media_pending = False
                self.session.add(msg)
                await self.session.flush()
                continue
            except Exception as exc:  # noqa: BLE001 — transient: keep flag, retry next tick
                logger.warning(
                    "media download failed branch=%d msg=%d: %s",
                    self.branch_id, msg.id, exc)
                continue
            stub.data = data
            msg.media_pending = False
            if stub.kind == "audio" and transcriber is not None:
                await self._transcribe_voice(msg, data, transcriber)
            self.session.add_all([stub, msg])
            await self.session.flush()
            done += 1
        if done:
            logger.info("media backfill branch=%d channel=%d: %d assets",
                        self.branch_id, channel_id, done)
        return done

    async def _transcribe_voice(self, msg: Message, audio: bytes, transcriber: Transcriber) -> None:
        """Replace a voice message's '🎤 voice' placeholder with its transcript, so the bot
        reads the spoken content. On failure keep the placeholder — never block the backfill."""
        try:
            text = await transcriber.transcribe(
                audio, mime="audio/mp4", thread_id=msg.thread_id, branch_id=self.branch_id)
        except Exception as exc:  # noqa: BLE001 — scope/transport error → keep placeholder
            logger.warning("voice transcribe failed branch=%d msg=%d: %s",
                           self.branch_id, msg.id, exc)
            return
        if text:
            msg.text = f"🎤 {text}"  # 🎤 marks it a voice; the prompt reads the words after it

    async def _pending_stub(self, message_id: int | None) -> MediaAsset | None:
        """The not-yet-downloaded MediaAsset for a message (data NULL, url set)."""
        q = (
            select(MediaAsset)
            .where(MediaAsset.message_id == message_id, MediaAsset.data.is_(None))  # type: ignore[union-attr]
            .limit(1)
        )
        return (await self.session.exec(q)).first()
