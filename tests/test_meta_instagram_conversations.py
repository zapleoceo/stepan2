"""A Meta Business channel must read BOTH inboxes on the Page.

/{page-id}/conversations returns Messenger by default. Instagram Direct sits behind the same
endpoint with platform=instagram. Without the second call the channel connects, authenticates,
reports healthy — and silently reads half of what the business receives. Verified live on
2026-08-02: page 207513496325789 returned its Messenger history and nothing from Direct, while
Direct plainly had a fresh message in it.

One platform failing must not cost the other: Instagram answers "(#200) the account owner has
disabled access to Instagram Direct messages" whenever a business leaves that setting off,
which is an ordinary state and not a reason to lose the Messenger threads too.
"""
from __future__ import annotations

import httpx
import pytest

from app.adapters.channels.transports import GraphTransportHTTP


class _FakeResp:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _FakeClient:
    """Records the params of every call so the test can assert on the platform parameter."""

    def __init__(self, by_platform: dict[str | None, dict]) -> None:
        self.by_platform = by_platform
        self.seen: list[str | None] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def get(self, url: str, params: dict) -> _FakeResp:
        # The transport asks once for the Page's linked IG id, so it can tell which participant
        # is itself. Not what this file tests — answer it and move on.
        if params.get("fields") == "instagram_business_account":
            return _FakeResp({"id": "207513496325789"})
        platform = params.get("platform")
        self.seen.append(platform)
        body = self.by_platform.get(platform)
        if isinstance(body, Exception):
            raise body
        return _FakeResp(body or {"data": []})


def _conv(cid: str, text: str) -> dict:
    return {"id": cid, "messages": {"data": [
        {"from": {"id": "u1"}, "message": text, "created_time": "2026-08-02T13:45:56+0000"}]}}


def _transport(client: _FakeClient) -> GraphTransportHTTP:
    t = GraphTransportHTTP(base_url="https://graph.facebook.com/v21.0",
                           account_id="207513496325789", token="T")  # noqa: S106
    t._client = lambda: client  # type: ignore[method-assign]
    return t


@pytest.mark.asyncio
async def test_both_inboxes_are_read() -> None:
    client = _FakeClient({
        None: {"data": [_conv("t_messenger", "hello from messenger")]},
        "instagram": {"data": [_conv("ig_1", "привет")]},
    })
    out = await _transport(client).fetch_conversations()
    assert client.seen == [None, "instagram"]
    assert {m["message"] for m in out} == {"hello from messenger", "привет"}


@pytest.mark.asyncio
async def test_instagram_disabled_does_not_lose_messenger() -> None:
    """The (#200) 'owner disabled Direct access' case — normal, not an outage."""
    client = _FakeClient({
        None: {"data": [_conv("t_messenger", "still here")]},
        "instagram": httpx.HTTPStatusError("200", request=None, response=None),  # type: ignore[arg-type]
    })
    out = await _transport(client).fetch_conversations()
    assert [m["message"] for m in out] == ["still here"]


@pytest.mark.asyncio
async def test_messenger_failure_does_not_lose_instagram() -> None:
    client = _FakeClient({
        None: httpx.ConnectError("down"),
        "instagram": {"data": [_conv("ig_1", "привет")]},
    })
    out = await _transport(client).fetch_conversations()
    assert [m["message"] for m in out] == ["привет"]


@pytest.mark.asyncio
async def test_empty_threads_are_skipped() -> None:
    client = _FakeClient({
        None: {"data": [{"id": "t_empty", "messages": {"data": []}}]},
        "instagram": {"data": []},
    })
    assert await _transport(client).fetch_conversations() == []
