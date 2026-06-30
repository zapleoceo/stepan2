"""Knowledge base CRUD routes."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.admin._branch import branch_ids_from_request

from ._i18n import apply_lang
from ._query import _branch_where
from ._ui_panels import (
    _knowledge_items_html,
    knowledge_edit_html,
    knowledge_new_html,
    knowledge_panel_html,
)

router = APIRouter()

_KNOW_Q = "SELECT id, slug, title, content FROM knowledge_doc {where} ORDER BY id"  # noqa: S608


@router.get("/knowledge/panel", response_class=HTMLResponse)
async def knowledge_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    where, params = _branch_where(branch_ids)
    async with session_scope() as session:
        docs = (await session.execute(text(_KNOW_Q.format(where=where)), params)).all()
    return HTMLResponse(knowledge_panel_html(list(docs)))


@router.get("/knowledge/list", response_class=HTMLResponse)
async def knowledge_list_partial(request: Request) -> HTMLResponse:
    """HTMX partial: list items for #know-list refresh after save/create."""
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    where, params = _branch_where(branch_ids)
    async with session_scope() as session:
        docs = (await session.execute(text(_KNOW_Q.format(where=where)), params)).all()
    return HTMLResponse(_knowledge_items_html(list(docs)))


@router.get("/knowledge/new", response_class=HTMLResponse)
async def knowledge_new(request: Request) -> HTMLResponse:
    apply_lang(request)
    return HTMLResponse(knowledge_new_html())


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
    return HTMLResponse(
        knowledge_edit_html(row[0], str(row[1]), str(row[2] or ""), str(row[3] or ""))
    )


@router.post("/knowledge/{doc_id}/save", response_class=HTMLResponse)
async def knowledge_save(
    doc_id: int,
    request: Request,
    title: str = Form(default=""),
    content: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        if branch_ids:
            await session.execute(
                text(
                    "UPDATE knowledge_doc SET title=:t, content=:c"
                    " WHERE id=:id AND branch_id=ANY(:bids)"
                ),
                {"t": title.strip(), "c": content.strip(), "id": doc_id, "bids": branch_ids},
            )
        else:
            await session.execute(
                text("UPDATE knowledge_doc SET title=:t, content=:c WHERE id=:id"),
                {"t": title.strip(), "c": content.strip(), "id": doc_id},
            )
        row = (
            await session.execute(
                text("SELECT id, slug, title, content FROM knowledge_doc WHERE id=:id"),
                {"id": doc_id},
            )
        ).first()
    if not row:
        return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
    resp = HTMLResponse(
        knowledge_edit_html(row[0], str(row[1]), str(row[2] or ""), str(row[3] or ""))
    )
    resp.headers["HX-Trigger"] = "refreshKnowledgeList"
    return resp


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
    resp = HTMLResponse(
        knowledge_edit_html(row[0], str(row[1]), str(row[2] or ""), str(row[3] or ""))
    )
    resp.headers["HX-Trigger"] = "refreshKnowledgeList"
    return resp
