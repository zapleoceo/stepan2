"""Каналы, привязанные только для чтения, не должны отправлять НИЧЕГО.

Номера менеджеров цепляются как связанное устройство ради одного: увидеть, куда уходит
лид после того, как Степан взял телефон. На том конце живой человек, который ведёт этот
же чат со своего смартфона. Строка, ушедшая отсюда, придёт клиенту так, будто её напечатал
менеджер — и никакой лог этого уже не отменит.

Поэтому запрет стоит двумя слоями: отправщик не доходит до сети, а адаптер отказывает,
даже если его позвали напрямую. Один слой — это договорённость, два — свойство системы.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.adapters.channels.whatsapp import WhatsAppAdapter
from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Outbox
from app.domain.enums import ChannelKind
from app.modules.conversation.outbox import OutboxSender
from app.modules.settings.service import invalidate
from app.ports.channel import READ_ONLY_ERROR, SendResult

# ── слой 1: адаптер ───────────────────────────────────────────────────────────


class _Transport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def fetch_messages(self) -> list[dict[str, Any]]:
        return []

    async def send_message(self, remote_jid: str, text: str) -> dict[str, Any]:
        self.sent.append((remote_jid, text))
        return {"key": {"id": "wa-1"}}

    async def connection_state(self) -> str:
        return "open"


async def test_a_read_only_adapter_refuses_without_touching_the_wire() -> None:
    t = _Transport()
    result = await WhatsAppAdapter(t, instance="i", read_only=True).send_text("62811@s", "hi")

    assert result.ok is False
    assert result.error == READ_ONLY_ERROR
    assert t.sent == []  # отказ ДО сети, а не разбор ответа после неё


async def test_the_same_adapter_still_sends_when_the_flag_is_off() -> None:
    """Флаг — свойство экземпляра: номер для фолоапа и номер менеджера ходят через один
    и тот же адаптер. Если бы запрет висел на классе, включить отправку позже было бы
    правкой кода, а не галкой."""
    t = _Transport()
    result = await WhatsAppAdapter(t, instance="i", read_only=False).send_text("62811@s", "hi")

    assert result.ok is True
    assert t.sent == [("62811@s", "hi")]


async def test_read_only_defaults_to_off() -> None:
    """Существующий номер для фолоапа не должен онеметь от одного лишь появления флага."""
    assert WhatsAppAdapter(_Transport(), instance="i").read_only is False


# ── слой 2: отправщик ─────────────────────────────────────────────────────────


class _FakeChannel:
    kind = ChannelKind.WHATSAPP

    def __init__(self, *, read_only: bool) -> None:
        self.read_only = read_only
        self.sent: list[tuple[str, str]] = []

    async def fetch_inbound(self) -> list[Any]:
        return []

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        self.sent.append((external_thread_id, text))
        return SendResult(ok=True, external_message_id="wa-1")

    async def session_status(self) -> Any:
        return None


async def _queued(s, *, source: str = "agent") -> tuple[int, int]:  # noqa: ANN001
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    channel = Channel(branch_id=branch.id, kind=ChannelKind.WHATSAPP)
    s.add(channel)
    await s.flush()
    lead = Lead(branch_id=branch.id)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(
        lead_id=lead.id, channel_id=channel.id, external_thread_id="62811@s",
    )
    s.add(thread)
    await s.flush()
    now = datetime.now(UTC).replace(tzinfo=None)
    s.add(Outbox(
        branch_id=branch.id, thread_id=thread.id, text="halo", source=source,
        status="pending", scheduled_at=now - timedelta(seconds=5),
    ))
    await s.flush()
    invalidate(branch.id)
    return branch.id, thread.id


async def test_the_sender_retires_the_line_instead_of_delivering_it(db_session) -> None:
    """`skipped`, а не `failed`: это не сбой, это канал, которому писать не положено.
    И не `pending` — иначе строка вернётся на следующем тике и будет висеть вечно."""
    bid, tid = await _queued(db_session)
    channel = _FakeChannel(read_only=True)

    row = await OutboxSender(db_session, bid, channel).send_next(tid)

    assert channel.sent == []
    assert row is not None
    assert row.status == "skipped"
    assert row.error == READ_ONLY_ERROR


@pytest.mark.parametrize("source", ["agent", "followup", "manager"])
async def test_no_source_gets_through_a_read_only_channel(db_session, source: str) -> None:
    """У кнопки менеджера есть обход почти всех гейтов — окна, капов, тихих часов. Здесь
    обхода нет: менеджер пишет с собственного телефона, а не нашими руками."""
    bid, tid = await _queued(db_session, source=source)
    channel = _FakeChannel(read_only=True)

    await OutboxSender(db_session, bid, channel).send_next(tid)

    assert channel.sent == []


async def test_a_writable_channel_is_untouched(db_session) -> None:
    bid, tid = await _queued(db_session)
    channel = _FakeChannel(read_only=False)

    row = await OutboxSender(db_session, bid, channel).send_next(tid)

    assert row is not None and row.status == "sent"
    assert channel.sent == [("62811@s", "halo")]
