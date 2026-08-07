"""Кого воронка считает своим — вопрос, на который отвечает запись, а не каждое чтение.

Раньше это были два коррелированных подзапроса по channel_thread внутри предиката. У `lead`
и без того 573 тысячи последовательных сканов на два миллиарда строк; добавлять к этому
подзапрос на строку в счётчиках, инбоксе и каждом отчёте — движение не в ту сторону.
Ответ меняется только когда к лиду цепляют тред.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.adapters.db.models import Branch, Channel, Lead
from app.domain.enums import ChannelKind
from app.modules.leads.ingest import IngestService
from app.ports.channel import InboundMessage


async def _branch(s) -> int:  # noqa: ANN001
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    return b.id


async def _channel(s, bid: int, *, read_only: bool) -> int:  # noqa: ANN001
    ch = Channel(branch_id=bid, kind=ChannelKind.WHATSAPP if read_only
                 else ChannelKind.INSTAGRAM, read_only=read_only)
    s.add(ch)
    await s.flush()
    return ch.id


def _msg(ext: str, mid: str) -> InboundMessage:
    return InboundMessage(external_thread_id=ext, sender_id="s", text="halo",
                          occurred_at=datetime.now(UTC).replace(tzinfo=None),
                          external_id=mid)


async def test_a_contact_first_seen_on_a_managers_number_is_not_ours(db_session) -> None:  # noqa: ANN001
    bid = await _branch(db_session)
    ch = await _channel(db_session, bid, read_only=True)

    await IngestService(db_session, bid).ingest(ch, [_msg("wa1", "m1")])

    lead = (await db_session.execute(Lead.__table__.select())).mappings().first()
    assert lead["manager_only"] is True


async def test_a_contact_who_wrote_to_us_is_ours(db_session) -> None:  # noqa: ANN001
    bid = await _branch(db_session)
    ch = await _channel(db_session, bid, read_only=False)

    await IngestService(db_session, bid).ingest(ch, [_msg("ig1", "m1")])

    lead = (await db_session.execute(Lead.__table__.select())).mappings().first()
    assert lead["manager_only"] is False


async def test_writing_to_us_later_makes_a_manager_contact_ours(db_session) -> None:  # noqa: ANN001
    """Человек, однажды написавший НАМ, наш — чем бы он ни был до этого. Обратного перехода
    нет: иначе один чат у менеджера выкидывал бы живого лида из воронки."""
    bid = await _branch(db_session)
    wa = await _channel(db_session, bid, read_only=True)
    ig = await _channel(db_session, bid, read_only=False)
    svc = IngestService(db_session, bid)

    await svc.ingest(wa, [_msg("wa1", "m1")])
    lead_id = (await db_session.execute(Lead.__table__.select())).mappings().first()["id"]
    # тот же человек пишет нам в инстаграм — отдельный тред, тот же лид только после сшивки,
    # поэтому здесь достаточно, что НОВЫЙ лид попадает в воронку
    await svc.ingest(ig, [_msg("ig1", "m2")])

    rows = (await db_session.execute(Lead.__table__.select())).mappings().all()
    assert any(r["manager_only"] is False for r in rows)
    assert any(r["id"] == lead_id for r in rows)


async def test_the_lead_is_dated_by_its_first_message_not_by_the_import(db_session) -> None:  # noqa: ANN001
    """Бэкфилл истории проставил 306 лидам час своего запуска, и каждый отчёт по дате
    прихода показал всплеск в день импорта вместо месяцев, которые переписка занимала."""
    bid = await _branch(db_session)
    ch = await _channel(db_session, bid, read_only=True)
    old = datetime(2026, 2, 2, 10, 0)

    await IngestService(db_session, bid).ingest(ch, [InboundMessage(
        external_thread_id="wa1", sender_id="s", text="halo",
        occurred_at=old, external_id="m1")])

    lead = (await db_session.execute(Lead.__table__.select())).mappings().first()
    assert lead["created_at"] == old


# ── чьё это сообщение на самом деле ───────────────────────────────────────────


async def test_our_own_line_polled_back_is_not_credited_to_the_manager(db_session) -> None:  # noqa: ANN001
    """Опрос не различает исходящие: со стороны Instagram они одинаковы. «Не наше» выводилось
    из наших же сохранённых отправок — и это выводится неверно ровно тогда, когда сломался
    учёт. Строка очереди 21101 была сгенерирована ботом, дошла до Instagram и осталась
    `canceled`; эха не нашлось, и ответ Степана про цену записался на менеджера (тред 6074).

    Очередь знает лучше таблицы сообщений: в ней лежит то, что мы СОЧИНИЛИ, чем бы строка
    ни кончилась."""
    from app.adapters.db.models import ChannelThread, Message, Outbox

    bid = await _branch(db_session)
    ch = await _channel(db_session, bid, read_only=False)
    lead = Lead(branch_id=bid)
    db_session.add(lead)
    await db_session.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch, external_thread_id="ig1")
    db_session.add(th)
    await db_session.flush()
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(Outbox(branch_id=bid, thread_id=th.id, text="Untuk biaya Rp 13.000.000",
                          source="agent", status="canceled", scheduled_at=now))
    await db_session.flush()

    await IngestService(db_session, bid).ingest(ch, [InboundMessage(
        external_thread_id="ig1", sender_id="us", text="Untuk biaya Rp 13.000.000",
        occurred_at=now, direction="out", external_id="ig-m1")])

    rows = (await db_session.execute(Message.__table__.select())).mappings().all()
    assert [r["sent_by"] for r in rows] == ["agent"]


async def test_a_line_the_manager_typed_is_still_theirs(db_session) -> None:  # noqa: ANN001
    """Гарантия в обратную сторону: живой человек, ответивший из приложения, не должен
    исчезнуть под меткой бота — иначе не видно, где работал человек."""
    from app.adapters.db.models import ChannelThread, Message

    bid = await _branch(db_session)
    ch = await _channel(db_session, bid, read_only=False)
    lead = Lead(branch_id=bid)
    db_session.add(lead)
    await db_session.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch, external_thread_id="ig1")
    db_session.add(th)
    await db_session.flush()

    await IngestService(db_session, bid).ingest(ch, [InboundMessage(
        external_thread_id="ig1", sender_id="us", text="halo, ini Maya ya",
        occurred_at=datetime.now(UTC).replace(tzinfo=None),
        direction="out", external_id="ig-m2")])

    rows = (await db_session.execute(Message.__table__.select())).mappings().all()
    assert [r["sent_by"] for r in rows] == ["manager"]
