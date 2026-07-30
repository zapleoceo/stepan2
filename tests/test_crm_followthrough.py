"""Диспетчер: отработать то, что менеджер записал в CRM.

Один проход на все результаты, а не крон на каждый. Инициативу проявляем только там, где
политика этого требует — думает, следующий набор, отказ; на wait_call (74% контактов) первыми
не пишем никогда.

Идемпотентность по паре (лид, статус): пока менеджер не поставил НОВЫЙ результат, второй раз
не пишем. Иначе часовой крон превратил бы «разбери отказ» в ежечасный долбёж.
"""
from __future__ import annotations

import pytest

import app.modules.crm.rescue as rescue_mod
from app.adapters.db.models import (
    AppSetting,
    Branch,
    Channel,
    ChannelThread,
    CrmLeadState,
    Lead,
    StageEvent,
)
from app.domain.enums import ChannelKind
from app.modules.crm.rescue import CrmRescueService


@pytest.fixture(autouse=True)
def _any_hour(monkeypatch):  # noqa: ANN001, ANN201
    """Окно рабочих часов — настоящее поведение, но оно сделало бы тесты зависимыми от
    времени прогона: ночной CI видел бы ноль и зелёный там, где отбор сломан."""
    monkeypatch.setattr(rescue_mod, "_WORK_START_H", 0)
    monkeypatch.setattr(rescue_mod, "_WORK_END_H", 24)


class _LLM:
    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        return "{}", {}


class _Svc(CrmRescueService):
    """Подменяем генерацию сообщения: она уже покрыта своими тестами, здесь важен отбор."""

    def __init__(self, *a, **kw) -> None:  # noqa: ANN002, ANN003
        super().__init__(*a, **kw)
        self.acted: list[tuple[int, str]] = []


async def _fixture(session, *, status: str, hour_ok: bool = True) -> tuple[int, int]:  # noqa: ANN001
    branch = Branch(name="T", lang="id", tz_offset_h=0 if hour_ok else 0)
    session.add(branch)
    await session.flush()
    for k, v in {"crm_rescue_enabled": "true", "agent_enabled": "true"}.items():
        session.add(AppSetting(branch_id=branch.id, key=k, value=v))
    channel = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM)
    session.add(channel)
    await session.flush()
    lead = Lead(branch_id=branch.id, stage="presenting", phone_e164="+628123",
                agent_enabled=True)
    session.add(lead)
    await session.flush()
    session.add(ChannelThread(lead_id=lead.id, channel_id=channel.id,
                              external_thread_id="ig-1"))
    session.add(CrmLeadState(branch_id=branch.id, lead_id=lead.id, exists_in_crm=True,
                             status=status, verdict="proceed"))
    await session.flush()
    from app.modules.settings.service import invalidate  # noqa: PLC0415
    invalidate(branch.id)
    return branch.id, lead.id


async def test_a_status_that_needs_a_conversation_is_picked_up(db_session, monkeypatch) -> None:
    bid, lead_id = await _fixture(db_session, status="result_fail")
    seen: list[tuple[int, str, str]] = []

    async def _fake(session, lead, status, goal, llm):  # noqa: ANN001, ANN202
        seen.append((lead.id, status, goal))
        from app.modules.leads.ops import LeadOpResult  # noqa: PLC0415
        return LeadOpResult(ok=True, detail="", lead_id=lead.id, message_queued=True)

    from app.modules.leads import ops  # noqa: PLC0415
    monkeypatch.setattr(ops, "crm_followthrough", _fake)
    svc = _Svc(db_session, bid, _LLM())
    monkeypatch.setattr(svc, "_recently_messaged", lambda _l: _false())
    assert await svc.run_followthrough() == 1
    assert seen and seen[0][1] == "result_fail"
    assert "почему не подошло" in seen[0][2]


async def test_wait_call_is_never_initiated(db_session, monkeypatch) -> None:
    """74% контактов. Менеджер ждёт созвона — сами не пишем."""
    bid, _ = await _fixture(db_session, status="wait_call")
    svc = _Svc(db_session, bid, _LLM())
    monkeypatch.setattr(svc, "_recently_messaged", lambda _l: _false())
    assert await svc.run_followthrough() == 0


async def test_the_same_status_is_acted_on_only_once(db_session, monkeypatch) -> None:
    bid, lead_id = await _fixture(db_session, status="result_think")
    db_session.add(StageEvent(
        branch_id=bid, lead_id=lead_id, from_stage="presenting", to_stage="presenting",
        actor="crm", reason="crm_followthrough: result_think"))
    await db_session.flush()
    svc = _Svc(db_session, bid, _LLM())
    monkeypatch.setattr(svc, "_recently_messaged", lambda _l: _false())
    assert await svc.run_followthrough() == 0


async def test_a_blocked_lead_is_never_touched(db_session, monkeypatch) -> None:
    bid, lead_id = await _fixture(db_session, status="result_fail")
    lead = await db_session.get(Lead, lead_id)
    lead.is_blocked = True
    db_session.add(lead)
    await db_session.flush()
    svc = _Svc(db_session, bid, _LLM())
    monkeypatch.setattr(svc, "_recently_messaged", lambda _l: _false())
    assert await svc.run_followthrough() == 0


async def _false() -> bool:
    return False
