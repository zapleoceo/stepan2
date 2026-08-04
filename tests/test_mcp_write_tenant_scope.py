"""The MCP write tools, driven for real, must not move a lead they cannot pin to one tenant.

Predicate-level scope tests already cover branch-scoped tokens. This covers the universal
token every live connector actually uses (mcp_token.branch_id is NULL for all of them): it
is allowed everywhere, which is exactly why close_deal used to hand off whichever branch's
lead the phone scan reached first.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import contextlib  # noqa: E402

from app.adapters.db.models import Branch, Lead  # noqa: E402
from app.api import _mcp_auth, mcp_remote  # noqa: E402
from app.domain.enums import Stage  # noqa: E402
from app.modules.mcp.tokens import McpAuthz  # noqa: E402

_PHONE = "+628123456789"
# FastMCP wraps the coroutine in a tool object; .fn is the underlying function.
_close_deal = getattr(mcp_remote.close_deal, "fn", mcp_remote.close_deal)
_find_lead = getattr(mcp_remote.find_lead, "fn", mcp_remote.find_lead)


async def _two_tenants(s) -> tuple[Lead, Lead]:
    out = []
    for name in ("Indonesia", "Malaysia"):
        b = Branch(name=name, lang="id")
        s.add(b)
        await s.flush()
        lead = Lead(branch_id=b.id, display_name=name, phone_e164=_PHONE,
                    stage=Stage.PRESENTING, agent_enabled=True)
        s.add(lead)
        await s.flush()
        out.append(lead)
    return out[0], out[1]


def _pin_session(monkeypatch, session) -> None:  # noqa: ANN001
    @contextlib.asynccontextmanager
    async def _scope():  # noqa: ANN202
        yield session
    monkeypatch.setattr(mcp_remote, "session_scope", _scope)


async def _as_universal_token(coro_factory):  # noqa: ANN001, ANN202
    token = _mcp_auth._authz_var.set(McpAuthz(branch_id=None))  # noqa: SLF001
    try:
        return await coro_factory()
    finally:
        _mcp_auth._authz_var.reset(token)


async def test_close_deal_refuses_a_phone_two_tenants_share(db_session, monkeypatch) -> None:
    indo, malay = await _two_tenants(db_session)
    _pin_session(monkeypatch, db_session)
    res = await _as_universal_token(lambda: _close_deal(phone=_PHONE))
    assert res["ok"] is False
    assert {c["branch_id"] for c in res["candidates"]} == {indo.branch_id, malay.branch_id}
    assert indo.stage == Stage.PRESENTING and malay.stage == Stage.PRESENTING
    assert indo.agent_enabled and malay.agent_enabled


async def test_close_deal_with_branch_id_touches_only_that_tenant(
        db_session, monkeypatch) -> None:
    """Positive control — the refusal above is a real disambiguation, not a blanket block."""
    indo, malay = await _two_tenants(db_session)
    _pin_session(monkeypatch, db_session)
    res = await _as_universal_token(
        lambda: _close_deal(phone=_PHONE, branch_id=indo.branch_id))
    assert res["ok"] is True and res["lead_id"] == indo.id
    assert indo.stage == Stage.HANDED_OFF and indo.agent_enabled is False
    assert malay.stage == Stage.PRESENTING and malay.agent_enabled is True


async def test_find_lead_reports_the_candidates_instead_of_one_tenant(
        db_session, monkeypatch) -> None:
    indo, malay = await _two_tenants(db_session)
    _pin_session(monkeypatch, db_session)
    res = await _as_universal_token(lambda: _find_lead(phone=_PHONE))
    assert res["ok"] is False and len(res["candidates"]) == 2
    assert "branch_id" in res["detail"]
