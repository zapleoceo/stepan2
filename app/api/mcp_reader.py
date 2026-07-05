"""Read-only MCP server (Streamable HTTP) — lets a reviewer (e.g. a director) pull
Stepan's dialogs and grade them, WITHOUT any ability to change the funnel.

Mounted at /reader/mcp, gated by STEPAN2_MCP_READ_SECRET (separate, independently
revocable from the write connector's MCP_SECRET). Only three tools, all read-only:
list_chats, get_chat, analyze_chat. There are no write tools on this surface, so the
token physically cannot move a lead.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.api._mcp_auth import split_tokens, token_guard
from app.config import settings
from app.modules.leads import ops

_LANG_NAME = {"ru": "Russian", "en": "English", "id": "Indonesian"}

mcp = FastMCP(
    "stepan-reader", stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


async def _resolve_thread(session, phone: str | None, thread_id: int | None):  # noqa: ANN001, ANN202
    """A chat is addressed by phone (→ the lead's newest thread) or by thread_id directly."""
    if thread_id is not None:
        row = (await session.execute(
            text("SELECT ct.id, l.branch_id, l.display_name, l.phone_e164"
                 " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
                 " WHERE ct.id = :t"), {"t": thread_id})).first()
        return tuple(row) if row else None
    if phone:
        lead = await ops.find_lead(session, phone)
        if lead is None:
            return None
        row = (await session.execute(
            text("SELECT id FROM channel_thread WHERE lead_id = :l ORDER BY id DESC LIMIT 1"),
            {"l": lead.id})).first()
        if row is None:
            return None
        return row[0], lead.branch_id, lead.display_name, lead.phone_e164
    return None


@mcp.tool()
async def list_chats(limit: int = 30, branch_id: int | None = None,
                     stage: str | None = None) -> dict:
    """List recent lead chats (most recently active first). Optional filters: branch_id,
    stage (new/nurturing/qualifying/presenting/objection/ready/handed_off/dormant/manager).
    Returns each chat's thread_id, lead name, phone, stage, branch, last activity and
    message count — use thread_id or phone with get_chat/analyze_chat."""
    limit = max(1, min(limit, 100))
    # Build the WHERE dynamically: an untyped NULL bind in "(:p IS NULL OR col = :p)"
    # makes asyncpg fail with AmbiguousParameterError, so only bind filters that are set.
    conds: list[str] = []
    params: dict = {"limit": limit}
    if branch_id is not None:
        conds.append("l.branch_id = :branch")
        params["branch"] = branch_id
    if stage is not None:
        conds.append("l.stage = :stage")
        params["stage"] = stage
    where_sql = (" WHERE " + " AND ".join(conds)) if conds else ""
    q = text(
        "SELECT ct.id, l.display_name, l.phone_e164, l.stage, l.branch_id,"
        "       MAX(m.occurred_at) AS last_at, COUNT(m.id) AS n"
        " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
        " LEFT JOIN message m ON m.thread_id = ct.id AND m.text <> ''"
        f"{where_sql}"
        " GROUP BY ct.id, l.display_name, l.phone_e164, l.stage, l.branch_id"
        " HAVING COUNT(m.id) > 0"
        " ORDER BY last_at DESC"
        " LIMIT :limit"
    )
    async with session_scope() as session:
        rows = (await session.execute(q, params)).all()
    return {"chats": [
        {"thread_id": r[0], "name": r[1], "phone": r[2], "stage": r[3],
         "branch_id": r[4], "last_at": r[5].isoformat() if r[5] else None, "messages": r[6]}
        for r in rows]}


@mcp.tool()
async def get_chat(phone: str | None = None, thread_id: int | None = None,
                   limit: int = 200) -> dict:
    """Return the full dialog of one chat (oldest→newest) so you can read and analyze it.
    Address it by lead phone (E.164) OR thread_id. Each message has sender (lead|agent),
    timestamp and text (the lead's original language)."""
    limit = max(1, min(limit, 1000))
    async with session_scope() as session:
        resolved = await _resolve_thread(session, phone, thread_id)
        if resolved is None:
            return {"ok": False, "detail": "chat not found"}
        tid, branch_id, name, ph = resolved
        rows = (await session.execute(
            text("SELECT direction, text, occurred_at FROM message"
                 " WHERE thread_id = :t AND text <> ''"
                 " ORDER BY occurred_at, id LIMIT :lim"),
            {"t": tid, "lim": limit})).all()
    return {
        "ok": True, "thread_id": tid, "branch_id": branch_id, "name": name, "phone": ph,
        "messages": [
            {"from": "lead" if r[0] == "in" else "agent",
             "at": r[2].isoformat() if r[2] else None, "text": r[1]}
            for r in rows],
    }


@mcp.tool()
async def analyze_chat(phone: str | None = None, thread_id: int | None = None,
                       lang: str = "ru") -> dict:
    """Grade one chat against Stepan's knowledge base: what the bot did right, where it
    contradicted the KB or invented facts, gaps in the KB, and what to improve. Address
    by phone or thread_id. `lang` is the report language (ru|en|id)."""
    from app.modules.conversation.coach_service import analyze_chat as _analyze  # noqa: PLC0415
    async with session_scope() as session:
        resolved = await _resolve_thread(session, phone, thread_id)
        if resolved is None:
            return {"ok": False, "detail": "chat not found"}
        tid, branch_id, _name, _ph = resolved
        report = await _analyze(session, branch_id, tid, BrokerLLM(),
                                lang=_LANG_NAME.get(lang, "Russian"))
    return {"ok": bool(report), "thread_id": tid, "analysis": report or "(empty)"}


def reader_app():  # noqa: ANN201
    """The token-guarded read-only Streamable HTTP ASGI app to mount at /reader."""
    return token_guard(mcp.streamable_http_app(),
                       lambda: split_tokens(settings().mcp_read_secret))
