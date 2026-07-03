"""Branch filter resolution — combines the UI cookie with the caller's identity.

Cookie value: comma-separated integer branch_ids, e.g. "1,3" (the UI selection).
The auth middleware attaches request.state.allowed_branch_ids (None = super_admin /
all branches). A scoped user can never exceed their allowed set by editing the cookie;
with auth disabled (no state) the cookie alone drives the filter, empty = show all.
"""
from __future__ import annotations

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


def allowed_branch_ids(request: Request) -> list[int] | None:
    """Branches the caller may ACT on (write/manage): None = act on any branch
    (super_admin, or auth disabled). Unlike branch_ids_from_request this ignores the
    view-filter cookie — a super-admin filtering their inbox to one branch must still
    be able to manage channels/chats of any other branch."""
    return getattr(getattr(request, "state", None), "allowed_branch_ids", None)
