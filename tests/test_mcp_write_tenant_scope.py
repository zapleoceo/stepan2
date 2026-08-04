"""The MCP write tools, driven for real, must not move a lead they cannot pin to one tenant.

Predicate-level scope tests already cover branch-scoped tokens. This covers the universal
token every live connector actually uses (mcp_token.branch_id is NULL for all sixteen,
including a read token held outside the company): it is allowed everywhere, which is
exactly why close_deal used to hand off whichever branch's lead the phone landed in.

Refusing only the COLLIDING phones was not enough — the common case is a number that exists
in exactly one branch, and it was the wrong branch. So a token that may reach every branch
has to name the one it means before it hands off, silences or DMs anybody.
"""
from __future__ import annotations

import inspect
import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import contextlib  # noqa: E402

from app.adapters.db.models import Branch, Lead  # noqa: E402
from app.api import _mcp_auth, mcp_remote  # noqa: E402
from app.domain.enums import Stage  # noqa: E402
from app.modules.mcp.tokens import McpAuthz, McpTokenService  # noqa: E402

_PHONE = "+628123456789"
# FastMCP wraps the coroutine in a tool object; .fn is the underlying function.
_close_deal = getattr(mcp_remote.close_deal, "fn", mcp_remote.close_deal)
_find_lead = getattr(mcp_remote.find_lead, "fn", mcp_remote.find_lead)


async def _branch(s, name: str) -> int:  # noqa: ANN001
    b = Branch(name=name, lang="id")
    s.add(b)
    await s.flush()
    return b.id


async def _lead(s, branch_id: int, name: str, phone: str = _PHONE) -> Lead:  # noqa: ANN001
    lead = Lead(branch_id=branch_id, display_name=name, phone_e164=phone,
                stage=Stage.PRESENTING, agent_enabled=True)
    s.add(lead)
    await s.flush()
    return lead


async def _two_tenants(s) -> tuple[Lead, Lead]:  # noqa: ANN001
    indo = await _lead(s, await _branch(s, "Indonesia"), "Budi")
    malay = await _lead(s, await _branch(s, "Malaysia"), "Aisyah")
    return indo, malay


def _pin_session(monkeypatch, session) -> None:  # noqa: ANN001
    @contextlib.asynccontextmanager
    async def _scope():  # noqa: ANN202
        yield session
    monkeypatch.setattr(mcp_remote, "session_scope", _scope)


async def _as_token(branch_id: int | None, coro_factory):  # noqa: ANN001, ANN202
    token = _mcp_auth._authz_var.set(McpAuthz(branch_id=branch_id))  # noqa: SLF001
    try:
        return await coro_factory()
    finally:
        _mcp_auth._authz_var.reset(token)


async def test_close_deal_refuses_a_universal_token_that_named_no_branch(
        db_session, monkeypatch) -> None:
    """The common case, and the one the first fix missed: the number is NOT ambiguous — it
    exists in exactly one branch, and that branch belongs to somebody else."""
    malay = await _lead(db_session, await _branch(db_session, "Malaysia"), "Aisyah")
    _pin_session(monkeypatch, db_session)
    res = await _as_token(None, lambda: _close_deal(phone=_PHONE))
    assert res["ok"] is False and "branch_id" in res["detail"]
    assert malay.stage == Stage.PRESENTING and malay.agent_enabled is True


async def test_close_deal_with_branch_id_touches_only_that_tenant(
        db_session, monkeypatch) -> None:
    """Positive control — the refusal above is a missing argument, not a blanket block."""
    indo, malay = await _two_tenants(db_session)
    _pin_session(monkeypatch, db_session)
    res = await _as_token(None, lambda: _close_deal(phone=_PHONE, branch_id=indo.branch_id))
    assert res["ok"] is True and res["lead_id"] == indo.id
    assert indo.stage == Stage.HANDED_OFF and indo.agent_enabled is False
    assert malay.stage == Stage.PRESENTING and malay.agent_enabled is True


async def test_a_branch_scoped_token_still_needs_no_branch_id(
        db_session, monkeypatch) -> None:
    """Positive control — a token that can only reach one branch has already named it."""
    indo, malay = await _two_tenants(db_session)
    _pin_session(monkeypatch, db_session)
    res = await _as_token(indo.branch_id, lambda: _close_deal(phone=_PHONE))
    assert res["ok"] is True and res["lead_id"] == indo.id
    assert malay.stage == Stage.PRESENTING


async def test_close_deal_refuses_a_phone_two_leads_of_that_branch_share(
        db_session, monkeypatch) -> None:
    """Naming the branch is not enough when the branch itself holds two matching rows."""
    bid = await _branch(db_session, "Indonesia")
    budi = await _lead(db_session, bid, "Budi")
    bagus = await _lead(db_session, bid, "Bagus", phone="+618123456789")
    _pin_session(monkeypatch, db_session)
    res = await _as_token(None, lambda: _close_deal(phone="08123456789", branch_id=bid))
    assert res["ok"] is False and len(res["candidates"]) == 2
    assert budi.stage == Stage.PRESENTING and bagus.stage == Stage.PRESENTING


async def test_reading_a_lead_needs_no_branch_id(db_session, monkeypatch) -> None:
    """Control on the blast radius: find_lead changes nothing, so it stays permissive —
    the requirement is on the calls that cannot be undone."""
    indo = await _lead(db_session, await _branch(db_session, "Indonesia"), "Budi")
    _pin_session(monkeypatch, db_session)
    res = await _as_token(None, lambda: _find_lead(phone=_PHONE))
    assert res["ok"] is True and res["lead_id"] == indo.id


async def test_find_lead_reports_the_candidates_instead_of_one_tenant(
        db_session, monkeypatch) -> None:
    await _two_tenants(db_session)
    _pin_session(monkeypatch, db_session)
    res = await _as_token(None, lambda: _find_lead(phone=_PHONE))
    assert res["ok"] is False and len(res["candidates"]) == 2
    assert "branch_id" in res["detail"]


def test_minting_a_token_cannot_default_to_universal() -> None:
    """All sixteen live tokens are universal because branch_id defaulted to None. None is
    still legal — it just has to be written down at the call site."""
    assert (inspect.signature(McpTokenService.create).parameters["branch_id"].default
            is inspect.Parameter.empty)
