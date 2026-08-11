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


# ── кто имеет право звать человека ────────────────────────────────────────────


class _Notifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str, **_kw: object) -> None:
        self.sent.append(text)


class _Port:
    def __init__(self, status: SessionStatus) -> None:
        self._s = status

    async def session_status(self) -> SessionStatus:
        return self._s


async def _frozen(db_session, kind: ChannelKind, notifier: _Notifier) -> bool:  # noqa: ANN001
    import app.worker.main as wm

    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=kind, handle="WA Citra" if kind == ChannelKind.WHATSAPP
                 else "ig_acc")
    db_session.add(ch)
    await db_session.flush()
    db_session.add(ChannelSession(channel_id=ch.id, secret_enc=encrypt('{"x":1}'),
                                  status=SessionStatus.ACTIVE))
    await db_session.flush()
    orig = wm._build_notifier  # noqa: SLF001
    wm._build_notifier = lambda _cfg: notifier  # noqa: SLF001
    try:
        return await wm._healthy(  # noqa: SLF001
            db_session, b.id, ch, _Port(SessionStatus.EXPIRED))
    finally:
        wm._build_notifier = orig  # noqa: SLF001


async def test_a_whatsapp_blip_freezes_but_stays_quiet(db_session) -> None:  # noqa: ANN001
    """11.08.2026: «WA Citra» прислал «требует ре-логина» на моргании сессии. Пока Дима
    открывал настройки, wa_watch уже вернул коннектор — страница показывала «активно», и
    сверить тревогу с интерфейсом было нельзя.

    У WhatsApp свой наблюдатель: он ждёт 15 минут прежде чем звать человека и сам тихо
    возвращает починившуюся сессию. Здесь окна ожидания нет, поэтому здесь и не кричим —
    но морозим, чтобы не долбиться в мёртвую сессию."""
    notifier = _Notifier()

    healthy = await _frozen(db_session, ChannelKind.WHATSAPP, notifier)

    assert healthy is False  # заморозка остаётся
    assert notifier.sent == []  # а тревога — дело wa_watch


async def test_instagram_still_shouts_and_names_itself_right(db_session) -> None:  # noqa: ANN001
    """У Instagram своего наблюдателя нет, значит этот алерт — единственный сигнал. И он
    обязан называть коннектор своим именем: текст был захардкожен «IG channel» для всех
    видов сразу, из-за чего воцап-номер представился инстаграмом."""
    notifier = _Notifier()

    healthy = await _frozen(db_session, ChannelKind.INSTAGRAM, notifier)

    assert healthy is False
    assert len(notifier.sent) == 1
    assert "INSTAGRAM" in notifier.sent[0]
    assert "ig_acc" in notifier.sent[0]
