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


def test_a_voice_note_is_media_and_says_so_in_the_text() -> None:
    """Раньше текст оставался пустым, и расшифровка читалась как молчание — ровно то, на что
    жаловался комментарий выше. Теперь на месте голосового стоит маркер: файла Evolution всё
    равно не отдаёт, но видно, что человек что-то прислал."""
    out = _wa_message({"key": {"remoteJid": "62811@s", "fromMe": True},
                       "message": {"audioMessage": {"seconds": 30}}})
    assert out["media_kind"] == "audio"
    assert "voice" in out["text"]
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


# ── @lid: где на самом деле лежит номер ───────────────────────────────────────


def test_the_real_number_is_read_from_the_masked_chats_alternate_address() -> None:
    """266 из первых 286 тредов пришли как @lid — приватный идентификатор без номера.
    Читая только его, сшивка по телефону накрывала 7% чатов. Настоящий адрес едет рядом,
    в remoteJidAlt."""
    out = _wa_message({"key": {"remoteJid": "60520501653592@lid",
                               "remoteJidAlt": "6285156469324@s.whatsapp.net",
                               "fromMe": False}, "message": {"conversation": "halo"}})
    assert out["lead_phone"] == "+6285156469324"
    assert out["remote_jid"] == "60520501653592@lid"  # тред по-прежнему свой


def test_an_unmasked_chat_still_works() -> None:
    out = _wa_message({"key": {"remoteJid": "628119720022@s.whatsapp.net"}, "message": {}})
    assert out["lead_phone"] == "+628119720022"


def test_a_masked_chat_with_no_alternate_yields_no_phone() -> None:
    out = _wa_message({"key": {"remoteJid": "605205@lid"}, "message": {}})
    assert out["lead_phone"] is None


def test_a_group_is_nobodys_phone() -> None:
    out = _wa_message({"key": {"remoteJid": "12036@g.us"}, "message": {}})
    assert out["lead_phone"] is None


# ── имя ───────────────────────────────────────────────────────────────────────


def test_the_name_comes_off_the_message_itself() -> None:
    """В списке чатов имя было у 4 из 238; на сообщении оно есть почти всегда."""
    out = _wa_message({"key": {"remoteJid": "1@lid", "fromMe": False},
                       "pushName": "Valian", "message": {"conversation": "halo"}})
    assert out["sender_name"] == "Valian"


def test_our_own_account_name_is_never_written_onto_the_lead() -> None:
    """На наших собственных элементах pushName — это имя школы. Записать его лиду значит
    переименовать половину базы в «Academy It Step»."""
    out = _wa_message({"key": {"remoteJid": "1@lid", "fromMe": True},
                       "pushName": "Academy It Step", "message": {"conversation": "baik"}})
    assert out["sender_name"] is None


# ── медиа без подписи ─────────────────────────────────────────────────────────


def test_media_without_a_caption_gets_a_marker_not_an_empty_bubble() -> None:
    """Evolution не умеет отдавать сам файл (download_media есть у Instagram и Meta, у него
    нет), поэтому запись выходила пустой: ни текста, ни вложения, ни заявки на загрузку. На
    12.08.2026 таких входящих 107 — 5% личных переписок и 26% групповых, — и в чате на их
    месте пусто. Тред 6527: десять сообщений из девятнадцати без единого символа."""
    from app.adapters.channels.transports import _wa_text_or_marker

    assert "voice" in _wa_text_or_marker({"audioMessage": {}})
    assert "image" in _wa_text_or_marker({"imageMessage": {}})
    assert "image" in _wa_text_or_marker({"videoMessage": {}})


def test_a_caption_wins_over_the_marker() -> None:
    from app.adapters.channels.transports import _wa_text_or_marker

    assert _wa_text_or_marker({"imageMessage": {"caption": "ini bukti transfer"}}) \
        == "ini bukti transfer"


def test_a_shape_with_no_text_and_no_media_stays_empty() -> None:
    """Маркер только там, где реально пришло содержимое. Пустое остаётся пустым."""
    from app.adapters.channels.transports import _wa_text_or_marker

    assert _wa_text_or_marker({"protocolMessage": {}}) == ""
    assert _wa_text_or_marker({}) == ""


def test_the_marker_never_freezes_the_thread() -> None:
    """reply._awaiting_media держит ход, пока последнее входящее ДОСЛОВНО равно заглушке
    ожидания. Файла для распознавания здесь не будет никогда, так что совпадение с ними
    заморозило бы тред навсегда — маркеры обязаны отличаться."""
    from app.adapters.channels.ig_parse import IMAGE_PENDING_PH, VOICE_PENDING_PH
    from app.adapters.channels.transports import _WA_MEDIA_UNREADABLE

    assert IMAGE_PENDING_PH not in _WA_MEDIA_UNREADABLE.values()
    assert VOICE_PENDING_PH not in _WA_MEDIA_UNREADABLE.values()


def test_the_wording_matches_what_the_rest_of_the_system_already_says() -> None:
    """Те же слова, что у медиа, которое не удалось распознать: контракт и интерфейс уже
    знают эту формулировку, и расхождение развело бы два одинаковых по смыслу состояния."""
    from app.adapters.channels.transports import _WA_MEDIA_UNREADABLE
    from app.modules.media.service import _IMAGE_UNAVAILABLE, _VOICE_UNAVAILABLE

    assert _WA_MEDIA_UNREADABLE["audio"] == _VOICE_UNAVAILABLE
    assert _WA_MEDIA_UNREADABLE["image"] == _IMAGE_UNAVAILABLE
