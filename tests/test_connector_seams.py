"""The shared paths must read the ConnectorSpec, not a hardcoded kind.

Each test flips a flag on a REGISTERED spec and asserts the shared code follows it. That is
the whole claim of this refactor: if any of these still branched on `ChannelKind.X` the flip
would change nothing and the test would fail — which is exactly how they were written.
"""
from __future__ import annotations

import dataclasses
import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from datetime import UTC, datetime, timedelta  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.adapters.db.models import (  # noqa: E402
    Branch,
    Channel,
    ChannelThread,
    Lead,
    Outbox,
    StageEvent,
)
from app.connectors.registry import REGISTRY  # noqa: E402
from app.connectors.spec import Capability, SendWindow  # noqa: E402
from app.domain.enums import ChannelKind  # noqa: E402
from app.modules.conversation.outbox import OutboxSender  # noqa: E402
from app.modules.settings.service import invalidate  # noqa: E402
from app.ports.channel import SendResult  # noqa: E402


class _Port:
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


def _respec(monkeypatch: pytest.MonkeyPatch, kind: ChannelKind, **changes: Any) -> None:
    """Replace one registered spec for the duration of a test."""
    monkeypatch.setitem(REGISTRY, kind, dataclasses.replace(REGISTRY[kind], **changes))


async def _queued_line(s, kind: ChannelKind) -> tuple[int, int]:  # noqa: ANN001
    b = Branch(name="B", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=kind)
    s.add(ch)
    lead = Lead(branch_id=b.id)
    s.add(lead)
    await s.flush()
    now = datetime.now(UTC).replace(tzinfo=None)
    thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="x",
                           window_until=now - timedelta(hours=1))  # window long closed
    s.add(thread)
    await s.flush()
    s.add(Outbox(branch_id=b.id, thread_id=thread.id, text="hi", source="agent",
                 status="pending", scheduled_at=now - timedelta(seconds=1)))
    await s.flush()
    invalidate(b.id)
    return b.id, thread.id


async def test_send_window_gate_follows_the_spec_flag_not_the_kind(
    db_session, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    """WhatsApp has no reply window today, so its queued line goes out over a closed one.
    Declare a window on it and the SHARED send path must start skipping — without any edit to
    outbox.py, which is the point.

    The two stored strings come from the spec as well. A gate generalised to every connector
    while still writing "meta_window_closed" would stamp Meta's name on a WhatsApp outbox row
    and into the dormancy reason an operator reads on the paused thread."""
    bid, tid = await _queued_line(db_session, ChannelKind.WHATSAPP)
    port = _Port(ChannelKind.WHATSAPP)
    assert (await OutboxSender(db_session, bid, port).send_next(tid)).status == "sent"
    assert len(port.sent) == 1

    bid2, tid2 = await _queued_line(db_session, ChannelKind.WHATSAPP)
    _respec(monkeypatch, ChannelKind.WHATSAPP, send_window=SendWindow(
        error_code="wa_window_closed", dormant_reason="WhatsApp window shut"))
    port2 = _Port(ChannelKind.WHATSAPP)
    row = await OutboxSender(db_session, bid2, port2).send_next(tid2)
    assert port2.sent == []
    assert row is not None and row.status == "skipped"
    assert row.error == "wa_window_closed"
    thread = await db_session.get(ChannelThread, tid2)
    parked = (await db_session.execute(
        select(StageEvent).where(StageEvent.thread_id == thread.id))).scalars().all()
    assert [e.reason for e in parked] == ["WhatsApp window shut"]


async def test_the_windowed_connectors_declare_it_and_keep_their_stored_codes() -> None:
    """Only connectors that really have a reply window say so, and each keeps calling a
    refused send exactly what it always called it.

    Two now, both for the same platform rule: Meta's 24h and WhatsApp's, the latter reached
    through the CRM sender. Every outbox row written since this gate existed carries these
    literals, and the inbox queries and the failed-send bubble match them, so they are stored
    values and not labels anyone may reword."""
    gated = {k for k, s in REGISTRY.items() if s.send_window is not None}
    assert gated == {ChannelKind.META_BUSINESS, ChannelKind.CRM_SENDER}

    window = REGISTRY[ChannelKind.META_BUSINESS].send_window
    assert window is not None
    assert window.error_code == "meta_window_closed"
    assert window.dormant_reason == "Meta 24h window closed — paused until lead writes"

    wa = REGISTRY[ChannelKind.CRM_SENDER].send_window
    assert wa is not None
    assert wa.error_code == "crm_wa_window_closed"


async def test_maintenance_crons_never_build_a_port_for_an_undeclared_capability(
    db_session, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    """WhatsApp declares no revoke. The port must not even be constructed — building one is a
    real network/credential operation, and the old hasattr check paid for it before throwing
    the result away."""
    from app.worker import main as worker_main

    built: list[int] = []

    async def _explode(session, channel):  # noqa: ANN001, ANN202
        built.append(channel.id or 0)
        raise AssertionError("port must not be built for an undeclared capability")

    monkeypatch.setattr(worker_main.wiring, "build_channel_port", _explode)

    b = Branch(name="B", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.WHATSAPP)
    db_session.add(ch)
    await db_session.flush()

    assert await worker_main._try_build_port(db_session, ch, Capability.REVOKE) is None
    assert built == []


async def test_comment_ingest_is_gated_on_the_comments_capability(
    db_session, monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    """Instagram is the only connector with a public comment surface. Take the capability away
    and the service must not touch the port, even though the port still has the methods."""
    from app.modules.comments.service import CommentService

    class _CommentPort:
        kind = ChannelKind.INSTAGRAM
        calls = 0

        async def fetch_comments(self, *, since=None):  # noqa: ANN001, ANN202
            _CommentPort.calls += 1
            return []

    b = Branch(name="B", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM, handle="x")
    db_session.add(ch)
    await db_session.flush()

    svc = CommentService(db_session, b.id, None, None, None)
    assert await svc.ingest(ch, _CommentPort()) == 0
    assert _CommentPort.calls == 1  # declared → the port IS asked

    _respec(monkeypatch, ChannelKind.INSTAGRAM, capabilities=frozenset())
    assert await svc.ingest(ch, _CommentPort()) == 0
    assert _CommentPort.calls == 1  # not declared → the port is not asked at all
