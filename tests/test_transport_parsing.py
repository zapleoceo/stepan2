"""The Instagram transport's parsing, which decides three things nothing downstream can undo.

Half of transports.py was uncovered. The network calls are the uninteresting half — these are
the parts that read what IG hands back and turn it into facts the rest of the system trusts:
whose message it is, how the lead found us, and whether they read us. Get any of them wrong
and nothing raises; the data is simply false from then on.
"""
from __future__ import annotations

import pytest

from app.adapters.channels.transports import (
    InstagrapiTransport,
    _detect_lead_source,
    _lead_seen,
    _paged_threads,
)

# ── whose message is this ─────────────────────────────────────────────────────

def _transport(settings: dict) -> InstagrapiTransport:
    return InstagrapiTransport(username="u", session_settings=settings, proxy=None)


class _Client:
    def __init__(self, user_id: object = None) -> None:
        self.user_id = user_id


def test_our_own_id_prefers_the_field_that_survives_a_restored_session() -> None:
    """We rebuild the client from a stored dump and never re-login, so `client.user_id` (set
    by login()) stays empty while `authorization_data.ds_user_id` survives — and it is the one
    IG stamps on our own polled items."""
    t = _transport({"authorization_data": {"ds_user_id": "17841400000"}})
    assert t._resolve_own_id(_Client(user_id="99")) == "17841400000"


def test_our_own_id_falls_back_to_the_live_client() -> None:
    assert _transport({}) ._resolve_own_id(_Client(user_id=42)) == "42"


def test_an_unresolvable_own_id_fails_the_poll_rather_than_guessing() -> None:
    """Direction is decided by `uid == own_id`, so a blank own id marks EVERY item inbound and
    files our own sends as the lead's. That happened: 1401 messages mislabelled. Skipping the
    poll costs one cycle; writing them costs the transcript and the model's turn-taking."""
    for settings in ({}, {"authorization_data": {}}, {"authorization_data": None}):
        with pytest.raises(RuntimeError, match="own IG user id"):
            _transport(settings)._resolve_own_id(_Client(user_id=None))


# ── how the lead found us ─────────────────────────────────────────────────────

def test_an_ad_click_is_recognised_in_either_attribution_shape() -> None:
    """IG returns send_attribution as a dict keyed by user id OR as a list of records. Both
    are live. A shape we fail to read makes every ad lead look organic — which loses the
    product anchor, the entry hint and the whole ad-attribution report at once."""
    as_dict = {"send_attribution": {"555": "Click to Direct"}}
    as_list = {"send_attribution": [{"user_id": "555", "display_name": "CTD"}]}
    assert _detect_lead_source(as_dict, 555) == "ad_clicktomsg"
    assert _detect_lead_source(as_list, 555) == "ad_clicktomsg"


@pytest.mark.parametrize("label", ["ctd", "Ads", "ad", "click to dm", "Click-to-Message"])
def test_the_wordings_meta_actually_sends_all_count_as_an_ad(label: str) -> None:
    assert _detect_lead_source({"send_attribution": {"1": label}}, 1) == "ad_clicktomsg"


def test_a_story_reply_is_its_own_entry_point() -> None:
    assert _detect_lead_source({"send_attribution": {"1": "Story Reply"}}, 1) == "story"


def test_attribution_belonging_to_someone_else_is_ignored() -> None:
    """The record must match THIS lead. Reading a neighbour's attribution would credit the ad
    to the wrong conversation."""
    assert _detect_lead_source({"send_attribution": {"999": "Click to Direct"}}, 555) is None


def test_no_attribution_means_no_assumption() -> None:
    for thread in ({}, {"send_attribution": None}, {"send_attribution": {}},
                   {"send_attribution": [{"display_name": "ads"}]},
                   {"send_attribution": {"1": None}}):
        assert _detect_lead_source(thread, 1) is None


# ── did they read us ──────────────────────────────────────────────────────────

def test_a_read_receipt_is_read_for_this_lead_only() -> None:
    thread = {"last_seen_at": {"555": {"timestamp": "1785252646000000"},
                               "999": {"timestamp": "1"}}}
    assert _lead_seen(thread, "555") == 1785252646000000
    assert _lead_seen(thread, "111") is None


def test_a_missing_or_unusable_receipt_is_absence_not_zero() -> None:
    """None and 0 are different answers: 0 would read as "opened at the epoch", i.e. seen."""
    for thread in ({}, {"last_seen_at": None}, {"last_seen_at": {"1": {}}},
                   {"last_seen_at": {"1": {"timestamp": None}}},
                   {"last_seen_at": {"1": {"timestamp": "not a number"}}},
                   {"last_seen_at": {"1": "not a dict"}}):
        assert _lead_seen(thread, "1") is None
    assert _lead_seen({"last_seen_at": {"1": {"timestamp": 5}}}, None) is None


# ── paging the inbox ──────────────────────────────────────────────────────────

class _PagingClient:
    """Serves `pages` in order and records the params it was called with."""

    def __init__(self, pages: list[dict]) -> None:
        self._pages = pages
        self.calls: list[dict] = []

    def private_request(self, _endpoint: str, params: dict) -> dict:  # noqa: ANN101
        self.calls.append(dict(params))
        return self._pages[len(self.calls) - 1]


def _page(n: int, *, has_older: bool, cursor: str | None = "c") -> dict:
    return {"inbox": {"threads": [{"thread_id": f"t{i}"} for i in range(n)],
                      "has_older": has_older, "oldest_cursor": cursor}}


def test_paging_follows_the_cursor_and_stops_at_the_amount() -> None:
    client = _PagingClient([_page(20, has_older=True), _page(20, has_older=True)])
    out = _paged_threads(client, "direct_v2/inbox/", amount=40)

    assert len(out) == 40
    assert "cursor" not in client.calls[0]         # first page asks for no cursor
    assert client.calls[1]["cursor"] == "c"        # then follows the one it was given
    assert client.calls[1]["direction"] == "older"


def test_paging_stops_when_instagram_says_there_is_no_more() -> None:
    client = _PagingClient([_page(5, has_older=False)])
    assert len(_paged_threads(client, "direct_v2/inbox/", amount=40)) == 5
    assert len(client.calls) == 1  # no pointless second round-trip


def test_paging_stops_when_the_cursor_goes_missing() -> None:
    """has_older says yes but no cursor comes back — asking again would replay page one
    forever."""
    client = _PagingClient([_page(20, has_older=True, cursor=None)])
    assert len(_paged_threads(client, "direct_v2/inbox/", amount=40)) == 20
    assert len(client.calls) == 1


def test_a_short_amount_still_makes_one_request() -> None:
    client = _PagingClient([_page(3, has_older=False)])
    assert len(_paged_threads(client, "direct_v2/inbox/", amount=1)) == 1
    assert len(client.calls) == 1


def test_an_empty_inbox_is_not_an_error() -> None:
    client = _PagingClient([{"inbox": {}}, {}])
    assert _paged_threads(client, "direct_v2/inbox/", amount=20) == []
