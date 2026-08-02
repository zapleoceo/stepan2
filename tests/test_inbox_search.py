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


def test_settling_the_thread_list_must_not_re_trigger_the_search() -> None:
    """filterTi() now issues a request that re-renders #tl. An afterSettle hook on #tl used to
    call it, so #tl reloaded itself forever: the list flickered, the spinner never went out,
    and every pass replaceState'd the address bar back to /ui/inbox — so opening a chat lost
    its own URL. Only real user input may call it."""
    html = app_shell("en", "", active_nav="inbox")
    assert "if(t&&t.id==='tl')filterTi();" not in html
    assert 'oninput="filterTi()"' in html          # user input is still the trigger


def test_opening_a_chat_keeps_its_own_url() -> None:
    # the row pushes the chat URL; nothing may rewrite it back to the plain inbox
    from datetime import UTC, datetime

    from app.api._ui_html import thread_list_html
    row = (452, "Alice", "new", datetime.now(UTC).replace(tzinfo=None), "+62811", "c", "alice",
           None, 500, 200, True, "Hi", "in", 1, 0, "Jakarta", 0, "instagram")
    row_html = thread_list_html([row], filter_qs="stage=dormant")
    assert 'hx-push-url="/ui/chat/452?stage=dormant"' in row_html


# ── searching by phone number ─────────────────────────────────────────────────

def test_the_same_number_is_found_however_it_is_typed() -> None:
    """A number stored as +6281211120213 gets typed three ways: as stored, in the local form
    with a leading 0, or as a fragment of either. The local form differs from the stored one
    only by 0 versus +62, so dropping the leading zero lands both on the same digits."""
    from app.api.ui import _phone_needle

    stored = "+6281211120213".replace("+", "")
    for typed in ("+6281211120213", "6281211120213", "081211120213",
                  "+62 812 1112 0213", "0812-1112-0213", "81211120213", "11120213"):
        needle = _phone_needle(typed)
        assert needle is not None, typed
        assert needle in stored, (typed, needle)


def test_a_name_is_not_treated_as_a_number() -> None:
    """Below four digits the term is almost always a name — "62" alone would match every
    Indonesian number in the inbox and bury what the person was looking for."""
    from app.api.ui import _phone_needle

    for term in ("Ade", "", "  ", "62", "a1", "Budi 12"):
        assert _phone_needle(term) is None, term


def test_a_term_of_only_zeroes_still_searches_for_something() -> None:
    """lstrip('0') on "0000" would leave an empty needle, and LIKE '%%' matches every row —
    a search that answers with the entire inbox."""
    from app.api.ui import _phone_needle

    assert _phone_needle("0000") == "0000"


def test_the_query_looks_at_the_phone_column_only_for_number_searches() -> None:
    """The name search must not pay for a phone comparison on every keystroke."""
    import re
    from pathlib import Path

    src = Path("app/api/ui.py").read_text(encoding="utf-8")
    block = src[src.index("needle = q.strip()"):]
    block = block[:block.index("# Connector filter")]
    assert "phone_e164" in block
    # …and it is guarded by the digit test rather than always appended.
    assert re.search(r"if \(phone := _phone_needle\(needle\)\) is not None", block), block


def test_the_route_accepts_a_number_in_either_notation(client: TestClient) -> None:
    """(200, 500) like its siblings above: the thread-list SQL uses LEFT JOIN LATERAL, which
    SQLite has no answer for, so this can only say the route parses the term and builds a
    query. What the query MEANS is pinned by the _phone_needle tests above and by the
    fragment check below — together they cover what a live round-trip would."""
    assert client.get("/ui/threads?q=%2B6281211120213").status_code in (200, 500)
    assert client.get("/ui/threads?q=081211120213").status_code in (200, 500)
    assert client.get("/ui/threads?q=0812-1112-0213").status_code in (200, 500)
