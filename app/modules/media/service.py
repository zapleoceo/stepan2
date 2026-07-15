"""MediaService — store + backfill IG media the ingest path can't carry (S1 media_backfill).

Ingest sees a media item with empty text and flags the message (media_pending=True);
this branch-scoped service later downloads the bytes via a channel transport and
attaches a MediaAsset, clearing the flag. A download failure leaves the flag set so
the next tick retries — nothing is lost and the loop never crashes."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Protocol

from sqlalchemy import or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.channels.ig_parse import IMAGE_PENDING_PH, VOICE_PENDING_PH
from app.adapters.db.models import MediaAsset, Message
from app.domain.clock import utc_now
from app.modules.conversation.translate import translate_text
from app.ports.llm import LLMPort

# When media can NEVER be understood (dead url, permanent download reject, or a failed
# transcription/vision call), we must move its text OFF the pending placeholder —
# ReplyService.decide holds the reply while the newest inbound still equals the placeholder,
# so leaving it would silently freeze the whole thread with no answer and no alert. The
# fallback text is non-placeholder, so the bot answers (and asks the lead to type instead).
_VOICE_UNAVAILABLE = "🎤 (voice — no transcript)"
_IMAGE_UNAVAILABLE = "🖼 (image — tidak bisa dibaca)"

# The bytes are downloaded once, but recognition (broker transcribe/vision) can fail for a
# while — the broker may be briefly down or its provider key not yet configured. Keep
# media_pending set on a failed recognition so the backfill cron retries it every tick, but
# only for this long after the message arrived; past it, give up and release the hold so the
# thread isn't frozen indefinitely. Covers "the voice hangs unanswered until the broker is
# fixed" without hammering a permanently-broken provider forever.
_MEDIA_RETRY_WINDOW = timedelta(hours=6)

# The cron ticks every 3 minutes, so retrying on every tick meant ~120 submits of the SAME
# media inside the retry window — and the broker already retries 8 times with its own
# backoff, so the two layers multiplied (a real incident: one image re-submitted 33 times in
# 90 minutes while vision was down). Space our own attempts out exponentially instead:
# 5, 10, 20, 40, 60, 60 ... minutes, so the 6h window costs ~8 attempts, not 120. When the
# provider recovers, the next due attempt picks the media up as before.
_MEDIA_RETRY_BASE = timedelta(minutes=5)
_MEDIA_RETRY_MAX = timedelta(hours=1)

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
        """Messages of this channel still awaiting a media download (capped batch).

        Skips anything whose back-off has not elapsed yet, so a failing item costs one
        attempt per back-off step instead of one per 3-minute tick."""
        q = (
            select(Message)
            .where(
                Message.branch_id == self.branch_id,
                Message.channel_id == channel_id,
                Message.media_pending.is_(True),  # type: ignore[union-attr]
                or_(
                    Message.media_next_try_at.is_(None),  # type: ignore[union-attr]
                    Message.media_next_try_at <= utc_now(),  # type: ignore[operator]
                ),
            )
            .limit(limit)
        )
        return list((await self.session.exec(q)).all())

    def _defer(self, msg: Message) -> None:
        """Transient failure: keep the item flagged, but push the next attempt out
        exponentially (capped) so we don't stack a retry storm on the broker's own retries."""
        msg.media_attempts = (msg.media_attempts or 0) + 1
        step = _MEDIA_RETRY_BASE * (2 ** (msg.media_attempts - 1))
        msg.media_next_try_at = utc_now() + min(step, _MEDIA_RETRY_MAX)
        msg.media_pending = True

    async def backfill(
        self, channel_id: int, downloader: MediaDownloader, limit: int,
        transcriber: Transcriber | None = None,
        describer: ImageDescriber | None = None,
        translator: LLMPort | None = None,
    ) -> int:
        """Download bytes for the media stub ingest attached to each pending message.

        Ingest records a MediaAsset stub (url set, data NULL) and flags the message; here
        we fill the stub's bytes. A stub without a live url just clears the flag; a
        download failure keeps the flag so the next tick retries — nothing is lost. For a
        voice message we transcribe the audio and for an image we caption it (broker) into
        the message text, so the bot answers what was SAID/SHOWN, not '🎤 voice' / '🖼 media'."""
        done = 0
        for msg in await self.pending(channel_id, limit):
            stub = await self._media_stub(msg.id)
            if stub is None or not stub.url:
                msg.media_pending = False  # nothing to fetch — don't retry forever
                self._release_media_hold(msg)
                self.session.add(msg)
                await self.session.flush()
                continue
            if stub.data is None:  # first pass — fetch the bytes (a retry already has them)
                try:
                    stub.data = await downloader.download_media(stub.url)
                except ValueError as exc:
                    # A permanent reject (e.g. the transport's size cap — a video too big to
                    # buffer): clear the flag so we don't re-stream it every tick forever.
                    logger.warning("media permanently skipped branch=%d msg=%d: %s",
                                   self.branch_id, msg.id, exc)
                    msg.media_pending = False
                    self._release_media_hold(msg)
                    self.session.add(msg)
                    await self.session.flush()
                    continue
                except Exception as exc:  # noqa: BLE001 — transient: keep flag, back off, retry
                    logger.warning("media download failed branch=%d msg=%d (attempt %d): %s",
                                   self.branch_id, msg.id, (msg.media_attempts or 0) + 1, exc)
                    self._defer(msg)
                    self.session.add(msg)
                    await self.session.flush()
                    continue
            recognized = await self._recognize(msg, stub, transcriber, describer)
            if recognized:
                await self._recache_translation(msg, translator)
                msg.media_pending = False
                msg.media_next_try_at = None
            elif recognized is False and self._retry_window_open(msg):
                # Recognition failed but the message is still fresh — the broker may be down
                # or its provider key not yet configured. Keep the flag, but back off: the
                # broker already retries internally, so a per-tick retry here multiplies load.
                self._defer(msg)
            else:
                # Nothing can recognize it, or we've retried past the window: stop holding the
                # thread. The bot answers (asks the lead to type) instead of freezing.
                self._release_media_hold(msg)
                msg.media_pending = False
            self.session.add_all([stub, msg])
            await self.session.flush()
            done += 1
        if done:
            logger.info("media backfill branch=%d channel=%d: %d assets",
                        self.branch_id, channel_id, done)
        return done

    async def recognize_now(
        self, message_id: int, *,
        transcriber: Transcriber | None = None,
        describer: ImageDescriber | None = None,
        translator: LLMPort | None = None,
    ) -> str | None:
        """Recognize ONE already-downloaded attachment on demand (the chat's manual button).

        Unlike backfill this ignores media_pending, so it also rescues media the cron gave up
        on (or never retried after an old failure), and it can re-run on media that already
        has a transcript. Returns the new message text, or None when there is nothing to work
        with (no stub, no bytes, or no recognizer for this kind). Never releases the media
        hold on failure — a manual attempt surfaces the error instead of silently giving up."""
        msg = await self.session.get(Message, message_id)
        if msg is None or msg.branch_id != self.branch_id:
            return None
        stub = await self._media_stub(message_id)
        if stub is None or stub.data is None:
            return None  # bytes not downloaded yet — the backfill owns fetching them
        if not await self._recognize(msg, stub, transcriber, describer):
            return None
        await self._recache_translation(msg, translator)
        msg.media_pending = False
        msg.media_next_try_at = None  # recognized — clear any pending back-off
        self.session.add(msg)
        await self.session.flush()
        return msg.text

    async def _recache_translation(self, msg: Message, translator: LLMPort | None) -> None:
        """The transcript/caption just replaced the placeholder, so any cached tr_text is a
        translation of the OLD placeholder — drop it. Then translate the real content into the
        cache (Russian, the operator base) so the chat log shows it without a lazy round-trip.
        On failure leave tr_text NULL — the on-view translate path retries."""
        msg.tr_text = None
        if translator is None:
            return
        body = (msg.text or "").strip()
        for mark in ("🎤 ", "🖼 "):  # translate the words, not the media marker
            if body.startswith(mark):
                body = body[len(mark):]
                break
        try:
            tr = await translate_text(translator, body, branch_id=self.branch_id,
                                      thread_id=msg.thread_id)
        except Exception as exc:  # noqa: BLE001 — translation is best-effort, never block backfill
            logger.warning("media translate failed branch=%d msg=%d: %s",
                           self.branch_id, msg.id, exc)
            return
        if tr:
            msg.tr_text = tr

    async def _recognize(
        self, msg: Message, stub: MediaAsset,
        transcriber: Transcriber | None, describer: ImageDescriber | None) -> bool | None:
        """Turn the media bytes into message text. Returns True on success, False when a
        recognizer ran but failed/empty (retryable), or None when nothing can handle this
        kind (no recognizer wired — never retry, just release)."""
        assert stub.data is not None
        if stub.kind == "audio" and transcriber is not None:
            return await self._transcribe_voice(msg, stub.data, transcriber)
        if stub.kind == "image" and describer is not None:
            return await self._describe_image(msg, stub.data, stub.mime, describer)
        return None

    def _retry_window_open(self, msg: Message) -> bool:
        return utc_now() - msg.occurred_at < _MEDIA_RETRY_WINDOW

    def _release_media_hold(self, msg: Message) -> None:
        """Media we've given up on must not keep its '🎤 voice' / '🖼 media' placeholder, or
        decide() holds the reply forever. Swap in a non-placeholder so the bot answers (asks
        the lead to type instead), and drop any stale placeholder translation."""
        before = (msg.text or "").strip()
        if before == VOICE_PENDING_PH:
            msg.text = _VOICE_UNAVAILABLE
        elif before == IMAGE_PENDING_PH:
            msg.text = _IMAGE_UNAVAILABLE
        if msg.text != before:
            msg.tr_text = None

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

    async def _media_stub(self, message_id: int | None) -> MediaAsset | None:
        """The MediaAsset for a message — whether or not its bytes are downloaded yet, so a
        recognition retry (data already present) reuses the same row instead of re-fetching."""
        q = select(MediaAsset).where(MediaAsset.message_id == message_id).limit(1)
        return (await self.session.exec(q)).first()
