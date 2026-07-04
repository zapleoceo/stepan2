"""Branch filter resolution — combines the UI cookie with the caller's identity.

Cookie value: comma-separated integer branch_ids, e.g. "1,3" (the UI selection).
The auth middleware attaches request.state.allowed_branch_ids (None = super_admin /
all branches). A scoped user can never exceed their allowed set by editing the cookie;
with auth disabled (no state) the cookie alone drives the filter, empty = show all.
"""
from __future__ import annotations

from fastapi import HTTPException
from starlette.requests import Request

BRANCH_COOKIE = "stepan2_branch"
_NO_ROWS = [-1]  # branch_id that can never match → authed user with zero branches


def branch_ids_from_request(request: Request) -> list[int] | None:
    """Resolve the effective branch_ids to filter by, or None (= all branches)."""
    raw = (request.cookies.get(BRANCH_COOKIE) or "").strip()
    selected = [int(p) for p in raw.split(",") if p.strip().isdigit()]

    allowed = getattr(getattr(request, "state", None), "allowed_branch_ids", None)
    if allowed is None:  # super_admin or auth disabled → cookie alone (None = all)
        return selected or None
    if not allowed:  # authenticated but no branch memberships → see nothing
        return _NO_ROWS
    if selected:  # narrow the selection to what the user is allowed to see
        return [b for b in selected if b in allowed] or allowed
    return allowed


def actor_from_request(request: Request) -> str:
    """Who is editing — the authenticated owner's id, or 'owner' when auth is disabled.
    Used as the `actor` on knowledge/product revisions."""
    user = getattr(getattr(request, "state", None), "user", None) or {}
    return str(user.get("uid") or user.get("tg") or "owner")


def is_branch_forbidden(branch_id: int, allowed: list[int] | None) -> bool:
    """True when the caller may not act on branch_id; empty list = access to nothing."""
    return allowed is not None and branch_id not in allowed


def allowed_branch_ids(request: Request) -> list[int] | None:
    """Branches the caller may ACT on (write/manage): None = act on any branch
    (super_admin, or auth disabled). Unlike branch_ids_from_request this ignores the
    view-filter cookie — a super-admin filtering their inbox to one branch must still
    be able to manage channels/chats of any other branch."""
    return getattr(getattr(request, "state", None), "allowed_branch_ids", None)


def is_super_admin(request: Request) -> bool:
    """True for a platform-wide super_admin — same permissive default the rest of the
    branch-scoping helpers use when auth is disabled (dev/local), so a bare `None` state
    doesn't accidentally lock the owner out before auth is configured."""
    return allowed_branch_ids(request) is None


def require_super_admin(request: Request) -> None:
    """FastAPI dependency: 403s any non-super-admin off platform-wide routes (member
    management, branch CRUD, the platform-wide bot kill switch)."""
    if not is_super_admin(request):
        raise HTTPException(status_code=403, detail="Super admin only")
