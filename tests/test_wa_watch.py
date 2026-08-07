"""Сторож привязанных телефонов: молчит на морганиях, будит на настоящем обрыве.

Упавшее связанное устройство выглядит ровно как тихий день — ничего не падает, ничего не
краснеет, диалоги просто перестают приходить. Узнать об этом должен человек, но не о каждом
переподключении: алерт, приходящий по любому поводу, отключают через неделю.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.crypto import encrypt
from app.adapters.db.models import Branch, Channel, ChannelSession
from app.domain.enums import ChannelKind, SessionStatus
from app.modules.channels import wa_watch


class _Notifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, *, text: str, topic_id: int | None = None) -> object:
        self.sent.append(text)
        return None


async def _channel(s, *, status: SessionStatus, last_ok: datetime | None):  # noqa: ANN001
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    channel = Channel(branch_id=branch.id, kind=ChannelKind.WHATSAPP,
                      handle="WA Maya", read_only=True)
    s.add(channel)
    await s.flush()
    s.add(ChannelSession(
        channel_id=channel.id, status=status, last_ok_at=last_ok,
        secret_enc=encrypt(json.dumps({"instance": "wa-1", "read_only": True})),
    ))
    await s.flush()
    return branch.id, channel.id


@pytest.fixture
def _linked(monkeypatch):  # noqa: ANN001, ANN201
    """Pin what Evolution reports, without a network."""
    def _set(value: bool | None) -> None:
        async def _fake(row, channel):  # noqa: ANN001, ANN202
            return value
        monkeypatch.setattr(wa_watch, "_is_linked", _fake)
    return _set


# ── моргание ──────────────────────────────────────────────────────────────────


async def test_a_brief_drop_says_nothing(db_session, _linked) -> None:  # noqa: ANN001
    """Evolution переподключается сам за минуты. Алерт на это — шум, который научит
    игнорировать и настоящий."""
    now = datetime.now(UTC).replace(tzinfo=None)
    bid, _ = await _channel(db_session, status=SessionStatus.ACTIVE,
                            last_ok=now - timedelta(minutes=2))
    _linked(False)
    tg = _Notifier()

    assert await wa_watch.watch(db_session, bid, tg) == 1
    assert tg.sent == []


async def test_a_long_outage_reaches_a_human(db_session, _linked) -> None:  # noqa: ANN001
    now = datetime.now(UTC).replace(tzinfo=None)
    bid, _ = await _channel(db_session, status=SessionStatus.ACTIVE,
                            last_ok=now - timedelta(hours=2))
    _linked(False)
    tg = _Notifier()

    await wa_watch.watch(db_session, bid, tg)

    assert len(tg.sent) == 1
    assert "WA Maya" in tg.sent[0]  # какой именно номер, а не «канал 18»


async def test_the_same_outage_is_reported_once(db_session, _linked) -> None:  # noqa: ANN001
    """Каждые пять минут — то же самое сообщение: так гасят уведомления целиком."""
    now = datetime.now(UTC).replace(tzinfo=None)
    bid, _ = await _channel(db_session, status=SessionStatus.ACTIVE,
                            last_ok=now - timedelta(hours=2))
    _linked(False)
    tg = _Notifier()

    await wa_watch.watch(db_session, bid, tg)
    await wa_watch.watch(db_session, bid, tg)

    assert len(tg.sent) == 1


# ── восстановление ────────────────────────────────────────────────────────────


async def test_a_session_that_healed_is_unfrozen(db_session, _linked) -> None:  # noqa: ANN001
    """Гейт здоровья выбрасывает канал из ACTIVE на любом не-open состоянии, а замороженный
    канал обычные циклы больше не опрашивают. Без этого двухминутный реконнект стоил бы
    постоянного простоя и напрасного пере-сканирования QR."""
    bid, ch_id = await _channel(db_session, status=SessionStatus.EXPIRED, last_ok=None)
    _linked(True)

    assert await wa_watch.watch(db_session, bid, _Notifier()) == 0

    row = (await db_session.execute(
        ChannelSession.__table__.select().where(ChannelSession.channel_id == ch_id)
    )).mappings().first()
    assert row["status"] == SessionStatus.ACTIVE
    assert row["last_ok_at"] is not None


# ── чего сторож НЕ делает ─────────────────────────────────────────────────────


async def test_our_own_outage_is_not_reported_as_theirs(db_session, _linked) -> None:  # noqa: ANN001
    """Недоступный Evolution ничего не говорит о телефоне менеджера. Сказать обратное —
    отправить человека пере-сканировать рабочий QR."""
    now = datetime.now(UTC).replace(tzinfo=None)
    bid, _ = await _channel(db_session, status=SessionStatus.ACTIVE,
                            last_ok=now - timedelta(hours=2))
    _linked(None)
    tg = _Notifier()

    assert await wa_watch.watch(db_session, bid, tg) == 0
    assert tg.sent == []


async def test_a_never_paired_channel_is_not_an_outage(db_session, _linked) -> None:  # noqa: ANN001
    branch = Branch(name="T", lang="id")
    db_session.add(branch)
    await db_session.flush()
    db_session.add(Channel(branch_id=branch.id, kind=ChannelKind.WHATSAPP, handle="new"))
    await db_session.flush()
    _linked(False)
    tg = _Notifier()

    assert await wa_watch.watch(db_session, branch.id, tg) == 0
    assert tg.sent == []


async def test_the_clock_starts_before_it_alarms(db_session, _linked) -> None:  # noqa: ANN001
    """Первый раз, когда канал увиден лежащим, известно лишь что он лежит сейчас — а не
    сколько. Алерт «отключён 0 мин» бесполезен."""
    bid, _ = await _channel(db_session, status=SessionStatus.ACTIVE, last_ok=None)
    _linked(False)
    tg = _Notifier()

    await wa_watch.watch(db_session, bid, tg)

    assert tg.sent == []
