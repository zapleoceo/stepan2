"""MCP tokens are branch-scoped: a token limited to one branch can never touch another
branch's leads, on any of the three MCP surfaces. A universal (branch_id=None) token —
including every env-secret token — keeps full cross-branch access."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import contextlib  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from app.api import _mcp_auth  # noqa: E402
from app.api import _routes_mcp as routes  # noqa: E402
from app.modules.mcp.tokens import (  # noqa: E402
    McpAuthz,
    McpBranchForbidden,
    mcp_effective_branch,
    mcp_guard_lead_branch,
)

# ── route-level helper (_routes_mcp) ────────────────────────────────────────

def test_effective_branch_universal_token_honours_request() -> None:
    authz = McpAuthz(branch_id=None)
    assert routes._effective_branch(authz, None) is None      # all branches
    assert routes._effective_branch(authz, 7) == 7            # caller's choice honoured


def test_effective_branch_scoped_token_pins_and_rejects() -> None:
    authz = McpAuthz(branch_id=3)
    assert routes._effective_branch(authz, None) == 3         # omitted → the token's branch
    assert routes._effective_branch(authz, 3) == 3            # same branch ok
    with pytest.raises(routes.HTTPException) as exc:
        routes._effective_branch(authz, 9)                   # a different branch → 403
    assert exc.value.status_code == 403


def test_guard_lead_branch_blocks_cross_branch_lead() -> None:
    scoped = McpAuthz(branch_id=3)
    routes._guard_lead_branch(scoped, SimpleNamespace(branch_id=3))       # ok
    with pytest.raises(routes.HTTPException) as exc:
        routes._guard_lead_branch(scoped, SimpleNamespace(branch_id=4))   # cross-branch
    assert exc.value.status_code == 404
    # universal token never blocks
    routes._guard_lead_branch(McpAuthz(branch_id=None), SimpleNamespace(branch_id=99))


# ── FastMCP contextvar helper (mcp_remote / mcp_reader) ─────────────────────

@contextlib.contextmanager
def _authz(branch_id):  # noqa: ANN001, ANN202
    reset = _mcp_auth._authz_var.set(McpAuthz(branch_id=branch_id))
    try:
        yield
    finally:
        _mcp_auth._authz_var.reset(reset)


def test_mcp_effective_branch_contextvar_scoped() -> None:
    with _authz(5):
        assert mcp_effective_branch(None) == 5
        assert mcp_effective_branch(5) == 5
        with pytest.raises(McpBranchForbidden):
            mcp_effective_branch(6)


def test_mcp_effective_branch_contextvar_universal() -> None:
    with _authz(None):
        assert mcp_effective_branch(None) is None
        assert mcp_effective_branch(8) == 8


def test_mcp_guard_lead_branch_contextvar() -> None:
    with _authz(5):
        mcp_guard_lead_branch(SimpleNamespace(branch_id=5))
        with pytest.raises(McpBranchForbidden):
            mcp_guard_lead_branch(SimpleNamespace(branch_id=7))
