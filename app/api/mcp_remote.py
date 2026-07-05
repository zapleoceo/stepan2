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

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.api._mcp_auth import token_guard
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


@mcp.tool()
async def sim_say(branch_id: int, session_key: str, text: str) -> dict:
    """Talk to Stepan AS A LEAD and get his sales reply — the REAL reply engine (same
    hybrid fast/smart routing leads hit), NOT the coach. Use to test his answers against
    the knowledge base with real or invented lead messages.

    branch_id is REQUIRED — Stepan is branch-scoped (KB, products, persona, language differ
    per branch). session_key names an isolated conversation (reuse it across turns to keep
    context; pick a new one to start a fresh scenario). Fully sandboxed: nothing is sent to
    Instagram and no real lead is touched.

    Returns Stepan's reply plus what the engine decided: funnel stage, product, captured
    needs (jobs/pains/gains), ready/needs_manager flags, and the LLM cost/model meta."""
    from app.modules.conversation.sim import SimService  # noqa: PLC0415
    async with session_scope() as session:
        return await SimService(session, BrokerLLM()).say(branch_id, session_key, text)


@mcp.tool()
async def sim_reset(branch_id: int, session_key: str) -> dict:
    """Wipe a sim conversation so the next sim_say starts fresh (clears its messages and
    resets the sandbox lead's needs/stage). Only affects the sandbox, never real leads."""
    from app.modules.conversation.sim import SimService  # noqa: PLC0415
    async with session_scope() as session:
        return await SimService(session, BrokerLLM()).reset(branch_id, session_key)


def connector_app():  # noqa: ANN201
    """The token-guarded Streamable HTTP ASGI app to mount at /connector. Accepts write
    tokens from the env secret or the mcp_token table (UI-managed)."""
    from app.modules.mcp.tokens import authorize_mcp  # noqa: PLC0415 (avoid import cycle)
    return token_guard(mcp.streamable_http_app(), lambda t: authorize_mcp(t, "write"))
