"""Facebook Login for Business — the flow a CLIENT uses to connect their own Page.

Until now a Meta channel was connected by pasting a System User token into the admin. That
works for us and for nobody else: a client will not go hunting for a token, and Meta's App
Review requires a screen recording of a user granting the permissions, which a paste box
cannot show. This module is the pure half of the flow — URL building, state signing, code
exchange — so it can be tested without a browser or a live app.

The state parameter is signed, not stored: it carries the channel id and is verified on the
way back. An unsigned state would let anyone hand us a code for a channel of their choosing.
"""
from __future__ import annotations

import time

import httpx

from app.api._session import sign, verify

_DIALOG = "https://www.facebook.com/{ver}/dialog/oauth"
_GRAPH = "https://graph.facebook.com/{ver}"

# What the agent genuinely needs, and nothing more — App Review rejects unused permissions,
# and every extra scope is one more thing the client is asked to trust us with.
#
# `pages_manage_metadata` is the one that looks droppable and is not: /{page-id}/subscribed_apps
# refuses without it, so leaving it out means subscribe_page 403s for every client that ever
# connects and the webhook never fires. It is also load-bearing for App Review itself — a
# permission the consent screen never shows is a permission the reviewer cannot see us ask for,
# and an unshown permission rejects the whole submission, not just itself.
SCOPES = (
    "pages_show_list",
    "pages_messaging",
    "pages_manage_metadata",
    "pages_read_engagement",
    "instagram_basic",
    "instagram_manage_messages",
    "business_management",
)

STATE_MAX_AGE_S = 900  # 15 min: long enough to read the consent screen, short enough to matter


def state_token(channel_id: int, secret: str) -> str:
    return sign({"ch": channel_id, "iat": int(time.time())}, secret)


def state_channel_id(token: str, secret: str) -> int | None:
    """Channel id from a state token, or None if forged, tampered with, or stale."""
    payload = verify(token, secret, STATE_MAX_AGE_S)
    if not payload:
        return None
    ch = payload.get("ch")
    return ch if isinstance(ch, int) else None


def authorize_url(*, app_id: str, redirect_uri: str, state: str, version: str) -> str:
    from urllib.parse import urlencode  # noqa: PLC0415

    q = urlencode({
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "response_type": "code",
        "scope": ",".join(SCOPES),
    })
    return f"{_DIALOG.format(ver=version)}?{q}"


async def exchange_code(
    *, code: str, app_id: str, app_secret: str, redirect_uri: str, version: str,
) -> str:
    """Authorization code → user access token. Raises httpx.HTTPError on a Graph failure."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{_GRAPH.format(ver=version)}/oauth/access_token",
            params={
                "client_id": app_id,
                "client_secret": app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        resp.raise_for_status()
    return str(resp.json().get("access_token", ""))


async def exchange_long_lived(
    *, user_token: str, app_id: str, app_secret: str, version: str,
) -> str:
    """Short-lived user token → long-lived one (~60 days).

    Skipping this is a silent time bomb: the token from the code exchange expires in about an
    hour, and a Page token minted from it inherits that lifetime, so the client's channel goes
    dead the same afternoon they connected it. A Page token derived from a LONG-lived user
    token does not expire at all — which is the only version worth storing.
    """
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{_GRAPH.format(ver=version)}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": user_token,
            },
        )
        resp.raise_for_status()
    return str(resp.json().get("access_token", "")) or user_token


async def list_pages(user_token: str, version: str) -> list[dict]:
    """Pages this user administers, each with its own Page token and linked IG account.

    The Page token is what the Send API needs; the user token cannot post as a Page.
    """
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{_GRAPH.format(ver=version)}/me/accounts",
            params={
                "access_token": user_token,
                "fields": "id,name,access_token,instagram_business_account",
            },
        )
        resp.raise_for_status()
    data = resp.json().get("data", [])
    return data if isinstance(data, list) else []
