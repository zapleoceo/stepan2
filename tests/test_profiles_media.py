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


async def _media_msg(s, bid, cid, *, ext, text="https://cdn/x.jpg", pending=True) -> Message:
    m = Message(branch_id=bid, thread_id=1, channel_id=cid, external_id=ext,
                direction="in", sent_by="lead", text=text, media_pending=pending)
    s.add(m)
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
    assert (await db_session.exec(select(MediaAsset))).first() is None


async def test_backfill_noop_when_nothing_flagged(db_session) -> None:
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    await _media_msg(db_session, bid, cid, ext="m1", pending=False)
    dl = FakeDownloader()
    assert await MediaService(db_session, bid).backfill(cid, dl, limit=20) == 0
    assert dl.calls == []


async def test_backfill_clears_flag_when_no_url(db_session) -> None:
    bid = await _branch(db_session)
    cid = await _channel(db_session, bid)
    msg = await _media_msg(db_session, bid, cid, ext="m1", text="just a caption")
    dl = FakeDownloader()
    assert await MediaService(db_session, bid).backfill(cid, dl, limit=20) == 0
    assert dl.calls == []
    refreshed = (await db_session.exec(select(Message).where(Message.id == msg.id))).first()
    assert refreshed.media_pending is False  # nothing to fetch — don't loop forever
