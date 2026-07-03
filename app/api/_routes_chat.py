"""Chat panel routes: panel, send, stage, suggest, translate."""
from __future__ import annotations

import html as _h
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import text

from app.adapters.db.models import Outbox
from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.admin._branch import allowed_branch_ids
from app.modules.conversation.translate import translate_message, translate_text

from ._i18n import apply_lang, t
from ._query import fetch_messages, fetch_messages_since, fetch_pending
from ._ui_html import (
    chat_block_pill_html,
    chat_bot_pill_html,
    chat_header_html,
    chat_panel_html,
    messages_html,
    set_render_tz,
    since_bubbles_html,
    suggest_box_html,
)

_AGENT_SOURCES = frozenset({"agent", "manager"})

router = APIRouter()
_log = logging.getLogger(__name__)

_VALID_STAGES = frozenset({
    "new", "nurturing", "qualifying", "presenting", "objection",
    "ready", "handed_off", "dormant", "manager",
})


async def _guarded_branch(session, thread_id: int, allowed: list[int] | None) -> int | None:
    """Thread's lead branch_id, or None if it doesn't exist or is outside the caller's
    allowed branches — the per-thread tenant-ownership guard (blocks cross-branch IDOR)."""
    row = (
        await session.execute(
            text(
                "SELECT l.branch_id, b.tz_offset_h FROM channel_thread ct"
                " JOIN lead l ON l.id = ct.lead_id"
                " JOIN branch b ON b.id = l.branch_id WHERE ct.id = :tid"
            ),
            {"tid": thread_id},
        )
    ).first()
    if row is None:
        return None
    if allowed is not None and row[0] not in allowed:
        return None
    set_render_tz(row[1])
    return row[0]


