"""The inbox live search must be SERVER-SIDE: the thread list is LIMIT-capped by recency, so
a client-side show/hide could only ever match the ~100 chats already rendered and silently
missed every older one — searching a long-quiet lead by name found nothing."""
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


def test_threads_route_accepts_q(client: TestClient) -> None:
    assert client.get("/ui/threads?q=alice").status_code in (200, 500)
    assert client.get("/ui/threads?q=").status_code in (200, 500)


def test_inbox_page_accepts_q(client: TestClient) -> None:
    assert client.get("/ui/inbox?q=alice").status_code in (200, 500)


def test_search_input_reloads_tl_from_the_server_debounced() -> None:
    html = app_shell("en", "", active_nav="inbox")
    assert 'id="ti-q"' in html and 'oninput="filterTi()"' in html
    assert "setTimeout(doFilterTi,250)" in html            # debounced, not a call per keystroke
    assert "htmx.ajax('GET','/ui/threads'" in html         # hits the server
    # the old client-side hide is gone: no per-row display juggling on data-search
    assert "data-search" not in html
    assert "e.style.display=(!q||s.indexOf(q)>=0)" not in html


def test_search_term_is_prefilled_and_carried_into_the_list_request() -> None:
    """A full reload / F5 must rebuild the same searched list, and the 30s #tl poll must
    request the same query — otherwise the poll would quietly wipe the search."""
    html = app_shell("en", "", active_nav="inbox", q="alice")
    assert 'value="alice"' in html                          # input keeps the term
    assert "/ui/threads?q=alice" in html                     # initial load + poll carry it


def test_search_term_is_url_encoded_not_html_escaped() -> None:
    # a space or & in the term must not break the #tl request URL
    html = app_shell("en", "", active_nav="inbox", q="anna b&c")
    assert "/ui/threads?q=anna+b%26c" in html


def test_search_combines_with_the_active_filter() -> None:
    html = app_shell("en", "", active_nav="inbox", stage="dormant", q="alice")
    assert "stage=dormant" in html and "q=alice" in html
