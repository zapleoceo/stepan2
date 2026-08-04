"""A lead who writes twice between two polls must not lose the first line.

The poller read only msgs[0] — the newest message of each conversation — so a burst was
truncated to its last line and the agent answered half the question. Reported live on
2026-08-03 on the official Graph connector.

Returning the whole thread is safe: ingest is idempotent on (channel_id, external_id), so
messages already stored are skipped. Our OWN messages are filtered here rather than left to
dedup, because Graph reports a different id for a message we sent than for the same message
read back — dedup cannot match them, and the agent would answer itself.
"""
from __future__ import annotations

import pytest

from app.adapters.channels.transports import GraphTransportHTTP

_PAGE = "207513496325789"
_OWN_IG = "17841406968997652"
_LEAD = "1690990202132445"


class _Resp:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _Client:
    """Serves one page of Instagram conversations and records the requested fields."""

    def __init__(self, convs: list[dict]) -> None:
        self.convs = convs
        self.fields: list[str] = []

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def get(self, _url: str, params: dict) -> _Resp:
        if params.get("fields") == "instagram_business_account":
            return _Resp({"id": _PAGE, "instagram_business_account": {"id": _OWN_IG}})
        self.fields.append(params.get("fields", ""))
        if params.get("platform") == "instagram":
            return _Resp({"data": self.convs})
        return _Resp({"data": []})


def _m(who: str, text: str, when: str) -> dict:
    return {"from": {"id": who}, "message": text, "created_time": when}


def _conv(msgs: list[dict], cid: str = "ig_1") -> dict:
    return {"id": cid,
            "participants": {"data": [{"id": _OWN_IG, "username": "zapleosoft"},
                                      {"id": _LEAD, "username": "client"}]},
            "messages": {"data": msgs}}


def _transport(client: _Client) -> GraphTransportHTTP:
    t = GraphTransportHTTP(base_url="https://graph.facebook.com/v21.0",
                           account_id=_PAGE, token="T")  # noqa: S106
    t._client = lambda: client  # type: ignore[method-assign]
    return t


@pytest.mark.asyncio
async def test_a_burst_of_three_is_returned_whole() -> None:
    """The exact reported failure: three lines, only the last used to survive."""
    client = _Client([_conv([  # Graph returns newest-first
        _m(_LEAD, "third", "2026-08-03T10:00:03+0000"),
        _m(_LEAD, "second", "2026-08-03T10:00:02+0000"),
        _m(_LEAD, "first", "2026-08-03T10:00:01+0000"),
    ])])
    out = await _transport(client).fetch_conversations()
    assert [m["message"] for m in out] == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_order_is_oldest_first() -> None:
    """Ingest appends in the order given; newest-first would store the dialogue backwards and
    the model would read the conversation inside out."""
    client = _Client([_conv([_m(_LEAD, "b", "2026-08-03T10:00:02+0000"),
                            _m(_LEAD, "a", "2026-08-03T10:00:01+0000")])])
    out = await _transport(client).fetch_conversations()
    assert [m["created_time"] for m in out] == ["2026-08-03T10:00:01+0000",
                                                "2026-08-03T10:00:02+0000"]


@pytest.mark.asyncio
async def test_our_own_replies_are_not_returned_as_inbound() -> None:
    """Now that the whole thread is read, this is no longer an edge case: every reply we ever
    sent is in that payload."""
    client = _Client([_conv([_m(_OWN_IG, "our reply", "2026-08-03T10:00:02+0000"),
                            _m(_LEAD, "their question", "2026-08-03T10:00:01+0000")])])
    out = await _transport(client).fetch_conversations()
    assert [m["message"] for m in out] == ["their question"]


@pytest.mark.asyncio
async def test_page_authored_messages_count_as_ours() -> None:
    """Messenger attributes our side to the Page id, Instagram to the IG account id."""
    client = _Client([_conv([_m(_PAGE, "sent as the Page", "2026-08-03T10:00:01+0000")])])
    assert await _transport(client).fetch_conversations() == []


@pytest.mark.asyncio
async def test_a_thread_we_spoke_last_in_yields_nothing_new() -> None:
    client = _Client([_conv([_m(_OWN_IG, "a", "2026-08-03T10:00:02+0000"),
                            _m(_PAGE, "b", "2026-08-03T10:00:01+0000")])])
    assert await _transport(client).fetch_conversations() == []


@pytest.mark.asyncio
async def test_identity_is_carried_on_every_message_of_the_burst() -> None:
    """Each line becomes its own InboundMessage, so each needs the sender's name."""
    client = _Client([_conv([_m(_LEAD, "b", "2026-08-03T10:00:02+0000"),
                            _m(_LEAD, "a", "2026-08-03T10:00:01+0000")])])
    out = await _transport(client).fetch_conversations()
    assert all(m["sender_username"] == "client" for m in out)
    assert all(m["from_id"] == _LEAD for m in out)


@pytest.mark.asyncio
async def test_messages_are_requested_with_an_explicit_limit() -> None:
    """Without a limit Graph decides how much history to send; the cap keeps the payload
    predictable and the burst window generous."""
    client = _Client([_conv([_m(_LEAD, "x", "2026-08-03T10:00:01+0000")])])
    await _transport(client).fetch_conversations()
    assert any("messages.limit(" in f for f in client.fields)


@pytest.mark.asyncio
async def test_the_conversation_cap_still_counts_conversations() -> None:
    """meta_live_conversations bounds THREADS. Now that one thread yields many messages, a
    cap applied to messages would silently stop reading threads partway."""
    from app.config import settings

    convs = [_conv([_m(_LEAD, f"m{i}", f"2026-08-03T10:00:0{i}+0000") for i in range(5)],
                   cid=f"ig_{n}") for n in range(3)]
    client = _Client(convs)
    out = await _transport(client).fetch_conversations()
    assert len({m["thread_id"] for m in out}) == 3  # all three threads read
    assert len(out) == 15  # and every message in them
    assert settings().meta_live_conversations >= 3


@pytest.mark.asyncio
async def test_two_messages_in_the_same_second_both_survive() -> None:
    """Graph timestamps are second-resolution and someone typing fast produces two lines with
    one created_time. Deduplicating on (thread, time, sender) alone threw the second away —
    the same loss this change exists to stop, one layer down."""
    client = _Client([_conv([_m(_LEAD, "and the price?", "2026-08-03T10:00:01+0000"),
                            _m(_LEAD, "hi", "2026-08-03T10:00:01+0000")])])
    out = await _transport(client).fetch_conversations()
    assert [m["message"] for m in out] == ["hi", "and the price?"]
