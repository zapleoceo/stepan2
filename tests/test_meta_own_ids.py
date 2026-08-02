"""Knowing which participant is US — the root of two live failures on 2026-08-02.

Participants are id-scoped per platform: Messenger lists the Page id, Instagram lists the IG
business account id. The transport only ever knew the Page id, so on Instagram nothing matched
and "the participant that isn't us" returned the FIRST one — us. Two consequences, both seen in
production:

  * sends went to our own account and Graph answered "(#100) no matching user" (2018001);
  * the lead was filed under our own Page name — "Zapleo Soft" appeared twice as a lead.

Resolving the Page's linked IG id once fixes both.
"""
from __future__ import annotations

import pytest

from app.adapters.channels.transports import GraphTransportHTTP

_PAGE = "207513496325789"
_OWN_IG = "17841406968997652"
_LEAD_IG = "1690990202132445"


class _Resp:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _Client:
    def __init__(self, ig_id: str | None = _OWN_IG, boom: bool = False) -> None:
        self.ig_id, self.boom, self.calls = ig_id, boom, 0

    async def get(self, url: str, params: dict) -> _Resp:
        self.calls += 1
        if self.boom:
            raise RuntimeError("graph down")
        if self.ig_id is None:
            return _Resp({"id": _PAGE})
        return _Resp({"id": _PAGE, "instagram_business_account": {"id": self.ig_id}})


def _t() -> GraphTransportHTTP:
    return GraphTransportHTTP(base_url="https://graph.facebook.com/v21.0",
                              account_id=_PAGE, token="T")  # noqa: S106


@pytest.mark.asyncio
async def test_own_ids_include_page_and_linked_instagram() -> None:
    assert await _t()._own_ids(_Client()) == {_PAGE, _OWN_IG}


@pytest.mark.asyncio
async def test_resolved_once_then_cached() -> None:
    """It runs on every poll and every send; a Graph call each time is a self-inflicted limit."""
    t, c = _t(), _Client()
    for _ in range(4):
        await t._own_ids(c)
    assert c.calls == 1


@pytest.mark.asyncio
async def test_page_without_instagram_still_works() -> None:
    assert await _t()._own_ids(_Client(ig_id=None)) == {_PAGE}


@pytest.mark.asyncio
async def test_lookup_failure_degrades_to_the_page_id() -> None:
    """Messenger must keep working even if the IG lookup fails."""
    assert await _t()._own_ids(_Client(boom=True)) == {_PAGE}


def _conv(participants: list[dict]) -> dict:
    return {"id": "c1", "participants": {"data": participants}}


def test_participant_picks_the_lead_not_us_on_instagram() -> None:
    conv = _conv([{"id": _OWN_IG, "username": "zapleosoft"},
                  {"id": _LEAD_IG, "username": "zapleo_ceo"}])
    assert GraphTransportHTTP._participant(conv, {_PAGE, _OWN_IG})["username"] == "zapleo_ceo"


def test_participant_is_the_lead_even_when_we_replied_last() -> None:
    """The old rule matched the last message's author; when that was us, the lead was named
    after our own Page."""
    conv = _conv([{"id": _OWN_IG, "username": "zapleosoft"},
                  {"id": _LEAD_IG, "username": "zapleo_ceo"}])
    who = GraphTransportHTTP._participant(conv, {_PAGE, _OWN_IG})
    assert who["id"] == _LEAD_IG


def test_participant_picks_the_human_on_messenger() -> None:
    conv = _conv([{"id": "37203559012625947", "name": "Khrystyna Horna"},
                  {"id": _PAGE, "name": "Zapleo Soft"}])
    assert GraphTransportHTTP._participant(conv, {_PAGE, _OWN_IG})["name"] == "Khrystyna Horna"


def test_no_participants_degrades_quietly() -> None:
    assert GraphTransportHTTP._participant({"id": "c"}, {_PAGE}) == {}
