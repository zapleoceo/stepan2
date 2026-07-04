"""Remote MCP server (Streamable HTTP) — for MCP clients that can only add a URL
(claude.ai web custom connectors, etc.), where a local stdio bridge isn't an option.

Mounted at /connector/mcp. Same four lead-funnel tools as the stdio bridge, but they
call the leads.ops domain layer in-process (no HTTP hop). Auth: the caller presents the
STEPAN2_MCP_SECRET either as `Authorization: Bearer <token>` or as `?key=<token>` in the
URL — the query form is a capability URL for web clients that can't set headers.

Stateless (stateless_http): every tool call is independent, so no server-side session
state and no sticky routing needed.
"""
from __future__ import annotations

import hmac

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.config import settings
from app.modules.leads import ops

# DNS-rebinding protection guards browser attacks on localhost dev servers by pinning
# the Host header; it's the wrong tool for a public server behind Cloudflare/nginx (the
# forwarded Host is unpredictable), and every request here already passes _TokenGuard.
mcp = FastMCP(
    "stepan", stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _fmt(res: ops.LeadOpResult) -> dict:
    return {
        "ok": res.ok, "detail": res.detail, "lead_id": res.lead_id, "name": res.name,
        "phone": res.phone, "from_stage": res.from_stage, "stage": res.stage,
        "message_queued": res.message_queued,
    }


@mcp.tool()
async def find_lead(phone: str, branch_id: int | None = None) -> dict:
    """Look up a lead by phone number (E.164, e.g. +6281234567890). Returns id, name,
    Instagram username, branch, current funnel stage and whether the bot is on. Call
    this first to confirm the lead exists before moving them."""
    async with session_scope() as session:
        lead = await ops.find_lead(session, phone, branch_id)
        if lead is None:
            return {"ok": False, "detail": f"no lead with phone {phone}"}
        return {
            "ok": True, "lead_id": lead.id, "name": lead.display_name,
            "phone": lead.phone_e164, "ig_username": lead.ig_username,
            "branch_id": lead.branch_id, "stage": str(lead.stage),
            "agent_enabled": lead.agent_enabled,
        }


@mcp.tool()
async def close_deal(phone: str, note: str | None = None) -> dict:
    """Mark a lead's deal as WON: hand the lead off (stage → handed_off) and stop the
    bot messaging them. `note` is journaled on the funnel event."""
    async with session_scope() as session:
        lead = await ops.find_lead(session, phone)
        if lead is None:
            return {"ok": False, "detail": f"no lead with phone {phone}"}
        return _fmt(await ops.close_deal(session, lead, note))


@mcp.tool()
async def call_failed(phone: str, note: str | None = None) -> dict:
    """Report that a phone call to the lead did NOT connect. Journals the failed call,
    re-enables the bot, and Stepan proactively messages the lead to continue in chat.
    A lead already handed off / dormant is pulled back to `qualifying`. `note` (e.g.
    'no answer', 'wrong number') is journaled."""
    async with session_scope() as session:
        lead = await ops.find_lead(session, phone)
        if lead is None:
            return {"ok": False, "detail": f"no lead with phone {phone}"}
        return _fmt(await ops.call_failed(session, lead, note, BrokerLLM()))


@mcp.tool()
async def move_lead(phone: str, stage: str, note: str | None = None) -> dict:
    """Move a lead to an explicit funnel stage. Valid: new, nurturing, qualifying,
    presenting, objection, ready, handed_off, dormant, manager. `manager` turns the bot
    off (human takeover); an active stage turns it back on. `note` is journaled."""
    async with session_scope() as session:
        lead = await ops.find_lead(session, phone)
        if lead is None:
            return {"ok": False, "detail": f"no lead with phone {phone}"}
        return _fmt(await ops.move_lead(session, lead, stage, note))


def _authorized(scope: Scope) -> bool:
    # MCP_SECRET may hold several comma-separated tokens (owner, partner, integration) —
    # each independently revocable.
    tokens = [t.strip() for t in settings().mcp_secret.split(",") if t.strip()]
    if not tokens:
        return False
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    token = headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token:  # capability-URL fallback: ?key=<token> for header-less web clients
        qs = scope.get("query_string", b"").decode()
        for part in qs.split("&"):
            if part.startswith("key="):
                token = part[4:]
                break
    return bool(token) and any(hmac.compare_digest(token, t) for t in tokens)


class _TokenGuard:
    """ASGI wrapper: reject unauthorized calls before they reach the MCP app."""

    def __init__(self, app) -> None:  # noqa: ANN001
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not _authorized(scope):
            await JSONResponse({"error": "unauthorized"}, status_code=401)(
                scope, receive, send)
            return
        await self._app(scope, receive, send)


def connector_app():  # noqa: ANN201
    """The token-guarded Streamable HTTP ASGI app to mount at /connector."""
    return _TokenGuard(mcp.streamable_http_app())
