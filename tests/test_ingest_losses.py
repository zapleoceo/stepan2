"""Ingest message-loss fixes: bursts kept, own replies recorded as out/manager with
last_out_at, real-id dedup vs OutboxSender rows, out never revives the bot."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import select

from app.adapters.channels.instagram import InstagramAdapter
from app.adapters.db.models import (
    Branch,
    Channel,
    ChannelThread,
    Lead,
    MediaAsset,
    Message,
)
from app.domain.enums import ChannelKind, Stage
from app.modules.leads.ingest import IngestService
from app.ports.channel import InboundMessage

_NOW = datetime.now(UTC).replace(tzinfo=None)


class FakeIGTransport:
    """Raw thread payload shaped like transports.InstagrapiTransport emits."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def fetch_threads(self) -> list[dict]:
        return self._rows

    async def send_direct(self, thread_id: str, text: str) -> dict:
        return {"item_id": "x"}

    async def account_health(self) -> str:
        return "ok"


async def _world(s) -> tuple[int, int, Lead, ChannelThread]:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    s.add(ch)
    await s.flush()
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    s.add(thread)
    await s.flush()
    return b.id, ch.id, lead, thread


def _in(text: str, *, ext: str, minutes_ago: int = 0, direction: str = "in") -> InboundMessage:
    return InboundMessage(
        external_thread_id="ig-1", sender_id="lead9" if direction == "in" else "own1",
        text=text, occurred_at=_NOW - timedelta(minutes=minutes_ago),
        direction=direction, external_id=ext,
    )


async def test_burst_of_lead_messages_all_ingested(db_session) -> None:
    bid, cid, _, _ = await _world(db_session)
    burst = [_in("раз", ext="i1", minutes_ago=3), _in("два", ext="i2", minutes_ago=2),
             _in("три", ext="i3", minutes_ago=1)]
    created = await IngestService(db_session, bid).ingest(cid, burst)
    assert [m.text for m in created] == ["раз", "два", "три"]


async def test_own_reply_recorded_as_manager_and_moves_last_out(db_session) -> None:
    bid, cid, lead, thread = await _world(db_session)
    lead.agent_enabled = False
    thread.followups_sent = 2
    await db_session.flush()
    rows = [_in("уже ответил с телефона", ext="i2", minutes_ago=1, direction="out")]
    created = await IngestService(db_session, bid).ingest(cid, rows)
    assert len(created) == 1 and created[0].sent_by == "manager"
    assert created[0].direction == "out"
    assert thread.last_out_at is not None  # bot no longer 'owes' a reply
    # out-messages never touch bot state / followup cycle:
    assert lead.agent_enabled is False
    assert thread.followups_sent == 2


async def test_own_send_polled_back_under_new_id_is_deduped(db_session) -> None:
    """OutboxSender already recorded the bot's send (send-API item id). The inbox poll
    re-surfaces the SAME message under a different item id, so external-id dedup misses it
    — content dedup on the out path must drop the poll-back echo (real prod dupe: one
    bubble showed up twice in the chat + LLM context)."""
    bid, cid, _, thread = await _world(db_session)
    db_session.add(Message(branch_id=bid, thread_id=thread.id, channel_id=cid,
                           external_id="send-api-id-1", direction="out", sent_by="agent",
                           text="Halo Kak! Ada yang bisa aku bantu?",
                           occurred_at=_NOW - timedelta(seconds=90)))
    await db_session.flush()
    # same text, DIFFERENT external id, a couple minutes later (poll latency) → the echo
    polled_back = InboundMessage(
        external_thread_id="ig-1", sender_id="own1", direction="out",
        external_id="inbox-poll-id-2", text="Halo Kak! Ada yang bisa aku bantu?",
        occurred_at=_NOW)
    created = await IngestService(db_session, bid).ingest(cid, [polled_back])
    assert created == []  # poll-back echo dropped
    n = len((await db_session.exec(
        select(Message).where(Message.thread_id == thread.id))).all())
    assert n == 1  # only the original send-record remains


async def test_genuine_manual_reply_from_ig_app_still_stored(db_session) -> None:
    """A human typing a NOVEL reply in the IG app must still be recorded — the content
    dedup only drops exact-text poll-back echoes, never a real distinct message."""
    bid, cid, _, thread = await _world(db_session)
    db_session.add(Message(branch_id=bid, thread_id=thread.id, channel_id=cid,
                           external_id="bot-send-1", direction="out", sent_by="agent",
                           text="Halo Kak!", occurred_at=_NOW - timedelta(seconds=30)))
    await db_session.flush()
    manual = InboundMessage(
        external_thread_id="ig-1", sender_id="own1", direction="out",
        external_id="manual-1", text="Kak, ini nomor WA tim kami: 0811...",
        occurred_at=_NOW)
    created = await IngestService(db_session, bid).ingest(cid, [manual])
    assert len(created) == 1 and created[0].text.startswith("Kak, ini nomor WA")


