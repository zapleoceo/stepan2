"""Connecting a Page must also SUBSCRIBE it to our webhook.

Nothing in this codebase ever called /{page-id}/subscribed_apps, so Meta had no reason to push
a single message: a client could complete the whole consent flow, see "Connected", and the
webhook endpoint would stay silent for ever. These tests pin that the connect flow subscribes,
and that a refusal (Meta answers 403 until the app is approved for pages_messaging) is loud in
the logs but never breaks connecting.
"""
from __future__ import annotations

import httpx
import pytest

from app.api import _routes_connect as connect
from app.modules.meta import subscribe as subscribe_mod

_TOKEN = "tok"  # noqa: S105 — test fixture, not a real token


class _FakeResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self) -> object:
        return self._body


class _FakeClient:
    def __init__(self, response: _FakeResponse | Exception) -> None:
        self._response = response
        self.calls: list[tuple[str, dict, dict]] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def post(self, url: str, *, params: dict, headers: dict) -> _FakeResponse:
        self.calls.append((url, params, headers))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _patch_client(monkeypatch, response: _FakeResponse | Exception) -> _FakeClient:
    client = _FakeClient(response)
    monkeypatch.setattr(subscribe_mod.httpx, "AsyncClient", lambda **_kw: client)
    return client


async def test_subscribe_posts_the_messages_field_with_a_header_token(monkeypatch) -> None:
    """`messages` is the field that makes Meta push DMs at all. The Page token goes in the
    Authorization header, never the query string — Graph errors are logged with the URL."""
    client = _patch_client(monkeypatch, _FakeResponse(200, {"success": True}))

    ok, _ = await subscribe_mod.subscribe_page(
        page_id="PAGE1", page_token=_TOKEN, fields="messages,messaging_referrals",
        version="v21.0")

    assert ok is True
    [(url, params, headers)] = client.calls
    assert url.endswith("/v21.0/PAGE1/subscribed_apps")
    assert params == {"subscribed_fields": "messages,messaging_referrals"}
    assert headers["Authorization"] == "Bearer tok"


@pytest.mark.parametrize(
    "response",
    [
        _FakeResponse(403, {"error": {"message": "(#200) Requires pages_messaging"}}),
        _FakeResponse(200, {"success": False}),
        httpx.ConnectError("no route"),
    ],
)
async def test_a_refusal_is_reported_not_raised(monkeypatch, response) -> None:
    """The caller is a user-facing connect page. Every failure mode has to come back as a
    verdict it can log, not an exception that turns into a 500 mid-consent."""
    _patch_client(monkeypatch, response)
    ok, detail = await subscribe_mod.subscribe_page(
        page_id="PAGE1", page_token=_TOKEN, fields="messages", version="v21.0")
    assert ok is False
    assert detail


async def test_connect_flow_subscribes_the_page_it_just_stored(monkeypatch) -> None:
    seen: dict[str, object] = {}

    async def _fake(*, page_id: str, page_token: str, fields: str, version: str):
        seen.update(page_id=page_id, page_token=page_token, fields=fields, version=version)
        return True, "ok"

    monkeypatch.setattr(connect, "subscribe_page", _fake)
    await connect._subscribe_webhook(5, {"id": "PAGE1", "access_token": _TOKEN})  # noqa: SLF001

    assert seen["page_id"] == "PAGE1"
    assert seen["page_token"] == _TOKEN
    assert "messages" in str(seen["fields"])


async def test_a_failed_subscription_logs_loudly_and_does_not_raise(monkeypatch) -> None:
    """Until App Review approves pages_messaging this call answers 403 for every client. That
    must not block connecting — the poll still ingests — but believing webhooks are live when
    they are not is exactly the silent failure this ERROR line exists to prevent."""
    async def _refused(**_kw: object):
        return False, "HTTP 403: (#200) Requires pages_messaging"

    errors: list[str] = []
    # Captured at the logger, not through caplog: the suite's log configuration is shared
    # state, and a level set by an unrelated test would make this pass or fail by ordering.
    monkeypatch.setattr(connect, "subscribe_page", _refused)
    monkeypatch.setattr(connect._log, "error",  # noqa: SLF001
                        lambda msg, *args, **_kw: errors.append(msg % args))

    await connect._subscribe_webhook(5, {"id": "PAGE1", "access_token": _TOKEN})  # noqa: SLF001

    assert any("WEBHOOK SUBSCRIPTION FAILED" in line for line in errors)
    assert any("pages_messaging" in line for line in errors)  # Graph's own words, not ours
