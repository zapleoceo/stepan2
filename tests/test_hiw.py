"""/hiw — the internal "how it works" page: renders fully and stays behind auth."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from fastapi.testclient import TestClient  # noqa: E402

from app.api._auth import SESSION_COOKIE, mint_session  # noqa: E402
from app.api._hiw import hiw_html  # noqa: E402
from app.api.main import app  # noqa: E402


class _StubSettings:
    """Minimal settings double for toggling the auth gate (mirrors test_auth_gate)."""

    def __init__(self, **kw: object) -> None:
        self.auth_enabled = kw.get("auth_enabled", True)
        self.session_secret = kw.get("session_secret", "testsecret")
        self.secret_key = kw.get("secret_key", "")


def _enable(monkeypatch, **kw) -> None:
    monkeypatch.setattr("app.api._auth.settings", lambda: _StubSettings(**kw))


def test_hiw_renders_key_sections() -> None:
    html = hiw_html()
    # top-down structure: hero, the message-journey pipeline, review crib, glossary
    assert "Как устроен Степан" in html
    assert "Путь одного сообщения" in html
    assert "Шпаргалка" in html
    assert "Словарик" in html
    # drill-down bottom level cites real code paths so claims stay verifiable
    assert "app/modules/conversation/reply.py" in html
    assert "app/worker/main.py" in html


def test_hiw_is_self_contained_full_page() -> None:
    html = hiw_html()
    assert html.lstrip().lower().startswith("<!doctype html>")
    # internal page: keep it out of search engines even if ever exposed
    assert 'name="robots" content="noindex' in html


def test_hiw_requires_session_when_auth_enabled(monkeypatch) -> None:
    _enable(monkeypatch)
    client = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
    resp = client.get("/hiw")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_hiw_with_valid_session_renders(monkeypatch) -> None:
    _enable(monkeypatch)
    token = mint_session(telegram_id=1, user_id=1, name="x", is_super=True, branch_ids=[])
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/hiw", cookies={SESSION_COOKIE: token})
    assert resp.status_code == 200
    assert "Как устроен Степан" in resp.text
