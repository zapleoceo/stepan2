"""Coach routes: say, apply, cancel, revert, panel."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.adapters.db.models import CoachingEdit
from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.admin._branch import (
    allowed_branch_ids,
    branch_ids_from_request,
    is_branch_forbidden,
    is_branch_write_forbidden,
    writable_branch_ids,
)
from app.domain.clock import utc_now
from app.modules.conversation.coach_service import (
    apply_edit,
    cancel_edit,
    create_pending_edit,
    generate_into_edit,
    revert_edit,
)

from ._i18n import apply_lang
from ._query import fetch_coach_data
from ._ui_panels import _coach_response, coach_chat_html

router = APIRouter()
_log = logging.getLogger(__name__)

# generation runs detached from the request so navigating away doesn't cancel it — hold a
# reference so the task isn't garbage-collected mid-flight.
_COACH_TASKS: set[asyncio.Task] = set()
_STALE_THINKING = timedelta(minutes=3)  # a 'thinking' older than this = generation died


async def _run_coach_generation(branch_id: int, edit_id: int) -> None:
    """Fill a pending edit's answer in the background (own session, survives client leave)."""
    try:
        async with session_scope() as session:
            edit = await session.get(CoachingEdit, edit_id)
            if edit is not None and edit.status == "thinking":
                await generate_into_edit(session, branch_id, edit, BrokerLLM())
    except Exception:  # noqa: BLE001 — never let a failed turn leave the row stuck 'thinking'
        _log.exception("coach generation failed edit=%d", edit_id)
        with contextlib.suppress(Exception):
            async with session_scope() as session:
                edit = await session.get(CoachingEdit, edit_id)
                if edit is not None and edit.status == "thinking":
                    edit.status = "failed"
                    edit.summary = "Ошибка генерации"
                    session.add(edit)


def _spawn_coach_generation(branch_id: int, edit_id: int) -> None:
    task = asyncio.create_task(_run_coach_generation(branch_id, edit_id))
    _COACH_TASKS.add(task)
    task.add_done_callback(_COACH_TASKS.discard)


def _coach_branch(request: Request) -> int:
    """The branch the operator is VIEWING (same source as the coach panel), constrained to
    one they may WRITE. Fixes the mismatch where the panel showed branch N via the view
    cookie but every write fell back to writable[0] (branch 1 for a super_admin) — a
    super/multi-branch admin coaching branch 3's panel silently edited branch 1's KB."""
    view = branch_ids_from_request(request)          # what the panel shows (view-filter cookie)
    writable = writable_branch_ids(request)          # None = write-anywhere (super/auth off)
    target = view[0] if view else None
    if target is not None and not is_branch_write_forbidden(target, writable):
        return target
    return writable[0] if writable else (target or 1)


@router.get("/coach/panel", response_class=HTMLResponse)
async def coach_panel_partial(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_id = _coach_branch(request)  # same branch the write routes use → panel and writes agree
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
    # Scoped by WRITE right (viewer can't coach); middleware already blocks a pure viewer.
    apply_lang(request)
    branch_id = _coach_branch(request)
    text_val = request_text.strip()
    # Persist the question FIRST (status 'thinking') and commit, then generate the answer in
    # the background — so leaving the page mid-generation loses neither the question nor the
    # answer. The returned bubble self-polls /coach/edit/{id} until the answer lands.
    async with session_scope() as session:
        edit = await create_pending_edit(session, branch_id, text_val)
        eid, created = edit.id, edit.created_at
    _spawn_coach_generation(branch_id, eid)
    return HTMLResponse(
        _coach_response(eid, text_val, "thinking", None, None, None, None, created))


@router.get("/coach/edit/{edit_id}", response_class=HTMLResponse)
async def coach_edit_poll(edit_id: int, request: Request) -> HTMLResponse:
    """Current state of one coach edit — polled by the 'thinking' bubble until the answer
    lands (or a stale 'thinking' is marked failed after a restart killed its background task)."""
    apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        edit = await session.get(CoachingEdit, edit_id)
        if edit is None or is_branch_forbidden(edit.branch_id, allowed):
            return HTMLResponse("")
        if edit.status == "thinking" and utc_now() - edit.created_at > _STALE_THINKING:
            edit.status = "failed"
            edit.summary = "Генерация прервалась — задай вопрос ещё раз"
            session.add(edit)
        html = _coach_response(
            edit.id, edit.request, edit.status, edit.slug,
            edit.old_text, edit.new_text, edit.summary, edit.created_at,
        )
    return HTMLResponse(html)


@router.post("/coach/apply/{edit_id}")
async def coach_apply(edit_id: int, request: Request) -> RedirectResponse:
    branch_id = _coach_branch(request)  # viewed branch, constrained to writable
    async with session_scope() as session:
        await apply_edit(session, branch_id, edit_id)
    return RedirectResponse("/ui/coach", status_code=303)


@router.post("/coach/cancel/{edit_id}")
async def coach_cancel(edit_id: int, request: Request) -> RedirectResponse:
    branch_id = _coach_branch(request)  # viewed branch, constrained to writable
    async with session_scope() as session:
        await cancel_edit(session, branch_id, edit_id)
    return RedirectResponse("/ui/coach", status_code=303)


@router.post("/coach/revert/{edit_id}")
async def coach_revert(edit_id: int, request: Request) -> RedirectResponse:
    branch_id = _coach_branch(request)  # viewed branch, constrained to writable
    async with session_scope() as session:
        await revert_edit(session, branch_id, edit_id)
    return RedirectResponse("/ui/coach", status_code=303)
