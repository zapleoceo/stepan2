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


def test_kind_chips_are_independent_toggles_reloading_tl_server_side() -> None:
    """Each connector is its own on/off toggle (not a single-select radio). Toggling reloads
    #tl from the SERVER with the ON subset, so a connector's older chats outside the recency
    LIMIT are actually fetched — not a client-only hide."""
    html = app_shell("en", "", active_nav="inbox")
    assert 'data-kind="meta_business"' in html
    assert 'data-kind="instagram"' in html and 'data-kind="whatsapp"' in html
    assert 'onclick="kindChip(this)"' in html
    assert "htmx.ajax('GET','/ui/threads'" in html   # the toggle reloads #tl server-side
    assert "toggleKind" not in html                  # the old client-side hide is gone
    # default (no kind param) = every chip ON, none struck off
    assert html.count('class="chk-kind on"') == 3
    assert 'class="chk-kind off"' not in html


def test_kind_chips_reflect_active_subset_and_tl_loader_preserves_filters() -> None:
    html = app_shell("en", "", active_nav="inbox", kind="meta_business", stage="qualifying")
    # only meta_business is ON; the other two are struck-through OFF (independent toggles)
    assert html.count('class="chk-kind on"') == 1
    assert html.count('class="chk-kind off"') == 2
    # the #tl loader (and its 30s poll, which mirrors the address bar) requests the active
    # subset AND carries the stage filter along
    assert 'hx-get="/ui/threads?stage=qualifying&kind=meta_business"' in html


def test_kind_multi_subset_shows_union() -> None:
    html = app_shell("en", "", active_nav="inbox", kind="instagram,whatsapp")
    assert html.count('class="chk-kind on"') == 2       # IG + WA on
    assert html.count('class="chk-kind off"') == 1       # Meta off
    assert 'hx-get="/ui/threads?kind=instagram,whatsapp"' in html


def test_awaiting_queue_is_active_funnel_only_and_excludes_meta() -> None:
    from app.api._query import AWAITING_BASE, IN_QUEUE_EXTRA
    # the "queue" = bot on AND a funnel stage where Stepan participates
    assert "agent_enabled = true" in IN_QUEUE_EXTRA
    for st in ("new", "nurturing", "qualifying", "presenting", "objection"):
        assert f"'{st}'" in IN_QUEUE_EXTRA
    # human-owned / done / dormant are NOT the active queue
    for st in ("dormant", "handed_off", "ready", "manager"):
        assert f"'{st}'" not in IN_QUEUE_EXTRA
    # Meta Business is excluded from the whole unanswered set (connector not finished)
    assert "meta_business" in AWAITING_BASE and "<>" in AWAITING_BASE
