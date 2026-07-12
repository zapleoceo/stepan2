"""The /privacy page must be public (Meta App Review fetches it without auth) and contain a
real policy — data used, purpose, retention, and a deletion contact."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from fastapi.testclient import TestClient  # noqa: E402

from app.api._auth import _is_public  # noqa: E402
from app.api.main import app  # noqa: E402


def test_privacy_is_public_path() -> None:
    assert _is_public("/privacy")  # auth middleware must never gate it


def test_privacy_page_serves_a_real_policy() -> None:
    r = TestClient(app, raise_server_exceptions=False).get("/privacy")
    assert r.status_code == 200
    body = r.text.lower()
    assert "privacy policy" in body
    assert "delete" in body and "@" in body        # a data-deletion contact
    assert "sell your data" in body                 # explicit no-sale statement
    assert "initiate a conversation" in body        # inbound-only, no unsolicited messaging
