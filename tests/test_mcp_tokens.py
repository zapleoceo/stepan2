"""MCP token issuing/revocation + authorize_mcp (env secret and DB token, per scope)."""
from __future__ import annotations

import contextlib
import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.config import settings  # noqa: E402
from app.modules.mcp import tokens as tok  # noqa: E402
from app.modules.mcp.tokens import McpTokenService, authorize_mcp, hash_token  # noqa: E402


async def test_create_stores_only_hash_and_prefix(db_session) -> None:
    raw, row = await McpTokenService(db_session).create("director", "read")
    assert row.scope == "read" and row.label == "director"
    assert row.token_hash == hash_token(raw) and raw != row.token_hash  # hash, not plaintext
    assert row.prefix == raw[:6] and row.revoked_at is None


async def test_match_active_excludes_revoked_and_other_scope(db_session) -> None:
    svc = McpTokenService(db_session)
    raw_r, _ = await svc.create("reviewer", "read")
    raw_w, _ = await svc.create("partner", "write")
    revoked_raw, revoked = await svc.create("old", "read")
    await svc.revoke(revoked.id)

    assert (await svc.match_active(hash_token(raw_r), "read")) is not None
    assert (await svc.match_active(hash_token(revoked_raw), "read")) is None  # revoked
    assert (await svc.match_active(hash_token(raw_w), "read")) is None        # other scope


async def test_authorize_stamps_last_used(monkeypatch, db_session) -> None:
    monkeypatch.setattr(settings(), "mcp_read_secret", "")
    _patch_scope(monkeypatch, db_session)
    raw, row = await McpTokenService(db_session).create("director", "read")
    assert row.last_used_at is None
    await authorize_mcp(raw, "read")
    assert row.last_used_at is not None   # stamped on use


async def test_revoke_is_idempotent(db_session) -> None:
    svc = McpTokenService(db_session)
    _, row = await svc.create("x", "write")
    assert await svc.revoke(row.id) is True
    assert await svc.revoke(row.id) is False   # already revoked
    assert await svc.revoke(999999) is False   # unknown id


async def test_authorize_accepts_env_secret_by_scope(monkeypatch, db_session) -> None:
    monkeypatch.setattr(settings(), "mcp_secret", "envwrite,other")
    monkeypatch.setattr(settings(), "mcp_read_secret", "envread")
    # DB path must be reachable but empty here → env decides
    _patch_scope(monkeypatch, db_session)
    envw = await authorize_mcp("envwrite", "write")
    assert envw is not None and envw.branch_id is None       # env tokens are universal
    assert (await authorize_mcp("envread", "read")) is not None
    assert await authorize_mcp("envread", "write") is None    # right token, wrong scope
    assert await authorize_mcp("nope", "write") is None
    assert await authorize_mcp("", "read") is None


async def test_authorize_accepts_active_db_token(monkeypatch, db_session) -> None:
    monkeypatch.setattr(settings(), "mcp_secret", "")
    monkeypatch.setattr(settings(), "mcp_read_secret", "")
    _patch_scope(monkeypatch, db_session)
    raw, row = await McpTokenService(db_session).create("director", "read")
    authz = await authorize_mcp(raw, "read")
    assert authz is not None and authz.branch_id is None      # universal token
    assert await authorize_mcp(raw, "write") is None          # read token can't do write
    await McpTokenService(db_session).revoke(row.id)
    assert await authorize_mcp(raw, "read") is None           # revoked → denied


async def test_authorize_carries_branch_scope(monkeypatch, db_session) -> None:
    """A branch-scoped token authorizes but reports its branch; universal reports None."""
    monkeypatch.setattr(settings(), "mcp_secret", "")
    monkeypatch.setattr(settings(), "mcp_read_secret", "")
    _patch_scope(monkeypatch, db_session)
    raw, _ = await McpTokenService(db_session).create("branch2", "write", branch_id=2)
    authz = await authorize_mcp(raw, "write")
    assert authz is not None and authz.branch_id == 2


def _patch_scope(monkeypatch, session) -> None:  # noqa: ANN001
    """authorize_mcp opens its own session_scope() (a different engine than the test's
    in-memory DB) — point it at the test session so the DB path sees created tokens."""
    @contextlib.asynccontextmanager
    async def _fake():
        yield session
    monkeypatch.setattr(tok, "session_scope", _fake)