async def test_media_message_flags_pending_stubs_asset_and_records_seen(db_session) -> None:
    bid, cid, _, _ = await _world(db_session)
    seen = _NOW - timedelta(minutes=1)
    msg = InboundMessage(
        external_thread_id="ig-1", sender_id="lead9", text="🖼 media",
        occurred_at=_NOW, external_id="m1",
        media_url="https://cdn/x.jpg", media_kind="image", lead_seen_at=seen)
    created = await IngestService(db_session, bid).ingest(cid, [msg])
    assert created[0].media_pending is True
    asset = (await db_session.exec(select(MediaAsset))).first()
    assert asset is not None and asset.url == "https://cdn/x.jpg"
    assert asset.kind == "image" and asset.data is None
    assert asset.message_id == created[0].id
    thread = (await db_session.exec(
        select(ChannelThread).where(ChannelThread.external_thread_id == "ig-1"))).first()
    assert thread.lead_seen_at == seen  # read-receipt captured


async def test_read_receipt_advances_even_when_all_messages_dedup_away(db_session) -> None:
    """A lead who READS our replies without answering produces no new message rows — every
    polled item dedups away by external_id. The thread's lead_seen_at must still advance
    (live bug: thread 452 showed 'read' in the IG app, our UI never marked it)."""
    bid, cid, _, thread = await _world(db_session)
    older_seen = _NOW - timedelta(hours=2)
    thread.lead_seen_at = older_seen
    db_session.add(Message(branch_id=bid, thread_id=thread.id, channel_id=cid,
                           external_id="i-old", direction="in", sent_by="lead",
                           text="halo", occurred_at=_NOW - timedelta(hours=3)))
    await db_session.flush()
    # the same old item polled again, now carrying a FRESH read receipt
    fresh_seen = _NOW - timedelta(minutes=1)
    repoll = InboundMessage(
        external_thread_id="ig-1", sender_id="lead9", text="halo",
        occurred_at=_NOW - timedelta(hours=3), external_id="i-old",
        lead_seen_at=fresh_seen)
    created = await IngestService(db_session, bid).ingest(cid, [repoll])
    assert created == []  # message itself correctly deduped
    assert thread.lead_seen_at == fresh_seen  # but the receipt moved forward
    # a STALE receipt on a later poll must never rewind it
    stale = InboundMessage(
        external_thread_id="ig-1", sender_id="lead9", text="halo",
        occurred_at=_NOW - timedelta(hours=3), external_id="i-old",
        lead_seen_at=older_seen)
    await IngestService(db_session, bid).ingest(cid, [stale])
    assert thread.lead_seen_at == fresh_seen


async def test_content_dedup_catches_pending_to_main_drift(db_session) -> None:
    """Same message reappears under a new external id (pending→main inbox) — item-level
    dedup misses it, content dedup (same text, same 2s window, same thread) catches it."""
    bid, cid, _, _ = await _world(db_session)
    a = _in("Привет, Дмитрий!", ext="340282...:2026-07-03T01:08:49:764")  # pending synthetic
    b = InboundMessage(external_thread_id="ig-1", sender_id="lead9",
                       text="Привет, Дмитрий!", occurred_at=a.occurred_at,
                       external_id="32891299694350702848")  # same msg, real IG item id
    created = await IngestService(db_session, bid).ingest(cid, [a, b])
    assert len(created) == 1  # the drift-dupe is dropped


async def test_media_not_content_deduped(db_session) -> None:
    """Two distinct photos share the placeholder text '🖼 media' — must both survive."""
    bid, cid, _, _ = await _world(db_session)
    m1 = InboundMessage(external_thread_id="ig-1", sender_id="lead9", text="🖼 media",
                        occurred_at=_NOW, external_id="m1",
                        media_url="http://cdn/a.jpg", media_kind="image")
    m2 = InboundMessage(external_thread_id="ig-1", sender_id="lead9", text="🖼 media",
                        occurred_at=_NOW, external_id="m2",
                        media_url="http://cdn/b.jpg", media_kind="image")
    created = await IngestService(db_session, bid).ingest(cid, [m1, m2])
    assert len(created) == 2  # media excluded from content dedup


