"""Строки для выключенного канала не должны вечно висеть в очереди.

30.07.2026 в админке постоянно светилось «4 сообщения ждут отправки». Все четыре стояли на
треде 2569, чей канал (официальный Meta) выключен. Отправщик такие каналы пропускает
сознательно — это фикс от 13.07, когда их строки, будучи самыми старыми, занимали каждый
слот батча и вся очередь выглядела замороженной. Но retire их никто не делал, поэтому они
сидели в `pending` неделями.

Цена не в самих строках, а в том, что счётчик перестаёт что-либо значить: за постоянной
четвёркой не видно, застряла ли там НАСТОЯЩАЯ отправка.
"""
from __future__ import annotations

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Outbox
from app.domain.enums import ChannelKind
from app.worker import wiring


async def _fixture(session, *, channel_active: bool) -> int:  # noqa: ANN001
    branch = Branch(name="T", lang="id")
    session.add(branch)
    await session.flush()
    channel = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM,
                      is_active=channel_active)
    session.add(channel)
    await session.flush()
    lead = Lead(branch_id=branch.id, stage="qualifying")
    session.add(lead)
    await session.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=channel.id,
                           external_thread_id="ig-1")
    session.add(thread)
    await session.flush()
    session.add(Outbox(branch_id=branch.id, thread_id=thread.id, text="halo",
                       source="agent"))
    await session.flush()
    return branch.id


async def test_a_row_on_a_switched_off_channel_is_retired(db_session) -> None:
    bid = await _fixture(db_session, channel_active=False)
    assert await wiring.sweep_undeliverable(db_session, bid) == 1
    row = (await db_session.execute(
        Outbox.__table__.select())).mappings().first()
    assert row["status"] == "skipped"
    assert "switched off" in row["error"]


async def test_a_row_on_a_live_channel_is_left_alone(db_session) -> None:
    bid = await _fixture(db_session, channel_active=True)
    assert await wiring.sweep_undeliverable(db_session, bid) == 0
    row = (await db_session.execute(
        Outbox.__table__.select())).mappings().first()
    assert row["status"] == "pending"


async def test_retiring_is_idempotent(db_session) -> None:
    """The sweep runs every send tick; it must not keep re-counting the same rows."""
    bid = await _fixture(db_session, channel_active=False)
    assert await wiring.sweep_undeliverable(db_session, bid) == 1
    assert await wiring.sweep_undeliverable(db_session, bid) == 0
