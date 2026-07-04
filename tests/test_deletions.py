"""IG unsend: revoke in IG first, delete locally only on success; last_out_at rewinds;
route requests unsend for outgoing and only local-deletes inbound."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import select

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
from app.domain.enums import ChannelKind
from app.modules.conversation.deletions import DeletionService

_NOW = datetime.now(UTC).replace(tzinfo=None)


class FakeRevoker:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[tuple[str, str]] = []

    async def revoke(self, external_thread_id: str, external_message_id: str) -> bool:
        self.calls.append((external_thread_id, external_message_id))
        return self.ok


async def _thread(s) -> tuple[int, int, int, ChannelThread]:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    s.add(ch)
    await s.flush()
    lead = Lead(branch_id=b.id)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1",
                           last_out_at=_NOW)
    s.add(thread)
    await s.flush()
    return b.id, ch.id, thread.id, thread


async def _msg(s, bid, cid, tid, *, ext, out=True, requested=True, minutes_ago=0) -> Message:
    m = Message(branch_id=bid, thread_id=tid, channel_id=cid, external_id=ext,
                direction="out" if out else "in", sent_by="agent" if out else "lead",
                text="hi", occurred_at=_NOW - timedelta(minutes=minutes_ago),
                delete_requested=requested)
    s.add(m)
    await s.flush()
    return m


async def test_successful_revoke_deletes_local(db_session) -> None:
    bid, cid, tid, _ = await _thread(db_session)
    await _msg(db_session, bid, cid, tid, ext="m1")
    rev = FakeRevoker(ok=True)
    assert await DeletionService(db_session, bid).process(cid, "ig-1", rev) == 1
    assert rev.calls == [("ig-1", "m1")]
    assert (await db_session.exec(select(Message))).first() is None


async def test_failed_revoke_keeps_message_and_flag(db_session) -> None:
    bid, cid, tid, _ = await _thread(db_session)
    await _msg(db_session, bid, cid, tid, ext="m1")
    assert await DeletionService(db_session, bid).process(cid, "ig-1", FakeRevoker(ok=False)) == 0
    msg = (await db_session.exec(select(Message))).first()
    assert msg is not None and msg.delete_requested is True  # stays for retry


async def test_deleting_last_out_rewinds_last_out_at(db_session) -> None:
    bid, cid, tid, thread = await _thread(db_session)
    await _msg(db_session, bid, cid, tid, ext="old", out=True, requested=False, minutes_ago=30)
    await _msg(db_session, bid, cid, tid, ext="new", out=True, requested=True, minutes_ago=1)
    await DeletionService(db_session, bid).process(cid, "ig-1", FakeRevoker(ok=True))
    refreshed = (await db_session.exec(
        select(ChannelThread).where(ChannelThread.id == tid))).first()
    assert refreshed.last_out_at is not None
    # rewound to the older remaining out-message, not the deleted newer one
    assert refreshed.last_out_at < _NOW - timedelta(minutes=20)


async def test_delete_local_rewinds_both_watermarks(db_session) -> None:
    """_delete_local rewinds BOTH last_out_at and last_in_at to the newest remaining
    message per direction — last_in_at drives the sidebar activity sort + reply window,
    and leaving it stale left the chat list in the wrong order after a delete."""
    bid, cid, tid, thread = await _thread(db_session)
    thread.last_in_at = _NOW
    await db_session.flush()
    await _msg(db_session, bid, cid, tid, ext="in-old", out=False, requested=False,
               minutes_ago=30)
    new_in = await _msg(db_session, bid, cid, tid, ext="in-new", out=False,
                        requested=False, minutes_ago=1)
    await DeletionService(db_session, bid)._delete_local(new_in)
    refreshed = (await db_session.exec(
        select(ChannelThread).where(ChannelThread.id == tid))).first()
    assert refreshed.last_in_at is not None
    assert refreshed.last_in_at < _NOW - timedelta(minutes=20)  # rewound to older inbound


async def test_inbound_and_unflagged_not_touched(db_session) -> None:
    bid, cid, tid, _ = await _thread(db_session)
    await _msg(db_session, bid, cid, tid, ext="in1", out=False, requested=True)
    await _msg(db_session, bid, cid, tid, ext="out-noflag", out=True, requested=False)
    rev = FakeRevoker(ok=True)
    assert await DeletionService(db_session, bid).process(cid, "ig-1", rev) == 0
    assert rev.calls == []
    assert len((await db_session.exec(select(Message))).all()) == 2
