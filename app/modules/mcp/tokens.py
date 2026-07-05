"""MCP bearer tokens: issue, list, revoke, and authorize.

Tokens are stored only as SHA-256 hashes (the plaintext is shown once at creation).
authorize_mcp() accepts a token valid for a scope from EITHER the env secret
(STEPAN2_MCP_SECRET / STEPAN2_MCP_READ_SECRET, comma-separated — the bootstrap path)
OR an active row in mcp_token. Env keeps already-issued tokens working; the DB is what
the MCP admin UI manages.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import McpToken
from app.adapters.db.session import session_scope
from app.api._mcp_auth import split_tokens
from app.config import settings
from app.domain.clock import utc_now

SCOPES = ("write", "read")


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class McpTokenService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, label: str, scope: str) -> tuple[str, McpToken]:
        """Mint a token; returns the plaintext (shown once) and the stored row (hash only)."""
        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {SCOPES}")
        raw = secrets.token_hex(32)
        tok = McpToken(label=label.strip() or scope, scope=scope,
                       token_hash=hash_token(raw), prefix=raw[:6])
        self.session.add(tok)
        await self.session.flush()
        return raw, tok

    async def list(self, scope: str | None = None) -> list[McpToken]:
        stmt = select(McpToken).order_by(McpToken.created_at.desc())
        if scope is not None:
            stmt = stmt.where(McpToken.scope == scope)
        return list((await self.session.execute(stmt)).scalars().all())

    async def revoke(self, token_id: int) -> bool:
        tok = await self.session.get(McpToken, token_id)
        if tok is None or tok.revoked_at is not None:
            return False
        tok.revoked_at = utc_now()
        self.session.add(tok)
        await self.session.flush()
        return True

    async def active_hashes(self, scope: str) -> set[str]:
        stmt = select(McpToken.token_hash).where(
            McpToken.scope == scope, McpToken.revoked_at.is_(None),  # type: ignore[union-attr]
        )
        return set((await self.session.execute(stmt)).scalars().all())


async def authorize_mcp(token: str, scope: str) -> bool:
    """True if `token` is valid for `scope` — via the env secret or an active DB token."""
    if not token:
        return False
    env_secret = settings().mcp_secret if scope == "write" else settings().mcp_read_secret
    if any(hmac.compare_digest(token, t) for t in split_tokens(env_secret)):
        return True
    presented = hash_token(token)
    async with session_scope() as session:
        hashes = await McpTokenService(session).active_hashes(scope)
    return any(hmac.compare_digest(presented, h) for h in hashes)
