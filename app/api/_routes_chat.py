"""Chat panel routes: panel, send, stage, suggest, translate."""
from __future__ import annotations

import html as _h
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import text

from app.adapters.db.models import Outbox, StageEvent, ThreadLog
from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.adapters.notify.telegram import TelegramNotifier
from app.admin._branch import (
    allowed_branch_ids,
    is_branch_forbidden,
    is_super_admin,
    writable_branch_ids,
)
from app.config import settings
from app.modules.conversation.needs import parse_needs
from app.modules.conversation.needs_translate import cached_needs, translated_needs
from app.modules.conversation.translate import (
    target_for_lang,
    translate_message,
    translate_text,
)
from app.modules.knowledge.repository import ProductRepo
from app.modules.notifications.alerts import AlertService
from app.modules.settings.service import get_settings

from ._i18n import apply_lang, current_lang, t
from ._query import (
    fetch_messages,
    fetch_messages_since,
    fetch_pending,
    fetch_thread_events,
)
from ._ui_html import (
    app_shell,
    chat_block_pill_html,
    chat_bot_pill_html,
    chat_header_html,
    chat_panel_html,
    messages_html,
    needs_block_html,
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


_MANUAL_ALERT_KIND = {"ready": "ready_deal", "manager": "needs_manager"}


def _branch_notifier(branch_cfg: object) -> TelegramNotifier | None:
    tok = settings().tg_bot_token
    grp = getattr(branch_cfg, "tg_group_id", "")
    if not tok or not grp:
        return None
    try:
        return TelegramNotifier(bot_token=tok, group_chat_id=int(grp))
    except (ValueError, TypeError):
        return None


def _actor_name(request: Request) -> str:
    """Display name of the acting manager for thread_log entries; 'manager' when auth is
    off (no session) or no name was captured at login."""
    state = getattr(request, "state", None)
    user = getattr(state, "user", None) if state is not None else None
    return str(user.get("nm")) if user and user.get("nm") else "manager"


async def _needs_for(session, lead_id: int, needs: str | None, needs_tr: str | None, lang: str):
    """Auto-translate the lead's captured needs into the current UI language, caching the
    result on lead.needs_tr so re-rendering the header never re-bills the same phrase.

    This calls the broker and therefore blocks on network latency — only the lazy
    /needs endpoint uses it. The main panel render uses cached_needs() instead, which is
    pure cache lookup with no I/O, so opening a chat never waits on the LLM."""
    profile, new_tr = await translated_needs(parse_needs(needs), needs_tr, lang, BrokerLLM())
    if new_tr is not None:
        await session.execute(
            text("UPDATE lead SET needs_tr = :v WHERE id = :id"), {"v": new_tr, "id": lead_id},
        )
        await session.flush()
    return profile


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
    if is_branch_forbidden(row[0], allowed):
        return None
    set_render_tz(row[1])
    return row[0]


async def _build_chat_panel(
    session, thread_id: int, allowed: list[int] | None,
) -> str | None:
    """Panel HTML for one thread, or None if it does not exist / is outside the caller's
    branches. Shared by the partial route (/panel) and the canonical page route."""
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
                " b.tz_offset_h, l.needs, l.id, l.needs_tr, l.manager_note"
                " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
                " JOIN branch b ON b.id = l.branch_id"
                " WHERE ct.id = :tid"
            ),
            {"tid": thread_id},
        )
    ).first()
    if not info or is_branch_forbidden(info[3], allowed):
        return None
    set_render_tz(info[21])
    msgs = await fetch_messages(session, thread_id)
    pending = await fetch_pending(session, thread_id)
    events = await fetch_thread_events(session, thread_id)
    products = [(p.slug, p.title) for p in await ProductRepo(session, info[3]).active()]
    (_, name, stage, _, product_slug, ig_id,
     phone, created_at, last_in_at,
     ig_username, avatar_url,
     lead_source, ad_id, ad_media_id, ad_preview_url, agent_enabled, is_blocked,
     follower_count, following_count, last_active_at, lead_seen_at, _tz, needs,
     lead_id, needs_tr, manager_note) = info
    needs_profile, needs_pending = cached_needs(parse_needs(needs), needs_tr, current_lang())
    return chat_panel_html(
        thread_id, str(name or "Lead"), str(stage or "new"), msgs, pending,
        product_slug=product_slug, ig_id=ig_id,
        phone=phone, created_at=created_at, last_in_at=last_in_at,
        ig_username=ig_username, avatar_url=avatar_url,
        lead_source=lead_source, ad_id=ad_id,
        ad_media_id=ad_media_id, ad_preview_url=ad_preview_url,
        agent_enabled=bool(agent_enabled), is_blocked=bool(is_blocked),
        follower_count=follower_count, following_count=following_count,
        last_active_at=last_active_at, lead_seen_at=lead_seen_at, needs=needs_profile,
        needs_pending=needs_pending, events=events, products=products,
        manager_note=manager_note,
    )


