"""Coach routes: say, apply, cancel, revert, panel."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.admin._branch import branch_ids_from_request
from app.modules.conversation.coach_service import apply_edit, cancel_edit, propose_edit

from ._i18n import apply_lang
from ._query import fetch_coach_data
from ._ui_panels import _coach_pair, coach_chat_html

router = APIRouter()


@router.get("/coach/panel", response_class=HTMLResponse)
async def coach_panel_partial(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    branch_id = branch_ids[0] if branch_ids else 1
    async with session_scope() as session:
        edits, notes = await fetch_coach_data(session, branch_id)
    return HTMLResponse(coach_chat_html(branch_id, edits, notes))


@router.post("/coach/say", response_class=HTMLResponse)
async def coach_say(
    request: Request,
    branch_id: int = Form(),
    request_text: str = Form(alias="request"),
) -> HTMLResponse:
    apply_lang(request)
    llm = BrokerLLM()
    async with session_scope() as session:
        edit = await propose_edit(session, branch_id, request_text.strip(), llm)
        html = _coach_pair(
            edit.id, edit.request, edit.status, edit.slug,
            edit.old_text, edit.new_text, edit.summary, edit.created_at,
        )
    return HTMLResponse(html)


@router.post("/coach/apply/{edit_id}")
async def coach_apply(edit_id: int, request: Request) -> RedirectResponse:
    branch_ids = branch_ids_from_request(request)
    branch_id = branch_ids[0] if branch_ids else 1
    async with session_scope() as session:
        await apply_edit(session, branch_id, edit_id)
    return RedirectResponse("/ui/coach", status_code=303)


@router.post("/coach/cancel/{edit_id}")
async def coach_cancel(edit_id: int, request: Request) -> RedirectResponse:
    branch_ids = branch_ids_from_request(request)
    branch_id = branch_ids[0] if branch_ids else 1
    async with session_scope() as session:
        await cancel_edit(session, branch_id, edit_id)
    return RedirectResponse("/ui/coach", status_code=303)


@router.post("/coach/revert/{edit_id}")
async def coach_revert(edit_id: int, request: Request) -> RedirectResponse:
    from sqlalchemy import text  # noqa: PLC0415

    branch_ids = branch_ids_from_request(request)
    branch_id = branch_ids[0] if branch_ids else 1
    async with session_scope() as session:
        row = (
            await session.execute(
                text(
                    "SELECT slug, new_text, old_text FROM coaching_edit"
                    " WHERE id=:id AND branch_id=:bid AND status='applied'"
                ),
                {"id": edit_id, "bid": branch_id},
            )
        ).first()
        if row and row[0] and row[2] is not None:
            await session.execute(
                text(
                    "UPDATE knowledge_doc SET content=:c"
                    " WHERE branch_id=:bid AND slug=:slug"
                ),
                {"c": row[2], "bid": branch_id, "slug": row[0]},
            )
            await session.execute(
                text("UPDATE coaching_edit SET status='reverted' WHERE id=:id"),
                {"id": edit_id},
            )
    return RedirectResponse("/ui/coach", status_code=303)
