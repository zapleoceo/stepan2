"""Stepan MCP server (stdio) — lets an MCP client (Claude Desktop, etc.) drive the
lead funnel by phone number.

Runs LOCALLY, not on the Hetzner box: Claude Desktop spawns this process, and it
calls Stepan's HTTPS /mcp API with a bearer token. Configure via env:

    STEPAN2_MCP_URL     base URL of the Stepan instance (default https://stepan2.zapleo.com)
    STEPAN2_MCP_SECRET  the bearer token (must equal STEPAN2_MCP_SECRET on the server)

Install + run:  pip install -r requirements.txt  &&  python stepan_mcp.py
See README.md for the Claude Desktop config snippet.
"""
from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

_BASE = os.environ.get("STEPAN2_MCP_URL", "https://stepan2.zapleo.com").rstrip("/")
_SECRET = os.environ.get("STEPAN2_MCP_SECRET", "")
_TIMEOUT = httpx.Timeout(90.0)  # call_failed generates a message via the broker — allow room

mcp = FastMCP("stepan")


def _headers() -> dict[str, str]:
    if not _SECRET:
        raise RuntimeError("STEPAN2_MCP_SECRET is not set")
    return {"Authorization": f"Bearer {_SECRET}"}


async def _get(path: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.get(f"{_BASE}{path}", params=params, headers=_headers())
        return _unwrap(r)


async def _post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.post(f"{_BASE}{path}", json=body, headers=_headers())
        return _unwrap(r)


def _unwrap(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:  # noqa: BLE001
            detail = r.text
        return {"ok": False, "error": f"{r.status_code}: {detail}"}
    return r.json()


@mcp.tool()
async def find_lead(phone: str, branch_id: int | None = None) -> dict:
    """Look up a lead by phone number (E.164, e.g. +6281234567890). Returns the lead's
    id, name, Instagram username, branch, current funnel stage and whether the bot is on.
    Use this first to confirm the lead exists before moving them."""
    return await _get("/mcp/find_lead", {"phone": phone, "branch_id": branch_id})


@mcp.tool()
async def close_deal(phone: str, note: str | None = None) -> dict:
    """Mark a lead's deal as WON. Hands the lead off (stage → handed_off) and stops the
    bot from messaging them further. `note` is journaled on the funnel event."""
    return await _post("/mcp/close_deal", {"phone": phone, "note": note})


@mcp.tool()
async def call_failed(phone: str, note: str | None = None) -> dict:
    """Report that a phone call to the lead did NOT connect. Journals the failed call,
    re-enables the bot, and Stepan proactively messages the lead to continue in chat.
    A lead already handed off / dormant is pulled back to `qualifying` so the bot works
    them again. `note` (e.g. 'no answer', 'wrong number') is journaled."""
    return await _post("/mcp/call_failed", {"phone": phone, "note": note})


@mcp.tool()
async def move_lead(phone: str, stage: str, note: str | None = None) -> dict:
    """Move a lead to an explicit funnel stage. Valid stages: new, nurturing, qualifying,
    presenting, objection, ready, handed_off, dormant, manager. `manager` turns the bot
    off (human takeover); an active stage turns it back on. `note` is journaled."""
    return await _post("/mcp/move_lead", {"phone": phone, "stage": stage, "note": note})


@mcp.tool()
async def sim_say(branch_id: int, session_key: str, message: str) -> dict:
    """Send one message to Stepan as a simulated lead, through the REAL reply engine
    (RAG + guard + routing) but fully sandboxed — never touches production leads or
    Instagram, not billed. `session_key` names the sandbox thread; reuse it across calls
    to continue the same conversation, or pick a fresh one to start over. Returns the
    bot's reply plus the decided stage/product/lead_type/needs_manager."""
    return await _post("/mcp/sim_say", {
        "branch_id": branch_id, "session_key": session_key, "message": message})


@mcp.tool()
async def sim_reset(branch_id: int, session_key: str) -> dict:
    """Wipe a sim conversation so the next sim_say starts fresh (clears its messages and
    resets the sandbox lead's needs/stage). Only affects the sandbox, never real leads."""
    return await _post("/mcp/sim_reset", {
        "branch_id": branch_id, "session_key": session_key})


@mcp.tool()
async def sim_persona(
    branch_id: int, persona: str, session_key: str, max_turns: int = 3,
) -> dict:
    """Run an auto-dialogue: an LLM plays a lead of a given archetype and talks to Stepan
    (the real reply engine) up to max_turns turns, then returns the transcript + what the
    engine decided (stage, captured jobs/pains/gains, ready/handoff). Bounded + resumable —
    call again with the same session_key to continue until `ended` is true.

    Personas: hot_ready, budget_student, skeptic_diy, confused_explorer, career_switcher,
    freelancer_upskill, parent_for_child, corporate_bulk, ghoster_busy, wrong_fit.
    Use a SIM/test branch_id (not a live branch). Fully sandboxed; nothing reaches Instagram."""
    return await _post("/mcp/sim_persona", {
        "branch_id": branch_id, "persona": persona, "session_key": session_key,
        "max_turns": max_turns})


if __name__ == "__main__":
    mcp.run()