@router.get("/chat/{thread_id}/panel", response_class=HTMLResponse)
async def chat_panel(thread_id: int, request: Request) -> HTMLResponse:
    """Bare panel partial (HTMX target). See chat_page for the shareable full-page URL."""
    apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        html = await _build_chat_panel(session, thread_id, allowed)
    if html is None:
        return HTMLResponse('<div class="emp">Thread not found</div>', status_code=404)
    return HTMLResponse(html)


@router.get("/chat/{thread_id}", response_class=HTMLResponse)
async def chat_page(
    thread_id: int, request: Request,
    stage: str = "", lead_type: str = "", ad_id: str = "", grp: str = "", audience: str = "",
) -> HTMLResponse:
    """Canonical, shareable chat URL. HTMX (HX-Request) gets the bare panel; a direct load,
    F5, or pasted link gets the full app shell with this chat open and highlighted. The inbox
    filter (stage/lead_type/audience/ad_id/grp) rides along in the query so a full reload keeps
    the filtered thread list instead of reverting to the whole inbox."""
    lang = apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        html = await _build_chat_panel(session, thread_id, allowed)
    is_hx = request.headers.get("HX-Request") == "true"
    if html is None:
        if is_hx:
            return HTMLResponse('<div class="emp">Thread not found</div>', status_code=404)
        return RedirectResponse("/ui/inbox", status_code=303)
    if is_hx:
        return HTMLResponse(html)
    resp = HTMLResponse(app_shell(
        lang, html, active_nav="inbox", is_super=is_super_admin(request),
        stage=stage.strip(), lead_type=lead_type.strip(), audience=audience.strip(),
        ad_id=ad_id.strip(), grp=grp.strip()))
    resp.set_cookie("stepan2_open_thread", str(thread_id), max_age=86400, samesite="lax")
    return resp


@router.get("/chat/{thread_id}/needs", response_class=HTMLResponse)
async def chat_needs_lazy(thread_id: int, request: Request) -> HTMLResponse:
    """Lazily translate the lead's needs box. The panel renders instantly from cache
    (see cached_needs in _build_chat_panel) and only loads this route — which calls the
    broker — when cached_needs found an untranslated phrase; hx-swap replaces the box in
    place once the broker responds, so opening a chat never blocks on LLM latency."""
    lang = apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        row = (
            await session.execute(
                text(
                    "SELECT l.branch_id, l.needs, l.needs_tr, l.id FROM channel_thread ct"
                    " JOIN lead l ON l.id = ct.lead_id WHERE ct.id = :tid"
                ),
                {"tid": thread_id},
            )
        ).first()
        if not row or is_branch_forbidden(row[0], allowed):
            return HTMLResponse("")
        _, needs, needs_tr, lead_id = row
        profile = await _needs_for(session, lead_id, needs, needs_tr, lang)
    return HTMLResponse(needs_block_html(profile, thread_id))


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
    if is_branch_forbidden(row[3], allowed):
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
    allowed = writable_branch_ids(request)  # write route: enforce WRITE role for the branch
    async with session_scope() as session:
        branch_id = await _guarded_branch(session, thread_id, allowed)
        if branch_id is None or not text_body:
            return await _rerender_feed(session, thread_id)
        session.add(Outbox(
            branch_id=branch_id, thread_id=thread_id, text=text_body, source=src,
            llm_info=(llm_info if src == "agent" else None),
        ))
        await session.flush()
        return await _rerender_feed(session, thread_id)


