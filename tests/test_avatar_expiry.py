"""An expired Instagram CDN link is not rendered.

The inbox lists every lead, most of them dormant. Their avatar was captured whenever they
last wrote — weeks ago for most — and Instagram signs those URLs with an expiry. Past it the
CDN answers 403, so opening the inbox fired one doomed request per stale avatar and filled the
browser console with them. Nothing renders differently for the operator (the fallback initial
is what they saw either way); what changes is that we stop asking.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.api._ui_html import _avatar, _avatar_expired

_BASE = "https://scontent-fra5-2.cdninstagram.com/v/t51.82787-19/579482212_x.jpg?stp=dst-jpg&"


def _url_expiring(delta: timedelta) -> str:
    oe = int((datetime.now(UTC) + delta).timestamp())
    return f"{_BASE}_nc_ht=scontent-fra5-2.cdninstagram.com&oe={oe:X}"


def test_a_link_that_expired_last_month_is_not_requested() -> None:
    assert _avatar_expired(_url_expiring(timedelta(days=-30)))


def test_a_link_still_valid_is_kept() -> None:
    assert not _avatar_expired(_url_expiring(timedelta(days=3)))


def test_a_url_with_no_expiry_is_left_alone() -> None:
    """Not every avatar comes from the signed CDN — an unsigned URL is somebody else's
    problem to validate, and silently blanking it would be a worse bug than a 403."""
    assert not _avatar_expired("https://example.com/pic.jpg")


def test_garbage_in_the_expiry_does_not_blank_the_avatar() -> None:
    assert not _avatar_expired(f"{_BASE}oe=zzzz")


def test_the_expired_avatar_falls_back_to_the_initial() -> None:
    html = _avatar("Andi", _url_expiring(timedelta(days=-30)))

    assert "cdninstagram" not in html
    assert ">A<" in html


def test_a_live_avatar_still_renders_the_image() -> None:
    url = _url_expiring(timedelta(days=3))

    assert url.replace("&", "&amp;") in _avatar("Andi", url)
