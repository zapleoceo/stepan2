"""Product CRUD routes."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.admin._branch import branch_ids_from_request

from ._i18n import apply_lang
from ._query import _branch_where
from ._ui_panels import product_edit_html, products_panel_html

router = APIRouter()


@router.get("/products/panel", response_class=HTMLResponse)
async def products_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    branch_ids = branch_ids_from_request(request)
    where, params = _branch_where(branch_ids)
    async with session_scope() as session:
        q = (
            "SELECT id, slug, title, is_active, sort_order"  # noqa: S608
            f" FROM product {where} ORDER BY sort_order, id"
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
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        if branch_ids:
            await session.execute(
                text(
                    "UPDATE product SET title=:t, content=:c, is_active=:a, sort_order=:s"
                    " WHERE id=:id AND branch_id=ANY(:bids)"
                ),
                {"t": title.strip(), "c": content.strip(), "a": active,
                 "s": sort_order, "id": prod_id, "bids": branch_ids},
            )
        else:
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
    branch_ids = branch_ids_from_request(request)
    branch_id = branch_ids[0] if branch_ids else 1
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


@router.post("/products/{prod_id}/delete")
async def products_delete(prod_id: int, request: Request) -> RedirectResponse:
    branch_ids = branch_ids_from_request(request)
    async with session_scope() as session:
        if branch_ids:
            await session.execute(
                text("DELETE FROM product WHERE id=:id AND branch_id=ANY(:bids)"),  # noqa: S608
                {"id": prod_id, "bids": branch_ids},
            )
        else:
            await session.execute(
                text("DELETE FROM product WHERE id=:id"),  # noqa: S608
                {"id": prod_id},
            )
    return RedirectResponse("/ui/products/panel", status_code=303)