@router.get(
    "/chat/{thread_id}/since/{after_id}/{after_stage_id}/{after_log_id}",
    response_class=HTMLResponse,
)
async def chat_since(
    thread_id: int, after_id: int, after_stage_id: int, after_log_id: int, request: Request,
) -> HTMLResponse:
    """Return only message bubbles/system-log lines newer than the three cursors, plus a
    fresh poll sentinel."""
    apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        if await _guarded_branch(session, thread_id, allowed) is None:
            return HTMLResponse("")
        rows = await fetch_messages_since(session, thread_id, after_id)
        pending = await fetch_pending(session, thread_id)
        events = await fetch_thread_events(session, thread_id, after_stage_id, after_log_id)
    return HTMLResponse(
        since_bubbles_html(
            list(rows), thread_id, after_id, pending=pending, events=list(events),
            after_stage_id=after_stage_id, after_log_id=after_log_id,
        )
    )


@router.post("/chat/{thread_id}/bot-toggle", response_class=HTMLResponse)
async def chat_bot_toggle(thread_id: int, request: Request) -> HTMLResponse:
    """Flip the per-lead agent_enabled flag for this thread's lead; re-render the pill."""
    apply_lang(request)
    allowed = writable_branch_ids(request)  # write route: enforce WRITE role for the branch
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
        if is_branch_forbidden(branch_id, allowed):
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
    allowed = writable_branch_ids(request)  # write route: enforce WRITE role for the branch
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
        if is_branch_forbidden(branch_id, allowed):
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


async def _rerender_feed(session, thread_id: int) -> HTMLResponse:
    msgs = await fetch_messages(session, thread_id)
    pending = await fetch_pending(session, thread_id)
    events = await fetch_thread_events(session, thread_id)
    return HTMLResponse(messages_html(msgs, pending, thread_id, events=events))


@router.post("/chat/{thread_id}/clear", response_class=HTMLResponse)
async def chat_clear(thread_id: int, request: Request) -> HTMLResponse:
    """Clear context: mark everything up to now as OUT of Stepan's context. Messages stay
    in the DB and in the chat window (greyed) — Stepan just stops feeding them to the LLM."""
    apply_lang(request)
    allowed = writable_branch_ids(request)  # write route: enforce WRITE role for the branch
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_scope() as session:
        branch_id = await _guarded_branch(session, thread_id, allowed)
        if branch_id is None:
            return HTMLResponse("")
        await session.execute(
            text("UPDATE channel_thread SET context_cleared_at=:t WHERE id=:tid"),
            {"t": now, "tid": thread_id},
        )
        session.add(ThreadLog(
            branch_id=branch_id, thread_id=thread_id, kind="context_cleared",
            actor=_actor_name(request),
        ))
        await session.flush()
        return await _rerender_feed(session, thread_id)


@router.post("/chat/{thread_id}/load-context", response_class=HTMLResponse)
async def chat_load_context(thread_id: int, request: Request) -> HTMLResponse:
    """Bring the full context back: un-grey every message (context_cleared_at=NULL) so it
    re-enters Stepan's context. Stored rows are only un-flagged — nothing is re-fetched or
    duplicated; new IG messages keep arriving via the normal ingest."""
    apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        branch_id = await _guarded_branch(session, thread_id, allowed)
        if branch_id is None:
            return HTMLResponse("")
        await session.execute(
            text("UPDATE channel_thread SET context_cleared_at=NULL WHERE id=:tid"),
            {"tid": thread_id},
        )
        session.add(ThreadLog(
            branch_id=branch_id, thread_id=thread_id, kind="context_loaded",
            actor=_actor_name(request),
        ))
        await session.flush()
        return await _rerender_feed(session, thread_id)


