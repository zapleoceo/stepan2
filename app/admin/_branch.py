"""Branch cookie helper — single source of truth for branch filter state.

The selected branch is stored in a plain (non-httponly) cookie so the JS
sidebar widget can read it. The value is an integer branch_id (not sensitive).
"""
from __future__ import annotations

from starlette.requests import Request

BRANCH_COOKIE = "stepan2_branch"


def branch_id_from_request(request: Request) -> int | None:
    """Return the branch_id from the filter cookie, or None (= show all)."""
    val = request.cookies.get(BRANCH_COOKIE)
    try:
        return int(val) if val else None
    except (ValueError, TypeError):
        return None
