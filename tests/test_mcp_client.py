"""One MCP client, and the failure policy that differs by direction.

Session opening was copied per caller and the copies disagreed: the CRM reader wrapped the
exchange in asyncio.timeout and passed nothing to the transport, the pusher passed a timeout
to the transport and wrapped nothing. Two callers, two meanings of "timeout". The sender
connector would have been a third.

The policy these tests pin is the part that is easy to get wrong later:
  reads fail OPEN — an unreachable CRM must mean "no opinion", never "hold the reply";
  writes RAISE — a send that failed is a message the lead is still waiting for.
"""
from __future__ import annotations

import asyncio
import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import json  # noqa: E402

import pytest  # noqa: E402

from app.adapters.mcp_client import McpUnavailable, call, payload, read  # noqa: E402


class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Result:
    def __init__(self, text: str | None) -> None:
        self.content = [_Block(text)] if text is not None else []


class _Session:
    """Records the calls and answers whatever it was given."""

    def __init__(self, answer: object = None, boom: Exception | None = None) -> None:
        self.answer, self.boom, self.calls = answer, boom, []

    async def call_tool(self, tool: str, args: dict) -> _Result:
        self.calls.append((tool, args))
        if self.boom:
            raise self.boom
        return _Result(json.dumps(self.answer) if self.answer is not None else None)


def test_payload_unwraps_the_content_envelope() -> None:
    assert payload(_Result(json.dumps({"data": [1, 2]}))) == {"data": [1, 2]}


def test_payload_of_prose_is_none_not_a_crash() -> None:
    """A rate-limit notice or an error page arrives where JSON was expected. That is a None
    the caller handles, not an exception three layers down."""
    assert payload(_Result("<html>429 Too Many Requests</html>")) is None


def test_payload_of_an_empty_answer_is_none() -> None:
    assert payload(_Result(None)) is None


@pytest.mark.asyncio
async def test_call_returns_the_parsed_answer() -> None:
    s = _Session(answer={"ok": True})
    assert await call(s, "sender_reply", {"text": "hi"}) == {"ok": True}
    assert s.calls == [("sender_reply", {"text": "hi"})]


@pytest.mark.asyncio
async def test_a_failed_call_raises_rather_than_returning_none() -> None:
    """The whole point of the split: a caller that is SENDING must be able to tell "the
    server said no" from "the server never answered", because only one of them is worth a
    retry — and returning None for both is how a lead's reply goes missing quietly."""
    s = _Session(boom=ConnectionResetError("connection reset"))
    with pytest.raises(McpUnavailable, match="sender_reply"):
        await call(s, "sender_reply", {"text": "hi"})


def _spy_warnings(monkeypatch) -> list[str]:  # noqa: ANN001
    """Capture what the module logs, without caplog.

    Another test in this suite calls logging.disable(), which short-circuits caplog and makes
    these assertions pass in isolation and fail in the full run — the same trap
    tests/test_channels.py:423 documents. Spying the module's own logger sidesteps it."""
    import app.adapters.mcp_client as mod  # noqa: PLC0415

    seen: list[str] = []
    monkeypatch.setattr(mod.logger, "warning",
                        lambda msg, *a, **_kw: seen.append(str(msg) % a if a else str(msg)))
    return seen


@pytest.mark.asyncio
async def test_a_read_that_fails_returns_none_and_says_what_failed(monkeypatch) -> None:  # noqa: ANN001
    """Reads fail open. The gate asks the CRM whether a manager already called; an
    unreachable CRM must not translate into silence towards the lead."""
    seen = _spy_warnings(monkeypatch)

    async def _using(_s: object) -> None:  # pragma: no cover — never reached
        raise AssertionError("should not run: the session never opens")

    out = await read("https://unreachable.invalid/mcp?token=secret",
                     timeout_s=0.05, using=_using, what="crm state")

    assert out is None
    assert any("crm state" in line for line in seen)  # a log nobody can act on is not a log


@pytest.mark.asyncio
async def test_the_token_never_reaches_the_log(monkeypatch) -> None:  # noqa: ANN001
    """The failure text is logged whole, so a token in the URL is a token in the log — the
    reason mcp_auth moves it into a header in the first place."""
    seen = _spy_warnings(monkeypatch)

    async def _using(_s: object) -> None:  # pragma: no cover
        raise AssertionError("unreachable")

    await read("https://unreachable.invalid/mcp?token=SUPERSECRET0123",
               timeout_s=0.05, using=_using, what="probe")

    assert seen  # the guard is only meaningful if something was logged at all
    assert not any("SUPERSECRET0123" in line for line in seen)


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed() -> None:
    """A cancelled task must stay cancelled. Catching Exception broadly and reporting a
    failure instead would leave a worker shutting down with a phantom error."""
    s = _Session(boom=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await call(s, "whatever", {})
