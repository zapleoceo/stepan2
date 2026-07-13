"""Saving a channel's metadata (which can flip is_active) must refresh the channel LIST, not
just the edit sub-form — otherwise the row keeps showing the old on/off state and the toggle
looks like it didn't save (the "can't disable the Meta channel" report)."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from starlette.requests import Request  # noqa: E402

import app.api._routes_channels as rc  # noqa: E402
from app.adapters.db.models import Branch, Channel  # noqa: E402


class _Scope:
    def __init__(self, s) -> None:
        self._s = s

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *_a) -> bool:
        return False


def _req() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                    "headers": []})


async def test_channel_save_refreshes_the_list(db_session, monkeypatch) -> None:
    b = Branch(name="B", lang="en")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind="meta_business", is_active=True)
    db_session.add(ch)
    await db_session.flush()
    monkeypatch.setattr(rc, "session_scope", lambda: _Scope(db_session))

    # uncheck "active" (is_active="") and save
    resp = await rc.channel_save(ch.id, _req(), handle="", account_id="", is_active="")

    assert resp.headers.get("HX-Trigger") == "refreshChannelList"  # list re-renders → row updates
    await db_session.refresh(ch)
    assert ch.is_active is False                                   # the toggle actually persisted
