"""Manager UI — 3-column layout (sidebar + thread list + panel).

Routes:
  GET  /ui/inbox                  — full page shell
  GET  /ui/threads                — HTMX: thread list partial
  GET  /ui/chat/{id}/panel        — HTMX: chat panel partial
  POST /ui/chat/{id}/send         — HTMX: send message, returns updated messages HTML
  GET  /ui/coach                  — full page (coach active)
  GET  /ui/coach/panel            — HTMX: coach chat panel
  POST /ui/coach/say              — HTMX: propose KB edit, returns chat bubble pair
  POST /ui/coach/apply/{id}       — apply edit, redirect
  POST /ui/coach/cancel/{id}      — cancel edit, redirect
  GET  /ui/knowledge/panel        — HTMX: KB doc list
  GET  /ui/knowledge/{id}/edit    — HTMX: KB doc edit form
  POST /ui/knowledge/{id}/save    — HTMX: save KB doc, returns edit panel
  GET  /ui/products/panel         — HTMX: products list
  GET  /ui/members/panel          — HTMX: members list (with user names)
  GET  /ui/settings/panel         — HTMX: settings list
  POST /ui/settings/{id}/save     — HTMX: save one setting, returns updated form row
  GET  /ui/lang/{code}            — set language cookie, redirect back
"""
from __future__ import annotations

import html as _h

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from starlette.responses import Response

from app.adapters.db.models import Outbox
from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.admin._branch import branch_ids_from_request
from app.modules.conversation.coach_service import apply_edit, cancel_edit, propose_edit

from ._i18n import LANG_COOKIE, LANGS, apply_lang, t
from ._ui_html import (
    app_shell,
    chat_header_html,
    chat_panel_html,
    messages_html,
    suggest_box_html,
    thread_list_html,
)
from ._ui_panels import (
    _coach_pair,
    coach_chat_html,
    knowledge_edit_html,
    knowledge_new_html,
    knowledge_panel_html,
    leads_panel_html,
    members_panel_html,
    outbox_panel_html,
    product_edit_html,
    products_panel_html,
    settings_panel_html,
)

router = APIRouter(prefix="/ui")

_THREAD_TMPL = (
    "SELECT ct.id, l.display_name, l.stage, ct.last_in_at,"
    " (SELECT m.text FROM message m WHERE m.thread_id = ct.id"
    "  ORDER BY m.occurred_at DESC, m.id DESC LIMIT 1) last_msg,"
    " (SELECT m.direction FROM message m WHERE m.thread_id = ct.id"
    "  ORDER BY m.occurred_at DESC, m.id DESC LIMIT 1) last_dir"
    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
    " {where}"
    " ORDER BY COALESCE(ct.last_in_at, ct.created_at) DESC LIMIT 100"
)


async def _fetch_threads(session, branch_ids: list[int] | None) -> list:
    if branch_ids:
        rows = await session.execute(
            text(_THREAD_TMPL.format(where="WHERE l.branch_id = ANY(:bids)")),
            {"bids": branch_ids},
        )
    else:
        rows = await session.execute(text(_THREAD_TMPL.format(where="")))
    return rows.all()


async def _coach_data(session, branch_id: int) -> tuple[list, list]:
    """Fetch coaching edits (ASC) and active notes for a branch."""
    edits = (
        await session.execute(
            text(
                "SELECT id, request, status, slug, old_text, new_text, summary, created_at"
                " FROM coaching_edit WHERE branch_id = :bid ORDER BY id ASC LIMIT 60"
            ),
            {"bid": branch_id},
        )
    ).all()
    notes = (
        await session.execute(
            text(
                "SELECT id, text FROM coaching_note"
                " WHERE branch_id = :bid AND active = true ORDER BY id"
            ),
            {"bid": branch_id},
        )
    ).all()
    return list(edits), list(notes)


# ─── full pages ───────────────────────────────────────────────────────────────

@router.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request) -> HTMLResponse:
    lang = apply_lang(request)
    empty = '<div class="emp">Select a conversation</div>'
    return HTMLResponse(app_shell(lang, empty, active_nav="inbox"))


