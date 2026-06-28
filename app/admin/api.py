"""Admin helper API — branch switcher endpoints.

Routes live under /_admin/ (NOT /admin/) because SQLAdmin mounts itself as
an ASGI sub-app at /admin and would intercept /admin/* before FastAPI.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import select

from app.adapters.db.models import Branch
from app.adapters.db.session import session_scope

from ._branch import BRANCH_COOKIE

router = APIRouter(prefix="/_admin", tags=["admin-meta"])


@router.get("/branches")
async def get_branches(request: Request) -> JSONResponse:
    """Return active branches + currently selected id (for the JS sidebar widget)."""
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
        "current": request.cookies.get(BRANCH_COOKIE, ""),
    })


@router.post("/set-branch")
async def set_branch(
    request: Request,
    branch_id: str = Form(default=""),
) -> RedirectResponse:
    """Set or clear the branch filter cookie; redirect back to referrer."""
    referer = request.headers.get("referer", "/admin/")
    resp = RedirectResponse(url=referer, status_code=303)
    if branch_id:
        resp.set_cookie(
            BRANCH_COOKIE,
            branch_id,
            path="/",
            httponly=False,  # JS sidebar reads it to pre-select the current value
            samesite="lax",
        )
    else:
        resp.delete_cookie(BRANCH_COOKIE, path="/")
    return resp
