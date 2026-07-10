"""GraphTransportHTTP.fetch_conversations must page through the Meta /conversations endpoint
via the `after` cursor (the default single page is ~25) up to the configured cap."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from typing import Any  # noqa: E402

from app.adapters.channels.transports import GraphTransportHTTP  # noqa: E402


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._p = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._p


class _FakeClient:
    """Serves conversations in pages keyed by the `after` cursor, recording each requested page."""

    def __init__(self, pages: list[dict]) -> None:
        self._pages = pages          # each: {"data": [...], "next": bool}
        self.requested_after: list[str | None] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def get(self, _url: str, params: dict[str, Any] | None = None) -> _FakeResp:
        after = (params or {}).get("after")
        self.requested_after.append(after)
        idx = 0 if after is None else int(after)
        page = self._pages[idx]
        body: dict[str, Any] = {"data": page["data"]}
        if page.get("next"):
            body["paging"] = {"next": "http://x", "cursors": {"after": str(idx + 1)}}
        return _FakeResp(body)


def _conv(cid: str) -> dict:
    return {"id": cid, "messages": {"data": [
        {"from": {"id": "lead"}, "message": f"m{cid}",
         "created_time": "2026-07-11T00:00:00+0000"}]}}


def _transport() -> GraphTransportHTTP:
    return GraphTransportHTTP(
        base_url="https://graph.facebook.com/v21.0", account_id="447",
        token="tok")  # noqa: S106 — dummy token in a unit test, not a secret


async def test_fetch_conversations_pages_through_all(monkeypatch) -> None:
    # three pages of 2 convs; the first two say there's a next, the third doesn't
    pages = [
        {"data": [_conv("1"), _conv("2")], "next": True},
        {"data": [_conv("3"), _conv("4")], "next": True},
        {"data": [_conv("5"), _conv("6")], "next": False},
    ]
    fake = _FakeClient(pages)
    t = _transport()
    monkeypatch.setattr(t, "_client", lambda: fake)

    out = await t.fetch_conversations()

    assert [c["thread_id"] for c in out] == ["1", "2", "3", "4", "5", "6"]
    assert fake.requested_after == [None, "1", "2"]  # followed the after cursor each page


async def test_fetch_conversations_stops_at_cap(monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setenv("STEPAN2_META_LIVE_CONVERSATIONS", "3")
    settings.cache_clear()
    # every page claims a next → only the cap bounds it
    pages = [{"data": [_conv(str(i)), _conv(str(i + 100))], "next": True} for i in range(10)]
    fake = _FakeClient(pages)
    t = _transport()
    monkeypatch.setattr(t, "_client", lambda: fake)

    out = await t.fetch_conversations()

    assert len(out) == 3            # capped, not the full 20 available
    settings.cache_clear()


async def test_fetch_conversations_skips_empty_threads(monkeypatch) -> None:
    pages = [{"data": [_conv("1"), {"id": "2", "messages": {"data": []}}, _conv("3")],
              "next": False}]
    fake = _FakeClient(pages)
    t = _transport()
    monkeypatch.setattr(t, "_client", lambda: fake)

    out = await t.fetch_conversations()

    assert [c["thread_id"] for c in out] == ["1", "3"]  # the message-less convo is dropped


class _SendClient:
    """Records the POST body; the GET (participant resolve) returns a fixed PSID; the POST
    returns a configurable status/body so both the happy path and a 400 can be exercised."""

    def __init__(self, status: int = 200, body: dict | None = None, text: str = "") -> None:
        self.status = status
        self.body = body if body is not None else {"message_id": "mid_1"}
        self.text = text
        self.posted: dict[str, Any] = {}

    async def __aenter__(self) -> _SendClient:
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def get(self, _url: str, params: dict[str, Any] | None = None):  # noqa: ANN202
        return _FakeResp({"participants": {"data": [
            {"id": "447"}, {"id": "PSID_LEAD"}]}})  # 447 = our account → picks PSID_LEAD

    async def post(self, _url: str, json: dict | None = None):  # noqa: ANN202, A002
        self.posted = json or {}
        resp = _FakeResp(self.body)
        resp.status_code = self.status  # type: ignore[attr-defined]
        resp.text = self.text  # type: ignore[attr-defined]
        return resp


async def test_send_message_includes_messaging_type_response(monkeypatch) -> None:
    """The Send API requires messaging_type for an in-window reply — omitting it is the 400
    that piled up on the Meta channel (2026-07-10)."""
    fake = _SendClient(status=200, body={"message_id": "mid_1"})
    t = _transport()
    monkeypatch.setattr(t, "_client", lambda: fake)
    out = await t.send_message("t_123", "halo kak")
    assert out["message_id"] == "mid_1"
    assert fake.posted["messaging_type"] == "RESPONSE"
    assert fake.posted["recipient"] == {"id": "PSID_LEAD"}  # resolved from participants
    assert fake.posted["message"] == {"text": "halo kak"}


async def test_send_message_surfaces_the_graph_error_body_on_4xx(monkeypatch) -> None:
    """A 4xx must raise with Graph's error BODY (subcode + message), not a bare status line —
    otherwise a 400 is undiagnosable in the log."""
    import pytest
    fake = _SendClient(status=400, body={}, text='{"error":{"code":100,"message":"bad param"}}')
    t = _transport()
    monkeypatch.setattr(t, "_client", lambda: fake)
    with pytest.raises(RuntimeError, match="bad param"):
        await t.send_message("t_123", "halo")
