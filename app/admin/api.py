"""Admin helper API — branch multi-filter endpoints.

Routes live under /_admin/ (NOT /admin/) because SQLAdmin mounts itself as
an ASGI sub-app at /admin and would intercept /admin/* before FastAPI.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import select

from app.adapters.db.models import Branch
from app.adapters.db.session import session_scope

from ._branch import BRANCH_COOKIE, branch_ids_from_request

router = APIRouter(prefix="/_admin", tags=["admin-meta"])


@router.get("/branches")
async def get_branches(request: Request) -> JSONResponse:
    """Return active branches + currently selected ids (list[int]) for the sidebar widget."""
    async with session_scope() as s:
        branches = (
            await s.exec(
                select(Branch)
                .where(Branch.is_active == True)  # noqa: E712
                .order_by(Branch.name)
            )
        ).all()
    return JSONResponse({
        "branches": [{"id": b.id, "name": b.name} for b in branches],
        "current": branch_ids_from_request(request) or [],
    })


@router.post("/set-branch")
async def set_branch(
    request: Request,
    branch_ids: str = Form(default=""),
) -> RedirectResponse:
    """Set or clear the branch filter cookie; redirect back to referrer.

    branch_ids is a comma-separated string of integer ids, e.g. "1,3".
    Empty string means clear filter (show all).
    """
    referer = request.headers.get("referer", "/admin/")
    resp = RedirectResponse(url=referer, status_code=303)
    clean = [p.strip() for p in branch_ids.split(",") if p.strip().isdigit()]
    if clean:
        resp.set_cookie(
            BRANCH_COOKIE,
            ",".join(clean),
            path="/",
            httponly=False,
            samesite="lax",
            secure=True,
        )
    else:
        resp.delete_cookie(BRANCH_COOKIE, path="/")
    return resp
