"""Meta's 24h messaging window: an AUTOMATED send into a closed window is skipped (not sent,
not a manager-facing failure); a MANAGER send still attempts; a non-Meta channel never gates
on the window (IG/WhatsApp have no hard 24h cutoff)."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from datetime import UTC, datetime, timedelta  # noqa: E402
from typing import Any  # noqa: E402

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Outbox  # noqa: E402
from app.domain.enums import ChannelKind  # noqa: E402
from app.modules.conversation.outbox import OutboxSender  # noqa: E402
from app.modules.settings.service import invalidate  # noqa: E402
from app.ports.channel import SendResult  # noqa: E402


class _Channel:
    def __init__(self, kind: ChannelKind) -> None:
        self.kind = kind
        self.sent: list[tuple[str, str]] = []

    async def fetch_inbound(self) -> list[Any]:
        return []

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        self.sent.append((external_thread_id, text))
        return SendResult(ok=True, external_message_id="x")

    async def session_status(self) -> Any:
        return None


async def _thread(s, kind: ChannelKind, *, window_closed: bool, source: str) -> tuple:
    b = Branch(name="B", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=kind)
    s.add(ch)
    lead = Lead(branch_id=b.id)
    s.add(lead)
    await s.flush()
    now = datetime.now(UTC).replace(tzinfo=None)
    win = now - timedelta(hours=1) if window_closed else now + timedelta(hours=1)
    t = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="x",
                      window_until=win)
    s.add(t)
    await s.flush()
    s.add(Outbox(branch_id=b.id, thread_id=t.id, text="hi", source=source,
                 status="pending", scheduled_at=now - timedelta(seconds=1)))
    await s.flush()
    invalidate(b.id)
    return b.id, t.id


async def test_meta_automated_send_skipped_when_window_closed(db_session) -> None:
    bid, tid = await _thread(db_session, ChannelKind.META_BUSINESS,
                             window_closed=True, source="agent")
    ch = _Channel(ChannelKind.META_BUSINESS)
    row = await OutboxSender(db_session, bid, ch).send_next(tid)
    assert ch.sent == []                     # doomed API call skipped
    assert row is not None and row.status == "skipped"
    assert row.error == "meta_window_closed"


async def test_meta_manager_send_attempts_even_when_window_closed(db_session) -> None:
    bid, tid = await _thread(db_session, ChannelKind.META_BUSINESS,
                             window_closed=True, source="manager")
    ch = _Channel(ChannelKind.META_BUSINESS)
    row = await OutboxSender(db_session, bid, ch).send_next(tid)
    assert len(ch.sent) == 1                 # manager send still attempts (7-day agent tag)
    assert row is not None and row.status == "sent"


async def test_meta_automated_send_goes_when_window_open(db_session) -> None:
    bid, tid = await _thread(db_session, ChannelKind.META_BUSINESS,
                             window_closed=False, source="agent")
    ch = _Channel(ChannelKind.META_BUSINESS)
    row = await OutboxSender(db_session, bid, ch).send_next(tid)
    assert len(ch.sent) == 1
    assert row is not None and row.status == "sent"


async def test_instagram_never_gates_on_window(db_session) -> None:
    bid, tid = await _thread(db_session, ChannelKind.INSTAGRAM,
                             window_closed=True, source="agent")
    ch = _Channel(ChannelKind.INSTAGRAM)
    row = await OutboxSender(db_session, bid, ch).send_next(tid)
    assert len(ch.sent) == 1                 # IG has no hard 24h cutoff — sends regardless
    assert row is not None and row.status == "sent"


async def test_fetch_pending_surfaces_failed_not_skipped(db_session) -> None:
    """A failed send must reach the manager's feed; a skipped (automated, expected) one must
    not — so the manager sees delivery problems without noise from window-gated auto-sends."""
    from app.api._query import fetch_pending
    b = Branch(name="B", lang="id")
    db_session.add(b)
    await db_session.flush()
    lead = Lead(branch_id=b.id)
    db_session.add(lead)
    ch = Channel(branch_id=b.id, kind=ChannelKind.META_BUSINESS)
    db_session.add(ch)
    await db_session.flush()
    t = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="x")
    db_session.add(t)
    await db_session.flush()
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(Outbox(branch_id=b.id, thread_id=t.id, text="queued", source="manager",
                          status="pending", scheduled_at=now))
    db_session.add(Outbox(branch_id=b.id, thread_id=t.id, text="lost", source="manager",
                          status="failed", error="meta_window_closed", scheduled_at=now))
    db_session.add(Outbox(branch_id=b.id, thread_id=t.id, text="auto", source="followup",
                          status="skipped", error="meta_window_closed", scheduled_at=now))
    await db_session.flush()

    rows = await fetch_pending(db_session, t.id)
    texts = {r[1]: r[5] for r in rows}  # text -> status
    assert texts == {"queued": "pending", "lost": "failed"}  # skipped auto-send stays hidden
    assert next(r[6] for r in rows if r[1] == "lost") == "meta_window_closed"  # error carried
