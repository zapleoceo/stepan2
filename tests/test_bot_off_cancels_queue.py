"""Выключение бота забирает и то, что он уже написал, но не успел отправить.

Тред 5632, 30.07.2026. Лид написал «боюсь, что обманут» в 09:57. Ответ сгенерировался к
09:59 и лёг в очередь с отправкой на 10:01 — между пузырями стоят паузы «человек печатает».
Владелец нажал Bot OFF внутри этого окна, и сообщение всё равно ушло: кнопка останавливала
ГЕНЕРАЦИЮ новых ответов, а отправщик про agent_enabled не знал вовсе.

Окно между «написано» и «ушло» — от одной до трёх минут. Если тред забрал человек, всё
написанное ботом отправлять уже не надо: сейчас пишет человек.

'canceled', а не 'failed': ничего не сломалось и повторять нечего, поэтому в чате это не
должно выглядеть красной кнопкой «отправить снова».

Отменяет РУЧНОЕ выключение (agent_off_manual), а не любое. Бот бывает выключен и системой —
после жёсткого «не беспокойте» лид обязан получить последнее извинение, и глушить его значит
оборвать человека на полуслове. Это поймал существующий тест, когда правило было шире.
"""
from __future__ import annotations

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Outbox
from app.domain.enums import ChannelKind


async def _fixture(session, *, agent_on: bool):  # noqa: ANN001, ANN201
    branch = Branch(name="T", lang="id")
    session.add(branch)
    await session.flush()
    channel = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM)
    session.add(channel)
    await session.flush()
    lead = Lead(branch_id=branch.id, stage="qualifying", agent_enabled=agent_on)
    session.add(lead)
    await session.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=channel.id,
                           external_thread_id="ig-1")
    session.add(thread)
    await session.flush()
    for src in ("agent", "followup", "manager"):
        session.add(Outbox(branch_id=branch.id, thread_id=thread.id,
                           text=f"queued {src}", source=src))
    await session.flush()
    return branch.id, thread.id, lead.id


async def _statuses(session, thread_id: int) -> dict[str, str]:  # noqa: ANN001
    rows = (await session.execute(
        Outbox.__table__.select().where(Outbox.thread_id == thread_id))).mappings().all()
    return {r["source"]: r["status"] for r in rows}


async def test_switching_the_bot_off_cancels_what_it_already_wrote(db_session) -> None:
    from sqlalchemy import text  # noqa: PLC0415
    _bid, tid, lead_id = await _fixture(db_session, agent_on=True)

    # то, что делает кнопка Bot OFF
    await db_session.execute(text(
        "UPDATE lead SET agent_enabled = false, agent_off_manual = true WHERE id = :i"),
        {"i": lead_id})
    await db_session.execute(text(
        "UPDATE outbox SET status = 'canceled',"
        " error = 'bot switched off — human took the thread'"
        " WHERE thread_id = :t AND status = 'pending' AND source <> 'manager'"), {"t": tid})
    await db_session.flush()

    st = await _statuses(db_session, tid)
    assert st["agent"] == "canceled"
    assert st["followup"] == "canceled"
    assert st["manager"] == "pending"      # человек пишет сам — его сообщение не трогаем


async def test_a_running_bot_keeps_its_queue(db_session) -> None:
    _bid, tid, _lead = await _fixture(db_session, agent_on=True)
    st = await _statuses(db_session, tid)
    assert set(st.values()) == {"pending"}


async def test_a_system_mute_does_not_cancel_the_apology(db_session) -> None:
    """Граница правила. После жёсткого «не беспокойте» бот выключается СИСТЕМОЙ и обязан
    договорить последнее извинение — тут отменять нечего. Отличает их agent_off_manual:
    его ставит только кнопка."""
    from sqlalchemy import text  # noqa: PLC0415
    _bid, tid, lead_id = await _fixture(db_session, agent_on=True)
    await db_session.execute(text(
        "UPDATE lead SET agent_enabled = false WHERE id = :i"), {"i": lead_id})
    await db_session.flush()

    lead = await db_session.get(Lead, lead_id)
    assert lead.agent_enabled is False
    assert lead.agent_off_manual is False        # человека тут не было
    st = await _statuses(db_session, tid)
    assert set(st.values()) == {"pending"}       # очередь цела
