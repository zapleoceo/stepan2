"""HTTP surface for the MCP connector — the thin layer the stdio MCP server calls.

Every route is gated by a Bearer token (STEPAN2_MCP_SECRET); the session-cookie gate
skips /mcp/* (see _auth._PUBLIC_PREFIXES) so external callers never need a UI login.
Leads are addressed by phone (E.164). Routes stay thin: auth → resolve lead → call
the leads.ops domain service → return JSON.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.modules.conversation.sim import SimService
from app.modules.leads import ops
from app.modules.mcp.tokens import authorize_mcp

router = APIRouter(prefix="/mcp", tags=["mcp"])


async def _auth(authorization: str | None) -> None:
    """Accept a write-scope token from the env secret or the mcp_token table."""
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not await authorize_mcp(token, "write"):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


class _PhoneReq(BaseModel):
    phone: str
    branch_id: int | None = None
    note: str | None = None


class _MoveReq(_PhoneReq):
    stage: str


class _SimSayReq(BaseModel):
    branch_id: int
    session_key: str
    message: str


def _op_response(res: ops.LeadOpResult) -> dict:
    if not res.ok:
        raise HTTPException(status_code=400, detail=res.detail)
    return {
        "ok": True, "lead_id": res.lead_id, "name": res.name, "phone": res.phone,
        "from_stage": res.from_stage, "stage": res.stage,
        "message_queued": res.message_queued, "detail": res.detail,
    }


@router.get("/find_lead")
async def find_lead(
    phone: str, branch_id: int | None = None,
    authorization: str | None = Header(default=None),
) -> dict:
    await _auth(authorization)
    async with session_scope() as session:
        lead = await ops.find_lead(session, phone, branch_id)
        if lead is None:
            raise HTTPException(status_code=404, detail=f"no lead with phone {phone}")
        return {
            "ok": True, "lead_id": lead.id, "name": lead.display_name,
            "phone": lead.phone_e164, "ig_username": lead.ig_username,
            "branch_id": lead.branch_id, "stage": str(lead.stage),
            "agent_enabled": lead.agent_enabled,
        }


async def _resolve(session, req: _PhoneReq):  # noqa: ANN001, ANN202
    lead = await ops.find_lead(session, req.phone, req.branch_id)
    if lead is None:
        raise HTTPException(status_code=404, detail=f"no lead with phone {req.phone}")
    return lead


@router.post("/move_lead")
async def move_lead(req: _MoveReq, authorization: str | None = Header(default=None)) -> dict:
    await _auth(authorization)
    async with session_scope() as session:
        lead = await _resolve(session, req)
        return _op_response(await ops.move_lead(session, lead, req.stage, req.note))


@router.post("/close_deal")
async def close_deal(req: _PhoneReq, authorization: str | None = Header(default=None)) -> dict:
    await _auth(authorization)
    async with session_scope() as session:
        lead = await _resolve(session, req)
        return _op_response(await ops.close_deal(session, lead, req.note))


@router.post("/call_failed")
async def call_failed(req: _PhoneReq, authorization: str | None = Header(default=None)) -> dict:
    await _auth(authorization)
    async with session_scope() as session:
        lead = await _resolve(session, req)
        return _op_response(await ops.call_failed(session, lead, req.note, BrokerLLM()))


@router.post("/sim_say")
async def sim_say(
    req: _SimSayReq, authorization: str | None = Header(default=None),
) -> dict:
    """One turn of a sandboxed lead conversation through the real reply path (see
    SimService) — for testing Stepan's behavior against the KB without touching prod/IG.
    session_key scopes the sandbox thread; repeat calls with the same key continue it."""
    await _auth(authorization)
    async with session_scope() as session:
        return await SimService(session, BrokerLLM()).say(
            req.branch_id, req.session_key, req.message)
