"""Two cosmetic-looking things that are really correctness.

1. Platform badge. One meta_business channel now serves BOTH Messenger and Instagram Direct,
   so mapping channel kind → icon put a Facebook mark on every Instagram conversation. Graph
   makes them tellable apart by the conversation id: "t_<digits>" for Messenger, base64 that
   starts with "aWdf" for Instagram.

2. The lead's name. Every Meta lead showed as "Lead" because the adapter dropped the identity
   Graph already returns. It splits by platform — Messenger gives `name`, Instagram gives
   `username` — and the right participant is the one whose id matches the inbound author, since
   which participant is "ours" also differs by platform.
"""
from __future__ import annotations

from app.adapters.channels.meta_business import MetaBusinessAdapter
from app.adapters.channels.transports import GraphTransportHTTP
from app.api._ui_html import _channel_badge, _is_instagram_thread

_IG_TID = "aWdfZAG06MTpJR01lc3NhZA2VUaHJlYWQ6MTc4NDE0MDY5"
_MSG_TID = "t_850802451942237"


def test_instagram_thread_id_is_recognised() -> None:
    assert _is_instagram_thread(_IG_TID)
    assert not _is_instagram_thread(_MSG_TID)
    assert not _is_instagram_thread(None)


def test_instagram_conversation_gets_the_instagram_badge() -> None:
    assert "fa-instagram" in _channel_badge("meta_business", _IG_TID)


def test_messenger_conversation_keeps_the_facebook_badge() -> None:
    assert "fa-facebook" in _channel_badge("meta_business", _MSG_TID)


def test_instagrapi_channel_is_unaffected() -> None:
    """The live branches run on kind=instagram and must look exactly as before."""
    assert "fa-instagram" in _channel_badge("instagram", None)


def test_unknown_kind_still_degrades_to_a_neutral_icon() -> None:
    assert "fa-comment" in _channel_badge("something_new", None)


def _conv(*, tid: str, from_id: str, participants: list[dict]) -> dict:
    return {"id": tid, "participants": {"data": participants},
            "messages": {"data": [{"from": {"id": from_id}, "message": "hi",
                                   "created_time": "2026-08-02T13:45:56+0000"}]}}


def test_picks_the_participant_who_sent_the_message() -> None:
    """Not "the one that isn't the page": on Instagram our own id is the IG business id, which
    the transport does not hold. Matching the author cannot pick the wrong side."""
    conv = _conv(tid=_IG_TID, from_id="1690990202132445", participants=[
        {"id": "17841406968997652", "username": "zapleosoft"},
        {"id": "1690990202132445", "username": "zapleo_ceo"},
    ])
    assert GraphTransportHTTP._participant(conv, "1690990202132445")["username"] == "zapleo_ceo"


def test_missing_participants_degrade_quietly() -> None:
    assert GraphTransportHTTP._participant({"id": "x"}, "1") == {}
    assert GraphTransportHTTP._participant(_conv(tid="t", from_id="", participants=[]), "") == {}


def test_adapter_passes_messenger_name_through() -> None:
    inbound = MetaBusinessAdapter(object(), account_id="207513496325789")._to_inbound({
        "thread_id": _MSG_TID, "from_id": "372", "message": "hi",
        "created_time": "2026-08-02T13:45:56+0000",
        "sender_name": "Khrystyna Horna", "sender_username": "",
    })
    assert inbound.sender_name == "Khrystyna Horna"
    assert inbound.sender_username is None


def test_adapter_passes_instagram_username_through() -> None:
    inbound = MetaBusinessAdapter(object(), account_id="207513496325789")._to_inbound({
        "thread_id": _IG_TID, "from_id": "169", "message": "привет",
        "created_time": "2026-08-02T13:45:56+0000",
        "sender_name": "", "sender_username": "zapleo_ceo",
    })
    assert inbound.sender_username == "zapleo_ceo"
    assert inbound.sender_name is None
