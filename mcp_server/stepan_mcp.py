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


if __name__ == "__main__":
    mcp.run()
