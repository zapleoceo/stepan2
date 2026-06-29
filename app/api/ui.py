"""Custom manager UI: inbox, chat view + manual send, coach mode (KB editor via LLM).

Routes at /ui/ — no additional auth (same security as /admin/).
Branch isolation uses the stepan2_branch cookie set by the admin sidebar.

GET  /ui/inbox                   — thread list (newest first)
GET  /ui/chat/{thread_id}        — conversation + send form
POST /ui/chat/{thread_id}/send   — add manager message to outbox
GET  /ui/coach                   — LLM-powered KB editor
POST /ui/coach/say               — HTMX: propose a KB change (partial HTML)
POST /ui/coach/apply/{edit_id}   — apply proposed KB change
POST /ui/coach/cancel/{edit_id}  — discard proposed KB change
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.adapters.db.models import Outbox
from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.admin._branch import branch_ids_from_request
from app.modules.conversation.coach_service import apply_edit, cancel_edit, propose_edit

from ._ui_html import chat_html, coach_html, coach_partial_html, inbox_html

router = APIRouter(prefix="/ui")


# ─── inbox ────────────────────────────────────────────────────────────────────

@router.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request) -> HTMLResponse:
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        if branch_ids:
            rows = await session.execute(
                text("""
                    SELECT ct.id, l.display_name, l.stage, ct.last_in_at,
                      (SELECT m.text FROM message m WHERE m.thread_id = ct.id
                       ORDER BY m.occurred_at DESC, m.id DESC LIMIT 1) last_msg,
                      (SELECT m.direction FROM message m WHERE m.thread_id = ct.id
                       ORDER BY m.occurred_at DESC, m.id DESC LIMIT 1) last_dir
                    FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id
                    WHERE l.branch_id = ANY(:bids)
                    ORDER BY COALESCE(ct.last_in_at, ct.created_at) DESC LIMIT 100
                """),
                {"bids": branch_ids},
            )
        else:
            rows = await session.execute(text("""
                SELECT ct.id, l.display_name, l.stage, ct.last_in_at,
                  (SELECT m.text FROM message m WHERE m.thread_id = ct.id
                   ORDER BY m.occurred_at DESC, m.id DESC LIMIT 1) last_msg,
                  (SELECT m.direction FROM message m WHERE m.thread_id = ct.id
                   ORDER BY m.occurred_at DESC, m.id DESC LIMIT 1) last_dir
                FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id
                ORDER BY COALESCE(ct.last_in_at, ct.created_at) DESC LIMIT 100
            """))
    return HTMLResponse(inbox_html(rows.all()))


# ─── chat ─────────────────────────────────────────────────────────────────────

@router.get("/chat/{thread_id}", response_class=HTMLResponse)
async def chat_view(thread_id: int) -> HTMLResponse:
    async with session_scope() as session:
        info = (
            await session.execute(
                text("SELECT ct.id, l.display_name, l.stage, l.branch_id "
                     "FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id WHERE ct.id = :tid"),
                {"tid": thread_id},
            )
        ).first()
        if not info:
            return HTMLResponse(
                "<h2 style='font-family:sans-serif;padding:2rem'>Thread not found</h2>",
                status_code=404,
            )
        msgs = (
            await session.execute(
                text("SELECT id, direction, sent_by, text, occurred_at FROM message "
                     "WHERE thread_id = :tid ORDER BY occurred_at, id"),
                {"tid": thread_id},
            )
        ).all()
        pending = (
            await session.execute(
                text("SELECT id, text, scheduled_at FROM outbox "
                     "WHERE thread_id = :tid AND status = 'pending' ORDER BY id"),
                {"tid": thread_id},
            )
        ).all()
    _, name, stage, _ = info
    return HTMLResponse(
        chat_html(thread_id, str(name or "Lead"), str(stage or "new"), msgs, pending)
    )


@router.post("/chat/{thread_id}/send")
async def chat_send(thread_id: int, text_body: str = Form(alias="text")) -> RedirectResponse:
    text_body = text_body.strip()
    if not text_body:
        return RedirectResponse(f"/ui/chat/{thread_id}", status_code=303)
    async with session_scope() as session:
        info = (
            await session.execute(
                text("SELECT l.branch_id FROM channel_thread ct "
                     "JOIN lead l ON l.id = ct.lead_id WHERE ct.id = :tid"),
                {"tid": thread_id},
            )
        ).first()
        if not info:
            return RedirectResponse("/ui/inbox", status_code=303)
        session.add(Outbox(
            branch_id=info[0], thread_id=thread_id, text=text_body, source="manager",
        ))
        await session.flush()
    return RedirectResponse(f"/ui/chat/{thread_id}", status_code=303)


# ─── coach ────────────────────────────────────────────────────────────────────

@router.get("/coach", response_class=HTMLResponse)
async def coach_page(request: Request) -> HTMLResponse:
    branch_ids = branch_ids_from_request(request)
    branch_id = branch_ids[0] if branch_ids else 1
    async with session_scope() as session:
        rows = (
            await session.execute(
                text("SELECT id, request, status, slug, old_text, new_text, summary, created_at "
                     "FROM coaching_edit WHERE branch_id = :bid ORDER BY id DESC LIMIT 50"),
                {"bid": branch_id},
            )
        ).all()
    return HTMLResponse(coach_html(branch_id, rows))


@router.post("/coach/say", response_class=HTMLResponse)
async def coach_say(
    branch_id: int = Form(),
    request_text: str = Form(alias="request"),
) -> HTMLResponse:
    llm = BrokerLLM()
    async with session_scope() as session:
        edit = await propose_edit(session, branch_id, request_text.strip(), llm)
        partial = coach_partial_html(
            edit.id, edit.request, edit.status, edit.slug,
            edit.old_text, edit.new_text, edit.summary,
        )
    return HTMLResponse(partial)


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
