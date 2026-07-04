"""MCP lead-ops: phone matching, stage moves, close_deal hand-off, call_failed re-arm."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.adapters.db.models import Branch, Lead  # noqa: E402
from app.domain.enums import Stage  # noqa: E402
from app.modules.leads import ops  # noqa: E402


async def _seed(s, stage: Stage = Stage.PRESENTING, phone: str = "+62 812-3456") -> int:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    lead = Lead(branch_id=b.id, display_name="Budi", phone_e164=phone,
                stage=stage, agent_enabled=True)
    s.add(lead)
    await s.flush()
    return lead.id


async def test_find_lead_normalizes_phone(db_session) -> None:
    await _seed(db_session, phone="+62 812-3456")
    # query with different spacing / no dashes still matches the stored number
    lead = await ops.find_lead(db_session, "+628123456")
    assert lead is not None and lead.display_name == "Budi"
    assert await ops.find_lead(db_session, "+62999") is None


async def test_move_lead_sets_stage_and_journals(db_session) -> None:
    lid = await _seed(db_session, Stage.NEW)
    lead = await db_session.get(Lead, lid)
    res = await ops.move_lead(db_session, lead, "qualifying", note="from CRM")
    assert res.ok and res.from_stage == "new" and res.stage == "qualifying"
    assert lead.agent_enabled is True

    from sqlalchemy import func, select

    from app.adapters.db.models import StageEvent
    n = (await db_session.execute(
        select(func.count()).select_from(StageEvent).where(StageEvent.lead_id == lid)
    )).scalar()
    assert n == 1


async def test_move_lead_to_manager_disables_bot(db_session) -> None:
    lid = await _seed(db_session, Stage.PRESENTING)
    lead = await db_session.get(Lead, lid)
    await ops.move_lead(db_session, lead, "manager")
    assert lead.stage == Stage.MANAGER and lead.agent_enabled is False


async def test_move_lead_rejects_unknown_stage(db_session) -> None:
    lid = await _seed(db_session)
    lead = await db_session.get(Lead, lid)
    res = await ops.move_lead(db_session, lead, "not_a_stage")
    assert not res.ok and "unknown stage" in res.detail


async def test_close_deal_hands_off_and_stops_bot(db_session) -> None:
    lid = await _seed(db_session, Stage.READY)
    lead = await db_session.get(Lead, lid)
    res = await ops.close_deal(db_session, lead, note="paid full")
    assert res.ok and res.stage == "handed_off"
    assert lead.stage == Stage.HANDED_OFF and lead.agent_enabled is False


async def test_call_failed_rearms_and_pulls_back_from_handed_off(db_session) -> None:
    # a lead already handed off, no chat thread → funnel effects apply, no message queued
    lid = await _seed(db_session, Stage.HANDED_OFF)
    lead = await db_session.get(Lead, lid)
    lead.agent_enabled = False
    db_session.add(lead)
    await db_session.flush()

    res = await ops.call_failed(db_session, lead, note="no answer", llm=None)
    assert res.ok and res.from_stage == "handed_off" and res.stage == "qualifying"
    assert lead.agent_enabled is True          # bot re-armed to continue via chat
    assert res.message_queued is False         # no thread → nothing to send
