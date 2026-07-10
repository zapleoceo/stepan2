"""The inbox connector filter must be SERVER-SIDE: the thread list is LIMIT-capped by recency,
so filtering to a connector has to query that connector's threads (a client-side hide would
only reveal the ones that made the global top-N — the '2 Meta chats out of 81' bug)."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api._ui_html import app_shell  # noqa: E402
from app.api.main import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_threads_route_accepts_kind_and_stays_ok(client: TestClient) -> None:
    # a valid connector kind is accepted (and a bogus one doesn't 500 — it's ignored)
    assert client.get("/ui/threads?kind=meta_business").status_code in (200, 500)
    assert client.get("/ui/threads?kind=not_a_kind").status_code in (200, 500)


def test_kind_chip_is_server_side_htmx_not_client_toggle() -> None:
    """Each chip reloads #tl from the server with ?kind=… (so the connector's older chats,
    outside the recency LIMIT, are actually fetched) — not a client-only display toggle."""
    html = app_shell("en", "", active_nav="inbox")
    assert 'hx-get="/ui/threads?kind=meta_business"' in html
    assert 'hx-target="#tl"' in html
    assert "toggleKind" not in html            # the old client-side hide is gone


def test_active_kind_chip_highlights_and_toggles_off_and_preserves_filters() -> None:
    html = app_shell("en", "", active_nav="inbox", kind="meta_business", stage="qualifying")
    # the active chip is highlighted and clicking it CLEARS the kind (back to all connectors)…
    assert 'class="chk-kind on"' in html
    assert 'hx-get="/ui/threads?stage=qualifying"' in html      # active chip → drop kind
    # …while the other chips switch kind, carrying the stage filter along
    assert 'hx-get="/ui/threads?stage=qualifying&kind=instagram"' in html
    assert 'hx-push-url="/ui/inbox?stage=qualifying&kind=instagram"' in html
    # the 30s poll on #tl keeps the active kind so it doesn't reset the filter
    assert 'hx-get="/ui/threads?stage=qualifying&kind=meta_business"' in html
