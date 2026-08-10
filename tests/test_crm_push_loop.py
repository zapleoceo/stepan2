"""Отправка в CRM не должна отвечать на ответ CRM.

Замер 10.08.2026 по живой базе: 39 из первых 84 отправок хендоффа были открыты нашим же
стенд-дауном — CRM говорила «сделка выиграна» или «менеджер уже звонил», gate._stand_down
переводил лида в manager, и этот перевод удовлетворял условию окна отправки. 28 лидов с
закрытой сделкой уехали обратно как wait_call. Менеджеры описывали это как «Степан шлёт
одного и того же по кругу».

Петля замыкалась ровно на двух кронах: чтение каждые 10 минут, отправка раз в час на :35.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.adapters.db.models import (
    Branch,
    Channel,
    ChannelThread,
    CrmLeadState,
    Lead,
    StageEvent,
)
from app.domain.enums import ChannelKind, Stage
from app.modules.crm.push_mcp import fetch_leads_with_phone, fetch_unpushed_handoffs

NOW = datetime.now(UTC).replace(tzinfo=None)


async def _lead(s, bid: int, *, stage: Stage, phone: str) -> Lead:  # noqa: ANN001
    lead = Lead(branch_id=bid, stage=stage, phone_e164=phone, display_name="T")
    s.add(lead)
    await s.flush()
    ch = (await s.execute(Channel.__table__.select())).first()
    if ch is None:
        c = Channel(branch_id=bid, kind=ChannelKind.INSTAGRAM)
        s.add(c)
        await s.flush()
        ch_id = c.id
    else:
        ch_id = ch[0]
    s.add(ChannelThread(lead_id=lead.id, channel_id=ch_id,
                        external_thread_id=f"t{lead.id}"))
    await s.flush()
    return lead


async def _branch(s) -> int:  # noqa: ANN001
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    return b.id


async def _moved(s, lead: Lead, *, actor: str, reason: str, ago_h: int = 1) -> None:  # noqa: ANN001
    s.add(StageEvent(
        branch_id=lead.branch_id, lead_id=lead.id, thread_id=None,
        from_stage=str(Stage.QUALIFYING), to_stage=str(lead.stage),
        actor=actor, reason=reason, created_at=NOW - timedelta(hours=ago_h)))
    await s.flush()


async def test_a_stand_down_does_not_open_the_push_window(db_session) -> None:  # noqa: ANN001
    """Ровно та петля. CRM сказала «отойдите» — отвечать ей «перезвоните» нечем."""
    bid = await _branch(db_session)
    lead = await _lead(db_session, bid, stage=Stage.MANAGER, phone="+628111111111")
    await _moved(db_session, lead, actor="crm", reason="crm hold: deal won")

    assert await fetch_unpushed_handoffs(db_session, bid, now=NOW) == []


async def test_a_real_handoff_still_goes(db_session) -> None:  # noqa: ANN001
    """Обратная гарантия: живой перевод менеджером или моделью уезжает как раньше."""
    bid = await _branch(db_session)
    lead = await _lead(db_session, bid, stage=Stage.HANDED_OFF, phone="+628222222222")
    await _moved(db_session, lead, actor="bot", reason="needs_manager")

    got = await fetch_unpushed_handoffs(db_session, bid, now=NOW)
    assert [g.lead_id for g in got] == [lead.id]


async def test_a_won_deal_is_never_pushed(db_session) -> None:  # noqa: ANN001
    """Второй слой: сделка закрыта — говорить о ней нечего, кто бы ни двигал стадию."""
    bid = await _branch(db_session)
    lead = await _lead(db_session, bid, stage=Stage.READY, phone="+628333333333")
    await _moved(db_session, lead, actor="Citra", reason="manual")
    db_session.add(CrmLeadState(branch_id=bid, lead_id=lead.id, deal_won=True))
    await db_session.flush()

    assert await fetch_unpushed_handoffs(db_session, bid, now=NOW) == []


async def test_a_won_deal_is_not_in_the_warm_drain_either(db_session) -> None:  # noqa: ANN001
    """Тёплый дренаж берёт лидов вне закрытых стадий — выигранная сделка и там не нужна."""
    bid = await _branch(db_session)
    lead = await _lead(db_session, bid, stage=Stage.NURTURING, phone="+628444444444")
    assert [g.lead_id for g in await fetch_leads_with_phone(db_session, bid, now=NOW)] == [lead.id]

    db_session.add(CrmLeadState(branch_id=bid, lead_id=lead.id, deal_won=True))
    await db_session.flush()

    assert await fetch_leads_with_phone(db_session, bid, now=NOW) == []
