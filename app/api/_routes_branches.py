"""Branch management routes — list, create, edit."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.adapters.db.session import session_scope

from ._i18n import apply_lang
from ._ui_panels import branch_edit_html, branches_panel_html

router = APIRouter()

_log = logging.getLogger(__name__)

_BRANCH_Q = "SELECT id, name, lang, tz_offset_h, is_active FROM branch ORDER BY id"

# Mirrors _DEFAULTS in app/modules/settings/service.py
_SEED_SETTINGS: dict[str, str] = {
    "agent_enabled_global": "true",
    "hourly_cap": "120",
    "daily_cap": "500",
    "quiet_start": "22",
    "quiet_end": "8",
    "followup_enabled": "false",
    "followup_schedule_h": "4,24,72",
}


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
    return HTMLResponse(
        branch_edit_html(row[0], str(row[1]), str(row[2]), int(row[3] or 7), bool(row[4]))
    )


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
        _log.info("created branch id=%s name=%r; seeded %d settings",
                  new_id, name, len(_SEED_SETTINGS))
    return HTMLResponse(branch_edit_html(new_id, name, lang, tz_offset_h, active, seeded=True))


@router.post("/branches/{branch_id}/save", response_class=HTMLResponse)
async def branches_save(
    branch_id: int,
    request: Request,
    name: str = Form(default=""),
    lang: str = Form(default="id"),
    tz_offset_h: int = Form(default=7),
    is_active: str = Form(default=""),
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
        await session.execute(
            text(
                "UPDATE branch SET name=:name, lang=:lang,"
                " tz_offset_h=:tz, is_active=:active WHERE id=:id"
            ),
            {"name": name, "lang": lang, "tz": tz_offset_h, "active": active, "id": branch_id},
        )
        rows = (await session.execute(text(_BRANCH_Q))).all()
    return HTMLResponse(branches_panel_html(list(rows)))
