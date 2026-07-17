"""Parity guard: the local stdio bridge must expose every tool the REST surface serves.

The bridge (mcp_server/stepan_mcp.py) is a thin proxy over /mcp/* — when a tool is added
to one side only, a client on the other transport silently lacks it (that's how sim_reset
and sim_persona went missing from Claude Desktop/Code for a while).
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.api import _routes_mcp as routes  # noqa: E402

_EXPECTED = {"find_lead", "move_lead", "close_deal", "call_failed",
             "sim_say", "sim_reset", "sim_persona"}


def test_rest_serves_every_expected_tool() -> None:
    paths = {getattr(r, "path", "") for r in routes.router.routes}
    assert {f"/mcp/{name}" for name in _EXPECTED} <= paths


async def test_bridge_exposes_the_same_tools_as_rest() -> None:
    from mcp_server.stepan_mcp import mcp as bridge
    names = {t.name for t in await bridge.list_tools()}
    rest = {p.removeprefix("/mcp/") for p in
            (getattr(r, "path", "") for r in routes.router.routes) if p.startswith("/mcp/")}
    assert rest <= names, f"bridge is missing REST tools: {rest - names}"
    assert _EXPECTED <= names
