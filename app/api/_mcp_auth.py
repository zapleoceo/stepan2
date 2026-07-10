"""Shared bearer-token guard for the mounted MCP ASGI apps (write connector + reader).

A token is accepted from `Authorization: Bearer <t>` or `?key=<t>` (capability URL for
header-less web clients). Each mount supplies its own token set via `tokens_fn`, so the
write connector and the read-only reader are gated by different, independently revocable
secrets.
"""
from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

# The authenticated MCP authorization (McpAuthz) for the current request, set by
# token_guard so the mounted FastMCP tools — which never see the raw token — can read
# the token's branch scope. None only outside a guarded request (tools always run inside).
_authz_var: contextvars.ContextVar[Any] = contextvars.ContextVar("mcp_authz", default=None)


def current_mcp_authz() -> Any:
    """The McpAuthz for the in-flight MCP request, or None outside one."""
    return _authz_var.get()


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


def token_guard(app, authorize: Callable[[str], Awaitable[Any]]):  # noqa: ANN001, ANN201
    """Wrap an ASGI app, rejecting HTTP calls whose token authorize() denies. `authorize`
    returns a truthy McpAuthz on success (or None to deny); the authz is stashed in a
    contextvar for the request so the app's tools can enforce its branch scope."""

    class _Guard:
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http":
                authz = await authorize(extract_token(scope))
                if not authz:
                    await JSONResponse({"error": "unauthorized"}, status_code=401)(
                        scope, receive, send)
                    return
                reset = _authz_var.set(authz)
                try:
                    await app(scope, receive, send)
                finally:
                    _authz_var.reset(reset)
                return
            await app(scope, receive, send)

    return _Guard()
