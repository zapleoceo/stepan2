"""A Meta Business channel must call Graph with a PAGE token, not the System User token.

Verified against live Graph on 2026-07-29: /{page-id}/conversations answers
"(#190) This method must be called with a Page Access Token" when given a System User token.
The settings field asks the operator for the System User token — the only one they can
actually obtain — so the derivation belongs in the wiring, not in their hands.

The last test is the guard rail for this change: Instagram (instagrapi) is the transport every
live branch runs on, and nothing here may reach it.
"""
from __future__ import annotations

import httpx
import pytest

from app.connectors import meta_business
from app.worker import wiring

_SU = "SU-TOKEN"  # noqa: S105
_PAGE = "207513496325789"


@pytest.fixture(autouse=True)
def _clear_cache():
    meta_business._PAGE_TOKENS.clear()
    yield
    meta_business._PAGE_TOKENS.clear()


@pytest.mark.asyncio
async def test_system_user_token_is_exchanged_for_a_page_token(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    async def fake(su: str, page: str) -> str:
        calls.append((su, page))
        return "PAGE-TOKEN"

    monkeypatch.setattr(meta_business, "page_access_token", fake)
    assert await meta_business._page_token_cached(_SU, _PAGE, 16) == "PAGE-TOKEN"
    assert calls == [(_SU, _PAGE)]


@pytest.mark.asyncio
async def test_exchange_runs_once_not_on_every_tick(monkeypatch) -> None:
    """build_channel_port runs on every worker tick; a Graph round-trip each time would be a
    self-inflicted rate limit."""
    calls = 0

    async def fake(su: str, page: str) -> str:
        nonlocal calls
        calls += 1
        return "PAGE-TOKEN"

    monkeypatch.setattr(meta_business, "page_access_token", fake)
    for _ in range(5):
        await meta_business._page_token_cached(_SU, _PAGE, 16)
    assert calls == 1


@pytest.mark.asyncio
async def test_rotating_the_source_token_invalidates_the_cache(monkeypatch) -> None:
    """Otherwise a revoked token keeps working from cache until the worker restarts."""
    seen: list[str] = []

    async def fake(su: str, page: str) -> str:
        seen.append(su)
        return f"PAGE-FOR-{su}"

    monkeypatch.setattr(meta_business, "page_access_token", fake)
    first = await meta_business._page_token_cached("SU-OLD", _PAGE, 16)
    second = await meta_business._page_token_cached("SU-NEW", _PAGE, 16)
    assert first != second
    assert seen == ["SU-OLD", "SU-NEW"]


@pytest.mark.asyncio
async def test_failed_exchange_degrades_instead_of_killing_the_channel(monkeypatch) -> None:
    async def boom(su: str, page: str) -> str:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(meta_business, "page_access_token", boom)
    assert await meta_business._page_token_cached(_SU, _PAGE, 16) == _SU


@pytest.mark.asyncio
async def test_no_page_id_means_no_graph_call(monkeypatch) -> None:
    async def fail(su: str, page: str) -> str:
        raise AssertionError("must not call Graph without a page id")

    monkeypatch.setattr(meta_business, "page_access_token", fail)
    assert await meta_business._page_token_cached(_SU, "", 16) == _SU


@pytest.mark.asyncio
async def test_instagram_channels_never_reach_the_exchange(monkeypatch, db_session) -> None:
    """The live branches run on instagrapi. This change must be invisible to them."""
    from app.adapters.db.models import Branch, Channel, ChannelSession
    from app.domain.enums import ChannelKind, SessionStatus

    async def fail(su: str, page: str) -> str:
        raise AssertionError("Instagram must not go through the Page-token exchange")

    monkeypatch.setattr(meta_business, "page_access_token", fail)

    branch = Branch(name="IG branch", tz_offset=7)
    db_session.add(branch)
    await db_session.flush()
    ch = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM, handle="itstep_jakarta")
    db_session.add(ch)
    await db_session.flush()
    db_session.add(ChannelSession(channel_id=ch.id, status=SessionStatus.ACTIVE,
                                  secret_enc=_enc({"settings": {}, "proxy": ""})))
    await db_session.commit()

    from app.adapters.channels.instagram import InstagramAdapter
    port = await wiring.build_channel_port(db_session, ch)
    # Still an Instagram adapter, and the monkeypatched exchange was never reached.
    assert isinstance(port, InstagramAdapter)


def _enc(payload: dict) -> str:
    import json as _json

    from app.adapters.crypto import encrypt
    return encrypt(_json.dumps(payload))