@router.post("/chat/{thread_id}/stage", response_class=HTMLResponse)
async def chat_stage(
    thread_id: int, request: Request, stage: str = Form(default="new"),
) -> HTMLResponse:
    apply_lang(request)
    if stage not in _VALID_STAGES:
        stage = "new"
    allowed = writable_branch_ids(request)  # write route: enforce WRITE role for the branch
    async with session_scope() as session:
        branch_id = await _guarded_branch(session, thread_id, allowed)
        if branch_id is None:
            return HTMLResponse('<div class="emp">Thread not found</div>', status_code=404)
        info = (
            await session.execute(
                text(
                    "SELECT ct.id, l.display_name, l.id as lead_id, l.stage as old_stage,"
                    " ct.product_slug, ct.external_thread_id,"
                    " l.phone_e164, l.created_at, ct.last_in_at,"
                    " l.ig_username, l.avatar_url,"
                    " ct.lead_source, ct.ad_id, ct.ad_media_id, ct.ad_preview_url,"
                    " l.agent_enabled, l.is_blocked,"
                    " l.follower_count, l.following_count, l.last_active_at, b.tz_offset_h,"
                    " l.needs, l.needs_tr, l.manager_note"
                    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
                    " JOIN branch b ON b.id = l.branch_id"
                    " WHERE ct.id = :tid"
                ),
                {"tid": thread_id},
            )
        ).first()
        if not info:
            return HTMLResponse('<div class="emp">Thread not found</div>', status_code=404)
        (_, name, lead_id, old_stage, product_slug, ig_id,
         phone, created_at, last_in_at,
         ig_username, avatar_url,
         lead_source, ad_id, ad_media_id, ad_preview_url, agent_enabled, is_blocked,
         follower_count, following_count, last_active_at, _tz, needs, needs_tr,
         manager_note) = info
        set_render_tz(_tz)
        products = [(pr.slug, pr.title) for pr in await ProductRepo(session, branch_id).active()]
        if stage != str(old_stage):
            await session.execute(
                text("UPDATE lead SET stage = :s WHERE id = :id"),
                {"s": stage, "id": lead_id},
            )
            session.add(StageEvent(
                branch_id=branch_id, lead_id=lead_id, thread_id=thread_id,
                from_stage=str(old_stage), to_stage=stage,
                actor=_actor_name(request), reason="manual",
            ))
            await session.flush()
            # Same rule as the bot: a move into READY / MANAGER pings the manager. Other
            # manual moves are silent (just the history line above).
            if stage in _MANUAL_ALERT_KIND:
                cfg = await get_settings(session, branch_id)
                who = _actor_name(request)
                try:
                    await AlertService(session, branch_id, _branch_notifier(cfg)).raise_alert(
                        lead_id=lead_id, kind=_MANUAL_ALERT_KIND[stage],
                        summary_en=f"Manager {who} moved the lead to {stage}",
                        summary_ru=f"Менеджер {who} перевёл лида в стадию {stage}",
                        thread_id=thread_id, lead_phone=phone,
                    )
                except Exception:
                    _log.warning("manual stage alert failed tid=%s", thread_id, exc_info=True)
        needs_profile, needs_pending = cached_needs(parse_needs(needs), needs_tr, current_lang())
    return HTMLResponse(
        chat_header_html(thread_id, str(name or "Lead"), stage,
                         product_slug=product_slug, ig_id=ig_id,
                         phone=phone, created_at=created_at, last_in_at=last_in_at,
                         ig_username=ig_username, avatar_url=avatar_url,
                         lead_source=lead_source, ad_id=ad_id,
                         ad_media_id=ad_media_id, ad_preview_url=ad_preview_url,
                         agent_enabled=bool(agent_enabled), is_blocked=bool(is_blocked),
                         follower_count=follower_count, following_count=following_count,
                         last_active_at=last_active_at, needs=needs_profile,
                         needs_pending=needs_pending, products=products,
                         manager_note=manager_note)
    )