@router.get("/coach", response_class=HTMLResponse)
async def coach_page(request: Request) -> HTMLResponse:
    lang = apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    branch_id = branch_ids[0] if branch_ids else 1
    async with session_scope() as session:
        edits, notes = await _coach_data(session, branch_id)
    panel = coach_chat_html(branch_id, edits, notes)
    return HTMLResponse(app_shell(lang, panel, active_nav="coach"))


# ─── HTMX partials ────────────────────────────────────────────────────────────

@router.get("/threads", response_class=HTMLResponse)
async def threads_partial(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        rows = await _fetch_threads(session, branch_ids)
    return HTMLResponse(thread_list_html(rows))


@router.get("/chat/{thread_id}/panel", response_class=HTMLResponse)
async def chat_panel(thread_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        info = (
            await session.execute(
                text(
                    "SELECT ct.id, l.display_name, l.stage, l.branch_id"
                    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id WHERE ct.id = :tid"
                ),
                {"tid": thread_id},
            )
        ).first()
        if not info:
            return HTMLResponse('<div class="emp">Thread not found</div>', status_code=404)
        msgs = (
            await session.execute(
                text(
                    "SELECT id, direction, sent_by, text, occurred_at FROM message"
                    " WHERE thread_id = :tid ORDER BY occurred_at, id"
                ),
                {"tid": thread_id},
            )
        ).all()
        pending = (
            await session.execute(
                text(
                    "SELECT id, text, scheduled_at FROM outbox"
                    " WHERE thread_id = :tid AND status = 'pending' ORDER BY id"
                ),
                {"tid": thread_id},
            )
        ).all()
    _, name, stage, _ = info
    return HTMLResponse(
        chat_panel_html(thread_id, str(name or "Lead"), str(stage or "new"), msgs, pending)
    )


@router.post("/chat/{thread_id}/send", response_class=HTMLResponse)
async def chat_send(
    thread_id: int, request: Request, text_body: str = Form(alias="text")
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
            msgs = (
                await session.execute(
                    text(
                        "SELECT id, direction, sent_by, text, occurred_at FROM message"
                        " WHERE thread_id = :tid ORDER BY occurred_at, id"
                    ),
                    {"tid": thread_id},
                )
            ).all()
            return HTMLResponse(messages_html(msgs, [], thread_id))
        session.add(Outbox(
            branch_id=info[0], thread_id=thread_id, text=text_body, source="manager",
        ))
        await session.flush()
        msgs = (
            await session.execute(
                text(
                    "SELECT id, direction, sent_by, text, occurred_at FROM message"
                    " WHERE thread_id = :tid ORDER BY occurred_at, id"
                ),
                {"tid": thread_id},
            )
        ).all()
        pending = (
            await session.execute(
                text(
                    "SELECT id, text, scheduled_at FROM outbox"
                    " WHERE thread_id = :tid AND status = 'pending' ORDER BY id"
                ),
                {"tid": thread_id},
            )
        ).all()
    return HTMLResponse(messages_html(msgs, pending, thread_id))


# ─── coach ────────────────────────────────────────────────────────────────────

@router.get("/coach/panel", response_class=HTMLResponse)
async def coach_panel_partial(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    branch_id = branch_ids[0] if branch_ids else 1
    async with session_scope() as session:
        edits, notes = await _coach_data(session, branch_id)
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


# ─── knowledge ────────────────────────────────────────────────────────────────

@router.get("/knowledge/panel", response_class=HTMLResponse)
async def knowledge_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        where = "WHERE branch_id = ANY(:bids)" if branch_ids else ""
        params = {"bids": branch_ids} if branch_ids else {}
        q = f"SELECT id, slug, title, content FROM knowledge_doc {where} ORDER BY id"  # noqa: S608
        docs = (await session.execute(text(q), params)).all()
    return HTMLResponse(knowledge_panel_html(list(docs)))


@router.get("/knowledge/{doc_id}/edit", response_class=HTMLResponse)
async def knowledge_edit(doc_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        row = (
            await session.execute(
                text("SELECT id, slug, title, content FROM knowledge_doc WHERE id = :id"),
                {"id": doc_id},
            )
        ).first()
    if not row:
        return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
    html = knowledge_edit_html(row[0], str(row[1]), str(row[2] or ""), str(row[3] or ""))
    return HTMLResponse(html)


@router.post("/knowledge/{doc_id}/save", response_class=HTMLResponse)
async def knowledge_save(
    doc_id: int,
    request: Request,
    title: str = Form(default=""),
    content: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        await session.execute(
            text("UPDATE knowledge_doc SET title = :t, content = :c WHERE id = :id"),
            {"t": title.strip(), "c": content.strip(), "id": doc_id},
        )
        row = (
            await session.execute(
                text("SELECT id, slug, title, content FROM knowledge_doc WHERE id = :id"),
                {"id": doc_id},
            )
        ).first()
    if not row:
        return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
    return HTMLResponse(
        knowledge_edit_html(row[0], str(row[1]), str(row[2] or ""), str(row[3] or ""))
    )


# ─── products ─────────────────────────────────────────────────────────────────

@router.get("/products/panel", response_class=HTMLResponse)
async def products_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        where = "WHERE branch_id = ANY(:bids)" if branch_ids else ""
        params = {"bids": branch_ids} if branch_ids else {}
        q = (
            "SELECT id, slug, title, is_active, sort_order"  # noqa: S608
            f" FROM product {where} ORDER BY sort_order, id"
        )
        rows = (await session.execute(text(q), params)).all()
    return HTMLResponse(products_panel_html(list(rows)))


# ─── members ──────────────────────────────────────────────────────────────────

@router.get("/members/panel", response_class=HTMLResponse)
async def members_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        where = "WHERE m.branch_id = ANY(:bids)" if branch_ids else ""
        params = {"bids": branch_ids} if branch_ids else {}
        q = (
            "SELECT u.id, u.telegram_id, m.role, u.name, m.branch_id"  # noqa: S608
            " FROM membership m JOIN app_user u ON u.id = m.user_id"
            f" {where} ORDER BY m.branch_id, m.role, u.name"
        )
        rows = (await session.execute(text(q), params)).all()
    return HTMLResponse(members_panel_html(list(rows)))


# ─── settings ─────────────────────────────────────────────────────────────────

@router.get("/settings/panel", response_class=HTMLResponse)
async def settings_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        where = "WHERE branch_id = ANY(:bids)" if branch_ids else ""
        params = {"bids": branch_ids} if branch_ids else {}
        q = f"SELECT id, branch_id, key, value FROM app_setting {where} ORDER BY key"  # noqa: S608
        rows = (await session.execute(text(q), params)).all()
    return HTMLResponse(settings_panel_html(list(rows)))


@router.post("/settings/{setting_id}/save", response_class=HTMLResponse)
async def settings_save(
    setting_id: int,
    request: Request,
    value: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        await session.execute(
            text("UPDATE app_setting SET value = :v WHERE id = :id"),
            {"v": value.strip(), "id": setting_id},
        )
        row = (
            await session.execute(
                text("SELECT id, branch_id, key, value FROM app_setting WHERE id = :id"),
                {"id": setting_id},
            )
        ).first()
    if not row:
        return HTMLResponse("—")
    val = str(row[3])
    save_lbl = _h.escape(t("set.save"))
    saved_lbl = _h.escape(t("set.saved"))
    return HTMLResponse(
        f'<form hx-post="/ui/settings/{setting_id}/save" hx-target="this"'
        f' hx-swap="outerHTML" style="display:flex;gap:.35rem;align-items:center">'
        f'<input class="set-val" name="value" value="{_h.escape(val)}">'
        f'<button class="btn-sm btn-p">{save_lbl}</button>'
        f'<span style="color:#51cf66;font-size:.75rem">{saved_lbl}</span>'
        f'</form>'
    )


# ─── leads ────────────────────────────────────────────────────────────────────

@router.get("/leads/panel", response_class=HTMLResponse)
async def leads_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        where = "WHERE branch_id = ANY(:bids)" if branch_ids else ""
        params = {"bids": branch_ids} if branch_ids else {}
        q = (
            "SELECT id, display_name, phone_e164, stage, created_at"  # noqa: S608
            f" FROM lead {where} ORDER BY created_at DESC LIMIT 200"
        )
        rows = (await session.execute(text(q), params)).all()
    return HTMLResponse(leads_panel_html(list(rows)))


# ─── outbox ───────────────────────────────────────────────────────────────────

@router.get("/outbox/panel", response_class=HTMLResponse)
async def outbox_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        where = "WHERE branch_id = ANY(:bids)" if branch_ids else ""
        params = {"bids": branch_ids} if branch_ids else {}
        q = (
            "SELECT id, thread_id, status, source, text, scheduled_at"  # noqa: S608
            f" FROM outbox {where} ORDER BY id DESC LIMIT 100"
        )
        rows = (await session.execute(text(q), params)).all()
    return HTMLResponse(outbox_panel_html(list(rows)))


# ─── chat: stage change + suggest ─────────────────────────────────────────────

@router.post("/chat/{thread_id}/stage", response_class=HTMLResponse)
async def chat_stage(
    thread_id: int, request: Request, stage: str = Form(default="new"),
) -> HTMLResponse:
    apply_lang(request)
    _VALID = {"new", "qualifying", "presenting", "objection",
               "ready", "handed_off", "dormant", "manager"}
    if stage not in _VALID:
        stage = "new"
    async with session_scope() as session:
        info = (
            await session.execute(
                text(
                    "SELECT ct.id, l.display_name, l.id as lead_id"
                    " FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id"
                    " WHERE ct.id = :tid"
                ),
                {"tid": thread_id},
            )
        ).first()
        if not info:
            return HTMLResponse('<div class="emp">Thread not found</div>', status_code=404)
        _, name, lead_id = info
        await session.execute(
            text("UPDATE lead SET stage = :s WHERE id = :id"),
            {"s": stage, "id": lead_id},
        )
    return HTMLResponse(
        chat_header_html(thread_id, str(name or "Lead"), stage)
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
    # Build conversation for the LLM
    convo_lines = "\n".join(
        f'{"Lead" if r[0] == "in" else "Bot"}: {(r[2] or "")[:200]}'
        for r in reversed(msgs)
    )
    messages = [
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
        draft, _ = await llm.chat(messages, capability="chat:fast", max_tokens=300)
    except Exception:
        draft = ""
    return HTMLResponse(suggest_box_html(thread_id, draft.strip()))


# ─── products CRUD ────────────────────────────────────────────────────────────

@router.get("/products/new", response_class=HTMLResponse)
async def products_new(request: Request) -> HTMLResponse:
    apply_lang(request)
    return HTMLResponse(product_edit_html(None, "", "", "", True, 0))


@router.get("/products/{prod_id}/edit", response_class=HTMLResponse)
async def products_edit(prod_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, slug, title, content, is_active, sort_order"
                    " FROM product WHERE id = :id"
                ),
                {"id": prod_id},
            )
        ).first()
    if not row:
        return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
    return HTMLResponse(
        product_edit_html(row[0], str(row[1]), str(row[2] or ""),
                          str(row[3] or ""), bool(row[4]), int(row[5] or 0))
    )


@router.post("/products/{prod_id}/save", response_class=HTMLResponse)
async def products_save(
    prod_id: int, request: Request,
    title: str = Form(default=""),
    content: str = Form(default=""),
    is_active: str = Form(default=""),
    sort_order: int = Form(default=0),
) -> HTMLResponse:
    apply_lang(request)
    active = bool(is_active)
    async with session_scope() as session:
        await session.execute(
            text(
                "UPDATE product SET title=:t, content=:c, is_active=:a, sort_order=:s"
                " WHERE id=:id"
            ),
            {"t": title.strip(), "c": content.strip(), "a": active,
             "s": sort_order, "id": prod_id},
        )
        row = (
            await session.execute(
                text(
                    "SELECT id, slug, title, content, is_active, sort_order"
                    " FROM product WHERE id = :id"
                ),
                {"id": prod_id},
            )
        ).first()
    if not row:
        return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
    return HTMLResponse(
        product_edit_html(row[0], str(row[1]), str(row[2] or ""),
                          str(row[3] or ""), bool(row[4]), int(row[5] or 0))
    )


@router.post("/products/create", response_class=HTMLResponse)
async def products_create(
    request: Request,
    slug: str = Form(default=""),
    title: str = Form(default=""),
    content: str = Form(default=""),
    is_active: str = Form(default=""),
    sort_order: int = Form(default=0),
) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    branch_id = branch_ids[0] if branch_ids else 1
    slug = slug.strip().lower().replace(" ", "_")
    if not slug:
        html = product_edit_html(None, "", title, content, bool(is_active), sort_order)
        return HTMLResponse(html)
    active = bool(is_active)
    async with session_scope() as session:
        await session.execute(
            text(
                "INSERT INTO product (branch_id, slug, title, content, is_active, sort_order)"
                " VALUES (:bid, :slug, :t, :c, :a, :s)"
                " ON CONFLICT (branch_id, slug) DO NOTHING"
            ),
            {"bid": branch_id, "slug": slug, "t": title.strip(),
             "c": content.strip(), "a": active, "s": sort_order},
        )
        row = (
            await session.execute(
                text(
                    "SELECT id, slug, title, content, is_active, sort_order"
                    " FROM product WHERE branch_id=:bid AND slug=:slug"
                ),
                {"bid": branch_id, "slug": slug},
            )
        ).first()
    if not row:
        return HTMLResponse('<div class="emp">Could not create product</div>', status_code=500)
    return HTMLResponse(
        product_edit_html(row[0], str(row[1]), str(row[2] or ""),
                          str(row[3] or ""), bool(row[4]), int(row[5] or 0))
    )


@router.post("/products/{prod_id}/delete")
async def products_delete(prod_id: int, request: Request) -> RedirectResponse:
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        where = "AND branch_id = ANY(:bids)" if branch_ids else ""
        params: dict = {"id": prod_id}
        if branch_ids:
            params["bids"] = branch_ids
        await session.execute(
            text(f"DELETE FROM product WHERE id=:id {where}"),  # noqa: S608
            params,
        )
    return RedirectResponse("/ui/products/panel", status_code=303)


# ─── knowledge: new doc ────────────────────────────────────────────────────────

@router.get("/knowledge/new", response_class=HTMLResponse)
async def knowledge_new(request: Request) -> HTMLResponse:
    apply_lang(request)
    return HTMLResponse(knowledge_new_html())


@router.post("/knowledge/create", response_class=HTMLResponse)
async def knowledge_create(
    request: Request,
    slug: str = Form(default=""),
    title: str = Form(default=""),
    content: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    branch_id = branch_ids[0] if branch_ids else 1
    slug = slug.strip().lower().replace(" ", "_")
    if not slug:
        return HTMLResponse(knowledge_new_html())
    async with session_scope() as session:
        await session.execute(
            text(
                "INSERT INTO knowledge_doc (branch_id, slug, title, content)"
                " VALUES (:bid, :slug, :t, :c)"
                " ON CONFLICT (branch_id, slug) DO NOTHING"
            ),
            {"bid": branch_id, "slug": slug, "t": title.strip(), "c": content.strip()},
        )
        row = (
            await session.execute(
                text(
                    "SELECT id, slug, title, content FROM knowledge_doc"
                    " WHERE branch_id=:bid AND slug=:slug"
                ),
                {"bid": branch_id, "slug": slug},
            )
        ).first()
    if not row:
        return HTMLResponse('<div class="emp">Could not create doc</div>', status_code=500)
    return HTMLResponse(
        knowledge_edit_html(row[0], str(row[1]), str(row[2] or ""), str(row[3] or ""))
    )


# ─── coach: revert ────────────────────────────────────────────────────────────

@router.post("/coach/revert/{edit_id}")
async def coach_revert(edit_id: int, request: Request) -> RedirectResponse:
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
            # restore old_text back to the knowledge doc
            await session.execute(
                text("UPDATE knowledge_doc SET content=:c WHERE branch_id=:bid AND slug=:slug"),
                {"c": row[2], "bid": branch_id, "slug": row[0]},
            )
            await session.execute(
                text("UPDATE coaching_edit SET status='reverted' WHERE id=:id"),
                {"id": edit_id},
            )
    return RedirectResponse("/ui/coach", status_code=303)


# ─── language switcher ────────────────────────────────────────────────────────

@router.get("/lang/{code}")
async def set_lang(code: str, request: Request) -> Response:
    lang = code if code in LANGS else "en"
    referer = request.headers.get("referer", "/ui/inbox")
    resp = RedirectResponse(referer, status_code=303)
    resp.set_cookie(LANG_COOKIE, lang, max_age=60 * 60 * 24 * 365, httponly=False, samesite="lax")
    return resp