async def test_manager_media_flags_pending_and_stubs_asset(db_session) -> None:
    """A manager sending a photo/video from the IG app must get the SAME stub-and-backfill
    treatment as lead-sent media (see test_media_message_flags_pending_stubs_asset_and_
    records_seen) — it used to render as a bare '🖼 media' placeholder forever because only
    the lead-side ingest branch created the MediaAsset stub."""
    bid, cid, _, _ = await _world(db_session)
    msg = InboundMessage(
        external_thread_id="ig-1", sender_id="own1", text="🖼 media",
        occurred_at=_NOW, external_id="m1", direction="out",
        media_url="https://cdn/manager.jpg", media_kind="image")
    created = await IngestService(db_session, bid).ingest(cid, [msg])
    assert created[0].sent_by == "manager"
    assert created[0].media_pending is True
    asset = (await db_session.exec(select(MediaAsset))).first()
    assert asset is not None and asset.url == "https://cdn/manager.jpg"
    assert asset.kind == "image" and asset.data is None
    assert asset.message_id == created[0].id


async def test_manager_media_not_content_deduped(db_session) -> None:
    """Two distinct manager-sent photos share the placeholder text — the wide out-echo
    dedup window must not collapse them into one (mirrors test_media_not_content_deduped
    for the lead-side path)."""
    bid, cid, _, _ = await _world(db_session)
    m1 = InboundMessage(external_thread_id="ig-1", sender_id="own1", text="🖼 media",
                        occurred_at=_NOW, external_id="m1", direction="out",
                        media_url="http://cdn/a.jpg", media_kind="image")
    m2 = InboundMessage(external_thread_id="ig-1", sender_id="own1", text="🖼 media",
                        occurred_at=_NOW, external_id="m2", direction="out",
                        media_url="http://cdn/b.jpg", media_kind="image")
    created = await IngestService(db_session, bid).ingest(cid, [m1, m2])
    assert len(created) == 2  # media excluded from the out-echo content dedup too


async def test_dedup_by_real_id_vs_outbox_recorded_row(db_session) -> None:
    bid, cid, _, thread = await _world(db_session)
    db_session.add(Message(branch_id=bid, thread_id=thread.id, channel_id=cid,
                           external_id="real-77", direction="out", sent_by="agent",
                           text="бот уже записал", occurred_at=_NOW))
    await db_session.flush()
    created = await IngestService(db_session, bid).ingest(
        cid, [_in("бот уже записал", ext="real-77", direction="out")]
    )
    assert created == []
    n = len((await db_session.exec(
        select(Message).where(Message.external_id == "real-77"))).all())
    assert n == 1


async def test_echo_of_own_reply_mislabeled_as_in_is_dropped(db_session) -> None:
    """IG can echo our own outgoing message back on a poll before/instead of tagging it
    'out' (own-id resolution flakiness) — the text lands as a synthetic 'in' row that
    reads as the lead repeating our reply. Same text, same thread, moments after our real
    'out' row → dropped, not stored as a second lead message."""
    bid, cid, _, thread = await _world(db_session)
    db_session.add(Message(branch_id=bid, thread_id=thread.id, channel_id=cid,
                           external_id="real-99", direction="out", sent_by="manager",
                           text="Investasinya 13jt, cicilan 4x", occurred_at=_NOW))
    await db_session.flush()
    echoed = InboundMessage(
        external_thread_id="ig-1", sender_id="lead9",
        text="Investasinya 13jt, cicilan 4x", occurred_at=_NOW,
        external_id="synthetic-echo-1",  # no real item_id — the observed failure shape
    )
    created = await IngestService(db_session, bid).ingest(cid, [echoed])
    assert created == []
    n = len((await db_session.exec(
        select(Message).where(Message.thread_id == thread.id))).all())
    assert n == 1  # only the original 'out' row


async def test_out_for_unknown_thread_is_skipped(db_session) -> None:
    bid, cid, _, _ = await _world(db_session)
    orphan = InboundMessage(external_thread_id="ig-UNKNOWN", sender_id="own1",
                            text="x", occurred_at=_NOW, direction="out", external_id="z1")
    assert await IngestService(db_session, bid).ingest(cid, [orphan]) == []


async def test_adapter_maps_direction_and_item_id() -> None:
    adapter = InstagramAdapter(FakeIGTransport([{
        "thread_id": "ig-1", "item_id": "it-5", "direction": "out",
        "sender_id": "own1", "text": "hi", "timestamp": 1_700_000_000_000_000,
    }]), handle="acc")
    (msg,) = await adapter.fetch_inbound()
    assert msg.direction == "out" and msg.external_id == "it-5"