@router.post("/chat/{thread_id}/product", response_class=HTMLResponse)
async def chat_product(
    thread_id: int, request: Request, product: str = Form(default=""),
) -> HTMLResponse:
    """Manager re-binds the thread's product; the change is logged to the chat history.

    product_slug biases which product card the bot foregrounds in its prompt (soft steer,
    not a hard filter). Empty value clears the binding."""
    apply_lang(request)
    new_slug = product.strip() or None
    allowed = writable_branch_ids(request)  # write route: enforce WRITE role for the branch
    async with session_scope() as session:
        branch_id = await _guarded_branch(session, thread_id, allowed)
        if branch_id is None:
            return HTMLResponse('<div class="emp">Thread not found</div>', status_code=404)
        info = (
            await session.execute(
                text(
                    "SELECT ct.id, l.display_name, l.stage, ct.product_slug,"
                    " ct.external_thread_id, l.phone_e164, l.created_at, ct.last_in_at,"
                    " l.ig_username, l.avatar_url,"
                    " ct.lead_source, ct.ad_id, ct.ad_media_id, ct.ad_preview_url,"
                    " l.agent_enabled, l.is_blocked,"
                    " l.follower_count, l.following_count, l.last_active_at, b.tz_offset_h,"
                    " l.needs, l.id, l.needs_tr, l.manager_note"
                    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
                    " JOIN branch b ON b.id = l.branch_id WHERE ct.id = :tid"
                ),
                {"tid": thread_id},
            )
        ).first()
        if not info:
            return HTMLResponse('<div class="emp">Thread not found</div>', status_code=404)
        (_, name, stage, old_slug, ig_id,
         phone, created_at, last_in_at,
         ig_username, avatar_url,
         lead_source, ad_id, ad_media_id, ad_preview_url, agent_enabled, is_blocked,
         follower_count, following_count, last_active_at, _tz, needs,
         lead_id, needs_tr, manager_note) = info
        set_render_tz(_tz)
        products = [(pr.slug, pr.title) for pr in await ProductRepo(session, branch_id).active()]
        if new_slug is not None and new_slug not in {sl for sl, _ in products}:
            new_slug = old_slug  # ignore a slug that isn't an active product of this branch
        if new_slug != old_slug:
            await session.execute(
                text("UPDATE channel_thread SET product_slug = :p,"
                     " product_source = 'manager' WHERE id = :tid"),
                {"p": new_slug, "tid": thread_id},
            )
            session.add(ThreadLog(
                branch_id=branch_id, thread_id=thread_id, kind="product_changed",
                detail=f"{old_slug or '∅'} → {new_slug or '∅'}",
                actor=_actor_name(request),
            ))
            await session.flush()
        needs_profile, needs_pending = cached_needs(parse_needs(needs), needs_tr, current_lang())
    return HTMLResponse(
        chat_header_html(thread_id, str(name or "Lead"), str(stage or "new"),
                         product_slug=new_slug, ig_id=ig_id,
                         phone=phone, created_at=created_at, last_in_at=last_in_at,
                         ig_username=ig_username, avatar_url=avatar_url,
                         lead_source=lead_source, ad_id=ad_id,
                         ad_media_id=ad_media_id, ad_preview_url=ad_preview_url,
                         agent_enabled=bool(agent_enabled), is_blocked=bool(is_blocked),
                         follower_count=follower_count, following_count=following_count,
                         last_active_at=last_active_at, needs=needs_profile,
                         needs_pending=needs_pending, products=products,
                         manager_note=manager_note)
    )


@router.post("/chat/{thread_id}/manager-note", response_class=HTMLResponse)
async def chat_manager_note(
    thread_id: int, request: Request, note: str = Form(default=""),
) -> HTMLResponse:
    """Save (or clear, on empty) a per-LEAD manager override note — injected into Stepan's
    prompt every turn until cleared (see prompt.manager_note_block). Distinct from the
    branch-wide CoachingNote: closes the gap where a manager manually demotes a wrongly-
    ready lead but has no way to tell the bot WHY, so it just marks ready=true again."""
    apply_lang(request)
    cleaned = note.strip() or None
    allowed = writable_branch_ids(request)  # write route: enforce WRITE role for the branch
    async with session_scope() as session:
        branch_id = await _guarded_branch(session, thread_id, allowed)
        if branch_id is None:
            return HTMLResponse('<div class="emp">Thread not found</div>', status_code=404)
        await session.execute(
            text(
                "UPDATE lead SET manager_note = :n, manager_note_by = :who,"
                " manager_note_at = :now"
                " WHERE id = (SELECT lead_id FROM channel_thread WHERE id = :tid)"
            ),
            {"n": cleaned, "who": _actor_name(request),
             "now": datetime.now(UTC).replace(tzinfo=None), "tid": thread_id},
        )
        await session.flush()
        info = (
            await session.execute(
                text(
                    "SELECT l.display_name, l.stage, ct.product_slug, ct.external_thread_id,"
                    " l.phone_e164, l.created_at, ct.last_in_at,"
                    " l.ig_username, l.avatar_url,"
                    " ct.lead_source, ct.ad_id, ct.ad_media_id, ct.ad_preview_url,"
                    " l.agent_enabled, l.is_blocked,"
                    " l.follower_count, l.following_count, l.last_active_at,"
                    " l.needs, l.needs_tr"
                    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
                    " WHERE ct.id = :tid"
                ),
                {"tid": thread_id},
            )
        ).first()
        if not info:
            return HTMLResponse('<div class="emp">Thread not found</div>', status_code=404)
        (name, stage, product_slug, ig_id, phone, created_at, last_in_at,
         ig_username, avatar_url, lead_source, ad_id, ad_media_id, ad_preview_url,
         agent_enabled, is_blocked, follower_count, following_count, last_active_at,
         needs, needs_tr) = info
        products = [(pr.slug, pr.title) for pr in await ProductRepo(session, branch_id).active()]
        needs_profile, needs_pending = cached_needs(parse_needs(needs), needs_tr, current_lang())
    return HTMLResponse(
        chat_header_html(thread_id, str(name or "Lead"), str(stage or "new"),
                         product_slug=product_slug, ig_id=ig_id,
                         phone=phone, created_at=created_at, last_in_at=last_in_at,
                         ig_username=ig_username, avatar_url=avatar_url,
                         lead_source=lead_source, ad_id=ad_id,
                         ad_media_id=ad_media_id, ad_preview_url=ad_preview_url,
                         agent_enabled=bool(agent_enabled), is_blocked=bool(is_blocked),
                         follower_count=follower_count, following_count=following_count,
                         last_active_at=last_active_at, needs=needs_profile,
                         needs_pending=needs_pending, products=products,
                         manager_note=cleaned)
    )


