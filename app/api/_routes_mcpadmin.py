"""MCP admin routes: token management (incoming) + CRM link (outgoing) + docs download.

Platform-level, so super-admin only. Every mutating route re-renders the whole page
(#mcp-page swap). Token plaintext is shown exactly once, right after creation.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.admin._branch import branch_ids_from_request, is_super_admin, writable_branch_ids
from app.config import settings
from app.modules.mcp.tokens import McpTokenService
from app.modules.settings.service import invalidate

from ._i18n import apply_lang
from ._ui_mcp import mcp_page_html

router = APIRouter()

_FORBIDDEN = HTMLResponse(
    '<div style="padding:1rem;color:#8b98a5">Раздел MCP доступен только '
    'супер-администратору.</div>',
    status_code=403)


def _base_url() -> str:
    return (settings().public_url or "https://stepan2.zapleo.com").rstrip("/")


def _branch_id(request: Request) -> int:
    ids = branch_ids_from_request(request)
    return ids[0] if ids else 1


async def _crm_cfg(session, branch_id: int) -> tuple[bool, str, bool]:
    rows = dict((await session.execute(
        text("SELECT key, value FROM app_setting WHERE branch_id = :b"
             " AND key IN ('crm_read_enabled','crm_state_url','crm_read_secret')"),
        {"b": branch_id})).all())
    enabled = (rows.get("crm_read_enabled", "") or "").lower() in ("true", "1", "yes")
    return enabled, rows.get("crm_state_url", "") or "", bool(rows.get("crm_read_secret"))


async def _render(request: Request, new_token: str | None = None) -> HTMLResponse:
    branch_id = _branch_id(request)
    async with session_scope() as session:
        tokens = await McpTokenService(session).list()
        enabled, url, has_secret = await _crm_cfg(session, branch_id)
        branches = [
            (r[0], r[1]) for r in (await session.execute(
                text("SELECT id, name FROM branch ORDER BY name"))).all()]
    return HTMLResponse(mcp_page_html(
        _base_url(), tokens, crm_enabled=enabled, crm_url=url,
        crm_has_secret=has_secret, new_token=new_token, branches=branches))


@router.get("/mcp/panel", response_class=HTMLResponse)
async def mcp_panel(request: Request) -> HTMLResponse:
    apply_lang(request)
    if not is_super_admin(request):
        return _FORBIDDEN
    return await _render(request)


@router.post("/mcp/token/create", response_class=HTMLResponse)
async def mcp_token_create(
    request: Request, label: str = Form(...), scope: str = Form("read"),
    branch_id: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    if not is_super_admin(request):
        return _FORBIDDEN
    if scope not in ("read", "write"):
        scope = "read"
    bid: int | None = None
    if branch_id.strip().isdigit():
        async with session_scope() as session:
            exists = (await session.execute(
                text("SELECT 1 FROM branch WHERE id = :b"),
                {"b": int(branch_id)})).first()
        bid = int(branch_id) if exists else None  # ignore an unknown/forged branch id
    async with session_scope() as session:
        raw, _ = await McpTokenService(session).create(label, scope, bid)
    return await _render(request, new_token=raw)


@router.post("/mcp/token/{token_id}/revoke", response_class=HTMLResponse)
async def mcp_token_revoke(token_id: int, request: Request) -> HTMLResponse:
    apply_lang(request)
    if not is_super_admin(request):
        return _FORBIDDEN
    async with session_scope() as session:
        await McpTokenService(session).revoke(token_id)
    return await _render(request)


@router.post("/mcp/outgoing/save", response_class=HTMLResponse)
async def mcp_outgoing_save(
    request: Request, url: str = Form(""), secret: str = Form(""),
    enabled: str = Form(default=""),
) -> HTMLResponse:
    apply_lang(request)
    if not is_super_admin(request):
        return _FORBIDDEN
    writable = writable_branch_ids(request)
    bid = writable[0] if writable else _branch_id(request)
    on = "true" if enabled else "false"
    async with session_scope() as session:
        await _set(session, bid, "crm_read_enabled", on)
        await _set(session, bid, "crm_state_url", url.strip())
        if secret.strip():  # blank = keep existing secret
            await _set(session, bid, "crm_read_secret", secret.strip())
    invalidate(bid)
    return await _render(request)


async def _set(session, branch_id: int, key: str, value: str) -> None:
    row = (await session.execute(
        text("SELECT id FROM app_setting WHERE branch_id = :b AND key = :k"),
        {"b": branch_id, "k": key})).first()
    if row is None:
        await session.execute(
            text("INSERT INTO app_setting (branch_id, key, value) VALUES (:b, :k, :v)"),
            {"b": branch_id, "k": key, "v": value})
    else:
        await session.execute(
            text("UPDATE app_setting SET value = :v WHERE id = :i"), {"v": value, "i": row[0]})


def _load_docs_md() -> str:
    docs = Path(__file__).resolve().parents[2] / "docs"
    parts = [(docs / n).read_text(encoding="utf-8")
             for n in ("mcp.md", "crm-read.md") if (docs / n).exists()]
    return "\n\n---\n\n".join(parts) or "# MCP\nДокументация не найдена в сборке."


@router.get("/mcp/docs")
async def mcp_docs(request: Request) -> Response:
    """Download a single connection guide (the two MCP docs concatenated)."""
    if not is_super_admin(request):
        return _FORBIDDEN
    host = _base_url().replace("https://", "").replace("http://", "")
    body = _load_docs_md().replace("stepan2.zapleo.com", host)
    return PlainTextResponse(
        body, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="stepan-mcp.md"'})
