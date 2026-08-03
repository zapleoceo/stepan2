"""Subscribe a Page to our webhook — the step without which the webhook receives nothing.

A verified callback URL only tells Meta the endpoint is ours. Delivery starts when the PAGE
is subscribed to the app for the `messages` field via /{page-id}/subscribed_apps. Nothing in
this codebase ever called it, so the handler could have been perfect and still never fired.

It can legitimately fail: until the app passes App Review for pages_messaging, Meta answers
403 here. That must be impossible to miss in the logs and must NOT break connecting — the
client's Page is properly connected either way, and the poll keeps ingesting meanwhile.
"""
from __future__ import annotations

import httpx

_GRAPH = "https://graph.facebook.com/{ver}"


async def subscribe_page(
    *, page_id: str, page_token: str, fields: str, version: str,
) -> tuple[bool, str]:
    """(subscribed, detail). Never raises — the caller is a user-facing connect flow."""
    if not page_id or not page_token:
        return False, "missing page id or page token"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # Token in the Authorization header, not a query param: Graph errors are logged
            # with the request URL, and a ?access_token= would print a live Page token.
            resp = await client.post(
                f"{_GRAPH.format(ver=version)}/{page_id}/subscribed_apps",
                params={"subscribed_fields": fields},
                headers={"Authorization": f"Bearer {page_token}"},
            )
    except httpx.HTTPError as exc:
        return False, f"transport error: {exc}"
    if resp.status_code >= 400:
        return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
    try:
        body = resp.json()
    except ValueError:
        return False, f"unparseable response: {resp.text[:300]}"
    return bool(body.get("success")), resp.text[:300]
