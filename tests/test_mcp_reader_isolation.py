"""Reader-MCP tenant isolation, driven through the ACTUAL tool functions (not just the
predicates). get_chat/analyze_chat resolve a thread's branch and MUST feed it to
mcp_effective_branch(), so a branch-3 reader token addressing a branch-4 thread_id is
rejected fail-closed. A refactor that dropped that call (or passed None) would let a scoped
token read any branch's dialog and still pass the predicate-level tests — this catches it."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.api import _mcp_auth, mcp_reader  # noqa: E402
from app.modules.mcp.tokens import McpAuthz, McpBranchForbidden  # noqa: E402

# FastMCP wraps the coroutine in a tool object; .fn is the underlying function (fallback to
# the object itself if a version keeps it directly callable).
_get_chat = getattr(mcp_reader.get_chat, "fn", mcp_reader.get_chat)
_analyze_chat = getattr(mcp_reader.analyze_chat, "fn", mcp_reader.analyze_chat)


async def _run_as_branch(scoped_branch: int | None, coro_factory):  # noqa: ANN001
    token = _mcp_auth._authz_var.set(McpAuthz(branch_id=scoped_branch))
    try:
        return await coro_factory()
    finally:
        _mcp_auth._authz_var.reset(token)


async def test_get_chat_rejects_cross_branch_thread(monkeypatch) -> None:
    # A branch-4 thread resolved for a branch-3 token.
    async def _fake_resolve(_session, _phone, _thread_id):
        return (4321, 4, "Budi", "+628123")

    monkeypatch.setattr(mcp_reader, "_resolve_thread", _fake_resolve)
    with pytest.raises(McpBranchForbidden):
        await _run_as_branch(3, lambda: _get_chat(thread_id=4321))


async def test_analyze_chat_rejects_cross_branch_thread(monkeypatch) -> None:
    async def _fake_resolve(_session, _phone, _thread_id):
        return (4321, 4, "Budi", "+628123")

    monkeypatch.setattr(mcp_reader, "_resolve_thread", _fake_resolve)
    with pytest.raises(McpBranchForbidden):
        await _run_as_branch(3, lambda: _analyze_chat(thread_id=4321))


async def test_get_chat_without_authz_is_fail_closed(monkeypatch) -> None:
    # No authz in context at all (contextvar default None) → must raise, never read.
    async def _fake_resolve(_session, _phone, _thread_id):
        return (4321, 4, "Budi", "+628123")

    monkeypatch.setattr(mcp_reader, "_resolve_thread", _fake_resolve)
    with pytest.raises(McpBranchForbidden):
        await _get_chat(thread_id=4321)


async def test_same_branch_token_passes_the_guard(monkeypatch) -> None:
    # Positive control: a same-branch token clears mcp_effective_branch (proves the guard is
    # not a blanket deny). Stop right after the guard so we don't need a seeded message table.
    async def _fake_resolve(_session, _phone, _thread_id):
        return (77, 3, "Ani", "+628999")

    passed = {"branch": None}

    def _spy_effective(branch_id):  # noqa: ANN001, ANN202
        passed["branch"] = branch_id
        raise _StopAfterGuard

    monkeypatch.setattr(mcp_reader, "_resolve_thread", _fake_resolve)
    monkeypatch.setattr(mcp_reader, "mcp_effective_branch", _spy_effective)
    with pytest.raises(_StopAfterGuard):
        await _run_as_branch(3, lambda: _get_chat(thread_id=77))
    assert passed["branch"] == 3  # the RESOLVED thread branch is what gets guarded


class _StopAfterGuard(Exception):
    pass
