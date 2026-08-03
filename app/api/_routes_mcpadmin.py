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
from app.admin._branch import (
    is_super_admin,
    selected_branch_id,
    writable_selected_branch_id,
)
from app.config import settings
from app.modules.mcp.tokens import McpTokenService
from app.modules.settings.repository import SettingRepo
from app.modules.settings.service import invalidate

from ._i18n import apply_lang, t
from ._ui_mcp import mcp_page_html

router = APIRouter()

_FORBIDDEN = HTMLResponse(
    '<div style="padding:1rem;color:#8b98a5">Раздел MCP доступен только '
    'супер-администратору.</div>',
    status_code=403)


def _base_url() -> str:
    return (settings().public_url or "https://stepan2.zapleo.com").rstrip("/")


async def _crm_cfg(session, branch_id: int) -> tuple[bool, str, bool]:
    rows = dict((await session.execute(
        text("SELECT key, value FROM app_setting WHERE branch_id = :b"
             " AND key IN ('crm_read_enabled','crm_state_url','crm_read_secret')"),
        {"b": branch_id})).all())
    enabled = (rows.get("crm_read_enabled", "") or "").lower() in ("true", "1", "yes")
    return enabled, rows.get("crm_state_url", "") or "", bool(rows.get("crm_read_secret"))


async def _render(
    request: Request, new_token: str | None = None, *,
    notice: str | None = None, status: int = 200,
) -> HTMLResponse:
    # The CRM block is per branch. With no single branch in view it says so rather than
    # showing branch 1's live CRM endpoint as if it belonged to whatever is on screen.
    branch_id = selected_branch_id(request)
    async with session_scope() as session:
        tokens = await McpTokenService(session).list()
        enabled, url, has_secret = (
            await _crm_cfg(session, branch_id) if branch_id is not None else (False, "", False))
        branches = [
            (r[0], r[1]) for r in (await session.execute(
                text("SELECT id, name FROM branch ORDER BY name"))).all()]
    return HTMLResponse(mcp_page_html(
        _base_url(), tokens, crm_enabled=enabled, crm_url=url,
        crm_has_secret=has_secret, new_token=new_token, branches=branches,
        crm_branch_selected=branch_id is not None, notice=notice), status_code=status)


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
    chose_branch = bool(branch_id.strip())  # empty = the operator picked "all branches"
    if chose_branch and branch_id.strip().isdigit():
        async with session_scope() as session:
            exists = (await session.execute(
                text("SELECT 1 FROM branch WHERE id = :b"),
                {"b": int(branch_id)})).first()
        bid = int(branch_id) if exists else None
    # Fail CLOSED: if a branch WAS chosen but doesn't resolve (stale/typo/forged), refuse —
    # never silently mint a universal (all-branch) token, which would grant MORE access.
    if chose_branch and bid is None:
        return await _render(
            request, notice="Выбранный филиал не найден — токен не создан.", status=400)
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
    # crm_state_url / crm_read_secret are per-branch credentials — writing them to a guessed
    # branch pointed one tenant's CRM reader at another's endpoint. No branch in view, no write.
    bid = writable_selected_branch_id(request)
    if bid is None:
        # Say it out loud. Re-rendering the page with a 200 and no message meant the operator
        # typed a CRM endpoint and a bearer secret, hit Save, watched the form come back
        # empty, and had no way to tell that nothing was stored.
        return await _render(request, notice=t("branch.pick_one"), status=403)
    on = "true" if enabled else "false"
    async with session_scope() as session:
        await _set(session, bid, "crm_read_enabled", on)
        await _set(session, bid, "crm_state_url", url.strip())
        if secret.strip():  # blank = keep existing secret
            await _set(session, bid, "crm_read_secret", secret.strip())
    invalidate(bid)
    return await _render(request)


async def _set(session, branch_id: int, key: str, value: str) -> None:
    await SettingRepo(session).upsert(key, value, branch_id=branch_id)


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
