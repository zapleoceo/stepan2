"""Список чатов стал списком лидов, и карточка описывает человека, а не переписку.

Раньше человек, пришедший в Instagram и продолживший у менеджера в WhatsApp, был двумя
карточками. Каждая рассказывала свою половину, и ни одна не говорила, что это один человек.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.api._ui_html import _conn_addr, thread_list_html


def _conn(**kw) -> dict:  # noqa: ANN003
    base = {"kind": "instagram", "handle": "IG itstep", "ext": "t1", "nick": "alice",
            "read_only": False, "tid": 1, "cin": 3, "cout": 2}
    return base | kw


def _lead(conns: list[dict], *, phone: str | None = "+62811", name: str = "Valian") -> tuple:
    return (9, name, "qualifying", datetime.now(UTC).replace(tzinfo=None), phone,
            "alice", None, None, None, True, "Hi", "in", 3, 2, "Jakarta", 0, 1, conns)


# ── что стоит первым ──────────────────────────────────────────────────────────


def test_the_phone_leads_because_it_is_what_ties_the_channels_together() -> None:
    html = thread_list_html([_lead([_conn()])])
    assert html.index("+62811") < html.index("Valian")


def test_a_lead_with_no_number_still_has_a_title() -> None:
    """WhatsApp маскирует большинство адресов: у 266 из 286 тредов номера нет вовсе.
    Карточка без заголовка — это карточка, которую нельзя найти глазами."""
    html = thread_list_html([_lead([_conn()], phone=None)])
    assert "Valian" in html


def test_the_time_shown_is_the_last_contact_anywhere() -> None:
    row = list(_lead([_conn()]))
    row[3] = datetime(2026, 7, 3, 14, 5)
    assert "03.07 14:05" in thread_list_html([tuple(row)])


# ── строка на коннектор ───────────────────────────────────────────────────────


def test_every_connector_the_person_is_reachable_on_is_listed() -> None:
    html = thread_list_html([_lead([
        _conn(),
        _conn(kind="whatsapp", handle="WA Maya", ext="628119720022@s.whatsapp.net",
              tid=2, cin=7, cout=9, read_only=True),
    ])])
    assert "IG itstep" in html and "WA Maya" in html


def test_each_connector_shows_its_own_traffic() -> None:
    """Общая сумма скрывает ровно то, ради чего объединяли: где разговор идёт, а где затих."""
    html = thread_list_html([_lead([
        _conn(cin=3, cout=2),
        _conn(kind="whatsapp", handle="WA Maya", tid=2, cin=7, cout=9),
    ])])
    assert "⬇3 ⬆2" in html
    assert "⬇7 ⬆9" in html


def test_a_read_only_account_says_so() -> None:
    """Оператор должен видеть, что в этот аккаунт писать нельзя, до того как попробует."""
    assert "👁" in thread_list_html([_lead([_conn(read_only=True)])])


# ── адрес на коннекторе ───────────────────────────────────────────────────────


def test_whatsapp_shows_the_number_when_there_is_one() -> None:
    assert _conn_addr(_conn(kind="whatsapp", ext="628119720022@s.whatsapp.net")) \
        == "+628119720022"


def test_a_masked_whatsapp_address_shows_nothing_rather_than_gibberish() -> None:
    """@lid — приватный идентификатор WhatsApp. Показать его как номер значит дать оператору
    цифры, которые никуда не звонят и ни с чем не сходятся."""
    assert _conn_addr(_conn(kind="whatsapp", ext="60520501653592@lid")) == ""


def test_a_social_connector_shows_the_handle() -> None:
    assert _conn_addr(_conn(kind="instagram", nick="alice")) == "@alice"


# ── клик ──────────────────────────────────────────────────────────────────────


def test_the_click_opens_the_conversation_the_person_is_actually_having() -> None:
    """У лида несколько тредов. Открыть не тот — открыть разговор, который закончился
    три недели назад."""
    html = thread_list_html([_lead([_conn(tid=2)], name="V")])
    assert 'hx-get="/ui/chat/1"' in html
