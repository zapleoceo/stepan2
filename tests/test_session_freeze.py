"""IG checkpoint kill-switch: mark_session_status flips ACTIVE→CHALLENGE so
build_channel_port stops loading the session (channel frozen until re-login)."""
from __future__ import annotations

import pytest

from app.adapters.channels.instagram import InstagramAdapter
from app.adapters.crypto import encrypt
from app.adapters.db.models import Branch, Channel, ChannelSession
from app.domain.enums import ChannelKind, SessionStatus
from app.worker import wiring


class _T:
    def __init__(self, health: str) -> None:
        self._h = health

    async def fetch_threads(self):  # noqa: ANN201
        return []

    async def send_direct(self, thread_id, text):  # noqa: ANN001, ANN201
        return {"item_id": "x"}

    async def revoke_direct(self, thread_id, item_id):  # noqa: ANN001, ANN201
        return None

    async def account_health(self) -> str:
        return self._h


async def _channel_with_session(s, status: SessionStatus = SessionStatus.ACTIVE) -> Channel:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM, handle="acc")
    s.add(ch)
    await s.flush()
    s.add(ChannelSession(channel_id=ch.id, secret_enc=encrypt('{"x":1}'), status=status))
    await s.flush()
    return ch


async def test_mark_flips_active_session(db_session) -> None:
    ch = await _channel_with_session(db_session)
    assert await wiring.mark_session_status(db_session, ch.id, SessionStatus.CHALLENGE) is True
    # build now refuses: no ACTIVE session remains
    with pytest.raises(RuntimeError):
        await wiring.build_channel_port(db_session, ch)


async def test_mark_noop_without_active_session(db_session) -> None:
    ch = await _channel_with_session(db_session, status=SessionStatus.CHALLENGE)
    assert await wiring.mark_session_status(db_session, ch.id, SessionStatus.EXPIRED) is False


async def test_adapter_maps_challenge_health() -> None:
    assert await InstagramAdapter(_T("challenge"), handle="acc").session_status() \
        == SessionStatus.CHALLENGE
    assert await InstagramAdapter(_T("ok"), handle="acc").session_status() \
        == SessionStatus.ACTIVE
