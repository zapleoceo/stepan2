"""CRM-over-MCP read path: state derivation from real payload shapes, reader selection,
fail-open, and the gate holding on MCP-sourced state."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from datetime import UTC, datetime, timedelta  # noqa: E402

from app.adapters.crm import CrmReader  # noqa: E402
from app.adapters.crm_mcp import CrmMcpReader  # noqa: E402
from app.adapters.db.models import AppSetting, Branch, Lead  # noqa: E402
from app.domain.enums import Stage  # noqa: E402
from app.modules.crm.gate import CrmGate, build_crm_reader, crm_read_url  # noqa: E402
from app.modules.settings.service import BranchSettings, invalidate  # noqa: E402

_MCP_URL = "https://mcp.example/mcp/crm?token=x"


def _cfg(**kw) -> BranchSettings:
    base = dict(
        agent_enabled=True, hourly_cap=0, daily_cap=0, quiet_start=0, quiet_end=0,
        reply_delay_min_s=0, reply_delay_max_s=0, tz_offset_h=7, tg_group_id="",
        followup_enabled=False, followup_schedule_h=[], daily_budget_usd=0.0,
        crm_enabled=False,
        crm_webhook_url="", meta_pixel_id="", meta_capi_token="",
    )
    return BranchSettings(**base, **kw)


# ─── source selection ───────────────────────────────────────────────────────────

def test_reader_factory_prefers_rest_then_mcp() -> None:
    assert isinstance(build_crm_reader(_cfg(crm_state_url="https://rest.example")), CrmReader)
    assert isinstance(build_crm_reader(_cfg(crm_mcp_url=_MCP_URL)), CrmMcpReader)
    # REST wins when both are set (explicit branch contract beats inherited platform MCP)
    assert isinstance(
        build_crm_reader(_cfg(crm_state_url="https://rest.example", crm_mcp_url=_MCP_URL)),
        CrmReader)


def test_crm_read_url_falls_back_to_mcp() -> None:
    assert crm_read_url(_cfg(crm_mcp_url=_MCP_URL)) == _MCP_URL
    assert crm_read_url(_cfg(crm_state_url="https://r.example", crm_mcp_url=_MCP_URL)) \
        == "https://r.example"
    assert crm_read_url(_cfg()) == ""


# ─── state derivation (real history payload shapes) ─────────────────────────────

def _call_row(hours_ago: float, answered: bool) -> dict:
    at = datetime.now(UTC) - timedelta(hours=hours_ago)
    return {"typeName": "out-call", "no_answer": "0" if answered else "1",
            "date_time": at.isoformat()}


def test_derive_contract_means_deal_won() -> None:
    st = CrmMcpReader("jakarta")._derive(1, [
        {"typeName": "contract", "date_time": "2026-07-14T11:53:35+07:00"},
        _call_row(500, answered=True),
    ])
    assert st["exists"] and st["deal_won"] is True


def test_derive_recent_answered_call_holds_but_old_one_does_not() -> None:
    recent = CrmMcpReader("jakarta")._derive(1, [_call_row(5, answered=True)])
    assert recent["manager_called"] is True         # inside the 72h hold window
    old = CrmMcpReader("jakarta")._derive(1, [_call_row(200, answered=True)])
    assert old["manager_called"] is False           # gone cold → Stepan may re-engage
    missed = CrmMcpReader("jakarta")._derive(1, [_call_row(5, answered=False)])
    assert missed["manager_called"] is False        # a no-answer call never holds


async def test_get_state_fails_open_on_transport_error(monkeypatch) -> None:
    reader = CrmMcpReader("jakarta")
    async def boom(url, phone):  # noqa: ANN001, ANN202
        raise RuntimeError("mcp down")
    monkeypatch.setattr(reader, "_fetch", boom)
    assert await reader.get_state(_MCP_URL, "", "+62812") is None


# ─── gate integration on an MCP-sourced state ───────────────────────────────────

class _FakeMcpReader:
    def __init__(self, state: dict | None) -> None:
        self._state = state
    async def get_state(self, url, secret, phone):  # noqa: ANN001, ANN201
        return self._state


async def _branch_with_mcp(s) -> int:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    s.add(AppSetting(branch_id=b.id, key="crm_read_enabled", value="true"))
    # platform-level row (branch_id NULL) — the branch inherits it, like prod
    s.add(AppSetting(branch_id=None, key="crm_mcp_url", value=_MCP_URL))
    await s.flush()
    invalidate(b.id)
    return b.id


async def test_gate_holds_on_mcp_deal_won(db_session) -> None:
    bid = await _branch_with_mcp(db_session)
    lead = Lead(branch_id=bid, phone_e164="+62812", stage=Stage.PRESENTING,
                agent_enabled=True)
    db_session.add(lead)
    await db_session.flush()
    state = {"exists": True, "crm_id": 138348, "deal_won": True, "manager_called": False}
    ok, reason = await CrmGate(db_session, bid, _FakeMcpReader(state)).allow_send(lead, "agent")
    assert ok is False and "deal won" in reason
    assert lead.agent_enabled is False and lead.stage == Stage.MANAGER


async def test_gate_allows_on_mcp_clean_state(db_session) -> None:
    bid = await _branch_with_mcp(db_session)
    lead = Lead(branch_id=bid, phone_e164="+62813", stage=Stage.QUALIFYING,
                agent_enabled=True)
    db_session.add(lead)
    await db_session.flush()
    state = {"exists": True, "crm_id": 1, "deal_won": False, "manager_called": False}
    ok, _ = await CrmGate(db_session, bid, _FakeMcpReader(state)).allow_send(lead, "agent")
    assert ok is True and lead.agent_enabled is True
