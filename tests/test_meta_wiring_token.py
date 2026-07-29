"""A Meta Business channel must build from the token the operator actually pasted.

The connector editor saves the system-user token as a per-channel app_setting. The worker
used to look for it in ChannelSession instead — and nothing in the codebase ever writes a
ChannelSession for this kind. So a correct token could be saved, shown as saved, and the
worker would still log "no active token" on every tick, forever. These tests pin the setting
as the source and keep the session as a fallback.
"""
from __future__ import annotations

import pytest

from app.adapters.channels.meta_business import MetaBusinessAdapter
from app.adapters.db.models import AppSetting, Branch, Channel
from app.domain.enums import ChannelKind
from app.worker.wiring import build_channel_port

_TOKEN = "EAAtest-system-user-token"  # noqa: S105 — fixture, not a credential
_PAGE = "207513496325789"


async def _channel(db_session, *, token: str | None = _TOKEN,
                   page: str | None = _PAGE) -> Channel:
    branch = Branch(name="Zapleo Demo")
    db_session.add(branch)
    await db_session.flush()
    channel = Channel(branch_id=branch.id, kind=ChannelKind.META_BUSINESS,
                      handle="Zapleo Soft", account_id=None, is_active=True)
    db_session.add(channel)
    await db_session.flush()
    if token is not None:
        db_session.add(AppSetting(branch_id=branch.id, channel_id=channel.id,
                                  key="meta_system_user_token", value=token))
    if page is not None:
        db_session.add(AppSetting(branch_id=branch.id, channel_id=channel.id,
                                  key="meta_page_id", value=page))
    await db_session.flush()
    return channel


@pytest.mark.asyncio
async def test_builds_from_the_channel_setting_without_any_session(db_session) -> None:
    channel = await _channel(db_session)
    port = await build_channel_port(db_session, channel)
    assert isinstance(port, MetaBusinessAdapter)


@pytest.mark.asyncio
async def test_page_id_comes_from_the_setting_when_the_channel_row_has_none(db_session) -> None:
    """account_id is what every Graph path is built from; an empty one silently 404s."""
    channel = await _channel(db_session)
    port = await build_channel_port(db_session, channel)
    assert port._account_id == _PAGE  # noqa: SLF001


@pytest.mark.asyncio
async def test_missing_token_still_raises(db_session) -> None:
    channel = await _channel(db_session, token=None)
    with pytest.raises(RuntimeError, match="no active token"):
        await build_channel_port(db_session, channel)


@pytest.mark.asyncio
async def test_graph_host_is_facebook_not_instagram(db_session) -> None:
    """The transport calls /{page-id}/conversations and /{page-id}/messages — Messenger Send
    API paths, which live on graph.facebook.com. The old default pointed at
    graph.instagram.com, where those paths do not exist."""
    channel = await _channel(db_session)
    port = await build_channel_port(db_session, channel)
    assert "graph.facebook.com" in port._t._base  # noqa: SLF001
