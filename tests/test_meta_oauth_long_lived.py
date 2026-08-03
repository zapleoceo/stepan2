"""A connected client must still be connected tomorrow.

The code exchange returns a user token that expires in about an hour, and /me/accounts mints
Page tokens with the lifetime of whoever asked. Store that and the client's channel dies the
same afternoon they set it up — with no error anywhere, because nothing failed at connect
time. Exchanging for a long-lived user token FIRST yields Page tokens that do not expire.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.modules.meta import oauth  # noqa: E402

_SHORT = "SHORT_USER_TOKEN"  # noqa: S105 — fixture, not a credential
_LONG = "LONG_USER_TOKEN"  # noqa: S105


class _Resp:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _Client:
    """Records every request so the test can assert on the grant type actually sent."""

    def __init__(self, body: dict | Exception) -> None:
        self.body, self.seen = body, []

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def get(self, url: str, params: dict) -> _Resp:
        self.seen.append(params)
        if isinstance(self.body, Exception):
            raise self.body
        return _Resp(self.body)


@pytest.mark.asyncio
async def test_exchange_asks_for_the_long_lived_grant(monkeypatch) -> None:
    client = _Client({"access_token": _LONG})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: client)

    out = await oauth.exchange_long_lived(
        user_token=_SHORT, app_id="APP", app_secret="SECRET", version="v21.0")  # noqa: S106

    assert out == _LONG
    sent = client.seen[0]
    assert sent["grant_type"] == "fb_exchange_token"
    assert sent["fb_exchange_token"] == _SHORT
    assert sent["client_secret"] == "SECRET"  # noqa: S105


@pytest.mark.asyncio
async def test_an_empty_answer_keeps_the_short_token(monkeypatch) -> None:
    """A connection that works for an hour beats no connection at all — but it must not be
    silently mistaken for a healthy one, which is what the log line at the call site is for."""
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kw: _Client({}))

    out = await oauth.exchange_long_lived(
        user_token=_SHORT, app_id="APP", app_secret="SECRET", version="v21.0")  # noqa: S106

    assert out == _SHORT


@pytest.mark.asyncio
async def test_graph_failure_propagates(monkeypatch) -> None:
    """The callback catches httpx.HTTPError and shows "try again" — it must actually get one."""
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **_kw: _Client(httpx.ConnectError("down")))

    with pytest.raises(httpx.HTTPError):
        await oauth.exchange_long_lived(
            user_token=_SHORT, app_id="APP", app_secret="SECRET", version="v21.0")  # noqa: S106
