"""Knowledge-base routes — tabbed tree, section editor, edit history."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from starlette.datastructures import FormData

from app.adapters.db.session import session_scope
from app.admin._branch import actor_from_request as _actor
from app.admin._branch import (
    branch_ids_from_request,
    is_branch_forbidden,
    is_branch_write_forbidden,
    writable_branch_ids,
)
from app.modules.knowledge.history import list_revisions, record_revision, restore_revision
from app.modules.knowledge.sections import reassemble

from ._i18n import apply_lang
from ._query import _branch_where
from ._ui_kb import kb_editor_html, kb_history_html, kb_products_html, kb_tree_html

router = APIRouter()

_DOC_COLS = "id, slug, title, content, category, sort_order, updated_by"
_DOC_Q = f"SELECT {_DOC_COLS} FROM knowledge_doc {{where}} ORDER BY sort_order, id"  # noqa: S608


async def _docs(session, branch_ids: list[int] | None) -> list:
    where, params = _branch_where(branch_ids)
    return list((await session.execute(text(_DOC_Q.format(where=where)), params)).all())


@router.get("/knowledge/tree", response_class=HTMLResponse)
async def knowledge_tree(request: Request) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        docs = await _docs(session, branch_ids_from_request(request))
    return HTMLResponse(kb_tree_html(docs))


@router.get("/knowledge/products", response_class=HTMLResponse)
async def knowledge_products_tab(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    where, params = _branch_where(branch_ids)
    async with session_scope() as session:
        rows = (await session.execute(
            text("SELECT id, slug, title, is_active, sort_order"  # noqa: S608
                 f" FROM product {where} ORDER BY sort_order, id"), params)).all()
    return HTMLResponse(kb_products_html(list(rows)))


@router.get("/knowledge/{doc_id}/edit", response_class=HTMLResponse)
async def knowledge_edit(doc_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        row = (await session.execute(
            text("SELECT id, slug, title, content, updated_by, branch_id"
                 " FROM knowledge_doc WHERE id = :id"), {"id": doc_id})).first()
    if not row:
        return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
    if is_branch_forbidden(row[5], branch_ids):
        return HTMLResponse('<div class="emp">Forbidden</div>', status_code=403)
    return HTMLResponse(kb_editor_html(
        row[0], str(row[1]), str(row[2] or ""), str(row[3] or ""), row[4]))


def _content_from_form(form: FormData) -> str:
    """Reassemble the section textareas (head_i/body_i, nsec) back into markdown."""
    try:
        n = int(str(form.get("nsec") or "0"))
    except ValueError:
        n = 0
    pairs = [(str(form.get(f"head_{i}") or ""), str(form.get(f"body_{i}") or ""))
             for i in range(n)]
    return reassemble(pairs)


@router.post("/knowledge/{doc_id}/save", response_class=HTMLResponse)
async def knowledge_save(doc_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    form = await request.form()
    title = str(form.get("title") or "").strip()
    content = _content_from_form(form)
    actor = _actor(request)
    writable = writable_branch_ids(request)
    async with session_scope() as session:
        prev = (await session.execute(
            text("SELECT branch_id, slug, content FROM knowledge_doc WHERE id=:id"),
            {"id": doc_id})).first()
        if prev is None:
            return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
        if is_branch_write_forbidden(prev[0], writable):  # WRITE role required for this branch
            return HTMLResponse('<div class="emp">Forbidden</div>', status_code=403)
        await session.execute(
            text("UPDATE knowledge_doc SET title=:t, content=:c,"
                 " updated_by=:a, updated_at=NOW() WHERE id=:id"),
            {"t": title, "c": content, "a": actor, "id": doc_id})
        await record_revision(
            session, branch_id=prev[0], entity_type="doc", slug=str(prev[1]),
            old_content=prev[2], new_content=content, actor=actor)
        row = (await session.execute(
            text("SELECT id, slug, title, content, updated_by"
                 " FROM knowledge_doc WHERE id=:id"), {"id": doc_id})).first()
    resp = HTMLResponse(kb_editor_html(
        row[0], str(row[1]), str(row[2] or ""), str(row[3] or ""), row[4]))
    resp.headers["HX-Trigger"] = "refreshKnowledgeList"
    return resp


@router.get("/knowledge/{doc_id}/history", response_class=HTMLResponse)
async def knowledge_history(doc_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        row = (await session.execute(
            text("SELECT branch_id, slug FROM knowledge_doc WHERE id=:id"),
            {"id": doc_id})).first()
        if not row:
            return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
        bid = row[0] if branch_ids else None
        revs = await list_revisions(session, bid, "doc", str(row[1]))
    return HTMLResponse(kb_history_html(f"/ui/knowledge/{doc_id}/edit", str(row[1]), revs))


@router.post("/knowledge/restore", response_class=HTMLResponse)
async def knowledge_restore(request: Request, rev_id: int = Form(...)) -> HTMLResponse:
    apply_lang(request)
    # Scope the restore by WRITE right, not the view filter — a viewer can't restore.
    # (WriteGuardMiddleware already blocks a pure viewer, so `writable` is never [] here.)
    writable = writable_branch_ids(request)
    bid = writable[0] if writable else None
    async with session_scope() as session:
        out = await restore_revision(session, bid, rev_id, actor=_actor(request))
        if out is None:
            return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
        _, slug = out
        row = (await session.execute(
            text("SELECT id, slug, title, content, updated_by"
                 " FROM knowledge_doc WHERE slug=:s"), {"s": slug})).first()
    if not row:
        return HTMLResponse('<div class="emp">Restored</div>')
    resp = HTMLResponse(kb_editor_html(
        row[0], str(row[1]), str(row[2] or ""), str(row[3] or ""), row[4]))
    resp.headers["HX-Trigger"] = "refreshKnowledgeList"
    return resp