@router.post("/chat/{thread_id}/suggest", response_class=HTMLResponse)
async def chat_suggest(thread_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    allowed = writable_branch_ids(request)  # write route: enforce WRITE role for the branch
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
        draft, _ = await llm.chat(llm_msgs, capability="chat:fast", max_tokens=300,
                                  workflow="suggest", thread_id=thread_id)
    except Exception as exc:
        _log.warning("suggest LLM error tid=%s: %s", thread_id, exc)
        draft = ""
    return HTMLResponse(suggest_box_html(thread_id, draft.strip()))


@router.post("/chat/{thread_id}/tr-draft", response_class=HTMLResponse)
async def chat_tr_draft(
    thread_id: int, request: Request, text_body: str = Form(alias="text"),
) -> HTMLResponse:
    """Translate an arbitrary draft (the Suggest textarea) to the manager's language."""
    lang_code = apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        if await _guarded_branch(session, thread_id, allowed) is None:
            return HTMLResponse("")
    target = target_for_lang(lang_code)
    try:
        tr = await translate_text(BrokerLLM(), text_body, target=target)
    except Exception as exc:
        _log.warning("draft translate error tid=%s: %s", thread_id, exc)
        return HTMLResponse("")
    return HTMLResponse(f"🌐 {_h.escape(tr)}" if tr else "")


_SUMMARY_MAX_MSGS = 60


@router.post("/chat/{thread_id}/translate", response_class=HTMLResponse)
async def chat_translate(thread_id: int, request: Request) -> HTMLResponse:
    """Summarize the WHOLE conversation in the current UI language (toolbar button).

    Not a single-message translation: it condenses the dialog so a manager who does not
    read the lead's language gets the gist. Target language follows the interface language."""
    lang_code = apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        if await _guarded_branch(session, thread_id, allowed) is None:
            return HTMLResponse("")
        rows = (
            await session.execute(
                text(
                    "SELECT direction, text FROM message"
                    " WHERE thread_id = :tid AND text <> ''"
                    " ORDER BY occurred_at DESC, id DESC LIMIT :lim"
                ),
                {"tid": thread_id, "lim": _SUMMARY_MAX_MSGS},
            )
        ).all()
    if not rows:
        return HTMLResponse("")
    convo = "\n".join(
        f"{'Lead' if r[0] == 'in' else 'Agent'}: {(r[1] or '').strip()}"
        for r in reversed(rows) if (r[1] or "").strip()
    )[:6000]
    target_lang = {
        "ru": "Russian", "en": "English", "id": "Indonesian",
    }.get(lang_code, "English")
    llm_msgs = [
        {
            "role": "system",
            "content": (
                f"Summarize this sales conversation in {target_lang}. Cover what the lead "
                "wants, key objections, and the current state. 3-6 short sentences, no "
                "preamble. Return ONLY the summary."
            ),
        },
        {"role": "user", "content": convo},
    ]
    llm = BrokerLLM()
    try:
        summary, _ = await llm.chat(llm_msgs, capability="chat:fast", max_tokens=500,
                                    workflow="translate", thread_id=thread_id)
    except Exception as exc:
        _log.warning("chat summary LLM error tid=%s: %s", thread_id, exc)
        summary = ""
    if not summary.strip():
        return HTMLResponse("")
    tr_lbl = _h.escape(t("chat.tr_result"))
    return HTMLResponse(
        f'<div style="position:relative;padding:.3rem 1.6rem .3rem .75rem;font-size:.76rem;'
        f'color:#8899aa;background:#141925;border-top:1px solid #2d3748;white-space:pre-wrap">'
        f'<button onclick="trClose({thread_id})" title="Close"'
        f' style="position:absolute;top:.2rem;right:.4rem;background:none;border:none;'
        f'color:#6b7685;font-size:1rem;line-height:1;cursor:pointer">×</button>'
        f'<span style="color:#4a5568">{tr_lbl}</span>'
        f' {_h.escape(summary.strip())}</div>'
    )


@router.get("/chat/{thread_id}/msg/{mid}/tr", response_class=HTMLResponse)
async def msg_translate_single(thread_id: int, mid: int, request: Request) -> HTMLResponse:
    """Translate a message bubble to the viewer's UI language (cached in message.tr_text —
    no re-billing). NOTE: the cache is a single column, not keyed by language — if operators
    view in different UI languages, the second viewer's request can hit a cache written for
    a different target than their own. Not solved here (would need a schema change);
    today's actual usage is Russian-only in practice, so it isn't yet a live problem."""
    lang_code = apply_lang(request)
    allowed = allowed_branch_ids(request)
    async with session_scope() as session:
        if await _guarded_branch(session, thread_id, allowed) is None:
            return HTMLResponse("")
        try:
            tr = await translate_message(
                session, mid, BrokerLLM(), target=target_for_lang(lang_code))
        except Exception as exc:
            _log.warning("per-msg translate error tid=%s mid=%s: %s", thread_id, mid, exc)
            return HTMLResponse("")  # empty → JS leaves the bubble as-is, lets the user retry
    return HTMLResponse(_h.escape(tr) if tr else "")


@router.post("/chat/{thread_id}/msg/{mid}/delete", response_class=HTMLResponse)
async def msg_delete(thread_id: int, mid: int, request: Request) -> HTMLResponse:
    """Retract a message. Outgoing → request an IG unsend (worker revokes, then the
    row disappears); inbound → we can't unsend the lead's message, so only our local
    copy is removed. Never claims a retraction that didn't happen in IG."""
    allowed = writable_branch_ids(request)  # write route: enforce WRITE role for the branch
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
        # rewind last_in_at so the sidebar re-sorts by real last-activity — a direct
        # inbound delete used to leave it stale, so the chat list kept the old order.
        await session.execute(
            text("UPDATE channel_thread SET last_in_at="
                 "(SELECT max(occurred_at) FROM message WHERE thread_id=:tid AND direction='in')"
                 " WHERE id=:tid"),
            {"tid": thread_id},
        )
    return HTMLResponse("")


@router.post("/chat/{thread_id}/pending/{oid}/delete", response_class=HTMLResponse)
async def pending_delete(thread_id: int, oid: int, request: Request) -> HTMLResponse:
    """Cancel a queued (unsent) reply — mark the outbox row skipped so the sender drops it."""
    allowed = writable_branch_ids(request)  # write route: enforce WRITE role for the branch
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
    """Translate a queued reply to the viewer's UI language; cache it on outbox.tr_text so
    the poll keeps it."""
    lang_code = apply_lang(request)
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
            tr = await translate_text(BrokerLLM(), row[0], target=target_for_lang(lang_code))
        except Exception as exc:
            _log.warning("pending translate error tid=%s oid=%s: %s", thread_id, oid, exc)
            return HTMLResponse("")
        if tr:
            await session.execute(
                text("UPDATE outbox SET tr_text=:t WHERE id=:oid"), {"t": tr, "oid": oid})
    return HTMLResponse(f"🌐 {_h.escape(tr)}" if tr else "")
