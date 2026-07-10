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
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import McpToken
from app.adapters.db.session import session_scope
from app.api._mcp_auth import current_mcp_authz, split_tokens
from app.config import settings
from app.domain.clock import utc_now

SCOPES = ("write", "read")
_TOUCH_THROTTLE = timedelta(seconds=60)  # don't rewrite last_used_at on every single call


@dataclass(frozen=True)
class McpAuthz:
    """Result of a successful MCP auth. `branch_id` is the ONE branch this token may touch;
    None = universal (every branch). Callers must enforce this scope on every lead access."""
    branch_id: int | None


class McpBranchForbidden(Exception):
    """A branch-scoped MCP token tried to reach a branch it isn't allowed to."""


# ── the ONE scope rule — both surfaces (HTTP routes + FastMCP tools) wrap these ─────
def scope_effective_branch(authz_branch: int | None, requested: int | None) -> int | None:
    """The branch a request may act on. Universal (authz_branch=None) → honour `requested`;
    branch-scoped → its own branch, rejecting a mismatching `requested`. The single source
    of truth for the scope rule; _routes_mcp and the FastMCP contextvar helpers both wrap
    it so the two surfaces can never drift."""
    if authz_branch is None:
        return requested
    if requested is not None and requested != authz_branch:
        raise McpBranchForbidden(
            "this token is limited to a single branch and cannot access another")
    return authz_branch


def scope_lead_allowed(authz_branch: int | None, lead_branch: int | None) -> bool:
    """A universal token may act on any lead; a branch-scoped token only on its own branch."""
    return authz_branch is None or lead_branch == authz_branch


def mcp_effective_branch(requested: int | None) -> int | None:
    """scope_effective_branch for the CURRENT MCP request (authz from the contextvar). Used
    by the mounted FastMCP tools, which can't pass the authz explicitly. Fail-CLOSED: if no
    authz is in context (a guarded tool should always have one), deny rather than default
    to universal — the isolation model's trust anchor."""
    authz = current_mcp_authz()
    if authz is None:
        raise McpBranchForbidden("no MCP authorization in context")
    return scope_effective_branch(authz.branch_id, requested)


def mcp_guard_lead_branch(lead: object) -> None:
    """Defence in depth for the FastMCP tools: a branch-scoped token must never act on a
    lead from another branch (a phone can resolve cross-branch). Fail-closed on missing authz."""
    authz = current_mcp_authz()
    if authz is None:
        raise McpBranchForbidden("no MCP authorization in context")
    if not scope_lead_allowed(authz.branch_id, getattr(lead, "branch_id", None)):
        raise McpBranchForbidden("no lead with that phone in this token's branch")


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class McpTokenService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, label: str, scope: str, branch_id: int | None = None,
    ) -> tuple[str, McpToken]:
        """Mint a token; returns the plaintext (shown once) and the stored row (hash only).
        branch_id=None → universal token (all branches); else scoped to that branch."""
        if scope not in SCOPES:
            raise ValueError(f"scope must be one of {SCOPES}")
        raw = secrets.token_hex(32)
        tok = McpToken(label=label.strip() or scope, scope=scope, branch_id=branch_id,
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

    async def match_active(self, token_hash: str, scope: str) -> McpToken | None:
        stmt = select(McpToken).where(
            McpToken.token_hash == token_hash, McpToken.scope == scope,
            McpToken.revoked_at.is_(None),  # type: ignore[union-attr]
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def touch(self, tok: McpToken) -> None:
        """Stamp last_used_at, throttled so a burst of calls isn't a burst of writes."""
        now = utc_now()
        if tok.last_used_at is None or now - tok.last_used_at >= _TOUCH_THROTTLE:
            tok.last_used_at = now
            self.session.add(tok)
            await self.session.flush()


async def authorize_mcp(token: str, scope: str) -> McpAuthz | None:
    """The token's authorization (its allowed branch scope) if valid for `scope`, else None.
    Env-secret tokens are universal (branch_id=None). A matching DB token also gets its
    last_used_at stamped (throttled) and carries its own branch scope."""
    if not token:
        return None
    env_secret = settings().mcp_secret if scope == "write" else settings().mcp_read_secret
    if any(hmac.compare_digest(token, t) for t in split_tokens(env_secret)):
        return McpAuthz(branch_id=None)  # env tokens are platform-wide (backward compat)
    async with session_scope() as session:
        svc = McpTokenService(session)
        tok = await svc.match_active(hash_token(token), scope)
        if tok is None:
            return None
        await svc.touch(tok)
        return McpAuthz(branch_id=tok.branch_id)
