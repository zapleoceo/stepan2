"""Public connect flow: a client grants access to their own Page with two clicks.

Two routes and no UI of its own. `/connect/meta/{ch}/start` sends the person to Meta's consent
screen; `/connect/meta/callback` takes the code back, turns it into a Page token and stores the
channel session — the same shape the manual paste form writes, so everything downstream is
unchanged.

Public on purpose: the callback is hit by a redirect from facebook.com with no session cookie.
Nothing here trusts the request itself — the channel id travels in a signed state parameter,
and a forged or stale one is refused.
"""
from __future__ import annotations

import json
import logging

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app.adapters.crypto import encrypt
from app.adapters.db.session import session_scope
from app.config import settings
from app.modules.meta.app_secret import app_secret_for
from app.modules.meta.oauth import (
    authorize_url,
    exchange_code,
    exchange_long_lived,
    list_pages,
    state_channel_id,
    state_token,
)
from app.modules.settings.service import get_channel_settings

router = APIRouter()
_log = logging.getLogger(__name__)


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        '<article style="max-width:560px;margin:4rem auto;padding:0 1.25rem;line-height:1.6;'
        'font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a">'
        f"<h1>{title}</h1>{body}</article>"
    )


async def _branch_of(channel_id: int) -> int | None:
    async with session_scope() as session:
        row = (await session.execute(
            text("SELECT branch_id FROM channel WHERE id=:id"), {"id": channel_id},
        )).first()
    return int(row[0]) if row else None


def _redirect_uri() -> str:
    return f"{settings().public_url.rstrip('/')}/connect/meta/callback"


@router.get("/connect/meta/{channel_id}/start", response_model=None)
async def start(channel_id: int) -> RedirectResponse | HTMLResponse:
    branch_id = await _branch_of(channel_id)
    if branch_id is None:
        return _page("Channel not found", "<p>Ask for a fresh link.</p>")
    async with session_scope() as session:
        cfg = await get_channel_settings(session, branch_id, channel_id)
    if not cfg.meta_app_id or not settings().public_url:
        return _page(
            "Not configured yet",
            "<p>This connector is missing its Meta app id or the public URL. "
            "Set them in the channel settings first.</p>",
        )
    url = authorize_url(
        app_id=cfg.meta_app_id,
        redirect_uri=_redirect_uri(),
        state=state_token(channel_id, settings().secret_key),
        version=settings().ig_graph_version,
    )
    return RedirectResponse(url, status_code=302)


@router.get("/connect/meta/callback")
async def callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
) -> HTMLResponse:
    if error or not code:
        # The person pressed Cancel, or Meta refused. Not an error on our side.
        return _page("Not connected", "<p>Nothing was changed. You can close this page.</p>")
    channel_id = state_channel_id(state, settings().secret_key)
    if channel_id is None:
        return _page("Link expired", "<p>Open the connect link again — it is valid 15 minutes.</p>")
    branch_id = await _branch_of(channel_id)
    if branch_id is None:
        return _page("Channel not found", "<p>Ask for a fresh link.</p>")

    async with session_scope() as session:
        cfg = await get_channel_settings(session, branch_id, channel_id)
    # Same resolver the webhook signature check uses: one app, one secret, one source. A copy
    # in the settings table would be a value able to disagree with itself after a rotation.
    secret = app_secret_for(branch_id)
    if not cfg.meta_app_id or not secret:
        return _page("Not configured yet", "<p>The Meta app id or secret is missing.</p>")

    try:
        user_token = await exchange_code(
            code=code, app_id=cfg.meta_app_id, app_secret=secret,
            redirect_uri=_redirect_uri(), version=settings().ig_graph_version,
        )
        # Before listing Pages, not after: a Page token inherits the lifetime of the user token
        # that minted it, and the one from the code exchange lasts about an hour.
        user_token = await exchange_long_lived(
            user_token=user_token, app_id=cfg.meta_app_id, app_secret=secret,
            version=settings().ig_graph_version,
        )
        pages = await list_pages(user_token, settings().ig_graph_version)
    except httpx.HTTPError as exc:
        _log.warning("meta connect failed channel=%s: %s", channel_id, exc)
        return _page("Meta refused the connection", "<p>Try again in a minute.</p>")

    wanted = (cfg.meta_page_id or "").strip()
    page = next((p for p in pages if p.get("id") == wanted), None) if wanted else (
        pages[0] if len(pages) == 1 else None
    )
    if page is None:
        # Either they granted nothing, or they administer several Pages and we cannot guess.
        names = "".join(f"<li>{p.get('name', '')} — <code>{p.get('id', '')}</code></li>"
                        for p in pages)
        return _page(
            "Choose the Page",
            "<p>Set the Page id in the channel settings and open the link again.</p>"
            f"<ul>{names}</ul>" if names else "<p>No Pages were granted.</p>",
        )

    await _persist(channel_id, page)
    return _page(
        "Connected",
        f"<p><strong>{page.get('name', '')}</strong> is connected. You can close this page.</p>",
    )


async def _persist(channel_id: int, page: dict) -> None:
    """Store the Page token exactly as the manual form does, so the worker sees no difference."""
    dump = {
        "token": page.get("access_token", ""),
        "account_id": page.get("id", ""),
        "base_url": f"https://graph.facebook.com/{settings().ig_graph_version}",
    }
    async with session_scope() as session:
        await session.execute(
            text("DELETE FROM channel_session WHERE channel_id=:id"), {"id": channel_id})
        await session.execute(
            text("UPDATE channel SET account_id=:a, is_active=true WHERE id=:id"),
            {"a": page.get("id", ""), "id": channel_id})
        await session.execute(
            text("INSERT INTO channel_session (channel_id, secret_enc, status)"
                 " VALUES (:ch, :sec, 'active')"),
            {"ch": channel_id, "sec": encrypt(json.dumps(dump))})
