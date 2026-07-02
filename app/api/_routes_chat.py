"""Chat panel routes: panel, send, stage, suggest, translate."""
from __future__ import annotations

import html as _h
import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.adapters.db.models import Outbox
from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM

from ._i18n import apply_lang, t
from ._query import fetch_messages, fetch_pending
from ._ui_html import chat_header_html, chat_panel_html, messages_html, suggest_box_html

router = APIRouter()
_log = logging.getLogger(__name__)

_VALID_STAGES = frozenset({
    "new", "nurturing", "qualifying", "presenting", "objection",
    "ready", "handed_off", "dormant", "manager",
})


@router.get("/chat/{thread_id}/panel", response_class=HTMLResponse)
async def chat_panel(thread_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        info = (
            await session.execute(
                text(
                    "SELECT ct.id, l.display_name, l.stage, l.branch_id,"
                    " ct.product_slug, ct.external_thread_id,"
                    " l.phone_e164, l.created_at, ct.last_in_at,"
                    " l.ig_username, l.avatar_url,"
                    " ct.lead_source, ct.ad_id, ct.ad_media_id, ct.ad_preview_url"
                    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
                    " WHERE ct.id = :tid"
                ),
                {"tid": thread_id},
            )
        ).first()
        if not info:
            return HTMLResponse('<div class="emp">Thread not found</div>', status_code=404)
        msgs = await fetch_messages(session, thread_id)
        pending = await fetch_pending(session, thread_id)
    (_, name, stage, _, product_slug, ig_id,
     phone, created_at, last_in_at,
     ig_username, avatar_url,
     lead_source, ad_id, ad_media_id, ad_preview_url) = info
    return HTMLResponse(
        chat_panel_html(
            thread_id, str(name or "Lead"), str(stage or "new"), msgs, pending,
            product_slug=product_slug, ig_id=ig_id,
            phone=phone, created_at=created_at, last_in_at=last_in_at,
            ig_username=ig_username, avatar_url=avatar_url,
            lead_source=lead_source, ad_id=ad_id,
            ad_media_id=ad_media_id, ad_preview_url=ad_preview_url,
        )
    )


@router.post("/chat/{thread_id}/send", response_class=HTMLResponse)
async def chat_send(
    thread_id: int, request: Request, text_body: str = Form(alias="text"),
) -> HTMLResponse:
    apply_lang(request)
    text_body = text_body.strip()
    async with session_scope() as session:
        info = (
            await session.execute(
                text(
                    "SELECT l.branch_id FROM channel_thread ct"
                    " JOIN lead l ON l.id = ct.lead_id WHERE ct.id = :tid"
                ),
                {"tid": thread_id},
            )
        ).first()
        if not info or not text_body:
            msgs = await fetch_messages(session, thread_id)
            return HTMLResponse(messages_html(msgs, [], thread_id))
        session.add(Outbox(
            branch_id=info[0], thread_id=thread_id, text=text_body, source="manager",
        ))
        await session.flush()
        msgs = await fetch_messages(session, thread_id)
        pending = await fetch_pending(session, thread_id)
    return HTMLResponse(messages_html(msgs, pending, thread_id))


@router.post("/chat/{thread_id}/stage", response_class=HTMLResponse)
async def chat_stage(
    thread_id: int, request: Request, stage: str = Form(default="new"),
) -> HTMLResponse:
    apply_lang(request)
    if stage not in _VALID_STAGES:
        stage = "new"
    async with session_scope() as session:
        info = (
            await session.execute(
                text(
                    "SELECT ct.id, l.display_name, l.id as lead_id,"
                    " ct.product_slug, ct.external_thread_id,"
                    " l.phone_e164, l.created_at, ct.last_in_at,"
                    " l.ig_username, l.avatar_url,"
                    " ct.lead_source, ct.ad_id, ct.ad_media_id, ct.ad_preview_url"
                    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
                    " WHERE ct.id = :tid"
                ),
                {"tid": thread_id},
            )
        ).first()
        if not info:
            return HTMLResponse('<div class="emp">Thread not found</div>', status_code=404)
        (_, name, lead_id, product_slug, ig_id,
         phone, created_at, last_in_at,
         ig_username, avatar_url,
         lead_source, ad_id, ad_media_id, ad_preview_url) = info
        await session.execute(
            text("UPDATE lead SET stage = :s WHERE id = :id"),
            {"s": stage, "id": lead_id},
        )
    return HTMLResponse(
        chat_header_html(thread_id, str(name or "Lead"), stage,
                         product_slug=product_slug, ig_id=ig_id,
                         phone=phone, created_at=created_at, last_in_at=last_in_at,
                         ig_username=ig_username, avatar_url=avatar_url,
                         lead_source=lead_source, ad_id=ad_id,
                         ad_media_id=ad_media_id, ad_preview_url=ad_preview_url)
    )


@router.post("/chat/{thread_id}/suggest", response_class=HTMLResponse)
async def chat_suggest(thread_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        msgs = (
            await session.execute(
                text(
                    "SELECT direction, sent_by, text FROM message"
                    " WHERE thread_id = :tid ORDER BY occurred_at DESC, id DESC LIMIT 10"
                ),
                {"tid": thread_id},
            )
        ).all()
    if not msgs:
        return HTMLResponse("")
    convo_lines = "\n".join(
        f'{"Lead" if r[0] == "in" else "Bot"}: {(r[2] or "")[:200]}'
        for r in reversed(msgs)
    )
    llm_msgs = [
        {
            "role": "system",
            "content": (
                "You are a helpful sales assistant. "
                "Based on the conversation, write a SHORT friendly reply to the lead. "
                "Reply in the same language as the lead. Max 3 sentences. "
                "Return ONLY the reply text."
            ),
        },
        {"role": "user", "content": convo_lines},
    ]
    llm = BrokerLLM()
    try:
        draft, _ = await llm.chat(llm_msgs, capability="chat:fast", max_tokens=300)
    except Exception as exc:
        _log.warning("suggest LLM error tid=%s: %s", thread_id, exc)
        draft = ""
    return HTMLResponse(suggest_box_html(thread_id, draft.strip()))


@router.post("/chat/{thread_id}/translate", response_class=HTMLResponse)
async def chat_translate(thread_id: int, request: Request) -> HTMLResponse:
    """Translate the last inbound message (global toolbar button)."""
    lang_code = apply_lang(request)
    async with session_scope() as session:
        row = (
            await session.execute(
                text(
                    "SELECT text FROM message"
                    " WHERE thread_id = :tid AND direction = 'in'"
                    " ORDER BY occurred_at DESC, id DESC LIMIT 1"
                ),
                {"tid": thread_id},
            )
        ).first()
    if not row or not row[0]:
        return HTMLResponse("")
    last_msg = (row[0] or "")[:800]
    target_lang = {
        "ru": "Russian", "en": "English", "id": "Indonesian",
    }.get(lang_code, "English")
    llm_msgs = [
        {
            "role": "system",
            "content": (
                f"Translate the following message to {target_lang}. "
                "Return ONLY the translated text, nothing else."
            ),
        },
        {"role": "user", "content": last_msg},
    ]
    llm = BrokerLLM()
    try:
        translation, _ = await llm.chat(llm_msgs, capability="chat:fast", max_tokens=400)
    except Exception as exc:
        _log.warning("translate LLM error tid=%s: %s", thread_id, exc)
        translation = ""
    if not translation.strip():
        return HTMLResponse("")
    tr_lbl = _h.escape(t("chat.tr_result"))
    return HTMLResponse(
        f'<div style="padding:.3rem .75rem;font-size:.76rem;color:#8899aa;'
        f'background:#141925;border-top:1px solid #2d3748">'
        f'<span style="color:#4a5568">{tr_lbl}</span>'
        f' {_h.escape(translation.strip())}</div>'
    )


@router.get("/chat/{thread_id}/msg/{mid}/tr", response_class=HTMLResponse)
async def msg_translate_single(thread_id: int, mid: int, request: Request) -> HTMLResponse:  # noqa: ARG001
    """Translate a specific message bubble to Russian (per-bubble 🌐 button)."""
    async with session_scope() as session:
        row = (
            await session.execute(
                text("SELECT text FROM message WHERE id = :mid AND thread_id = :tid"),
                {"mid": mid, "tid": thread_id},
            )
        ).first()
    if not row or not row[0]:
        return HTMLResponse("")
    msg_text = (row[0] or "")[:800]
    llm_msgs = [
        {
            "role": "system",
            "content": "Translate the following message to Russian. Return ONLY the translation.",
        },
        {"role": "user", "content": msg_text},
    ]
    llm = BrokerLLM()
    try:
        translation, _ = await llm.chat(llm_msgs, capability="chat:fast", max_tokens=400)
        return HTMLResponse(_h.escape(translation.strip()))
    except Exception as exc:
        _log.warning("per-msg translate error tid=%s mid=%s: %s", thread_id, mid, exc)
        return HTMLResponse(_h.escape(msg_text))


@router.post("/chat/{thread_id}/msg/{mid}/delete", response_class=HTMLResponse)
async def msg_delete(thread_id: int, mid: int) -> HTMLResponse:
    """Retract a message. Outgoing → request an IG unsend (worker revokes, then the
    row disappears); inbound → we can't unsend the lead's message, so only our local
    copy is removed. Never claims a retraction that didn't happen in IG."""
    async with session_scope() as session:
        row = (
            await session.execute(
                text("SELECT direction FROM message WHERE id=:mid AND thread_id=:tid"),
                {"mid": mid, "tid": thread_id},
            )
        ).first()
        if row is None:
            return HTMLResponse("")
        if row[0] == "out":
            await session.execute(
                text("UPDATE message SET delete_requested=true WHERE id=:mid"),
                {"mid": mid},
            )
            return HTMLResponse(
                '<div class="bb bb-o" style="opacity:.5;font-style:italic;font-size:.78rem">'
                "⏳ отзывается…</div>"
            )
        await session.execute(
            text("DELETE FROM message WHERE id=:mid AND thread_id=:tid"),
            {"mid": mid, "tid": thread_id},
        )
    return HTMLResponse("")
