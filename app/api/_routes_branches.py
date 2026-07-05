"""Branch management routes — list, create, edit. Super_admin only: branch CRUD is a
platform-wide action, not something a branch_admin/viewer of one branch should reach."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.admin._branch import require_super_admin
from app.modules.knowledge.canonical import ensure_canonical_docs
from app.modules.knowledge.source import copy_kb
from app.modules.settings.schema import defaults as _schema_defaults

from ._i18n import apply_lang
from ._ui_panels import branch_edit_html, branches_panel_html

router = APIRouter(dependencies=[Depends(require_super_admin)])

_log = logging.getLogger(__name__)

_BRANCH_Q = "SELECT id, name, lang, tz_offset_h, is_active FROM branch ORDER BY id"


async def _other_branches(session, exclude_id: int) -> list[tuple[int, str]]:  # noqa: ANN001
    rows = (await session.execute(
        text("SELECT id, name FROM branch WHERE id <> :x ORDER BY name"),
        {"x": exclude_id})).all()
    return [(r[0], r[1]) for r in rows]


async def _edit_form(session, branch_id: int, *, seeded: bool = False) -> str:  # noqa: ANN001
    row = (await session.execute(
        text("SELECT name, lang, tz_offset_h, is_active, kb_source_branch_id"
             " FROM branch WHERE id=:id"), {"id": branch_id})).first()
    if not row:
        return '<div class="emp">Branch not found</div>'
    others = await _other_branches(session, branch_id)
    return branch_edit_html(branch_id, str(row[0]), str(row[1]), int(row[2] or 7),
                            bool(row[3]), seeded=seeded, kb_source_branch_id=row[4],
                            other_branches=others)

# Single source of truth — a new branch seeds exactly the schema's defaults (DRY).
_SEED_SETTINGS: dict[str, str] = _schema_defaults()


@router.get("/branches/panel", response_class=HTMLResponse)
async def branches_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        rows = (await session.execute(text(_BRANCH_Q))).all()
    return HTMLResponse(branches_panel_html(list(rows)))


@router.get("/branches/new", response_class=HTMLResponse)
async def branches_new(request: Request) -> HTMLResponse:
    apply_lang(request)
    return HTMLResponse(branch_edit_html(None, "", "id", 7, is_active=True))


@router.get("/branches/{branch_id}/edit", response_class=HTMLResponse)
async def branches_edit(branch_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        row = (
            await session.execute(
                text("SELECT id, name, lang, tz_offset_h, is_active FROM branch WHERE id=:id"),
                {"id": branch_id},
            )
        ).first()
    if not row:
        return HTMLResponse('<div class="emp">Branch not found</div>', status_code=404)
    async with session_scope() as session:
        return HTMLResponse(await _edit_form(session, branch_id))


@router.post("/branches/create", response_class=HTMLResponse)
async def branches_create(
    request: Request,
    name: str = Form(default=""),
    lang: str = Form(default="id"),
    tz_offset_h: int = Form(default=7),
    is_active: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    name = name.strip()
    if not name:
        return HTMLResponse(branch_edit_html(None, "", lang, tz_offset_h, bool(is_active)))
    _valid_langs = {
        "id", "ms", "en", "ru", "zh", "ar", "vi", "th", "hi", "ko", "ja", "es", "fr", "de",
        "pt", "tr",
    }
    lang = lang if lang in _valid_langs else "id"
    active = bool(is_active)
    async with session_scope() as session:
        row = (
            await session.execute(
                text(
                    "INSERT INTO branch (name, lang, tz_offset_h, is_active, created_at)"
                    " VALUES (:name, :lang, :tz, :active, NOW()) RETURNING id"
                ),
                {"name": name, "lang": lang, "tz": tz_offset_h, "active": active},
            )
        ).first()
        new_id = row[0]
        for key, value in _SEED_SETTINGS.items():
            await session.execute(
                text(
                    "INSERT INTO app_setting (branch_id, key, value)"
                    " VALUES (:bid, :key, :val)"
                    " ON CONFLICT (branch_id, key) DO NOTHING"
                ),
                {"bid": new_id, "key": key, "val": value},
            )
        created = await ensure_canonical_docs(session, new_id, lang)
        _log.info("created branch id=%s name=%r; seeded %d settings + %d KB docs",
                  new_id, name, len(_SEED_SETTINGS), created)
        return HTMLResponse(await _edit_form(session, new_id, seeded=True))


@router.post("/branches/{branch_id}/save", response_class=HTMLResponse)
async def branches_save(
    branch_id: int,
    request: Request,
    name: str = Form(default=""),
    lang: str = Form(default="id"),
    tz_offset_h: int = Form(default=7),
    is_active: str = Form(default=""),
    kb_source_branch_id: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    name = name.strip()
    _valid_langs = {
        "id", "ms", "en", "ru", "zh", "ar", "vi", "th", "hi", "ko", "ja", "es", "fr", "de",
        "pt", "tr",
    }
    lang = lang if lang in _valid_langs else "id"
    active = bool(is_active)
    async with session_scope() as session:
        kb_src = await _valid_kb_source(session, branch_id, kb_source_branch_id)
        await session.execute(
            text(
                "UPDATE branch SET name=:name, lang=:lang, tz_offset_h=:tz,"
                " is_active=:active, kb_source_branch_id=:kb WHERE id=:id"
            ),
            {"name": name, "lang": lang, "tz": tz_offset_h, "active": active,
             "kb": kb_src, "id": branch_id},
        )
        return HTMLResponse(await _edit_form(session, branch_id))


async def _valid_kb_source(session, branch_id: int, raw: str) -> int | None:  # noqa: ANN001
    """Parse the chosen KB-source branch; reject self and chains (source must be a real
    KB branch, never itself linked)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        src = int(raw)
    except ValueError:
        return None
    if src == branch_id:
        return None
    row = (await session.execute(
        text("SELECT kb_source_branch_id FROM branch WHERE id=:id"), {"id": src})).first()
    if row is None or row[0] is not None:  # unknown branch, or a source that is itself linked
        return None
    return src


@router.post("/branches/{branch_id}/copy-kb", response_class=HTMLResponse)
async def branches_copy_kb(
    branch_id: int, request: Request, src_branch_id: int = Form(...),
) -> HTMLResponse:
    apply_lang(request)
    async with session_scope() as session:
        n = await copy_kb(session, branch_id, src_branch_id)
        # a copy makes this branch's KB its own again — drop any live link
        await session.execute(
            text("UPDATE branch SET kb_source_branch_id=NULL WHERE id=:id"), {"id": branch_id})
        _log.info("copied KB into branch=%d from=%d: %d rows", branch_id, src_branch_id, n)
        return HTMLResponse(await _edit_form(session, branch_id, seeded=(n > 0)))
