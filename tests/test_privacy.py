"""The three legal pages Meta App Review fetches anonymously: privacy, terms, data deletion.

Two things are pinned beyond "the page renders". First, they must stay OUTSIDE auth — a review
runs logged out, and a policy behind a login is the same as no policy. Second, the operator
named on them must be the product's own legal entity: the policy used to name the EdTech client
the product grew out of, which both misidentified the controller and leaked the vertical.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from fastapi.testclient import TestClient  # noqa: E402

from app.api._auth import _is_public  # noqa: E402
from app.api.main import app  # noqa: E402

_PAGES = ("/privacy", "/terms", "/data-deletion")


def _get(path: str) -> str:
    r = TestClient(app, raise_server_exceptions=False).get(path)
    assert r.status_code == 200, path
    return r.text.lower()


def test_every_legal_page_is_public() -> None:
    for path in _PAGES:
        assert _is_public(path), path


def test_privacy_page_serves_a_real_policy() -> None:
    body = _get("/privacy")
    assert "privacy policy" in body
    assert "delete" in body and "@" in body        # a data-deletion contact
    assert "sell your data" in body                 # explicit no-sale statement
    assert "start the conversation" in body         # inbound-only, no unsolicited messaging


def test_privacy_covers_this_website_not_only_client_channels() -> None:
    """The landing runs a demo chat and (when ads are live) the Meta pixel. A policy that
    described only the client-channel role would be silently wrong for the person reading it."""
    body = _get("/privacy")
    assert "demo chat" in body
    assert "pixel" in body


def test_privacy_names_both_roles() -> None:
    body = _get("/privacy")
    assert "controller" in body and "processor" in body


def test_legal_pages_name_the_product_entity_not_the_edtech_client() -> None:
    for path in _PAGES:
        body = _get(path)
        assert "zapleo" in body
        assert "itstep" not in body and "it step" not in body


def test_terms_cover_ai_output_and_platform_rules() -> None:
    body = _get("/terms")
    assert "terms of service" in body
    assert "automatically" in body          # replies are AI-generated
    assert "unsolicited bulk" in body       # anti-spam obligation


def test_data_deletion_states_a_deadline_and_a_route() -> None:
    body = _get("/data-deletion")
    assert "30 days" in body
    assert "@" in body


def test_landing_links_all_three() -> None:
    """Reachable by URL is not enough — a reviewer looks for the link on the product page."""
    body = _get("/")
    for path in _PAGES:
        assert f'href="{path}"' in body, path
