"""Окно чата показывает всю переписку с человеком, а не один его канал.

Объединение сделало одного человека одной карточкой. Открыть эту карточку и увидеть только
самый свежий канал — тот же самый разрыв, на один экран позже: у лида 6351 переписка в
инстаграме существует и не видна, потому что последним двигался WhatsApp.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
from app.api._query import fetch_messages
from app.api._ui_html import _bubble, _manager_phone_notice
from app.domain.enums import ChannelKind


async def _lead_on_two_channels(s) -> tuple[int, int]:  # noqa: ANN001
    """Возвращает (ig_thread_id, wa_thread_id) одного лида."""
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ig = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM, handle="IG itstep")
    wa = Channel(branch_id=b.id, kind=ChannelKind.WHATSAPP, handle="WA Excel",
                 manager_phone=True)
    s.add(ig)
    s.add(wa)
    await s.flush()
    lead = Lead(branch_id=b.id)
    s.add(lead)
    await s.flush()
    ig_t = ChannelThread(lead_id=lead.id, channel_id=ig.id, external_thread_id="ig")
    wa_t = ChannelThread(lead_id=lead.id, channel_id=wa.id, external_thread_id="wa")
    s.add(ig_t)
    s.add(wa_t)
    await s.flush()
    now = datetime.now(UTC).replace(tzinfo=None)
    s.add(Message(branch_id=b.id, thread_id=ig_t.id, channel_id=ig.id, direction="in",
                  text="halo dari instagram", external_id="m1",
                  occurred_at=now - timedelta(days=3)))
    s.add(Message(branch_id=b.id, thread_id=wa_t.id, channel_id=wa.id, direction="out",
                  text="baik kak, dari whatsapp", external_id="m2", sent_by="manager",
                  occurred_at=now))
    await s.flush()
    return ig_t.id, wa_t.id


async def test_opening_one_thread_shows_the_whole_correspondence(db_session) -> None:  # noqa: ANN001
    ig_t, wa_t = await _lead_on_two_channels(db_session)
    rows = await fetch_messages(db_session, wa_t)
    texts = [r[3] for r in rows]
    assert "halo dari instagram" in texts
    assert "baik kak, dari whatsapp" in texts


async def test_the_feed_is_ordered_by_time_not_by_channel(db_session) -> None:  # noqa: ANN001
    """Иначе это не одна переписка, а две, поставленные в столбик."""
    _ig, wa_t = await _lead_on_two_channels(db_session)
    rows = await fetch_messages(db_session, wa_t)
    assert [r[3] for r in rows] == ["halo dari instagram", "baik kak, dari whatsapp"]


async def test_each_bubble_carries_the_account_it_came_through(db_session) -> None:  # noqa: ANN001
    """В смешанной ленте строка без происхождения нечитаема: непонятно, что клиент видел
    и куда пошёл бы ответ."""
    _ig, wa_t = await _lead_on_two_channels(db_session)
    rows = await fetch_messages(db_session, wa_t)
    html = "".join(_bubble(r, wa_t) for r in rows)
    assert "IG itstep" in html
    assert "WA Excel" in html


async def test_opening_the_other_thread_gives_the_same_feed(db_session) -> None:  # noqa: ANN001
    """Лента принадлежит человеку, а не двери, через которую в неё вошли."""
    ig_t, wa_t = await _lead_on_two_channels(db_session)
    assert [r[0] for r in await fetch_messages(db_session, ig_t)] \
        == [r[0] for r in await fetch_messages(db_session, wa_t)]


# ── поле ввода ────────────────────────────────────────────────────────────────


def test_the_composer_warns_when_the_newest_channel_is_a_managers() -> None:
    """Оператор читает переписку менеджера и печатает в поле под ней. Строка встала бы в
    очередь и была бы снята позже и в другом месте — сказать до, а не объяснять после."""
    notice = _manager_phone_notice([{"handle": "WA Excel", "manager_phone": True}])
    assert "WA Excel" in notice


def test_no_warning_when_we_can_actually_answer() -> None:
    assert _manager_phone_notice([{"handle": "IG itstep", "manager_phone": False}]) == ""
    assert _manager_phone_notice([]) == ""


def test_the_panel_actually_renders_the_warning() -> None:
    """Тест на саму функцию проходил, а вызова в панели не было — замена не совпала с
    шаблоном, и предупреждение существовало, но никому не показывалось."""
    from app.api._ui_html import chat_panel_html

    html = chat_panel_html(
        7, "Valian", "handed_off", [], [],
        conns=[{"handle": "WA Excel", "manager_phone": True, "kind": "whatsapp"}],
    )
    assert "fin-ro" in html
    assert "WA Excel" in html