@router.get("/chat/{thread_id}/panel", response_class=HTMLResponse)
async def chat_panel(thread_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        info = (
            await session.execute(
                text(
                    "SELECT ct.id, l.display_name, l.stage, l.branch_id,"
                    " ct.product_slug, ct.external_thread_id,"
                    " l.phone_e164, l.created_at, ct.last_in_at,"
                    " l.ig_username, l.avatar_url,"
                    " ct.lead_source, ct.ad_id, ct.ad_media_id, ct.ad_preview_url,"
                    " l.agent_enabled, l.is_blocked,"
                    " l.follower_count, l.following_count, l.last_active_at, ct.lead_seen_at,"
                    " b.tz_offset_h"
                    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
                    " JOIN branch b ON b.id = l.branch_id"
                    " WHERE ct.id = :tid"
                ),
                {"tid": thread_id},
            )
        ).first()
        if not info or (allowed is not None and info[3] not in allowed):
            return HTMLResponse('<div class="emp">Thread not found</div>', status_code=404)
        set_render_tz(info[21])
        msgs = await fetch_messages(session, thread_id)
        pending = await fetch_pending(session, thread_id)
    (_, name, stage, _, product_slug, ig_id,
     phone, created_at, last_in_at,
     ig_username, avatar_url,
     lead_source, ad_id, ad_media_id, ad_preview_url, agent_enabled, is_blocked,
     follower_count, following_count, last_active_at, lead_seen_at, _tz) = info
    return HTMLResponse(
        chat_panel_html(
            thread_id, str(name or "Lead"), str(stage or "new"), msgs, pending,
            product_slug=product_slug, ig_id=ig_id,
            phone=phone, created_at=created_at, last_in_at=last_in_at,
            ig_username=ig_username, avatar_url=avatar_url,
            lead_source=lead_source, ad_id=ad_id,
            ad_media_id=ad_media_id, ad_preview_url=ad_preview_url,
            agent_enabled=bool(agent_enabled), is_blocked=bool(is_blocked),
            follower_count=follower_count, following_count=following_count,
            last_active_at=last_active_at, lead_seen_at=lead_seen_at,
        )
    )


_MIME_FOR_KIND = {"image": "image/jpeg", "video": "video/mp4", "audio": "audio/mp4"}


@router.get("/media/{asset_id}")
async def chat_media(asset_id: int, request: Request) -> Response:
    """Serve a downloaded MediaAsset's bytes (branch-guarded; private cache)."""
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        row = (
            await session.execute(
                text("SELECT data, mime, kind, branch_id FROM media_asset WHERE id = :id"),
                {"id": asset_id},
            )
        ).first()
    if row is None or row[0] is None:
        return Response(status_code=404)
    if allowed is not None and row[3] not in allowed:
        return Response(status_code=404)
    mime = row[1] or _MIME_FOR_KIND.get(row[2], "application/octet-stream")
    return Response(content=row[0], media_type=mime,
                    headers={"Cache-Control": "private, max-age=86400"})


@router.post("/chat/{thread_id}/send", response_class=HTMLResponse)
async def chat_send(
    thread_id: int,
    request: Request,
    text_body: str = Form(alias="text"),
    source: str = Form(default="manager"),
    llm_info: str | None = Form(default=None),
) -> HTMLResponse:
    apply_lang(request)
    text_body = text_body.strip()
    src = source if source in _AGENT_SOURCES else "manager"
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        branch_id = await _guarded_branch(session, thread_id, allowed)
        if branch_id is None or not text_body:
            msgs = await fetch_messages(session, thread_id)
            return HTMLResponse(messages_html(msgs, [], thread_id))
        session.add(Outbox(
            branch_id=branch_id, thread_id=thread_id, text=text_body, source=src,
            llm_info=(llm_info if src == "agent" else None),
        ))
        await session.flush()
        msgs = await fetch_messages(session, thread_id)
        pending = await fetch_pending(session, thread_id)
    return HTMLResponse(messages_html(msgs, pending, thread_id))


@router.get("/chat/{thread_id}/since/{after_id}", response_class=HTMLResponse)
async def chat_since(thread_id: int, after_id: int, request: Request) -> HTMLResponse:
    """Return only message bubbles newer than after_id plus a fresh poll sentinel."""
    apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        if await _guarded_branch(session, thread_id, allowed) is None:
            return HTMLResponse("")
        rows = await fetch_messages_since(session, thread_id, after_id)
        pending = await fetch_pending(session, thread_id)
    return HTMLResponse(
        since_bubbles_html(list(rows), thread_id, after_id, pending=pending))


@router.post("/chat/{thread_id}/bot-toggle", response_class=HTMLResponse)
async def chat_bot_toggle(thread_id: int, request: Request) -> HTMLResponse:
    """Flip the per-lead agent_enabled flag for this thread's lead; re-render the pill."""
    apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        info = (
            await session.execute(
                text(
                    "SELECT l.id, l.branch_id, l.agent_enabled FROM channel_thread ct"
                    " JOIN lead l ON l.id = ct.lead_id WHERE ct.id = :tid"
                ),
                {"tid": thread_id},
            )
        ).first()
        if not info:
            return HTMLResponse("")
        lead_id, branch_id, enabled = info
        if allowed and branch_id not in allowed:
            return HTMLResponse(chat_bot_pill_html(thread_id, bool(enabled)))
        new_val = not bool(enabled)
        await session.execute(
            text("UPDATE lead SET agent_enabled = :v WHERE id = :id"),
            {"v": new_val, "id": lead_id},
        )
    return HTMLResponse(chat_bot_pill_html(thread_id, new_val))


@router.post("/chat/{thread_id}/block", response_class=HTMLResponse)
async def chat_block(thread_id: int, request: Request) -> HTMLResponse:
    """Toggle the lead's is_blocked flag; blocking also mutes the bot. Re-render the pill."""
    apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        info = (
            await session.execute(
                text(
                    "SELECT l.id, l.branch_id, l.is_blocked FROM channel_thread ct"
                    " JOIN lead l ON l.id = ct.lead_id WHERE ct.id = :tid"
                ),
                {"tid": thread_id},
            )
        ).first()
        if not info:
            return HTMLResponse("")
        lead_id, branch_id, blocked = info
        if allowed is not None and branch_id not in allowed:
            return HTMLResponse(chat_block_pill_html(thread_id, bool(blocked)))
        new_val = not bool(blocked)
        if new_val:
            await session.execute(
                text("UPDATE lead SET is_blocked=true, agent_enabled=false WHERE id=:id"),
                {"id": lead_id},
            )
        else:
            await session.execute(
                text("UPDATE lead SET is_blocked=false WHERE id=:id"), {"id": lead_id}
            )
    return HTMLResponse(chat_block_pill_html(thread_id, new_val))


@router.post("/chat/{thread_id}/clear", response_class=HTMLResponse)
async def chat_clear(thread_id: int, request: Request) -> HTMLResponse:
    """Set context_cleared_at=now — dialog before it stops entering the prompt (local
    reset; IG history untouched)."""
    apply_lang(request)
    allowed = allowed_branch_ids(request)
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_scope() as session:
        if await _guarded_branch(session, thread_id, allowed) is None:
            return HTMLResponse("")
        await session.execute(
            text("UPDATE channel_thread SET context_cleared_at=:t WHERE id=:tid"),
            {"t": now, "tid": thread_id},
        )
    return HTMLResponse("")


@router.post("/chat/{thread_id}/stage", response_class=HTMLResponse)
async def chat_stage(
    thread_id: int, request: Request, stage: str = Form(default="new"),
) -> HTMLResponse:
    apply_lang(request)
    if stage not in _VALID_STAGES:
        stage = "new"
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        if await _guarded_branch(session, thread_id, allowed) is None:
            return HTMLResponse('<div class="emp">Thread not found</div>', status_code=404)
        info = (
            await session.execute(
                text(
                    "SELECT ct.id, l.display_name, l.id as lead_id,"
                    " ct.product_slug, ct.external_thread_id,"
                    " l.phone_e164, l.created_at, ct.last_in_at,"
                    " l.ig_username, l.avatar_url,"
                    " ct.lead_source, ct.ad_id, ct.ad_media_id, ct.ad_preview_url,"
                    " l.agent_enabled, l.is_blocked,"
                    " l.follower_count, l.following_count, l.last_active_at, b.tz_offset_h"
                    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
                    " JOIN branch b ON b.id = l.branch_id"
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
         lead_source, ad_id, ad_media_id, ad_preview_url, agent_enabled, is_blocked,
         follower_count, following_count, last_active_at, _tz) = info
        set_render_tz(_tz)
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
                         ad_media_id=ad_media_id, ad_preview_url=ad_preview_url,
                         agent_enabled=bool(agent_enabled), is_blocked=bool(is_blocked),
                         follower_count=follower_count, following_count=following_count,
                         last_active_at=last_active_at)
    )


@router.post("/chat/{thread_id}/suggest", response_class=HTMLResponse)
async def chat_suggest(thread_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        if await _guarded_branch(session, thread_id, allowed) is None:
            return HTMLResponse("")
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
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        if await _guarded_branch(session, thread_id, allowed) is None:
            return HTMLResponse("")
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
async def msg_translate_single(thread_id: int, mid: int, request: Request) -> HTMLResponse:
    """Translate a message bubble to Russian (cached in message.tr_text — no re-billing)."""
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        if await _guarded_branch(session, thread_id, allowed) is None:
            return HTMLResponse("")
        try:
            tr = await translate_message(session, mid, BrokerLLM())
        except Exception as exc:
            _log.warning("per-msg translate error tid=%s mid=%s: %s", thread_id, mid, exc)
            orig = (
                await session.execute(
                    text("SELECT text FROM message WHERE id=:mid"), {"mid": mid}
                )
            ).first()
            return HTMLResponse(_h.escape(orig[0]) if orig and orig[0] else "")
    return HTMLResponse(_h.escape(tr) if tr else "")


@router.post("/chat/{thread_id}/msg/{mid}/delete", response_class=HTMLResponse)
async def msg_delete(thread_id: int, mid: int, request: Request) -> HTMLResponse:
    """Retract a message. Outgoing → request an IG unsend (worker revokes, then the
    row disappears); inbound → we can't unsend the lead's message, so only our local
    copy is removed. Never claims a retraction that didn't happen in IG."""
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        if await _guarded_branch(session, thread_id, allowed) is None:
            return HTMLResponse("")
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


@router.post("/chat/{thread_id}/pending/{oid}/delete", response_class=HTMLResponse)
async def pending_delete(thread_id: int, oid: int, request: Request) -> HTMLResponse:
    """Cancel a queued (unsent) reply — mark the outbox row skipped so the sender drops it."""
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        if await _guarded_branch(session, thread_id, allowed) is None:
            return HTMLResponse("")
        await session.execute(
            text("UPDATE outbox SET status='skipped'"
                 " WHERE id=:oid AND thread_id=:tid AND status='pending'"),
            {"oid": oid, "tid": thread_id},
        )
    return HTMLResponse("")  # bubble removed; OOB refresh won't re-add a skipped row


@router.post("/chat/{thread_id}/pending/{oid}/tr", response_class=HTMLResponse)
async def pending_translate(thread_id: int, oid: int, request: Request) -> HTMLResponse:
    """Translate a queued reply to Russian; cache it on outbox.tr_text so the poll keeps it."""
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        if await _guarded_branch(session, thread_id, allowed) is None:
            return HTMLResponse("")
        row = (await session.execute(
            text("SELECT text, tr_text FROM outbox WHERE id=:oid AND thread_id=:tid"),
            {"oid": oid, "tid": thread_id},
        )).first()
        if not row or not row[0]:
            return HTMLResponse("")
        if row[1]:  # already translated (cache) — no LLM call
            return HTMLResponse(f"🌐 {_h.escape(row[1])}")
        try:
            tr = await translate_text(BrokerLLM(), row[0])
        except Exception as exc:
            _log.warning("pending translate error tid=%s oid=%s: %s", thread_id, oid, exc)
            return HTMLResponse("")
        if tr:
            await session.execute(
                text("UPDATE outbox SET tr_text=:t WHERE id=:oid"), {"t": tr, "oid": oid})
    return HTMLResponse(f"🌐 {_h.escape(tr)}" if tr else "")
