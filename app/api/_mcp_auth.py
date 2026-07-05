"""Shared bearer-token guard for the mounted MCP ASGI apps (write connector + reader).

A token is accepted from `Authorization: Bearer <t>` or `?key=<t>` (capability URL for
header-less web clients). Each mount supplies its own token set via `tokens_fn`, so the
write connector and the read-only reader are gated by different, independently revocable
secrets.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send


def extract_token(scope: Scope) -> str:
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    token = headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token:
        for part in scope.get("query_string", b"").decode().split("&"):
            if part.startswith("key="):
                return part[4:]
    return token


def split_tokens(secret: str) -> list[str]:
    """A secret env var may hold several comma-separated tokens (owner, partner, …)."""
    return [t.strip() for t in secret.split(",") if t.strip()]


def token_guard(app, authorize: Callable[[str], Awaitable[bool]]):  # noqa: ANN001, ANN201
    """Wrap an ASGI app, rejecting HTTP calls whose token authorize() denies."""

    class _Guard:
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http" and not await authorize(extract_token(scope)):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(
                    scope, receive, send)
                return
            await app(scope, receive, send)

    return _Guard()
