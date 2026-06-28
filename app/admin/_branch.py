"""Branch cookie helper — single source of truth for branch filter state.

Cookie value: comma-separated integer branch_ids, e.g. "1,3".
Empty / missing cookie means "show all" (no WHERE clause).
Not httponly so the JS widget can read the current selection without an API call.
"""
from __future__ import annotations

from starlette.requests import Request

BRANCH_COOKIE = "stepan2_branch"


def branch_ids_from_request(request: Request) -> list[int] | None:
    """Return selected branch_ids from cookie, or None (= show all)."""
    raw = (request.cookies.get(BRANCH_COOKIE) or "").strip()
    if not raw:
        return None
    ids = [int(p) for p in raw.split(",") if p.strip().isdigit()]
    return ids if ids else None
