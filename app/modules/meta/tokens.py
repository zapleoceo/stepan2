"""Derive a Page access token from a Meta System User token.

A System User token with `pages_show_list` scope can list every Page it has been granted
access to via `/me/accounts`, each with its own long-lived derived Page token. This lets an
operator paste the System User token once (already stored per branch) instead of hunting down
and re-pasting a Page token whenever it's needed.
"""
from __future__ import annotations

import httpx

_GRAPH_BASE = "https://graph.facebook.com"


async def page_access_token(system_user_token: str, page_id: str) -> str:
    """Return the Page access token for `page_id`, derived from `system_user_token`.

    Raises ValueError if the page isn't among the accounts visible to this token.
    """
    async with httpx.AsyncClient(base_url=_GRAPH_BASE, timeout=15) as client:
        resp = await client.get("/v19.0/me/accounts", params={"access_token": system_user_token})
        resp.raise_for_status()
    for row in resp.json().get("data", []):
        if row.get("id") == page_id:
            return row["access_token"]
    raise ValueError(f"Page {page_id} not found among this System User's accessible Pages")
