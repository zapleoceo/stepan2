"""build_channel_port: Instagram, WhatsApp and Meta wired from ChannelSession."""
import json

import pytest

from app.adapters import crypto
from app.adapters.channels.instagram import InstagramAdapter
from app.adapters.db.models import Branch, Channel, ChannelSession
from app.domain.enums import ChannelKind, SessionStatus
from app.worker.wiring import build_channel_port


async def _ig_channel(s, *, with_session: bool):
    b = Branch(name="ID", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM, handle="itstep_jakarta")
    s.add(ch)
    await s.flush()
    if with_session:
        dump = {"uuids": {"device_id": "x"}, "cookies": {}}  # фейковый instagrapi dump
        s.add(ChannelSession(channel_id=ch.id, status=SessionStatus.ACTIVE,
                             secret_enc=crypto.encrypt(json.dumps(dump))))
        await s.flush()
    return ch


async def test_build_instagram_port(db_session):
    ch = await _ig_channel(db_session, with_session=True)
    port = await build_channel_port(db_session, ch)
    assert isinstance(port, InstagramAdapter)
    assert port.kind == ChannelKind.INSTAGRAM   # instagrapi не импортируется (lazy)


async def test_build_port_no_session_raises(db_session):
    ch = await _ig_channel(db_session, with_session=False)
    with pytest.raises(RuntimeError):
        await build_channel_port(db_session, ch)


async def test_build_whatsapp_no_session_raises(db_session):
    b = Branch(name="ID")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.WHATSAPP, handle="x")
    db_session.add(ch)
    await db_session.flush()
    # WA is wired; without a credential session it raises RuntimeError
    with pytest.raises(RuntimeError, match="no WhatsApp config"):
        await build_channel_port(db_session, ch)
