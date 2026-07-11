"""ProfileService + MediaService: stale refresh, TTL skip, failure safety, backfill."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlmodel import select

from app.adapters.db.models import Branch, Channel, Lead, MediaAsset, Message, _utcnow
from app.domain.enums import ChannelKind, Stage
from app.modules.leads.profiles import PROFILE_TTL, ProfileService
from app.modules.media.service import MediaService


class FakeProfileFetcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def fetch_profile(self, ig_user_id: str) -> dict[str, Any] | None:
        self.calls.append(ig_user_id)
        if self.fail:
            return None
        return {"follower_count": 500, "following_count": 200}


class FakeDownloader:
    def __init__(self, *, fail: bool = False, data: bytes = b"BYTES") -> None:
        self.fail = fail
        self.data = data
        self.calls: list[str] = []

    async def download_media(self, url: str) -> bytes:
        self.calls.append(url)
        if self.fail:
            raise RuntimeError("cdn 403")
        return self.data


async def _branch(s) -> int:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    return b.id


async def _lead(s, bid, *, ig="1", stage=Stage.QUALIFYING, synced_min_ago=None) -> Lead:
    synced = None if synced_min_ago is None else _utcnow() - timedelta(minutes=synced_min_ago)
    lead = Lead(branch_id=bid, ig_user_id=ig, stage=stage, profile_synced_at=synced)
    s.add(lead)
    await s.flush()
    return lead


# ─── profiles ───

async def test_stale_lead_refreshed(db_session) -> None:
    bid = await _branch(db_session)
    lead = await _lead(db_session, bid)  # never synced
    fetcher = FakeProfileFetcher()
    assert await ProfileService(db_session, bid).refresh(fetcher, limit=20) == 1
    assert fetcher.calls == ["1"]
    refreshed = (await db_session.exec(select(Lead).where(Lead.id == lead.id))).first()
    assert refreshed.follower_count == 500
    assert refreshed.following_count == 200
    assert refreshed.profile_synced_at is not None
    assert refreshed.last_active_at is not None


async def test_fresh_lead_skipped(db_session) -> None:
    bid = await _branch(db_session)
    await _lead(db_session, bid, synced_min_ago=10)  # inside 6h TTL
    fetcher = FakeProfileFetcher()
    assert await ProfileService(db_session, bid).refresh(fetcher, limit=20) == 0
    assert fetcher.calls == []


async def test_expired_ttl_lead_refreshed(db_session) -> None:
    bid = await _branch(db_session)
    old_min = int(PROFILE_TTL.total_seconds() // 60) + 30
    await _lead(db_session, bid, synced_min_ago=old_min)
    assert await ProfileService(db_session, bid).refresh(FakeProfileFetcher(), limit=20) == 1


async def test_refresh_backfills_missing_name_and_avatar(db_session) -> None:
    bid = await _branch(db_session)
    lead = await _lead(db_session, bid)  # no display_name / ig_username / avatar

    class _NamedFetcher:
        async def fetch_profile(self, _id: str) -> dict[str, Any]:
            return {"follower_count": 1, "following_count": 2, "username": "budi",
                    "full_name": "Budi S", "avatar_url": "https://cdn/x.jpg"}

    await ProfileService(db_session, bid).refresh(_NamedFetcher(), limit=20)
    got = (await db_session.exec(select(Lead).where(Lead.id == lead.id))).first()
    assert got.display_name == "Budi S" and got.ig_username == "budi"
    assert got.avatar_url == "https://cdn/x.jpg"


async def test_bot_silent_stage_lead_skipped(db_session) -> None:
    bid = await _branch(db_session)
    await _lead(db_session, bid, stage=Stage.HANDED_OFF)  # bot-silent → not active funnel
    fetcher = FakeProfileFetcher()
    assert await ProfileService(db_session, bid).refresh(fetcher, limit=20) == 0
    assert fetcher.calls == []


async def test_failure_leaves_lead_untouched(db_session) -> None:
    bid = await _branch(db_session)
    lead = await _lead(db_session, bid)
    assert await ProfileService(db_session, bid).refresh(FakeProfileFetcher(fail=True), 20) == 0
    refreshed = (await db_session.exec(select(Lead).where(Lead.id == lead.id))).first()
    assert refreshed.follower_count is None
    assert refreshed.profile_synced_at is None  # untouched → retried next tick


# ─── media ───

async def _channel(s, bid) -> int:
    ch = Channel(branch_id=bid, kind=ChannelKind.INSTAGRAM)
    s.add(ch)
    await s.flush()
    return ch.id


async def _media_msg(s, bid, cid, *, ext, url="https://cdn/x.jpg", kind="image",
                     pending=True, stub=True, at=None) -> Message:
    ph = "🎤 voice" if kind == "audio" else "🖼 media"
    kw = {"occurred_at": at} if at is not None else {}
    m = Message(branch_id=bid, thread_id=1, channel_id=cid, external_id=ext,
                direction="in", sent_by="lead", text=ph, media_pending=pending, **kw)
    s.add(m)
    await s.flush()
    if stub:  # ingest attaches a not-yet-downloaded MediaAsset (url set, data NULL)
        s.add(MediaAsset(branch_id=bid, message_id=m.id, kind=kind, url=url))
        await s.flush()
    return m


async def test_store_persists_asset(db_session) -> None:
    bid = await _branch(db_session)
    asset = await MediaService(db_session, bid).store(None, "image", "image/jpeg", "u", b"x")
    got = (await db_session.exec(select(MediaAsset))).first()
    assert got is not None and got.id == asset.id
    assert got.branch_id == bid and got.kind == "image" and got.data == b"x"


async def test_backfill_downloads_attaches_clears_flag(db_session) -> None:
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    msg = await _media_msg(db_session, bid, cid, ext="m1")
    dl = FakeDownloader()
    assert await MediaService(db_session, bid).backfill(cid, dl, limit=20) == 1
    assert dl.calls == ["https://cdn/x.jpg"]
    refreshed = (await db_session.exec(select(Message).where(Message.id == msg.id))).first()
    assert refreshed.media_pending is False
    asset = (await db_session.exec(select(MediaAsset))).first()
    assert asset is not None and asset.message_id == msg.id and asset.data == b"BYTES"


async def test_backfill_failure_keeps_pending(db_session) -> None:
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    msg = await _media_msg(db_session, bid, cid, ext="m1")
    assert await MediaService(db_session, bid).backfill(cid, FakeDownloader(fail=True), 20) == 0
    refreshed = (await db_session.exec(select(Message).where(Message.id == msg.id))).first()
    assert refreshed.media_pending is True  # stays flagged for retry
    asset = (await db_session.exec(select(MediaAsset))).first()
    assert asset is not None and asset.data is None  # stub kept, undownloaded


async def test_backfill_permanent_reject_clears_flag(db_session) -> None:
    """A ValueError (e.g. the transport's size cap on a huge video) is permanent — the flag
    is cleared so it isn't re-streamed every tick forever, unlike a transient failure."""
    class _TooBigDownloader:
        async def download_media(self, url: str) -> bytes:
            raise ValueError("media exceeds 62914560 bytes — refusing to buffer")

    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    msg = await _media_msg(db_session, bid, cid, ext="m1")
    assert await MediaService(db_session, bid).backfill(cid, _TooBigDownloader(), 20) == 0
    refreshed = (await db_session.exec(select(Message).where(Message.id == msg.id))).first()
    assert refreshed.media_pending is False  # cleared → won't retry the oversized media


async def test_backfill_noop_when_nothing_flagged(db_session) -> None:
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    await _media_msg(db_session, bid, cid, ext="m1", pending=False)
    dl = FakeDownloader()
    assert await MediaService(db_session, bid).backfill(cid, dl, limit=20) == 0
    assert dl.calls == []


class FakeTranscriber:
    def __init__(self, *, text: str = "halo apa kabar", fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls = 0

    async def transcribe(self, audio, *, mime="audio/mp4", thread_id=None, branch_id=None):  # noqa: ANN001, ANN003
        self.calls += 1
        if self.fail:
            raise RuntimeError("project lacks scope: llm:audio")
        return self.text


async def test_voice_backfill_transcribes_into_message_text(db_session) -> None:
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    msg = await _media_msg(db_session, bid, cid, ext="v1", kind="audio", url="https://cdn/v.mp4")
    tr = FakeTranscriber(text="berapa harga kursusnya")
    assert await MediaService(db_session, bid).backfill(
        cid, FakeDownloader(), limit=20, transcriber=tr) == 1
    assert tr.calls == 1
    refreshed = (await db_session.exec(select(Message).where(Message.id == msg.id))).first()
    assert refreshed.text == "🎤 berapa harga kursusnya"  # placeholder replaced with content


class FakeTranslator:
    """A minimal LLMPort.chat that returns a fixed Cyrillic string, so translate_text's
    _looks_translated gate passes (it requires Cyrillic for a Russian target)."""

    def __init__(self, ru: str = "перевод сообщения") -> None:
        self.ru = ru
        self.calls = 0

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003
        self.calls += 1
        return self.ru, {"cost_usd": 0.0}


async def test_voice_backfill_caches_russian_translation(db_session) -> None:
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    msg = await _media_msg(db_session, bid, cid, ext="v1", kind="audio", url="https://cdn/v.mp4")
    trn = FakeTranslator(ru="сколько стоит курс")
    assert await MediaService(db_session, bid).backfill(
        cid, FakeDownloader(), limit=20,
        transcriber=FakeTranscriber(text="berapa harga kursusnya"), translator=trn) == 1
    refreshed = (await db_session.exec(select(Message).where(Message.id == msg.id))).first()
    assert refreshed.text == "🎤 berapa harga kursusnya"
    assert refreshed.tr_text == "сколько стоит курс"  # log shows the RU translation too
    assert trn.calls == 1


async def test_backfill_invalidates_stale_placeholder_translation(db_session) -> None:
    """If an operator viewed the bubble before backfill, tr_text cached a translation of the
    '🎤 voice' placeholder — rewriting the text must drop that stale cache (here no translator,
    so it clears to NULL and the on-view path re-translates the real content)."""
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    msg = await _media_msg(db_session, bid, cid, ext="v1", kind="audio", url="https://cdn/v.mp4")
    msg.tr_text = "🎤 голос"  # stale placeholder translation
    db_session.add(msg)
    await db_session.flush()
    assert await MediaService(db_session, bid).backfill(
        cid, FakeDownloader(), limit=20,
        transcriber=FakeTranscriber(text="halo kak")) == 1
    refreshed = (await db_session.exec(select(Message).where(Message.id == msg.id))).first()
    assert refreshed.text == "🎤 halo kak" and refreshed.tr_text is None  # stale cache dropped


async def test_recognition_failure_keeps_pending_within_window(db_session) -> None:
    """A fresh voice whose transcription fails (broker down / provider key not yet configured)
    keeps media_pending set — the bytes are stored, and the backfill cron retries next tick
    so it self-heals once the broker is fixed, instead of giving up after one attempt."""
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    msg = await _media_msg(db_session, bid, cid, ext="v1", kind="audio", url="https://cdn/v.mp4")
    assert await MediaService(db_session, bid).backfill(
        cid, FakeDownloader(), limit=20, transcriber=FakeTranscriber(fail=True)) == 1
    refreshed = (await db_session.exec(select(Message).where(Message.id == msg.id))).first()
    assert refreshed.text == "🎤 voice" and refreshed.media_pending is True  # held, will retry
    asset = (await db_session.exec(select(MediaAsset))).first()
    assert asset.data == b"BYTES"  # bytes kept — the retry reuses them, no re-download


async def test_recognition_failure_releases_hold_past_window(db_session) -> None:
    """Past the retry window a permanently-failing transcription stops holding the thread: the
    placeholder is swapped for a non-pending fallback so the bot answers (asks the lead to type)."""
    from datetime import timedelta

    from app.domain.clock import utc_now
    from app.modules.media.service import _MEDIA_RETRY_WINDOW, _VOICE_UNAVAILABLE

    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    old = utc_now() - _MEDIA_RETRY_WINDOW - timedelta(minutes=1)
    msg = await _media_msg(db_session, bid, cid, ext="v1", kind="audio",
                           url="https://cdn/v.mp4", at=old)
    assert await MediaService(db_session, bid).backfill(
        cid, FakeDownloader(), limit=20, transcriber=FakeTranscriber(fail=True)) == 1
    refreshed = (await db_session.exec(select(Message).where(Message.id == msg.id))).first()
    assert refreshed.text == _VOICE_UNAVAILABLE and refreshed.media_pending is False


async def test_recognition_retry_succeeds_on_a_downloaded_asset(db_session) -> None:
    """A retry (bytes already downloaded, still media_pending) transcribes WITHOUT re-fetching."""
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    msg = await _media_msg(db_session, bid, cid, ext="v1", kind="audio", url="https://cdn/v.mp4")
    s = MediaService(db_session, bid)
    assert await s.backfill(cid, FakeDownloader(), 20, transcriber=FakeTranscriber(fail=True)) == 1
    dl = FakeDownloader()  # second tick: must NOT be called again
    assert await s.backfill(cid, dl, 20, transcriber=FakeTranscriber(text="halo")) == 1
    assert dl.calls == []  # bytes reused, no re-download
    refreshed = (await db_session.exec(select(Message).where(Message.id == msg.id))).first()
    assert refreshed.text == "🎤 halo" and refreshed.media_pending is False


class FakeDescriber:
    def __init__(self, *, text: str = "screenshot harga kursus", fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls = 0

    async def describe_image(self, image, *, mime="image/jpeg", thread_id=None, branch_id=None):  # noqa: ANN001, ANN003
        self.calls += 1
        if self.fail:
            raise RuntimeError("project lacks scope: llm:vision")
        return self.text


async def test_image_backfill_describes_into_message_text(db_session) -> None:
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    msg = await _media_msg(db_session, bid, cid, ext="i1", kind="image")
    desc = FakeDescriber(text="jadwal kelas SMM")
    assert await MediaService(db_session, bid).backfill(
        cid, FakeDownloader(), limit=20, describer=desc) == 1
    assert desc.calls == 1
    refreshed = (await db_session.exec(select(Message).where(Message.id == msg.id))).first()
    assert refreshed.text == "🖼 jadwal kelas SMM"  # placeholder replaced with description


async def test_image_describe_failure_keeps_pending_within_window(db_session) -> None:
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    msg = await _media_msg(db_session, bid, cid, ext="i1", kind="image")
    assert await MediaService(db_session, bid).backfill(
        cid, FakeDownloader(), limit=20, describer=FakeDescriber(fail=True)) == 1
    refreshed = (await db_session.exec(select(Message).where(Message.id == msg.id))).first()
    assert refreshed.text == "🖼 media" and refreshed.media_pending is True  # held, will retry


async def test_image_backfill_does_not_transcribe(db_session) -> None:
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    await _media_msg(db_session, bid, cid, ext="m1", kind="image")
    tr = FakeTranscriber()
    await MediaService(db_session, bid).backfill(cid, FakeDownloader(), limit=20, transcriber=tr)
    assert tr.calls == 0  # only audio is transcribed


async def test_backfill_clears_flag_when_no_stub(db_session) -> None:
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    msg = await _media_msg(db_session, bid, cid, ext="m1", stub=False)
    dl = FakeDownloader()
    assert await MediaService(db_session, bid).backfill(cid, dl, limit=20) == 0
    assert dl.calls == []
    refreshed = (await db_session.exec(select(Message).where(Message.id == msg.id))).first()
    assert refreshed.media_pending is False  # nothing to fetch — don't loop forever
