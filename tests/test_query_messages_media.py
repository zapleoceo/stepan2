"""fetch_messages/fetch_messages_since media resolution: each message row must carry the
FIRST media_asset with data (by id), matching the pre-optimization correlated-subquery
behaviour — this file guards the LEFT JOIN + ROW_NUMBER() rewrite (perf fix: two
correlated subqueries per row -> one join for the whole thread) against a regression."""
from __future__ import annotations

from datetime import UTC, datetime

from app.adapters.db.models import (
    Branch,
    Channel,
    ChannelThread,
    Lead,
    MediaAsset,
    Message,
)
from app.api._query import fetch_messages, fetch_messages_since
from app.domain.enums import ChannelKind

_NOW = datetime.now(UTC).replace(tzinfo=None)


async def _setup(s) -> tuple[int, int]:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id)
    s.add_all([ch, lead])
    await s.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    s.add(thread)
    await s.flush()
    return b.id, thread.id


async def test_message_gets_first_media_asset_with_data(db_session) -> None:
    branch_id, thread_id = await _setup(db_session)
    m = Message(branch_id=branch_id, thread_id=thread_id, channel_id=1, external_id="m1",
                direction="in", sent_by="lead", text="[photo]", occurred_at=_NOW)
    db_session.add(m)
    await db_session.flush()
    # a data=None placeholder row (e.g. failed download) must be skipped in favour of the
    # next real asset — mirrors the old subquery's "ma.data IS NOT NULL" filter.
    db_session.add(MediaAsset(branch_id=branch_id, message_id=m.id, kind="image", data=None))
    good = MediaAsset(branch_id=branch_id, message_id=m.id, kind="video", data=b"x")
    db_session.add(good)
    await db_session.flush()

    rows = await fetch_messages(db_session, thread_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.media_id == good.id
    assert row.media_kind == "video"


async def test_message_without_media_has_null_media_columns(db_session) -> None:
    branch_id, thread_id = await _setup(db_session)
    m = Message(branch_id=branch_id, thread_id=thread_id, channel_id=1, external_id="m1",
                direction="in", sent_by="lead", text="hi", occurred_at=_NOW)
    db_session.add(m)
    await db_session.flush()

    rows = await fetch_messages(db_session, thread_id)
    assert rows[0].media_id is None
    assert rows[0].media_kind is None


async def test_fetch_messages_since_also_resolves_media(db_session) -> None:
    branch_id, thread_id = await _setup(db_session)
    m1 = Message(branch_id=branch_id, thread_id=thread_id, channel_id=1, external_id="m1",
                 direction="in", sent_by="lead", text="old", occurred_at=_NOW)
    db_session.add(m1)
    await db_session.flush()
    m2 = Message(branch_id=branch_id, thread_id=thread_id, channel_id=1, external_id="m2",
                 direction="in", sent_by="lead", text="[img]", occurred_at=_NOW)
    db_session.add(m2)
    await db_session.flush()
    asset = MediaAsset(branch_id=branch_id, message_id=m2.id, kind="image", data=b"y")
    db_session.add(asset)
    await db_session.flush()

    rows = await fetch_messages_since(db_session, thread_id, m1.id)
    assert len(rows) == 1
    assert rows[0].media_id == asset.id
    assert rows[0].media_kind == "image"
