"""Отчёты считают ЛЮДЕЙ, а не переписки.

Пока у человека был один разговор, разницы не было. После объединения Instagram и WhatsApp
менеджера в одного лида — есть: таблица с заголовком «лидов с этого объявления» тихо
показывала бы, сколько чатов они открыли. У стадий было хуже: SUM(CASE stage=…) прибавляет
единицу на КАЖДЫЙ тред, поэтому лид в `ready` с двумя каналами считался за две продажи.
"""
from __future__ import annotations

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead
from app.api._query import fetch_ad_funnel, fetch_organic_funnel, fetch_stage_counts
from app.domain.enums import ChannelKind


async def _two_threads(s, *, ad_id: str | None, stage: str = "ready",  # noqa: ANN001
                       merged: bool = False) -> int:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ig = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    wa = Channel(branch_id=b.id, kind=ChannelKind.WHATSAPP, manager_phone=True)
    s.add(ig)
    s.add(wa)
    await s.flush()
    lead = Lead(branch_id=b.id, stage=stage)
    s.add(lead)
    await s.flush()
    other = None
    if merged:
        other = Lead(branch_id=b.id, stage=stage, is_merged_into=lead.id)
        s.add(other)
        await s.flush()
    s.add(ChannelThread(lead_id=lead.id, channel_id=ig.id, external_thread_id="a",
                        ad_id=ad_id))
    s.add(ChannelThread(lead_id=lead.id, channel_id=wa.id, external_thread_id="b",
                        ad_id=ad_id))
    if other is not None:
        s.add(ChannelThread(lead_id=other.id, channel_id=ig.id, external_thread_id="c",
                            ad_id=ad_id))
    await s.flush()
    return b.id


async def test_one_person_on_two_channels_is_one_lead_in_the_ad_funnel(db_session) -> None:  # noqa: ANN001
    bid = await _two_threads(db_session, ad_id="123")
    rows = await fetch_ad_funnel(db_session, [bid])
    assert len(rows) == 1
    assert rows[0].total == 1, "counted threads, not people"


async def test_a_two_channel_sale_is_one_sale(db_session) -> None:  # noqa: ANN001
    """Самая дорогая версия ошибки: продажи удваиваются, и цена лида выглядит вдвое лучше."""
    bid = await _two_threads(db_session, ad_id="123", stage="ready")
    rows = await fetch_ad_funnel(db_session, [bid])
    assert rows[0].won == 1


async def test_the_organic_funnel_counts_people_too(db_session) -> None:  # noqa: ANN001
    bid = await _two_threads(db_session, ad_id=None)
    total, _pipeline, won, *_ = await fetch_organic_funnel(db_session, [bid])
    assert total == 1
    assert won == 1


async def test_a_merged_record_is_not_a_second_person(db_session) -> None:  # noqa: ANN001
    """Поглощённая запись — тот же человек во второй раз. Её тред уехал к выжившему, но сама
    строка осталась, чтобы выданный id не повис."""
    bid = await _two_threads(db_session, ad_id="123", merged=True)
    rows = await fetch_ad_funnel(db_session, [bid])
    assert rows[0].total == 1


async def test_the_stage_counters_ignore_a_merged_record(db_session) -> None:  # noqa: ANN001
    # No branch filter: that path uses `= ANY(:bids)`, which is Postgres-only, and the test
    # schema is SQLite. The database here holds one branch, so the answer is the same.
    await _two_threads(db_session, ad_id="123", stage="qualifying", merged=True)
    counts = await fetch_stage_counts(db_session, None)
    assert counts.get("qualifying") == 1
