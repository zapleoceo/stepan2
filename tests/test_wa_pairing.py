"""Привязка номера: имя инстанса, состояния панели и то, что она никогда не врёт о статусе.

Панель раньше просила URL, instance и API-ключ — три поля, осмысленные только для того,
кто уже создал инстанс руками где-то ещё. Теперь оператор отвечает на единственный вопрос,
на который может ответить: чей это номер и можно ли в него писать.
"""
from __future__ import annotations

import pytest

from app.connectors.whatsapp_ui import _ch_wa_form, wa_instance_name, wa_qr_panel
from app.domain.enums import SessionStatus

# ── имя инстанса ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("typed", [
    "+62 811-1185-8519", "6281111858519", "+62-811-1185-8519", "62 811 1185 8519",
])
def test_however_the_number_is_typed_it_is_one_instance(typed: str) -> None:
    """Имя выводится из цифр, а не печатается. Два канала на один номер подрались бы за
    один и тот же слот связанного устройства на телефоне."""
    assert wa_instance_name(typed) == "wa-6281111858519"


def test_a_number_with_no_digits_yields_no_instance() -> None:
    """Роут ловит это до сети: создавать инстанс с пустым именем незачем."""
    assert wa_instance_name("+ - ()") == "wa-"


# ── форма ─────────────────────────────────────────────────────────────────────


def test_the_form_asks_for_a_phone_and_nothing_technical() -> None:
    html = _ch_wa_form(7)
    assert 'name="phone"' in html
    assert "/ui/channels/7/wa/pair" in html
    for gone in ("base_url", "instance", "api_key"):
        assert gone not in html


def test_read_only_is_ticked_by_default() -> None:
    """Первый номер — менеджерский, и цена ошибки несимметрична: лишняя галка ничего не
    ломает, забытая означает, что бот может написать клиенту от имени человека."""
    html = _ch_wa_form(7)
    assert 'name="read_only"' in html
    assert "checked" in html


def test_an_error_is_shown_on_the_form_not_swallowed() -> None:
    assert "boom" in _ch_wa_form(7, error="boom")


# ── панель QR ─────────────────────────────────────────────────────────────────


def test_the_qr_panel_shows_the_code_and_watches_for_the_phone() -> None:
    html = wa_qr_panel(3, "data:image/png;base64,AAA", "+62 811")
    assert "data:image/png;base64,AAA" in html
    assert "/ui/channels/3/wa/state" in html
    assert "every 3s" in html


def test_the_panel_names_the_number_being_linked() -> None:
    """Оператор привязывает четыре номера подряд — панель без номера превращается в
    рулетку «чей телефон я сейчас держу»."""
    assert "+62 811-1185-8519" in wa_qr_panel(3, "data:x", "+62 811-1185-8519")


def test_a_missing_qr_says_so_instead_of_rendering_a_broken_image() -> None:
    html = wa_qr_panel(3, "", "+62 811")
    assert "<img" not in html
    assert "/ui/channels/3/wa/pair" in html  # кнопка «новый код» остаётся достижимой


def test_the_panel_offers_a_way_out() -> None:
    """Отмена отвязывает наше устройство. Брошенный инстанс держал бы один из четырёх
    слотов на чужом аккаунте просто так."""
    assert "/ui/channels/3/wa/cancel" in wa_qr_panel(3, "data:x", "+62 811")


def test_a_hostile_number_cannot_inject_markup() -> None:
    html = wa_qr_panel(3, "data:x", '"><script>alert(1)</script>')
    assert "<script>" not in html


# ── статус сессии ─────────────────────────────────────────────────────────────


def test_pending_is_not_active() -> None:
    """Пока телефон не подтвердил, воркер не должен получить порт на этот канал:
    active_session_settings отдаёт только ACTIVE."""
    assert SessionStatus.PENDING.value == "pending"
    assert SessionStatus.PENDING is not SessionStatus.ACTIVE


# ── сборка порта ──────────────────────────────────────────────────────────────


async def test_a_paired_channel_builds_its_port_from_the_environment(
    db_session, monkeypatch,  # noqa: ANN001
) -> None:
    """Панель перестала спрашивать адрес и ключ — их знает окружение. Сборка порта про это
    не знала и падала на `KeyError: 'base_url'`, а падала она в воркере: канал привязан,
    в интерфейсе «активно», и ровно ноль диалогов. Поймано на первом живом номере."""
    import json

    from app.adapters.crypto import encrypt
    from app.adapters.db.models import Branch, Channel, ChannelSession
    from app.config import settings
    from app.connectors.whatsapp import build_port
    from app.domain.enums import ChannelKind, SessionStatus

    monkeypatch.setenv("STEPAN2_EVOLUTION_URL", "http://evolution:8080")
    monkeypatch.setenv("STEPAN2_EVOLUTION_API_KEY", "k")
    settings.cache_clear()

    branch = Branch(name="T", lang="id")
    db_session.add(branch)
    await db_session.flush()
    channel = Channel(branch_id=branch.id, kind=ChannelKind.WHATSAPP)
    db_session.add(channel)
    await db_session.flush()
    db_session.add(ChannelSession(
        channel_id=channel.id,
        secret_enc=encrypt(json.dumps(
            {"instance": "wa-628119720022", "phone": "+628119720022", "read_only": True})),
        status=SessionStatus.ACTIVE,
    ))
    await db_session.flush()

    port = await build_port(db_session, channel)

    assert port.read_only is True
    settings.cache_clear()


async def test_a_channel_paired_by_the_old_form_still_works(
    db_session, monkeypatch,  # noqa: ANN001
) -> None:
    """Строка, записанная старой формой из трёх полей, несёт свои адрес и ключ. Она должна
    продолжать работать без пере-привязки — иначе релиз молча гасит живой канал."""
    import json

    from app.adapters.crypto import encrypt
    from app.adapters.db.models import Branch, Channel, ChannelSession
    from app.config import settings
    from app.connectors.whatsapp import build_port
    from app.domain.enums import ChannelKind, SessionStatus

    monkeypatch.setenv("STEPAN2_EVOLUTION_URL", "")
    monkeypatch.setenv("STEPAN2_EVOLUTION_API_KEY", "")
    settings.cache_clear()

    branch = Branch(name="T", lang="id")
    db_session.add(branch)
    await db_session.flush()
    channel = Channel(branch_id=branch.id, kind=ChannelKind.WHATSAPP)
    db_session.add(channel)
    await db_session.flush()
    db_session.add(ChannelSession(
        channel_id=channel.id,
        secret_enc=encrypt(json.dumps({"base_url": "https://old.example.com",
                                       "instance": "legacy", "api_key": "old-key"})),
        status=SessionStatus.ACTIVE,
    ))
    await db_session.flush()

    port = await build_port(db_session, channel)

    assert port.read_only is False  # старая форма про флаг не знала → канал остаётся пишущим
    settings.cache_clear()
