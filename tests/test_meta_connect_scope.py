"""The Meta connect form must read the system-user token at CHANNEL scope.

meta_system_user_token is declared scope="channel" in settings/schema.py, so it is saved as
app_setting(branch_id, channel_id, key). The connect handler used to resolve it through
get_settings(branch_id), which skips the channel tier entirely: the token sat one column away
and the form still answered "No meta_system_user_token", with nothing in the logs to say why.

The assertion is on the resolver the handler calls, not on the HTTP response, so it stays true
regardless of how the form renders.
"""
from __future__ import annotations

import pytest

from app.adapters.db.models import AppSetting, Branch, Channel
from app.domain.enums import ChannelKind
from app.modules.settings.service import get_channel_settings, get_settings, invalidate


@pytest.mark.asyncio
async def test_channel_token_is_invisible_to_the_branch_resolver(db_session) -> None:
    branch = Branch(name="TEST", tz_offset=7)
    db_session.add(branch)
    await db_session.flush()
    channel = Channel(branch_id=branch.id, kind=ChannelKind.META_BUSINESS,
                      handle="Zapleo Soft", account_id="207513496325789", is_active=False)
    db_session.add(channel)
    await db_session.flush()
    db_session.add(AppSetting(branch_id=branch.id, channel_id=channel.id,
                              key="meta_system_user_token", value="EAA-channel-scoped"))
    await db_session.commit()
    invalidate(branch.id)

    branch_cfg = await get_settings(db_session, branch.id)
    channel_cfg = await get_channel_settings(db_session, branch.id, channel.id)

    # The bug in one line: the branch view cannot see it, the channel view can.
    assert branch_cfg.meta_system_user_token == ""
    assert channel_cfg.meta_system_user_token == "EAA-channel-scoped"  # noqa: S105


def test_connect_handler_resolves_at_channel_scope() -> None:
    """Guards the call site itself: a future edit back to get_settings would compile, pass every
    other test, and silently break connecting a Meta channel again."""
    import inspect

    from app.api import _routes_channels

    src = inspect.getsource(_routes_channels.meta_connect)
    assert "get_channel_settings(session, branch_id, ch_id)" in src
    assert "get_settings(session, branch_id)" not in src
