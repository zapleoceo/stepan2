"""Разбор того, что отдаёт Evolution: чьё сообщение, что в нём написано, и есть ли медиа.

Номера менеджеров привязываются ради одного вопроса — куда уходит лид после того, как
Степан взял телефон. Ответ на него целиком лежит в этих трёх фактах, и ни один из них не
падает при ошибке: неверное направление молча переворачивает автора, непрочитанный тип
сообщения молча превращает живой диалог в пустые строки.
"""
from __future__ import annotations

import pytest

from app.adapters.channels.transports import (
    _qr_data_uri,
    _wa_media_kind,
    _wa_message,
    _wa_records,
    _wa_text,
)

# ── чьё сообщение ─────────────────────────────────────────────────────────────


def test_the_manager_half_of_the_chat_is_kept_and_marked_out() -> None:
    """`fromMe` раньше означало «выбросить» — канал строился только на отправку. На номере
    менеджера это ровно та половина, ради которой устройство и привязано."""
    out = _wa_message({"key": {"remoteJid": "62811@s", "fromMe": True, "id": "A"},
                       "message": {"conversation": "baik kak"}})
    assert out["direction"] == "out"
    assert out["text"] == "baik kak"


def test_the_lead_half_stays_inbound() -> None:
    out = _wa_message({"key": {"remoteJid": "62811@s", "fromMe": False, "id": "B"},
                       "message": {"conversation": "berapa harganya"}})
    assert out["direction"] == "in"


def test_a_missing_flag_is_inbound_not_a_guess() -> None:
    """Направление решается здесь, где лежит сырой флаг. Догадка ниже по потоку — «кто
    похож на отправителя» — это ровно то, что перевернуло автора у 1401 сообщения в IG."""
    assert _wa_message({"key": {"remoteJid": "62811@s"}, "message": {}})["direction"] == "in"


def test_the_platform_message_id_is_carried_for_dedup() -> None:
    assert _wa_message({"key": {"id": "3EB0"}, "message": {}})["external_id"] == "3EB0"
    assert _wa_message({"key": {}, "message": {}})["external_id"] is None


# ── что написано ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("body", "expected"), [
    ({"conversation": "halo"}, "halo"),
    ({"extendedTextMessage": {"text": "ini linknya"}}, "ini linknya"),   # ответ / ссылка
    ({"imageMessage": {"caption": "ini brosurnya"}}, "ini brosurnya"),
    ({"videoMessage": {"caption": "cek video"}}, "cek video"),
    ({"documentMessage": {"caption": "invoice"}}, "invoice"),
    ({"buttonsResponseMessage": {"selectedDisplayText": "Daftar"}}, "Daftar"),
    ({"listResponseMessage": {"title": "Kelas malam"}}, "Kelas malam"),
])
def test_every_shape_a_human_reads_is_read(body: dict, expected: str) -> None:
    """Раньше читался только `conversation` — простой пузырь и всё. Ответ с цитатой, ссылка
    и подпись к фото приходят под другими ключами, и чат выглядел бы наполовину пустым."""
    assert _wa_text(body) == expected


def test_text_inside_a_disappearing_envelope_is_found() -> None:
    """Исчезающие сообщения кладут настоящее внутрь конверта. Не развернув его, увидишь
    пустоту там, где шёл разговор."""
    assert _wa_text({"ephemeralMessage": {"message": {"conversation": "oke kak"}}}) == "oke kak"


def test_an_unknown_shape_is_empty_not_an_error() -> None:
    for body in ({}, None, {"pollCreationMessage": {"name": "x"}}, {"conversation": None}):
        assert _wa_text(body) == ""


def test_a_cyclic_envelope_cannot_hang_the_poll() -> None:
    """Разворачивание конвертов ограничено по глубине: подвисший цикл здесь остановил бы
    весь ингест канала, а не одно сообщение."""
    body: dict = {}
    body["ephemeralMessage"] = {"message": body}
    assert _wa_text(body) == ""


# ── медиа ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("body", "kind"), [
    ({"audioMessage": {"seconds": 12}}, "audio"),
    ({"imageMessage": {}}, "image"),
    ({"videoMessage": {}}, "video"),
    ({"stickerMessage": {}}, "image"),
    ({"documentMessage": {}}, "document"),
    ({"conversation": "halo"}, None),
])
def test_non_text_bubbles_are_recorded_as_media(body: dict, kind: str | None) -> None:
    """Голосовое — это половина индонезийской продажи. Записанное пустой строкой, оно
    читается в расшифровке как молчание менеджера."""
    assert _wa_media_kind(body) == kind


def test_a_voice_note_is_media_even_though_it_has_no_text() -> None:
    out = _wa_message({"key": {"remoteJid": "62811@s", "fromMe": True},
                       "message": {"audioMessage": {"seconds": 30}}})
    assert out["media_kind"] == "audio"
    assert out["text"] == ""
    assert out["direction"] == "out"


# ── форма ответа ──────────────────────────────────────────────────────────────


def test_both_payload_shapes_of_find_messages_are_read() -> None:
    """Одни версии отдают голый список, другие — {messages:{records:[…]}}. Прочитав только
    одну форму, получишь пустой ящик вместо ошибки — и решишь, что чатов нет."""
    one = {"key": {"id": "A"}}
    assert _wa_records([one]) == [one]
    assert _wa_records({"messages": [one]}) == [one]
    assert _wa_records({"messages": {"records": [one]}}) == [one]


def test_an_unreadable_payload_is_empty_not_a_crash() -> None:
    for payload in (None, "nope", {}, {"messages": None}, {"messages": {"records": None}}):
        assert _wa_records(payload) == []


# ── QR ────────────────────────────────────────────────────────────────────────


def test_the_qr_is_normalised_to_something_an_img_tag_can_show() -> None:
    assert _qr_data_uri({"qrcode": {"base64": "AAA"}}) == "data:image/png;base64,AAA"
    assert _qr_data_uri({"base64": "data:image/png;base64,BBB"}).startswith("data:image/png")
    assert _qr_data_uri({}) == ""


# ── как именно спрашиваем ─────────────────────────────────────────────────────


async def test_messages_are_requested_by_post_because_get_is_a_404_on_v2() -> None:
    """v1 отвечал на GET, v2 — только на POST с телом. Разница не абстрактная: канал
    показывает «активно», телефон показывает связанное устройство, а диалогов ноль, и
    единственный след — строка в логе воркера. Поймано на живом номере."""
    from app.adapters.channels.transports import EvolutionTransport

    seen: dict[str, object] = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None: ...
        def json(self) -> dict:
            return {"messages": {"records": [
                {"key": {"remoteJid": "62811@s", "fromMe": True, "id": "A"},
                 "message": {"conversation": "baik kak"}}]}}

    class _Client:
        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *_a) -> bool:
            return False

        async def post(self, path: str, json: dict) -> _Resp:  # noqa: A002
            seen.setdefault("calls", []).append((path, json))  # type: ignore[union-attr]
            seen["method"] = "POST"
            return _Resp()

        async def get(self, path: str) -> _Resp:
            seen["method"] = "GET"
            return _Resp()

    t = EvolutionTransport(base_url="http://evolution:8080", instance="wa-1", api_key="k")
    t._client = lambda: _Client()  # noqa: SLF001

    out = await t.fetch_messages()

    assert seen["method"] == "POST"
    assert seen["calls"][0] == ("/chat/findMessages/wa-1", {})
    assert out[0]["direction"] == "out"  # и половина менеджера доезжает
