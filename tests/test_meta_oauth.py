"""Facebook Login for Business — the flow a client uses to connect their own Page.

The callback is public: Meta redirects a browser to it with no session cookie, so nothing in
the request can be trusted. Everything here guards that seam — the channel id travels in a
signed state parameter, and a forged, tampered or stale one must be refused. Without that,
anyone could hand us a code and attach their Page to someone else's channel.
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.api._auth import _is_public  # noqa: E402
from app.modules.meta.oauth import (  # noqa: E402
    SCOPES,
    authorize_url,
    state_channel_id,
    state_token,
)

_SECRET = "test-secret"  # noqa: S105


def test_state_round_trips() -> None:
    assert state_channel_id(state_token(16, _SECRET), _SECRET) == 16


def test_state_signed_with_another_key_is_refused() -> None:
    """The whole point: a state we did not sign must not select a channel."""
    assert state_channel_id(state_token(16, "attacker"), _SECRET) is None


def test_tampered_state_is_refused() -> None:
    token = state_token(16, _SECRET)
    body, sig = token.split(".", 1)
    assert state_channel_id(f"{body}x.{sig}", _SECRET) is None
    assert state_channel_id("garbage", _SECRET) is None
    assert state_channel_id("", _SECRET) is None


def test_stale_state_is_refused(monkeypatch) -> None:
    """A link left open for an hour should not still attach a Page."""
    token = state_token(16, _SECRET)
    later = time.time() + 3600
    monkeypatch.setattr(time, "time", lambda: later)
    assert state_channel_id(token, _SECRET) is None


def test_authorize_url_carries_app_redirect_state_and_scopes() -> None:
    url = authorize_url(app_id="123", redirect_uri="https://x.test/cb",
                        state="ST", version="v21.0")
    assert url.startswith("https://www.facebook.com/v21.0/dialog/oauth?")
    assert "client_id=123" in url
    assert "state=ST" in url
    assert "response_type=code" in url
    assert "redirect_uri=https%3A%2F%2Fx.test%2Fcb" in url
    for scope in SCOPES:
        assert scope in url


def test_scopes_stay_minimal() -> None:
    """App Review rejects permissions the app cannot demonstrate, and every extra scope is one
    more thing the client is asked to trust us with."""
    assert set(SCOPES) == {
        "pages_show_list", "pages_messaging", "pages_manage_metadata",
        "pages_read_engagement", "instagram_basic", "instagram_manage_messages",
        "business_management",
    }


def test_the_submitted_permissions_are_the_requested_ones() -> None:
    """The set above is not an internal preference — it is what the App Review submission asks
    for, and the two drifting apart is how the previous attempt would have failed.

    A permission in the submission but not in SCOPES never appears on the consent screen, so
    the reviewer cannot see it being granted and rejects the ENTIRE submission. The reverse
    (asked for, never submitted) is a permission the client grants and we can never use.
    """
    submitted = {
        "pages_show_list", "pages_manage_metadata", "pages_messaging",
        "business_management", "instagram_manage_messages", "pages_read_engagement",
        "instagram_basic",
    }
    assert set(SCOPES) == submitted, (
        "docs/meta-review-readiness.md lists what the submission asks for; change both or "
        "neither"
    )


def test_the_page_subscription_scope_is_present() -> None:
    """/{page-id}/subscribed_apps answers 403 without it, which turns off the webhook for every
    client silently — connecting still succeeds, so nothing looks broken until nothing arrives.
    """
    assert "pages_manage_metadata" in SCOPES


def test_connect_paths_are_public() -> None:
    """Meta redirects the browser back with no cookie — behind auth this is a dead flow."""
    assert _is_public("/connect/meta/callback")
    assert _is_public("/connect/meta/16/start")


def test_ui_paths_are_still_gated() -> None:
    assert not _is_public("/ui/inbox")
    assert not _is_public("/connectors")
