"""Кого воронка считает своим — и кто отвечает на этот вопрос.

Раньше отвечала колонка `lead.manager_only`: она заменила два коррелированных подзапроса по
channel_thread внутри предиката воронки, потому что у `lead` и без того 573 тысячи
последовательных сканов на два миллиарда строк. Ответ был верный, но стоял РЯДОМ со стадией,
которая говорила другое — 265 из 302 таких лидов висели в стадии `new` с включённым ботом.

Теперь отвечает сама стадия. Первое сообщение с личного номера менеджера переводит лида в
MANAGER: она вне всех воронковых списков, глушит бота через domain.funnel.apply_stage, и —
в отличие от флага — менеджер может вернуть её обратно. Подзапросы при этом НЕ вернулись:
предикат воронки стал одним сравнением по индексу.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.adapters.db.models import Branch, Channel, Lead, StageEvent
from app.domain.enums import ChannelKind, Stage
from app.modules.leads.ingest import IngestService
from app.modules.leads.ops import move_lead
from app.ports.channel import InboundMessage


async def _branch(s) -> int:  # noqa: ANN001
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    return b.id


async def _channel(s, bid: int, *, manager_phone: bool) -> int:  # noqa: ANN001
    ch = Channel(branch_id=bid, kind=ChannelKind.WHATSAPP if manager_phone
                 else ChannelKind.INSTAGRAM, manager_phone=manager_phone)
    s.add(ch)
    await s.flush()
    return ch.id


def _msg(ext: str, mid: str) -> InboundMessage:
    return InboundMessage(external_thread_id=ext, sender_id="s", text="halo",
                          occurred_at=datetime.now(UTC).replace(tzinfo=None),
                          external_id=mid)


async def _leads(s) -> list:  # noqa: ANN001
    return (await s.execute(Lead.__table__.select())).mappings().all()


async def test_a_first_message_on_a_managers_number_hands_the_lead_over(db_session) -> None:  # noqa: ANN001
    """Стадия и тумблер бота ставятся вместе — порознь они и расходятся."""
    bid = await _branch(db_session)
    ch = await _channel(db_session, bid, manager_phone=True)

    await IngestService(db_session, bid).ingest(ch, [_msg("wa1", "m1")])

    lead = (await _leads(db_session))[0]
    assert lead["stage"] == Stage.MANAGER
    assert lead["agent_enabled"] is False


async def test_the_handover_is_journalled_with_the_stage_it_came_from(db_session) -> None:  # noqa: ANN001
    """Менеджер должен видеть, откуда лида забрали, иначе вернуть его некуда."""
    bid = await _branch(db_session)
    wa = await _channel(db_session, bid, manager_phone=True)
    lead = Lead(branch_id=bid, stage=Stage.PRESENTING)
    db_session.add(lead)
    await db_session.flush()
    from app.adapters.db.models import ChannelThread
    db_session.add(ChannelThread(lead_id=lead.id, channel_id=wa, external_thread_id="seed"))
    await db_session.flush()

    await IngestService(db_session, bid).ingest(wa, [_msg("wa_new", "m1")])

    events = (await db_session.execute(StageEvent.__table__.select())).mappings().all()
    handover = [e for e in events if e["to_stage"] == str(Stage.MANAGER)]
    assert len(handover) == 1
    assert handover[0]["actor"] == "manager_phone"  # свой actor: это не хендофф для CRM
    assert "manager" in handover[0]["reason"]


async def test_a_contact_who_wrote_to_us_stays_in_the_funnel(db_session) -> None:  # noqa: ANN001
    bid = await _branch(db_session)
    ch = await _channel(db_session, bid, manager_phone=False)

    await IngestService(db_session, bid).ingest(ch, [_msg("ig1", "m1")])

    lead = (await _leads(db_session))[0]
    assert lead["stage"] == Stage.NEW
    assert lead["agent_enabled"] is True


async def test_a_later_message_does_not_undo_the_managers_decision(db_session) -> None:  # noqa: ANN001
    """Ради этого правило и висит на ПЕРВОМ сообщении коннектора, а не на каждом. Менеджер
    закончил и вернул лида Степану — следующее сообщение на тот же номер не должно молча
    отменить это решение. Лид всё равно не остаётся без присмотра: отвечаем туда, где человек
    написал последним, а этот тред для ответа не выбирается никогда."""
    bid = await _branch(db_session)
    wa = await _channel(db_session, bid, manager_phone=True)
    svc = IngestService(db_session, bid)

    await svc.ingest(wa, [_msg("wa1", "m1")])
    lead_row = (await _leads(db_session))[0]
    lead = await db_session.get(Lead, lead_row["id"])
    await move_lead(db_session, lead, Stage.QUALIFYING.value, note="менеджер вернул")

    await svc.ingest(wa, [_msg("wa1", "m2")])

    lead = await db_session.get(Lead, lead_row["id"])
    assert lead.stage == Stage.QUALIFYING
    assert lead.agent_enabled is True


async def test_handing_back_re_arms_the_bot_and_clears_a_manual_mute(db_session) -> None:  # noqa: ANN001
    """Возврат в воронку — это и есть передача треда боту; оставленная заглушка Bot OFF
    сделала бы перевод выполненным на вид и молчащим на деле."""
    bid = await _branch(db_session)
    wa = await _channel(db_session, bid, manager_phone=True)
    await IngestService(db_session, bid).ingest(wa, [_msg("wa1", "m1")])
    lead = await db_session.get(Lead, (await _leads(db_session))[0]["id"])
    lead.agent_off_manual = True

    await move_lead(db_session, lead, Stage.NURTURING.value)

    assert lead.stage == Stage.NURTURING
    assert lead.agent_enabled is True
    assert lead.agent_off_manual is False


async def test_the_lead_is_dated_by_its_first_message_not_by_the_import(db_session) -> None:  # noqa: ANN001
    """Бэкфилл истории проставил 306 лидам час своего запуска, и каждый отчёт по дате
    прихода показал всплеск в день импорта вместо месяцев, которые переписка занимала."""
    bid = await _branch(db_session)
    ch = await _channel(db_session, bid, manager_phone=True)
    old = datetime(2026, 2, 2, 10, 0)

    await IngestService(db_session, bid).ingest(ch, [InboundMessage(
        external_thread_id="wa1", sender_id="s", text="halo",
        occurred_at=old, external_id="m1")])

    lead = (await _leads(db_session))[0]
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
    ch = await _channel(db_session, bid, manager_phone=False)
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
    ch = await _channel(db_session, bid, manager_phone=False)
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
