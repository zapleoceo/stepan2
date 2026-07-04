"""Coach routes: say, apply, cancel, revert, panel."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.admin._branch import branch_ids_from_request
from app.modules.conversation.coach_service import (
    apply_edit,
    cancel_edit,
    propose_edit,
    revert_edit,
)

from ._i18n import apply_lang
from ._query import fetch_coach_data
from ._ui_panels import _coach_response, coach_chat_html

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
    request_text: str = Form(alias="request"),
) -> HTMLResponse:
    # branch_id is resolved server-side, same as every other coach route — the form's
    # hidden branch_id field is never trusted (a client could submit any branch it likes).
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    branch_id = branch_ids[0] if branch_ids else 1
    llm = BrokerLLM()
    async with session_scope() as session:
        edit = await propose_edit(session, branch_id, request_text.strip(), llm)
        # only the coach's reply — the manager's own message was appended optimistically
        # on the client the instant they hit send (coachSend).
        html = _coach_response(
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
    branch_ids = branch_ids_from_request(request)
    branch_id = branch_ids[0] if branch_ids else 1
    async with session_scope() as session:
        await revert_edit(session, branch_id, edit_id)
    return RedirectResponse("/ui/coach", status_code=303)
