"""CRM missed-call rescue: selection guard-rails — who gets picked up and who is left
alone. The nudge generation itself is call_failed's, covered in test_lead_ops."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from datetime import timedelta  # noqa: E402

from app.adapters.db.models import (  # noqa: E402
    AppSetting,
    Branch,
    Channel,
    ChannelThread,
    Lead,
    Outbox,
    StageEvent,
)
from app.domain.clock import utc_now  # noqa: E402
from app.domain.enums import ChannelKind, Stage  # noqa: E402
from app.modules.crm import rescue as rescue_mod  # noqa: E402
from app.modules.crm.rescue import CrmRescueService  # noqa: E402
from app.modules.leads.ops import LeadOpResult  # noqa: E402
from app.modules.settings.service import invalidate  # noqa: E402

_MCP_URL = "https://mcp.example/mcp/crm?token=x"


class _FakeReader:
    """Looks like CrmMcpReader: has list_missed_out_calls."""

    def __init__(self, missed: list[tuple[str, str]]) -> None:
        self._missed = missed

    async def list_missed_out_calls(self, url, days=3, max_pages=3):  # noqa: ANN001, ANN201
        return self._missed

    async def get_state(self, url, secret, phone):  # noqa: ANN001, ANN201
        return {"exists": True, "deal_won": False, "manager_called": False}


def _patch(monkeypatch, reader, calls: list) -> None:  # noqa: ANN001
    monkeypatch.setattr(rescue_mod, "build_crm_reader", lambda cfg: reader)
    async def fake_call_failed(session, lead, note, llm):  # noqa: ANN001, ANN202
        calls.append((lead.id, note))
        return LeadOpResult(ok=True, detail=note, lead_id=lead.id, message_queued=True)
    monkeypatch.setattr(rescue_mod.ops, "call_failed", fake_call_failed)


async def _branch(s, *, enabled: bool = True) -> int:
    b = Branch(name="T", lang="id", tz_offset_h=7)
    s.add(b)
    await s.flush()
    s.add(AppSetting(branch_id=b.id, key="crm_rescue_enabled",
                     value="true" if enabled else "false"))
    s.add(AppSetting(branch_id=b.id, key="agent_enabled_global", value="true"))
    import sqlalchemy as sa
    have = (await s.execute(sa.select(AppSetting).where(
        AppSetting.branch_id.is_(None), AppSetting.key == "crm_mcp_url"))).first()
    if have is None:  # platform row is shared across branches — insert once
        s.add(AppSetting(branch_id=None, key="crm_mcp_url", value=_MCP_URL))
    await s.flush()
    invalidate(b.id)
    return b.id


async def _lead(s, bid: int, phone: str, **kw) -> Lead:
    lead = Lead(branch_id=bid, phone_e164=phone, stage=kw.pop("stage", Stage.DORMANT),
                agent_enabled=kw.pop("agent_enabled", True), **kw)
    s.add(lead)
    await s.flush()
    ch = Channel(branch_id=bid, kind=ChannelKind.INSTAGRAM)
    s.add(ch)
    await s.flush()
    s.add(ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id=f"t{lead.id}"))
    await s.flush()
    return lead


def _in_work_hours(monkeypatch) -> None:  # noqa: ANN001
    class _Now:
        hour = 12
    monkeypatch.setattr(rescue_mod, "branch_now", lambda tz: _Now())


async def test_rescues_matched_lead_and_respects_cap(monkeypatch, db_session) -> None:
    bid = await _branch(db_session)
    l1 = await _lead(db_session, bid, "+62811")
    l2 = await _lead(db_session, bid, "+62812")
    l3 = await _lead(db_session, bid, "+62813")
    calls: list = []
    _patch(monkeypatch, _FakeReader([("62811", "2026-07-17T10:00:00+07:00"),
                                     ("62812", "2026-07-17T09:00:00+07:00"),
                                     ("62813", "2026-07-17T08:00:00+07:00")]), calls)
    _in_work_hours(monkeypatch)
    n = await CrmRescueService(db_session, bid, llm=None).run()
    assert n == 2 and len(calls) == 2                      # per-run cap
    assert {c[0] for c in calls} == {l1.id, l2.id}         # newest missed first
    assert all("CRM missed call" in c[1] for c in calls)
    assert l3.id not in {c[0] for c in calls}


async def test_skips_human_owned_active_and_cooldown(monkeypatch, db_session) -> None:
    bid = await _branch(db_session)
    await _lead(db_session, bid, "+62821", agent_enabled=False)   # manager owns → skipped
    active = await _lead(db_session, bid, "+62822")
    # Stepan messaged this thread an hour ago → already engaging
    thr = (await db_session.execute(
        __import__("sqlalchemy").select(ChannelThread)
        .where(ChannelThread.lead_id == active.id))).scalars().first()
    thr.last_out_at = utc_now() - timedelta(hours=1)
    db_session.add(thr)
    cooled = await _lead(db_session, bid, "+62823")
    db_session.add(StageEvent(branch_id=bid, lead_id=cooled.id, from_stage="dormant",
                              to_stage="qualifying", actor="mcp",
                              reason="call_failed: CRM missed call 2026-07-16"))
    await db_session.flush()
    calls: list = []
    _patch(monkeypatch, _FakeReader([("62821", "t"), ("62822", "t"), ("62823", "t")]), calls)
    _in_work_hours(monkeypatch)
    assert await CrmRescueService(db_session, bid, llm=None).run() == 0
    assert calls == []


async def test_pending_outbox_counts_as_active(monkeypatch, db_session) -> None:
    bid = await _branch(db_session)
    lead = await _lead(db_session, bid, "+62831")
    thr = (await db_session.execute(
        __import__("sqlalchemy").select(ChannelThread)
        .where(ChannelThread.lead_id == lead.id))).scalars().first()
    db_session.add(Outbox(branch_id=bid, thread_id=thr.id, text="hi", status="pending"))
    await db_session.flush()
    calls: list = []
    _patch(monkeypatch, _FakeReader([("62831", "t")]), calls)
    _in_work_hours(monkeypatch)
    assert await CrmRescueService(db_session, bid, llm=None).run() == 0


async def test_disabled_or_night_does_nothing(monkeypatch, db_session) -> None:
    bid = await _branch(db_session, enabled=False)
    await _lead(db_session, bid, "+62841")
    calls: list = []
    _patch(monkeypatch, _FakeReader([("62841", "t")]), calls)
    _in_work_hours(monkeypatch)
    assert await CrmRescueService(db_session, bid, llm=None).run() == 0   # flag off

    bid2 = await _branch(db_session)
    await _lead(db_session, bid2, "+62842")
    class _Night:
        hour = 3
    monkeypatch.setattr(rescue_mod, "branch_now", lambda tz: _Night())
    assert await CrmRescueService(db_session, bid2, llm=None).run() == 0  # night
    assert calls == []
