"""One way to talk to an MCP server, for every MCP server we talk to.

Opening a session was copied per caller: the CRM reader wrapped the whole exchange in
asyncio.timeout and passed nothing to the transport, the CRM pusher passed a timeout to the
transport and wrapped nothing, and each caught exceptions its own way. Two copies already
disagreed about what a timeout means; sender would have been a third.

The rule that matters is not the plumbing but the failure policy, and it differs by direction:

  READING is fail-open. A CRM outage must not silence a live sales bot — the gate treats
  "no opinion" as "carry on", so a failed read returns None and the caller shrugs.

  WRITING is not. A send that failed is a message the lead is still waiting for, so the
  caller has to hear about it and retry from the outbox. `call` raises; only `read` swallows.

The token travels in the Authorization header, never in the query string — see mcp_auth: a
token in a URL ends up in the text of any transport error, and those get logged whole.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from app.adapters.mcp_auth import connect_args, redact

logger = logging.getLogger(__name__)


class McpUnavailable(RuntimeError):
    """The server could not be reached or refused the exchange.

    Distinct from a tool answering "no" — that is a result, and the caller decides what it
    means. This is "we never got an answer", which is what a retry is for."""


@asynccontextmanager
async def session(url: str, *, timeout_s: float) -> AsyncIterator[Any]:
    """An initialized MCP session, or McpUnavailable.

    The timeout covers the WHOLE exchange, not just the connect: a server that accepts the
    connection and then stalls is the failure mode that actually happens, and a connect-only
    timeout does not catch it."""
    # Local import so the app stays importable without the mcp package installed.
    from mcp.client.session import ClientSession  # noqa: PLC0415
    from mcp.client.streamable_http import streamablehttp_client  # noqa: PLC0415

    target, headers = connect_args(url)
    delivered = False
    try:
        async with asyncio.timeout(timeout_s):
            async with streamablehttp_client(target, headers=headers) as (read, write, _):
                async with ClientSession(read, write) as s:
                    await s.initialize()
                    yield s
                    # Skipped when the body raised: that exception is thrown in at the yield.
                    delivered = True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # A failure while CLOSING a session whose work already finished is not a failed
        # exchange. streamablehttp_client raises ClosedResourceError on teardown, and this
        # handler used to turn it into McpUnavailable, discarding an answer already received
        # and parsed. That killed the CRM read gate outright — 25 of 25 reads in a day were
        # logged as failures while the server was healthy and answering, and because reads
        # fail open, nothing surfaced except leads quietly never getting their CRM state.
        if delivered:
            logger.debug("mcp session teardown after a completed exchange (%s): %s",
                         redact(url), str(exc)[:200])
            return
        raise McpUnavailable(f"{redact(url)}: {str(exc)[:200]}") from exc


def payload(result: Any) -> dict | list | None:
    """The JSON an MCP tool returned, out of the content envelope it arrives in.

    Servers wrap the answer in a list of content blocks and put the JSON in the text of the
    first one. A server that answers with prose instead — an error page, a rate-limit notice —
    parses as nothing, and that is a None the caller must handle, not a crash."""
    content = getattr(result, "content", None) or []
    if not content:
        return None
    text = getattr(content[0], "text", None)
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("mcp tool answered with non-JSON: %s", str(text)[:160])
        return None


async def call(s: Any, tool: str, args: dict[str, Any]) -> dict | list | None:
    """Call a tool on an open session. Raises McpUnavailable if the call itself fails."""
    try:
        return payload(await s.call_tool(tool, args))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise McpUnavailable(f"{tool}: {str(exc)[:200]}") from exc


async def read(
    url: str, *, timeout_s: float, using: Callable[[Any], Any], what: str,
) -> Any | None:
    """Run a read-only exchange, returning None on any failure.

    `what` names the operation in the log — a warning saying only "mcp failed" is one nobody
    can act on. Reads fail open on purpose: the gate reads CRM state to decide whether to hold
    a reply, and an unreachable CRM must mean "no opinion", never "stay silent"."""
    try:
        async with session(url, timeout_s=timeout_s) as s:
            return await using(s)
    except McpUnavailable as exc:
        logger.warning("mcp read failed (%s): %s", what, exc)
        return None
