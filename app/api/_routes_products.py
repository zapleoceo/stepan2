"""Product CRUD routes."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.admin._branch import (
    actor_from_request,
    branch_ids_from_request,
    is_branch_forbidden,
    is_branch_write_forbidden,
    writable_branch_ids,
)
from app.modules.knowledge.history import list_revisions, record_revision, restore_revision

from ._i18n import apply_lang
from ._query import _branch_where, fetch_branch_tz
from ._ui_html import set_render_tz
from ._ui_kb import kb_history_html
from ._ui_panels import product_edit_html, products_panel_html

router = APIRouter()


@router.get("/products/panel", response_class=HTMLResponse)
async def products_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    where, params = _branch_where(branch_ids, col="p.branch_id")
    async with session_scope() as session:
        q = (
            "SELECT p.id, p.slug, p.title, p.is_active, p.sort_order, p.kind, b.name"  # noqa: S608,E501
            " FROM product p JOIN branch b ON b.id = p.branch_id"
            f" {where} ORDER BY b.name, p.sort_order, p.id"
        )
        rows = (await session.execute(text(q), params)).all()
    return HTMLResponse(products_panel_html(list(rows)))


@router.get("/products/new", response_class=HTMLResponse)
async def products_new(request: Request) -> HTMLResponse:
    apply_lang(request)
    return HTMLResponse(product_edit_html(None, "", "", "", True, 0))


@router.get("/products/{prod_id}/edit", response_class=HTMLResponse)
async def products_edit(prod_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, slug, title, content, is_active, sort_order, branch_id"
                    " FROM product WHERE id = :id"
                ),
                {"id": prod_id},
            )
        ).first()
    if not row:
        return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
    if is_branch_forbidden(row[6], branch_ids):
        return HTMLResponse('<div class="emp">Forbidden</div>', status_code=403)
    return HTMLResponse(
        product_edit_html(
            row[0], str(row[1]), str(row[2] or ""),
            str(row[3] or ""), bool(row[4]), int(row[5] or 0),
        )
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
    actor = actor_from_request(request)
    writable = writable_branch_ids(request)
    async with session_scope() as session:
        prev = (await session.execute(
            text("SELECT branch_id, slug, content FROM product WHERE id=:id"),
            {"id": prod_id})).first()
        if prev is None:
            return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
        if is_branch_write_forbidden(prev[0], writable):  # WRITE role required for this branch
            return HTMLResponse('<div class="emp">Forbidden</div>', status_code=403)
        await session.execute(
            text("UPDATE product SET title=:t, content=:c, is_active=:a, sort_order=:s,"
                 " updated_by=:by, updated_at=NOW() WHERE id=:id"),
            {"t": title.strip(), "c": content.strip(), "a": active,
             "s": sort_order, "id": prod_id, "by": actor},
        )
        await record_revision(
            session, branch_id=prev[0], entity_type="product", slug=str(prev[1]),
            old_content=prev[2], new_content=content.strip(), actor=actor)
        row = (
            await session.execute(
                text(
                    "SELECT id, slug, title, content, is_active, sort_order"
                    " FROM product WHERE id=:id"
                ),
                {"id": prod_id},
            )
        ).first()
    if not row:
        return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
    return HTMLResponse(
        product_edit_html(
            row[0], str(row[1]), str(row[2] or ""),
            str(row[3] or ""), bool(row[4]), int(row[5] or 0),
        )
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
    # Create in a branch the caller may WRITE (super: None → default branch 1). The
    # WriteGuardMiddleware already blocks a pure viewer, so `writable` is never [] here.
    writable = writable_branch_ids(request)
    branch_id = writable[0] if writable else 1
    slug = slug.strip().lower().replace(" ", "_")
    if not slug:
        return HTMLResponse(
            product_edit_html(None, "", title, content, bool(is_active), sort_order)
        )
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
        product_edit_html(
            row[0], str(row[1]), str(row[2] or ""),
            str(row[3] or ""), bool(row[4]), int(row[5] or 0),
        )
    )


@router.get("/products/{prod_id}/history", response_class=HTMLResponse)
async def products_history(prod_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        row = (await session.execute(
            text("SELECT branch_id, slug FROM product WHERE id=:id"), {"id": prod_id})).first()
        if not row:
            return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
        tz_by_branch = await fetch_branch_tz(session, [row[0]])
        set_render_tz(tz_by_branch.get(row[0], 0))
        bid = row[0] if branch_ids else None
        revs = await list_revisions(session, bid, "product", str(row[1]))
    return HTMLResponse(kb_history_html(
        f"/ui/products/{prod_id}/edit", str(row[1]), revs, restore_url="/ui/products/restore"))


@router.post("/products/restore", response_class=HTMLResponse)
async def products_restore(request: Request, rev_id: int = Form(...)) -> HTMLResponse:
    apply_lang(request)
    writable = writable_branch_ids(request)  # scope by WRITE right, not view (viewer can't)
    bid = writable[0] if writable else None
    async with session_scope() as session:
        out = await restore_revision(session, bid, rev_id, actor=actor_from_request(request))
        if out is None:
            return HTMLResponse('<div class="emp">Not found</div>', status_code=404)
        row = (await session.execute(
            text("SELECT id, slug, title, content, is_active, sort_order"
                 " FROM product WHERE slug=:s"), {"s": out[1]})).first()
    if not row:
        return HTMLResponse('<div class="emp">Restored</div>')
    return HTMLResponse(product_edit_html(
        row[0], str(row[1]), str(row[2] or ""), str(row[3] or ""), bool(row[4]), int(row[5] or 0)))


@router.post("/products/{prod_id}/delete")
async def products_delete(prod_id: int, request: Request) -> RedirectResponse:
    writable = writable_branch_ids(request)  # delete only from a branch the caller may WRITE
    async with session_scope() as session:
        if writable:
            await session.execute(
                text("DELETE FROM product WHERE id=:id AND branch_id=ANY(:bids)"),  # noqa: S608
                {"id": prod_id, "bids": writable},
            )
        else:
            await session.execute(
                text("DELETE FROM product WHERE id=:id"),  # noqa: S608
                {"id": prod_id},
            )
    return RedirectResponse("/ui/products/panel", status_code=303)
