"""Заблокированный лид не уезжает в CRM ни одним из путей.

Инвариант простой и заявлен владельцем прямо: если лид заблокирован, Степан игнорирует его
целиком — ни ответов, ни фолоу-апов, ни реактивации, ни уведомлений в Telegram. Уведомления
закрыли 28.07 в единой точке AlertService; про CRM не вспомнили, и оба сгона фильтровали
только стадию и телефон.

Живая цена на 30.07.2026: из 29 заблокированных филиала 1 трое имели телефон, и ДВОЕ уже
уехали в CRM. Менеджер получил задачу перезвонить человеку, которого мы сами пометили спамом.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, StageEvent
from app.domain.enums import ChannelKind
from app.modules.crm.push_mcp import (
    _log_window_drops,
    fetch_leads_with_phone,
    fetch_unpushed_handoffs,
)

_NOW = datetime.now(UTC).replace(tzinfo=None)


async def _lead(session, bid: int, cid: int, *, stage: str, blocked: bool) -> int:  # noqa: ANN001
    lead = Lead(branch_id=bid, stage=stage, phone_e164="+6281234567890",
                is_blocked=blocked)
    session.add(lead)
    await session.flush()
    session.add(ChannelThread(lead_id=lead.id, channel_id=cid,
                              external_thread_id=f"t{lead.id}"))
    session.add(StageEvent(
        branch_id=bid, lead_id=lead.id, from_stage="qualifying", to_stage=stage,
        actor="bot", created_at=_NOW - timedelta(hours=1)))
    await session.flush()
    return lead.id


async def _fixture(session):  # noqa: ANN001, ANN201
    branch = Branch(name="T", lang="id")
    session.add(branch)
    await session.flush()
    channel = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM)
    session.add(channel)
    await session.flush()
    return branch.id, channel.id


async def test_warm_sweep_skips_a_blocked_lead(db_session) -> None:
    bid, cid = await _fixture(db_session)
    await _lead(db_session, bid, cid, stage="qualifying", blocked=True)
    assert await fetch_leads_with_phone(db_session, bid) == []


async def test_handoff_sweep_skips_a_blocked_lead(db_session) -> None:
    bid, cid = await _fixture(db_session)
    await _lead(db_session, bid, cid, stage="manager", blocked=True)
    assert await fetch_unpushed_handoffs(db_session, bid, now=_NOW) == []


async def test_a_blocked_lead_is_not_counted_as_work_nobody_will_do(db_session) -> None:
    """He is not lost — he is ignored on purpose. Counting him would inflate the warning
    the counter exists to make trustworthy."""
    bid, cid = await _fixture(db_session)
    await _lead(db_session, bid, cid, stage="manager", blocked=True)
    # эскалация вне окна → иначе он попал бы в обычную очередь, а не в счётчик потерь
    await db_session.execute(StageEvent.__table__.update().values(
        created_at=_NOW - timedelta(days=30)))
    await db_session.flush()
    assert await _log_window_drops(db_session, bid, _NOW) == 0


@pytest.mark.parametrize("stage", ["qualifying", "manager"])
async def test_an_unblocked_lead_still_goes(db_session, stage: str) -> None:
    """The guard must not swallow everyone — the normal path is unchanged."""
    bid, cid = await _fixture(db_session)
    await _lead(db_session, bid, cid, stage=stage, blocked=False)
    warm = await fetch_leads_with_phone(db_session, bid)
    hand = await fetch_unpushed_handoffs(db_session, bid, now=_NOW)
    assert len(warm) + len(hand) == 1
