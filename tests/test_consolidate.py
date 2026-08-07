"""Один человек в двух местах: тред Степана и чат менеджера сшиваются по номеру.

Воронка обрывалась на телефоне. Степан брал номер, менеджер продолжал в WhatsApp, и всё
дальнейшее происходило там, куда мы не смотрели — купивший лид выглядел так же, как
пропавший.

Три решения, принятые владельцем, проверяются здесь: контакты менеджера не лиды, пока
номер не совпал; совпадение глушит бота; сшивка автоматическая.
"""
from __future__ import annotations

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead
from app.domain.enums import ChannelKind, Stage
from app.modules.leads import consolidate


async def _branch(s) -> int:  # noqa: ANN001
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    return b.id


async def _lead_on(s, bid: int, *, read_only: bool, phone: str | None,  # noqa: ANN001
                   stage: str = "qualifying", name: str | None = None) -> Lead:
    ch = Channel(branch_id=bid, kind=ChannelKind.WHATSAPP if read_only
                 else ChannelKind.INSTAGRAM, read_only=read_only)
    s.add(ch)
    await s.flush()
    lead = Lead(branch_id=bid, phone_e164=phone, stage=stage, display_name=name)
    s.add(lead)
    await s.flush()
    s.add(ChannelThread(lead_id=lead.id, channel_id=ch.id,
                        external_thread_id=f"t{lead.id}"))
    await s.flush()
    return lead


# ── сшивка ────────────────────────────────────────────────────────────────────


async def test_two_records_with_one_number_become_one(db_session) -> None:  # noqa: ANN001
    bid = await _branch(db_session)
    ig = await _lead_on(db_session, bid, read_only=False, phone="+628111")
    wa = await _lead_on(db_session, bid, read_only=True, phone="+628111",
                        stage=Stage.MANAGER, name="Valian")

    survivor = await consolidate.merge_by_phone(db_session, ig)

    assert survivor is not None and survivor.id == ig.id
    absorbed = await db_session.get(Lead, wa.id)
    assert absorbed.is_merged_into == ig.id


async def test_the_threads_follow_the_survivor(db_session) -> None:  # noqa: ANN001
    """Смысл сшивки — увидеть весь цикл в одном месте. Оставить переписку у поглощённой
    записи значит сделать ровно ту половину работы, которая не помогает."""
    bid = await _branch(db_session)
    ig = await _lead_on(db_session, bid, read_only=False, phone="+628111")
    wa = await _lead_on(db_session, bid, read_only=True, phone="+628111",
                        stage=Stage.MANAGER)

    await consolidate.merge_by_phone(db_session, ig)

    threads = await consolidate._threads_of(db_session, ig.id)  # noqa: SLF001
    assert len(threads) == 2
    assert await consolidate._threads_of(db_session, wa.id) == []  # noqa: SLF001


async def test_the_lead_stepan_worked_is_the_one_that_survives(db_session) -> None:  # noqa: ANN001
    """Не «который старше»: история менеджера обычно старше рекламы, породившей лида, и
    победа по возрасту перенесла бы живую запись воронки на контакт, которого в ней не было."""
    bid = await _branch(db_session)
    wa = await _lead_on(db_session, bid, read_only=True, phone="+628111",
                        stage=Stage.MANAGER)
    ig = await _lead_on(db_session, bid, read_only=False, phone="+628111")

    survivor = await consolidate.merge_by_phone(db_session, wa)

    assert survivor.id == ig.id


async def test_what_only_the_managers_copy_knew_is_kept(db_session) -> None:  # noqa: ANN001
    """У контакта менеджера часто единственное настоящее имя."""
    bid = await _branch(db_session)
    ig = await _lead_on(db_session, bid, read_only=False, phone="+628111", name=None)
    await _lead_on(db_session, bid, read_only=True, phone="+628111",
                   stage=Stage.MANAGER, name="Valian")

    survivor = await consolidate.merge_by_phone(db_session, ig)

    assert survivor.display_name == "Valian"


# ── совпадение глушит бота ────────────────────────────────────────────────────


async def test_a_match_hands_the_lead_to_the_human(db_session) -> None:  # noqa: ANN001
    """Менеджер уже в разговоре. Бот, продолжающий вести инстаграм, дал бы клиенту два
    параллельных диалога от одной школы, расходящихся в цене."""
    bid = await _branch(db_session)
    ig = await _lead_on(db_session, bid, read_only=False, phone="+628111")
    await _lead_on(db_session, bid, read_only=True, phone="+628111", stage=Stage.MANAGER)

    survivor = await consolidate.merge_by_phone(db_session, ig)

    assert survivor.stage == Stage.HANDED_OFF
    assert survivor.agent_enabled is False


# ── чего сшивка НЕ делает ─────────────────────────────────────────────────────


async def test_a_lead_without_a_number_merges_with_nobody(db_session) -> None:  # noqa: ANN001
    """Большинство чатов WhatsApp приходят под @lid и номера не несут вовсе. Пустой ключ
    не должен слить их всех в одного человека."""
    bid = await _branch(db_session)
    a = await _lead_on(db_session, bid, read_only=False, phone=None)
    await _lead_on(db_session, bid, read_only=True, phone=None, stage=Stage.MANAGER)

    assert await consolidate.merge_by_phone(db_session, a) is None


async def test_a_number_unique_to_one_lead_changes_nothing(db_session) -> None:  # noqa: ANN001
    bid = await _branch(db_session)
    a = await _lead_on(db_session, bid, read_only=False, phone="+628111")
    await _lead_on(db_session, bid, read_only=True, phone="+628222", stage=Stage.MANAGER)

    assert await consolidate.merge_by_phone(db_session, a) is None


async def test_the_same_number_in_another_branch_is_another_person(db_session) -> None:  # noqa: ANN001
    """Филиалы изолированы. Слияние через границу отдало бы чужому оператору переписку."""
    first, second = await _branch(db_session), await _branch(db_session)
    a = await _lead_on(db_session, first, read_only=False, phone="+628111")
    b = await _lead_on(db_session, second, read_only=True, phone="+628111",
                       stage=Stage.MANAGER)

    assert await consolidate.merge_by_phone(db_session, a) is None
    assert (await db_session.get(Lead, b.id)).is_merged_into is None


# ── подметание ────────────────────────────────────────────────────────────────


async def test_the_sweep_catches_numbers_that_arrived_before_this_code(db_session) -> None:  # noqa: ANN001
    """Телефоны появлялись и до сшивки — из CRM, из рук оператора, из старого ингеста."""
    bid = await _branch(db_session)
    await _lead_on(db_session, bid, read_only=False, phone="+628111")
    await _lead_on(db_session, bid, read_only=True, phone="+628111", stage=Stage.MANAGER)

    assert await consolidate.sweep(db_session, bid) == 1
    assert await consolidate.sweep(db_session, bid) == 0  # идемпотентно
