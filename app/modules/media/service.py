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

from app.adapters.channels.ig_parse import IMAGE_PENDING_PH, VOICE_PENDING_PH
from app.adapters.db.models import MediaAsset, Message

# When media can NEVER be understood (dead url, permanent download reject, or a failed
# transcription/vision call), we must move its text OFF the pending placeholder —
# ReplyService.decide holds the reply while the newest inbound still equals the placeholder,
# so leaving it would silently freeze the whole thread with no answer and no alert. The
# fallback text is non-placeholder, so the bot answers (and asks the lead to type instead).
_VOICE_UNAVAILABLE = "🎤 (voice — no transcript)"
_IMAGE_UNAVAILABLE = "🖼 (image — tidak bisa dibaca)"

logger = logging.getLogger(__name__)


class MediaDownloader(Protocol):
    async def download_media(self, url: str) -> bytes: ...


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, *, mime: str = ...,
                         thread_id: int | None = ..., branch_id: int | None = ...) -> str: ...


class ImageDescriber(Protocol):
    async def describe_image(self, image: bytes, *, mime: str = ...,
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
        describer: ImageDescriber | None = None,
    ) -> int:
        """Download bytes for the media stub ingest attached to each pending message.

        Ingest records a MediaAsset stub (url set, data NULL) and flags the message; here
        we fill the stub's bytes. A stub without a live url just clears the flag; a
        download failure keeps the flag so the next tick retries — nothing is lost. For a
        voice message we transcribe the audio and for an image we caption it (broker) into
        the message text, so the bot answers what was SAID/SHOWN, not '🎤 voice' / '🖼 media'."""
        done = 0
        for msg in await self.pending(channel_id, limit):
            stub = await self._pending_stub(msg.id)
            if stub is None or not stub.url:
                msg.media_pending = False  # nothing to fetch — don't retry forever
                self._release_voice_hold(msg)
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
                self._release_voice_hold(msg)
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
            # On a failed transcript/caption, swap the placeholder for a non-pending fallback
            # so decide()'s media hold releases and the thread never freezes (the bot answers
            # and asks the lead to type). The bytes are saved either way — no re-download.
            if stub.kind == "audio" and transcriber is not None:
                if not await self._transcribe_voice(msg, data, transcriber):
                    self._release_voice_hold(msg)
            elif stub.kind == "image" and describer is not None:
                if not await self._describe_image(msg, data, stub.mime, describer):
                    self._release_image_hold(msg)
            self.session.add_all([stub, msg])
            await self.session.flush()
            done += 1
        if done:
            logger.info("media backfill branch=%d channel=%d: %d assets",
                        self.branch_id, channel_id, done)
        return done

    def _release_voice_hold(self, msg: Message) -> None:
        """A voice note we've given up on must not keep its '🎤 voice' placeholder, or decide()
        holds the reply forever (see _VOICE_UNAVAILABLE). Swap in a non-placeholder so the bot
        answers — it will ask the lead to type the message instead."""
        if (msg.text or "").strip() == VOICE_PENDING_PH:
            msg.text = _VOICE_UNAVAILABLE

    def _release_image_hold(self, msg: Message) -> None:
        """Same as _release_voice_hold, for an image we couldn't caption."""
        if (msg.text or "").strip() == IMAGE_PENDING_PH:
            msg.text = _IMAGE_UNAVAILABLE

    async def _transcribe_voice(
        self, msg: Message, audio: bytes, transcriber: Transcriber) -> bool:
        """Replace a voice message's '🎤 voice' placeholder with its transcript, so the bot
        reads the spoken content. Returns True when text was written; False on failure/empty
        (caller releases the hold) — never block the backfill."""
        try:
            text = await transcriber.transcribe(
                audio, mime="audio/mp4", thread_id=msg.thread_id, branch_id=self.branch_id)
        except Exception as exc:  # noqa: BLE001 — scope/transport error → release the hold
            logger.warning("voice transcribe failed branch=%d msg=%d: %s",
                           self.branch_id, msg.id, exc)
            return False
        if not text:
            return False
        msg.text = f"🎤 {text}"  # 🎤 marks it a voice; the prompt reads the words after it
        return True

    async def _describe_image(
        self, msg: Message, image: bytes, mime: str | None, describer: ImageDescriber) -> bool:
        """Replace an image's '🖼 media' placeholder with a caption, so the bot answers what
        was shown (a screenshot, a payment proof, a product photo). Returns True when text was
        written; False on failure/empty (caller releases the hold)."""
        try:
            text = await describer.describe_image(
                image, mime=mime or "image/jpeg",
                thread_id=msg.thread_id, branch_id=self.branch_id)
        except Exception as exc:  # noqa: BLE001 — scope/transport error → release the hold
            logger.warning("image describe failed branch=%d msg=%d: %s",
                           self.branch_id, msg.id, exc)
            return False
        if not text:
            return False
        msg.text = f"🖼 {text}"  # 🖼 marks it an image; the prompt reads the description after it
        return True

    async def _pending_stub(self, message_id: int | None) -> MediaAsset | None:
        """The not-yet-downloaded MediaAsset for a message (data NULL, url set)."""
        q = (
            select(MediaAsset)
            .where(MediaAsset.message_id == message_id, MediaAsset.data.is_(None))  # type: ignore[union-attr]
            .limit(1)
        )
        return (await self.session.exec(q)).first()
