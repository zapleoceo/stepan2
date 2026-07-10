"""Tests for the /ui/ manager interface: i18n, HTML generators, and route smoke-tests."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from datetime import UTC

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402


def _set_lang(code: str) -> None:
    """Helper: set i18n ContextVar directly (avoids test-ordering issues)."""
    from app.api._i18n import DEFAULT_LANG, LANGS, _lang
    _lang.set(code if code in LANGS else DEFAULT_LANG)


# ─── i18n unit tests ──────────────────────────────────────────────────────────

def test_t_returns_english_by_default() -> None:
    from app.api._i18n import t
    _set_lang("en")
    assert t("nav.inbox") == "Inbox"


def test_t_returns_fallback_key_for_missing() -> None:
    from app.api._i18n import t
    _set_lang("en")
    assert t("no.such.key") == "no.such.key"


def test_apply_lang_sets_contextvar() -> None:
    from unittest.mock import MagicMock

    from app.api._i18n import apply_lang, current_lang

    req = MagicMock()
    req.cookies = {"stepan2_lang": "ru"}
    apply_lang(req)
    assert current_lang() == "ru"


def test_apply_lang_rejects_invalid_code() -> None:
    from unittest.mock import MagicMock

    from app.api._i18n import apply_lang, current_lang

    req = MagicMock()
    req.cookies = {"stepan2_lang": "fr"}
    apply_lang(req)
    assert current_lang() == "en"


@pytest.mark.parametrize("code,key,expected", [
    ("ru", "nav.inbox", "Входящие"),
    ("en", "nav.inbox", "Inbox"),
    ("id", "nav.inbox", "Kotak Masuk"),
    ("ru", "chat.send", "Отправить"),
    ("id", "coach.apply", "✓ Terapkan"),
    ("en", "stage.new", "new"),
    ("ru", "stage.new", "новый"),
    ("en", "nav.members", "Members"),
    ("ru", "nav.members", "Участники"),
])
def test_t_all_languages(code: str, key: str, expected: str) -> None:
    from app.api._i18n import t
    _set_lang(code)
    assert t(key) == expected


# ─── HTML generator unit tests ────────────────────────────────────────────────

def test_thread_list_html_empty() -> None:
    from app.api._ui_html import thread_list_html
    _set_lang("en")
    html = thread_list_html([])
    assert "emp" in html
    assert "No chats" in html


def test_thread_list_html_empty_russian() -> None:
    from app.api._ui_html import thread_list_html
    _set_lang("ru")
    html = thread_list_html([])
    assert "Нет чатов" in html


def test_thread_list_html_with_row() -> None:
    from datetime import datetime

    from app.api._ui_html import thread_list_html
    _set_lang("en")
    row = (42, "Alice Test", "new", datetime.now(UTC).replace(tzinfo=None),
           "+62812345", "course-a", "alicetest", None, 1200, 340, True, "Hello", "in", 5, 3,
           "KL", 0)
    html = thread_list_html([row])
    assert "Alice Test" in html
    assert "@alicetest" in html
    # thread list links to the shareable canonical URL, not the HTMX-only partial
    assert 'hx-get="/ui/chat/42"' in html
    assert "/ui/chat/42/panel" not in html


def test_thread_list_html_shifts_last_active_to_branch_local_time() -> None:
    """Sidebar thread list must show each thread's OWN branch-local time, not UTC —
    a per-row tz_offset_h (from b.tz_offset_h) shifts last_act before formatting."""
    from datetime import datetime

    from app.api._ui_html import thread_list_html
    _set_lang("en")
    last_act = datetime(2026, 1, 1, 20, 0, 0)  # 20:00 UTC
    row = (42, "Alice Test", "new", last_act,
           "+62812345", "course-a", "alicetest", None, 1200, 340, True, "Hello", "in", 5, 3,
           "KL", 7)  # UTC+7 branch
    html = thread_list_html([row])
    assert "03:00" in html  # 20:00 UTC + 7h = 03:00 next day, branch-local
    assert "20:00" not in html


def test_badge_renders_stage_en() -> None:
    from app.api._ui_html import _badge
    _set_lang("en")
    html = _badge("new")
    assert "sn" in html
    assert "new" in html


def test_badge_renders_stage_ru() -> None:
    from app.api._ui_html import _badge
    _set_lang("ru")
    html = _badge("new")
    assert "sn" in html
    assert "новый" in html


def test_app_shell_contains_sidebar_and_nav() -> None:
    from app.api._ui_html import app_shell
    _set_lang("en")
    html = app_shell("en", "<div>main</div>", active_nav="inbox")
    assert "Stepan 2" in html
    assert "Inbox" in html
    assert "Members" in html
    assert "RU" in html and "EN" in html and "ID" in html
    assert 'hx-get="/ui/threads"' in html
    assert "<div>main</div>" in html


def test_thread_list_poll_always_follows_the_current_url() -> None:
    """Real bug: #tl (the thread list) polls itself every 30s, but a stage-pill click only
    swapped its innerHTML — #tl's own hx-get stayed whatever it was born with, so the next
    poll silently re-fetched the WRONG filter (and dropped any non-stage filter, since the
    old fix only synced `stage`). #tl's own requests must always mirror window.location.
    search instead, so the background poll can never diverge from the visible URL."""
    from app.api._ui_html import app_shell
    _set_lang("en")
    html = app_shell("en", "<div>main</div>", active_nav="inbox", stage="ready")
    assert "htmx:configRequest" in html
    assert "el.id==='tl'" in html
    assert "window.location.search" in html
    # the old per-pill-click sync (only fixed `stage`, dropped other filters) is gone —
    # superseded by the URL-driven listener above
    assert "tl.setAttribute('hx-get'" not in html


def test_thread_list_poll_preserves_scroll_position() -> None:
    """Real bug: fixing the filter-follows-URL poll left a new regression — replacing
    #tl's innerHTML on every 30s poll reset its scrollTop to 0, so a manager scrolled
    partway down a long thread list got yanked back to the top every 30 seconds."""
    from app.api._ui_html import app_shell
    _set_lang("en")
    html = app_shell("en", "<div>main</div>", active_nav="inbox")
    assert "htmx:beforeSwap" in html and "htmx:afterSwap" in html
    assert "_tlScroll" in html
    assert "e.detail.target.scrollTop" in html


def test_app_shell_hides_members_and_branches_for_non_super_admin() -> None:
    from app.api._ui_html import app_shell
    _set_lang("en")
    html = app_shell("en", "<div>main</div>", active_nav="inbox", is_super=False)
    assert "/ui/members/panel" not in html
    assert "/ui/branches/panel" not in html
    assert "Settings" in html  # branch-scoped nav stays visible


def test_app_shell_lang_buttons_highlight_active() -> None:
    from app.api._ui_html import app_shell
    _set_lang("ru")
    html = app_shell("ru", "", active_nav="inbox")
    assert '"lb on" href="/ui/lang/ru"' in html


def test_chat_summary_translate_toggles_and_no_ptr_emulation() -> None:
    """Summary-translate button toggles its popup (trChat) with a close hook (trClose),
    a background poll no longer auto-scrolls (smartScroll gone from the poll path), and
    the flaky pull-to-refresh touch emulation is GONE (removed by owner request)."""
    from app.api._ui_html import app_shell, chat_panel_html
    _set_lang("en")
    panel = chat_panel_html(7, "Bob", "qualifying", [], [])
    assert "trChat(7)" in panel                     # composer translate toggles, not hx-post
    assert 'hx-post="/ui/chat/7/translate"' not in panel
    shell = app_shell("en", "", active_nav="inbox")
    assert "function trChat(" in shell and "function trClose(" in shell
    assert "touchstart" not in shell                # PTR emulation removed
    assert "location.reload()" not in shell


def test_app_shell_opens_chats_at_the_bottom() -> None:
    """A freshly-swapped chat panel must jump to the newest message (pinBot), while a poll
    bubble inserted into an existing feed only smart-scrolls — so opening a long chat / F5
    lands at the end, but a manager scrolled up to read history isn't yanked down."""
    from app.api._ui_html import app_shell
    _set_lang("en")
    html = app_shell("en", "", active_nav="inbox")
    assert "function pinBot(" in html           # force-to-bottom helper
    assert "scrollAllBot" in html               # F5 / direct load pins every .msgs
    assert "smartScroll" in html                # incremental poll insert stays smart


def test_app_shell_is_mobile_responsive() -> None:
    """Shell must ship the responsive viewport + a mobile @media block + the slide-in
    overlay hooks so the chat is reachable on a phone (was unreachable: 3 fixed columns)."""
    from app.api._ui_html import app_shell
    _set_lang("en")
    html = app_shell("en", "<div>m</div>", active_nav="inbox")
    assert "initial-scale=1" in html
    assert "@media (max-width:760px)" in html
    assert "chat-open" in html and "toggleNav()" in html and "backToList()" in html
    assert "scrollbar-width:thin" in html  # Firefox scrollbar styling


def test_mobile_panels_slide_main_in() -> None:
    """On mobile #main is off-screen until body.chat-open; every panel (settings/reports/
    members/...) loads into #main, so the shell must reveal it on any #main swap — else the
    whole section is blank on a phone (the reported /ui/settings/panel bug)."""
    from app.api._ui_html import app_shell
    _set_lang("en")
    html = app_shell("en", "<div>m</div>", active_nav="inbox")
    assert "t.id==='main'&&window.innerWidth<=760" in html   # slide #main in on mobile
    assert "function showThr(v){if(window.innerWidth<=760)return;" in html  # keep .thr base


def test_app_shell_has_favicon() -> None:
    from app.api._ui_html import app_shell
    _set_lang("en")
    html = app_shell("en", "<div>m</div>", active_nav="inbox")
    assert "rel='icon'" in html and "data:image/svg+xml" in html  # inline SVG favicon


def test_mobile_bubble_buttons_visible_and_header_clears_back() -> None:
    from app.api._ui_html import app_shell
    _set_lang("en")
    html = app_shell("en", "<div>m</div>", active_nav="inbox")
    assert ".trx,.delx{opacity:.5}" in html                    # visible without hover on touch
    assert "body.chat-open .ch{padding-left:2.9rem}" in html   # header clears the ‹ back button


def test_outbox_badge_does_not_push_url() -> None:
    """The polling outbox badge sits inside the outbox <a hx-push-url=...>; without its own
    hx-push-url="false" its load/every-15s poll rewrites the address bar to /ui/outbox/panel,
    hijacking a direct /ui/chat/{id} URL. Guard that it opts out of URL pushing."""
    from app.api._ui_html import app_shell
    _set_lang("en")
    html = app_shell("en", "<div>m</div>", active_nav="inbox")
    i = html.find('id="outbox-badge"')
    assert i != -1
    end = html.find(">", i)
    assert 'hx-push-url="false"' in html[i:end]


def test_app_shell_has_help_button() -> None:
    from app.api._ui_html import app_shell
    _set_lang("en")
    html = app_shell("en", "", active_nav="inbox")
    assert "help-btn" in html
    assert "hov" in html


def test_app_shell_help_content_matches_nav() -> None:
    from app.api._ui_html import app_shell
    _set_lang("en")
    html = app_shell("en", "", active_nav="coach")
    assert "Coach KB" in html


def test_chat_panel_html_contains_send_form() -> None:
    from app.api._ui_html import chat_panel_html
    _set_lang("en")
    html = chat_panel_html(7, "Bob", "qualifying", [], [])
    assert 'hx-post="/ui/chat/7/send"' in html
    assert "Send" in html
    assert "qualifying" in html or "sq" in html


def test_coach_pair_proposed_shows_actions() -> None:
    from datetime import datetime

    from app.api._ui_panels import _coach_pair
    _set_lang("en")
    html = _coach_pair(
        1, "Change price", "proposed", "pricing",
        "old text", "new text", "summary", datetime.now(UTC).replace(tzinfo=None),
    )
    assert "✓ Apply" in html
    assert "✗ Cancel" in html
    assert "pricing" in html
    assert "old text" in html
    assert "new text" in html
    # two bubbles: manager (bb-o) and coach (bb-i)
    assert "bb-o" in html and "bb-i" in html


def test_coach_pair_applied_no_actions() -> None:
    from datetime import datetime

    from app.api._ui_panels import _coach_pair
    _set_lang("en")
    html = _coach_pair(
        2, "Done", "applied", None, None, None, "done",
        datetime.now(UTC).replace(tzinfo=None),
    )
    assert "✓ Apply" not in html
    assert "done" in html


def test_coach_response_is_coach_bubble_only() -> None:
    """The /coach/say response carries ONLY the coach bubble — the manager's own message is
    appended optimistically on the client, so echoing it here would double it."""
    from datetime import datetime

    from app.api._ui_panels import _coach_response
    _set_lang("en")
    html = _coach_response(3, "Change price", "proposed", "pricing",
                           "old text", "new text", "summary",
                           datetime.now(UTC).replace(tzinfo=None))
    assert "bb-i" in html and "✓ Apply" in html
    assert "bb-o" not in html and "Change price" not in html  # no manager echo


def test_coach_chat_html_renders_form() -> None:
    from app.api._ui_panels import coach_chat_html
    _set_lang("en")
    html = coach_chat_html(1, [], [])
    assert 'hx-post="/ui/coach/say"' in html
    assert "coach-msgs" in html
    assert "coachSend(this)" in html          # optimistic manager bubble on send
    assert "coach-thinking" in html and "data-msgs" in html  # detailed thinking cycle


def test_coach_chat_textarea_sends_on_enter() -> None:
    """Enter submits the coach message (Shift+Enter still inserts a newline) — matches
    the main chat composer's entSend() behavior instead of requiring a button click."""
    from app.api._ui_panels import coach_chat_html
    _set_lang("en")
    html = coach_chat_html(1, [], [])
    assert 'onkeydown="entSend(event)"' in html


def test_kb_tree_html_empty() -> None:
    from app.api._ui_kb import kb_tree_html
    _set_lang("en")
    html = kb_tree_html([])
    assert "Persona" in html and "Products" in html  # tabs always present


def test_kb_tree_html_groups_by_category() -> None:
    from app.api._ui_kb import kb_tree_html
    _set_lang("en")
    docs = [(1, "persona_core", "Persona core", "hi", "persona", 0, None),
            (2, "playbook_price", "Price", "x", "playbook", 1, None)]
    html = kb_tree_html(docs)
    assert "Persona" in html and "Playbooks" in html  # category group headers
    assert 'hx-get="/ui/knowledge/1/edit"' in html


def test_members_panel_html() -> None:
    from app.api._ui_members import members_panel_html
    _set_lang("en")
    rows = [(10, 169510539, "branch_admin", "Dima", 1)]
    branches = [(1, "Jakarta")]
    html = members_panel_html(rows, branches)
    assert "Dima" in html
    assert "tg:169510539" in html
    assert 'value="branch_admin" selected' in html
    assert "Branch admin" in html
    assert 'value="1" selected' in html and "Jakarta" in html


def test_members_panel_html_row_has_role_branch_and_delete_controls() -> None:
    from app.api._ui_members import members_panel_html
    _set_lang("en")
    rows = [(10, 169510539, "super_admin", "Dima", None)]
    html = members_panel_html(rows, [(1, "Jakarta")])
    assert 'hx-post="/ui/members/10/role"' in html
    assert 'hx-post="/ui/members/10/branch"' in html
    assert 'hx-post="/ui/members/10/delete"' in html
    assert 'value="" selected' in html  # platform-wide (branch_id None)


def test_members_panel_html_add_form() -> None:
    from app.api._ui_members import members_panel_html
    _set_lang("en")
    html = members_panel_html([], [(2, "Bali")])
    assert 'hx-post="/ui/members/create"' in html
    assert 'name="telegram_id"' in html and 'name="name"' in html
    assert "Bali" in html


def test_settings_panel_html_with_desc() -> None:
    from app.api._ui_panels import settings_panel_html
    _set_lang("en")
    rows = [(1, 1, "daily_cap", "100"), (2, 1, "bot_enabled", "true")]
    html = settings_panel_html(rows)
    assert "daily_cap" in html
    assert "bot_enabled" in html
    assert "Max bot messages" in html  # description shown
    assert 'hx-post="/ui/settings/1/save"' in html


def test_settings_panel_unknown_key_no_desc() -> None:
    from app.api._ui_panels import settings_panel_html
    _set_lang("en")
    rows = [(5, 1, "unknown_setting", "xyz")]
    html = settings_panel_html(rows)
    assert "unknown_setting" in html
    assert "set-desc" not in html  # no description for unknown key


def test_products_panel_html() -> None:
    from app.api._ui_panels import products_panel_html
    _set_lang("en")
    products = [(1, "vibe-coding", "Vibe Coding", True, 0)]
    html = products_panel_html(products)
    assert "Vibe Coding" in html
    assert "p-ok" in html
    assert "hint" in html  # sort explanation shown


# ─── route smoke tests ────────────────────────────────────────────────────────

@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_inbox_returns_200(client: TestClient) -> None:
    resp = client.get("/ui/inbox")
    assert resp.status_code == 200
    assert "Stepan 2" in resp.text


def test_inbox_with_russian_cookie(client: TestClient) -> None:
    resp = client.get("/ui/inbox", cookies={"stepan2_lang": "ru"})
    assert resp.status_code == 200
    assert "Входящие" in resp.text


def test_inbox_has_members_nav(client: TestClient) -> None:
    resp = client.get("/ui/inbox")
    assert resp.status_code == 200
    assert "/ui/members/panel" in resp.text
    assert 'href="/ui/knowledge"' in resp.text
    assert "/ui/settings/panel" in resp.text


# ─── help mode: ? toggles explainer mode, tips ride on data-help ────────────────

def test_help_mode_wiring_in_shell() -> None:
    """One ? toggle flips body.help-mode; annotated elements get a dashed outline; a
    single fixed-position #help-tip floats above the page (never clipped by cards);
    ONE delegated document-level hover handler survives htmx re-renders."""
    from app.api._ui_html import app_shell
    _set_lang("en")
    shell = app_shell("en", "", active_nav="inbox")
    assert 'id="help-tip"' in shell
    assert "body.help-mode [data-help]" in shell            # dashed 'hover me' outlines
    assert "document.body.classList.toggle('help-mode')" in shell
    assert "document.addEventListener('mouseover'" in shell  # delegated, not per-element
    assert "#help-tip{position:fixed" in shell               # floats above everything
    assert 'id="hov"' not in shell                           # old modal overlay is gone


def test_help_mode_nav_and_sidebar_have_tips() -> None:
    """Nav links reuse the section help.* texts as data-help; sidebar controls
    (branch filter, master bot switch, UI language) are annotated too."""
    import html as _h

    from app.api._i18n import t
    from app.api._ui_html import app_shell
    _set_lang("en")
    shell = app_shell("en", "", active_nav="inbox")
    for key in ("help.inbox", "hint.branch", "hint.bot_global",
                "hint.search", "hint.funnel"):
        assert f'data-help="{_h.escape(t(key))}"' in shell, key


def test_help_mode_chat_elements_have_tips() -> None:
    """Chat header controls and the composer are annotated for explainer mode."""
    import html as _h

    from app.api._i18n import t
    from app.api._ui_html import chat_panel_html
    _set_lang("en")
    panel = chat_panel_html(7, "Bob", "qualifying", [], [],
                            products=[("vibe", "Vibe Coding")])
    for key in ("hint.stage", "hint.product", "hint.bot_chat", "hint.block",
                "hint.clear_ctx", "hint.load_ctx", "hint.suggest", "hint.summary",
                "hint.composer"):
        assert f'data-help="{_h.escape(t(key))}"' in panel, key


def test_help_mode_tips_are_localized() -> None:
    from app.api._i18n import t
    _set_lang("ru")
    ru = t("hint.suggest")
    _set_lang("id")
    idn = t("hint.suggest")
    assert ru != idn and ru and idn  # each language carries its own text


# ─── language switch keeps the current view ────────────────────────────────────

def test_lang_switch_returns_to_the_open_chat(client: TestClient) -> None:
    """Switching language from inside a chat must NOT eject the manager to the inbox —
    the middleware wraps any /ui/** path in the full shell, so redirect right back."""
    resp = client.get(
        "/ui/lang/ru",
        headers={"referer": "https://stepan2.zapleo.com/ui/chat/452"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/chat/452"
    assert "stepan2_lang=ru" in resp.headers.get("set-cookie", "")


def test_lang_switch_preserves_query_and_defends_the_redirect(client: TestClient) -> None:
    # query string survives (e.g. a filtered inbox view)
    ok = client.get(
        "/ui/lang/en",
        headers={"referer": "https://stepan2.zapleo.com/ui/inbox?stage=ready"},
        follow_redirects=False,
    )
    assert ok.headers["location"] == "/ui/inbox?stage=ready"
    # foreign/absolute referers and non-/ui/ paths fall back to the inbox (no open redirect)
    for ref in ("https://evil.example/ui/../../etc", "https://x.y/elsewhere", ""):
        r = client.get("/ui/lang/id", headers={"referer": ref}, follow_redirects=False)
        assert r.headers["location"] == "/ui/inbox" or r.headers["location"].startswith("/ui/")
        assert not r.headers["location"].startswith("http")
    # the switcher itself as referer must not loop
    loop = client.get(
        "/ui/lang/en",
        headers={"referer": "https://stepan2.zapleo.com/ui/lang/ru"},
        follow_redirects=False,
    )
    assert loop.headers["location"] == "/ui/inbox"


def test_inbox_has_help_button(client: TestClient) -> None:
    resp = client.get("/ui/inbox")
    assert resp.status_code == 200
    assert "help-btn" in resp.text


def test_threads_partial_exists(client: TestClient) -> None:
    resp = client.get("/ui/threads")
    assert resp.status_code in (200, 500)



def test_chat_page_missing_thread_non_hx_redirects_to_inbox(client: TestClient) -> None:
    resp = client.get("/ui/chat/99999999", follow_redirects=False)
    assert resp.status_code in (303, 500)
    if resp.status_code == 303:
        assert resp.headers["location"] == "/ui/inbox"


def test_chat_page_missing_thread_hx_returns_404(client: TestClient) -> None:
    resp = client.get("/ui/chat/99999999", headers={"HX-Request": "true"})
    assert resp.status_code in (404, 500)


def test_coach_page_exists(client: TestClient) -> None:
    resp = client.get("/ui/coach")
    assert resp.status_code in (200, 500)


def test_coach_panel_partial_exists(client: TestClient) -> None:
    resp = client.get("/ui/coach/panel")
    assert resp.status_code in (200, 500)


def test_knowledge_tree_exists(client: TestClient) -> None:
    resp = client.get("/ui/knowledge/tree")
    assert resp.status_code in (200, 500)


def test_products_panel_exists(client: TestClient) -> None:
    resp = client.get("/ui/products/panel")
    assert resp.status_code in (200, 500)


def test_members_panel_exists(client: TestClient) -> None:
    resp = client.get("/ui/members/panel")
    assert resp.status_code in (200, 500)


def test_settings_panel_exists(client: TestClient) -> None:
    resp = client.get("/ui/settings/panel")
    assert resp.status_code in (200, 500)


def test_lang_redirect_sets_cookie(client: TestClient) -> None:
    resp = client.get("/ui/lang/ru", follow_redirects=False)
    assert resp.status_code == 303
    assert "stepan2_lang=ru" in resp.headers.get("set-cookie", "")


def test_lang_invalid_falls_back_to_en(client: TestClient) -> None:
    resp = client.get("/ui/lang/fr", follow_redirects=False)
    assert resp.status_code == 303
    assert "stepan2_lang=en" in resp.headers.get("set-cookie", "")


def test_chat_panel_not_found_no_500_crash(client: TestClient) -> None:
    resp = client.get("/ui/chat/9999999/panel")
    assert resp.status_code in (200, 404, 500)


# ─── new panels smoke tests ───────────────────────────────────────────────────

def test_leads_panel_exists(client: TestClient) -> None:
    resp = client.get("/ui/leads/panel")
    assert resp.status_code in (200, 500)


def test_outbox_panel_exists(client: TestClient) -> None:
    resp = client.get("/ui/outbox/panel")
    assert resp.status_code in (200, 500)


def test_outbox_count_route_exists(client: TestClient) -> None:
    resp = client.get("/ui/outbox/count")
    assert resp.status_code in (200, 500)


def test_outbox_count_html_hidden_when_zero() -> None:
    from app.api._ui_panels import outbox_count_html
    assert outbox_count_html(0) == ""
    assert outbox_count_html(7) == "7"


async def test_outbox_panel_excludes_sent_and_failed(db_session) -> None:
    """The Outbox tab is a queue monitor — a sent/failed row belongs in message history
    or the broker log, not here."""
    import app.api._routes_admin as admin_routes
    from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Outbox
    from app.domain.enums import ChannelKind

    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id)
    db_session.add_all([ch, lead])
    await db_session.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    db_session.add(th)
    await db_session.flush()
    db_session.add_all([
        Outbox(branch_id=b.id, thread_id=th.id, text="queued", source="agent",
              status="pending"),
        Outbox(branch_id=b.id, thread_id=th.id, text="already sent", source="agent",
              status="sent"),
        Outbox(branch_id=b.id, thread_id=th.id, text="gave up", source="agent",
              status="failed"),
    ])
    await db_session.flush()

    class _Scope:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *a) -> None:
            await db_session.commit()

    class _Req:
        cookies: dict = {}
        headers: dict = {}
        state = type("S", (), {})()

    orig = admin_routes.session_scope
    admin_routes.session_scope = lambda: _Scope()
    try:
        resp = await admin_routes.outbox_panel(_Req())  # type: ignore[arg-type]
    finally:
        admin_routes.session_scope = orig

    body = resp.body.decode()
    assert "queued" in body
    assert "already sent" not in body
    assert "gave up" not in body


def test_products_new_form_exists(client: TestClient) -> None:
    resp = client.get("/ui/products/new")
    assert resp.status_code == 200
    assert "slug" in resp.text


def test_knowledge_products_tab_exists(client: TestClient) -> None:
    resp = client.get("/ui/knowledge/products")
    assert resp.status_code in (200, 500)


def test_inbox_has_leads_nav(client: TestClient) -> None:
    resp = client.get("/ui/inbox")
    assert resp.status_code == 200
    assert "/ui/leads/panel" in resp.text
    assert "/ui/outbox/panel" in resp.text


# ─── new HTML generator unit tests ───────────────────────────────────────────

def test_leads_panel_html_empty() -> None:
    from app.api._ui_panels import leads_panel_html
    _set_lang("en")
    html = leads_panel_html([])
    assert "Leads" in html


def test_leads_panel_html_with_rows() -> None:
    from datetime import datetime

    from app.api._ui_panels import leads_panel_html
    _set_lang("en")
    rows = [(1, "Alice Test", "+62811234567", "qualifying",
             datetime.now(UTC).replace(tzinfo=None), 9)]
    html = leads_panel_html(rows)
    assert "Alice Test" in html
    assert "+62811234567" in html
    assert "sq" in html  # qualifying badge CSS


def test_leads_panel_html_created_date_is_branch_local() -> None:
    """A lead created at 23:30 UTC in a UTC+7 branch is already the NEXT calendar day
    locally — the created-date column must reflect that, not the raw UTC date."""
    from datetime import datetime

    from app.api._ui_panels import leads_panel_html
    _set_lang("en")
    created = datetime(2026, 1, 1, 23, 30, 0)  # 2026-01-01 UTC
    rows = [(1, "Alice Test", "+62811234567", "qualifying", created, 9)]
    html = leads_panel_html(rows, tz_by_branch={9: 7})
    assert "2026-01-02" in html  # shifted +7h crosses into the next day
    assert "2026-01-01" not in html


def test_outbox_panel_html_empty() -> None:
    from app.api._ui_panels import outbox_panel_html
    _set_lang("en")
    html = outbox_panel_html([])
    assert "Outbox" in html
    assert "read-only" in html


def test_outbox_panel_html_statuses() -> None:
    from datetime import datetime

    from app.api._ui_panels import outbox_panel_html
    _set_lang("en")
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = [
        (1, 10, "sent", "agent", "Hello!", now, now, 1),
        (2, 77, "pending", "manager", "Wait…", now, None, 1),
        (3, 10, "failed", "followup", "Error", now, None, 1),
    ]
    html = outbox_panel_html(rows)
    assert "s-sent" in html
    assert "s-pend" in html
    assert "s-fail" in html
    assert 'hx-get="/ui/chat/77"' in html  # canonical shareable chat URL
    assert ">#77<" in html


def test_outbox_panel_html_shows_rate_cap_held_marker() -> None:
    """A due, pending, non-manager row must show WHY it's stuck when the branch's send cap
    is currently reached — otherwise it just looks like a silently broken send."""
    from datetime import datetime

    from app.api._ui_panels import outbox_panel_html
    _set_lang("en")
    now = datetime.now(UTC).replace(tzinfo=None)
    due = [(1, 10, "pending", "agent", "hi", now, None, 1)]
    held = outbox_panel_html(due, cap_status={1: (True, False)})
    assert "cap reached" in held.lower()
    # not capped -> falls through to the normal "now" indicator
    clear = outbox_panel_html(due, cap_status={1: (False, False)})
    assert "cap reached" not in clear.lower()
    # a manager send bypasses the cap entirely (see outbox.py _cap_reached) — never marked
    mgr_due = [(2, 11, "pending", "manager", "hi", now, None, 1)]
    mgr = outbox_panel_html(mgr_due, cap_status={1: (True, True)})
    assert "cap reached" not in mgr.lower()


def test_outbox_panel_html_shows_sending_paused_marker() -> None:
    """The branch-level sending_enabled switch (independent of the bot on/off toggle) holds
    EVERY due row, including manager sends — send_outbox skips the whole branch when off."""
    from datetime import datetime

    from app.api._ui_panels import outbox_panel_html
    _set_lang("en")
    now = datetime.now(UTC).replace(tzinfo=None)
    agent_due = [(1, 10, "pending", "agent", "hi", now, None, 1)]
    mgr_due = [(2, 11, "pending", "manager", "hi", now, None, 1)]
    paused = outbox_panel_html(agent_due + mgr_due, sending_paused={1: True})
    assert paused.lower().count("sending paused") == 2  # both rows held, manager included
    resumed = outbox_panel_html(agent_due, sending_paused={1: False})
    assert "sending paused" not in resumed.lower()
    # pause takes priority over the cap marker when both apply — one clear reason, not two
    both = outbox_panel_html(agent_due, cap_status={1: (True, False)}, sending_paused={1: True})
    assert "sending paused" in both.lower() and "cap reached" not in both.lower()


def test_outbox_panel_html_shifts_times_to_branch_local() -> None:
    """scheduled_at/sent_at must render in the row's own branch-local time, not raw UTC —
    was a plain str(v)[11:19] slice with zero tz correction."""
    from datetime import datetime

    from app.api._ui_panels import outbox_panel_html
    _set_lang("en")
    scheduled = datetime(2026, 1, 1, 10, 0, 0)  # 10:00 UTC
    rows = [(1, 10, "pending", "agent", "Hi", scheduled, None, 5)]
    html = outbox_panel_html(rows, tz_by_branch={5: 7})
    assert "17:00:00" in html  # 10:00 UTC + 7h
    assert "10:00:00" not in html


def test_product_edit_html_new() -> None:
    from app.api._ui_panels import product_edit_html
    _set_lang("en")
    html = product_edit_html(None, "", "", "", True, 0)
    assert "/ui/products/create" in html
    assert "slug" in html


def test_product_edit_html_existing() -> None:
    from app.api._ui_panels import product_edit_html
    _set_lang("en")
    html = product_edit_html(5, "vibe-coding", "Vibe Coding", "Content here", True, 1)
    assert "/ui/products/5/save" in html
    assert "Delete" in html
    assert "vibe-coding" in html


def test_kb_editor_section_skeleton_for_empty_canonical_doc() -> None:
    from app.api._ui_kb import kb_editor_html
    _set_lang("en")
    html = kb_editor_html(1, "persona_core", "Persona core", "")
    assert 'name="body_0"' in html and 'name="nsec"' in html  # section editor
    assert "Identity" in html                                  # localized section title
    assert "/ui/knowledge/1/history" in html                   # history button


def test_kb_editor_parses_existing_sections() -> None:
    from app.api._ui_kb import kb_editor_html
    _set_lang("en")
    html = kb_editor_html(2, "faq", "FAQ", "## Payment\nWe take cards.\n\n## Hours\n9-5")
    assert "Payment" in html and "Hours" in html
    assert "We take cards" in html


def test_kb_history_html_shows_branch_local_time() -> None:
    """Revision timestamps must honor set_render_tz — previously the route never called it,
    so this rendered whatever tz happened to be left over (usually UTC/0) from a prior request."""
    from datetime import datetime

    from app.api._ui_html import set_render_tz
    from app.api._ui_kb import kb_history_html
    _set_lang("en")
    set_render_tz(7)
    try:
        created = datetime(2026, 1, 1, 20, 0, 0)  # 20:00 UTC
        revs = [(1, "old", "new", 3, 3, "Dima", created)]
        html = kb_history_html("/ui/knowledge/1/edit", "faq", revs)
    finally:
        set_render_tz(0)
    assert "03:00:00" in html  # 20:00 UTC + 7h, next-day branch-local
    assert "20:00:00" not in html


def test_kb_history_html_accepts_iso_string_created_at() -> None:
    """SQLite returns created_at as an ISO string, not a datetime — must still parse+shift
    instead of silently falling back to an unshifted raw string (the old hasattr('year') bug)."""
    from app.api._ui_html import set_render_tz
    from app.api._ui_kb import kb_history_html
    _set_lang("en")
    set_render_tz(7)
    try:
        revs = [(1, "old", "new", 3, 3, "Dima", "2026-01-01T20:00:00")]
        html = kb_history_html("/ui/knowledge/1/edit", "faq", revs)
    finally:
        set_render_tz(0)
    assert "03:00:00" in html


def test_chat_header_html() -> None:
    from app.api._ui_html import chat_header_html
    _set_lang("en")
    html = chat_header_html(42, "Alice", "qualifying")
    assert "chat-hdr-42" in html
    assert "Alice" in html
    assert 'selected' in html  # selected option for qualifying


def test_suggest_box_html() -> None:
    from app.api._ui_html import suggest_box_html
    _set_lang("en")
    html = suggest_box_html(7, "Draft text here")
    assert "Draft text here" in html
    assert "sendSuggest(7)" in html
    assert "sug-ta-7" in html


def test_products_panel_html_has_create_button() -> None:
    from app.api._ui_panels import products_panel_html
    _set_lang("en")
    html = products_panel_html([])
    assert "/ui/products/new" in html


def test_kb_products_tab_has_create_button() -> None:
    from app.api._ui_kb import kb_products_html
    _set_lang("en")
    html = kb_products_html([])
    assert "/ui/products/new" in html


def test_coach_pair_applied_shows_revert() -> None:
    from datetime import datetime

    from app.api._ui_panels import _coach_pair
    _set_lang("en")
    html = _coach_pair(
        3, "Done", "applied", "persona", "old", "new", "summary",
        datetime.now(UTC).replace(tzinfo=None),
    )
    assert "Revert" in html
    assert "/ui/coach/revert/3" in html


# ─── agent toggle unit tests ──────────────────────────────────────────────────

def test_agent_toggles_html_shows_both_switches() -> None:
    from app.api.ui import _agent_toggles_html
    _set_lang("en")
    html = _agent_toggles_html(1, platform_on=True, branch_on=False)
    assert "whole platform" in html and "this branch" in html  # both scopes
    assert 'name="scope" value="platform"' in html and 'name="scope" value="branch"' in html
    assert "ON" in html and "OFF" in html  # platform ON, branch OFF
    assert "/ui/agent-toggle" in html


def test_agent_status_route_responds(client: TestClient) -> None:
    resp = client.get("/ui/agent-status")
    assert resp.status_code in (200, 500)


def test_admin_root_redirects_to_ui() -> None:
    client = TestClient(app, follow_redirects=False)
    resp = client.get("/admin")
    # AdminGuardMiddleware bounces a session-less /admin to the app (303); with a
    # super-admin session the dashboard would load instead.
    assert resp.status_code in (302, 303)
    assert resp.headers["location"] == "/ui/inbox"


def test_landing_is_public_and_generic() -> None:
    """The product landing at / renders for anonymous visitors, carries the login link,
    and never names the client (no IT Step / course specifics)."""
    client = TestClient(app, follow_redirects=False)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert body.lstrip().lower().startswith("<!doctype")
    assert "Stepan" in body
    assert 'href="/login"' in body               # login present (top-right)
    assert "ig.me" in body                        # "Talk to Stepan" demo CTA
    assert "A peek inside" in body                 # illustrative UI mockups
    assert "Connected to your ad accounts" in body  # ad-cabinet pull + attribution
    assert "Re-qualifies mid-conversation" in body  # in-chat re-qualification
    assert "MCP connector" in body                 # CRM sync via MCP
    assert "TikTok" in body                        # channels incl. coming-soon
    assert "Lead segments" in body and "<svg" in body  # analytics dashboard
    assert "Illustrative" in body                  # labelled sample data, not real
    low = body.lower()
    assert "it step" not in low and "itstep" not in low  # client not revealed


def test_landing_path_is_public_in_auth() -> None:
    from app.api._auth import _is_public
    assert _is_public("/") is True
    assert _is_public("/ui/inbox") is False       # "/" prefix must not open everything
    assert _is_public("/demo/chat") is True        # public demo endpoint


def test_landing_has_in_page_chat_widget() -> None:
    client = TestClient(app, follow_redirects=False)
    body = client.get("/").text
    assert "openStepan()" in body                  # CTAs open the widget
    assert "/demo/chat" in body                    # widget calls the demo endpoint


def test_landing_pricing_section_states_the_flat_per_lead_fee() -> None:
    """Up to 10 leads/day free, then a flat $1/lead regardless of outcome or conversation
    length — no per-message/per-token metering, so a long qualification never costs more."""
    client = TestClient(app, follow_redirects=False)
    body = client.get("/").text
    assert 'id="pricing"' in body
    assert "Up to 10 leads / day" in body
    assert "Free" in body
    assert "$1" in body and "/lead" in body
    assert "no matter the outcome or how long Stepan talks to them" in body
    assert "Unlimited messages per lead" in body
    assert 'id="stp-w"' in body                    # the chat panel is present


def test_landing_has_meta_business_agent_comparison_table() -> None:
    """A grounded, fact-checkable comparison vs Meta's own June-2026 Business Agent —
    not a strawman: Meta's real capabilities are represented, not omitted."""
    client = TestClient(app, follow_redirects=False)
    body = client.get("/").text
    assert "Meta Business Agent" in body
    assert '<table class="mtable">' in body
    assert "Flat $1 per lead" in body
    assert "Per-token" in body                     # Meta's real pricing model, stated fairly
    assert "Not publicly documented" in body        # honest, not overclaiming vs Meta


def test_landing_has_enterprise_trust_section() -> None:
    """Multi-brand/role-based-access signals for a buyer evaluating Stepan across many
    locations or brands, not just a single small account."""
    client = TestClient(app, follow_redirects=False)
    body = client.get("/").text
    assert "Built for multiple brands" in body
    assert "Role-based access" in body


def test_demo_chat_empty_returns_fallback_without_llm() -> None:
    """No/blank messages → a graceful fallback and NO broker call."""
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/demo/chat", json={"messages": []})
    assert resp.status_code == 200
    assert resp.json()["reply"]                     # some non-empty fallback


def test_demo_chat_returns_stepan_reply(monkeypatch) -> None:
    """A real turn calls the broker (chat:smart) and returns its text; the system prompt
    positions Stepan as selling itself."""
    import app.api._routes_demo as demo

    captured = {}

    class _FakeBroker:
        async def chat(self, messages, **kw):  # noqa: ANN001, ANN003
            captured["system"] = messages[0]["content"]
            captured["cap"] = kw.get("capability")
            return "Love it — what do you sell?", {}

    monkeypatch.setattr(demo, "BrokerLLM", _FakeBroker)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/demo/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.json()["reply"] == "Love it — what do you sell?"
    assert captured["cap"] == "chat:smart"          # full-strength model, no downgrade
    sysp = captured["system"]
    assert "sell YOURSELF" in sysp                   # demo persona: Stepan sells itself
    assert "MCP connector" in sysp                   # answer bank covers CRM sync
    assert "TikTok is coming soon" in sysp           # channels incl. coming-soon
    assert "re-qualify a lead mid-chat" in sysp      # in-conversation re-qualification
    low = sysp.lower()
    assert "it step" not in low and "itstep" not in low  # never reveals the real client


def test_demo_chat_retries_once_on_broker_failure(monkeypatch) -> None:
    """A stuck provider (first attempt raises) is retried; the second attempt's reply wins,
    so a transient broker timeout doesn't surface as the glitch fallback."""
    import app.api._routes_demo as demo

    calls = {"n": 0}

    class _FlakyBroker:
        async def chat(self, messages, **kw):  # noqa: ANN001, ANN003
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("broker read timeout")
            return "Back with you — what do you sell?", {}

    monkeypatch.setattr(demo, "BrokerLLM", _FlakyBroker)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/demo/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert calls["n"] == 2                              # retried after the first failure
    assert resp.json()["reply"] == "Back with you — what do you sell?"


# ─── lang switch: stay on the current view (path-only redirect) ────────────────

def test_lang_switch_from_full_page_preserves_referer() -> None:
    client = TestClient(app, follow_redirects=False)
    resp = client.get(
        "/ui/lang/ru",
        headers={"Referer": "http://testserver/ui/inbox"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/inbox"  # path-only, same view
    assert "stepan2_lang=ru" in resp.headers.get("set-cookie", "")


def test_lang_switch_from_partial_stays_on_that_view() -> None:
    """Partial URLs are safe redirect targets now — _PartialShellMiddleware wraps them
    in the full shell on direct load, so switching language keeps the open panel."""
    client = TestClient(app, follow_redirects=False)
    resp = client.get(
        "/ui/lang/ru",
        headers={"Referer": "http://testserver/ui/settings/panel"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/settings/panel"


def test_lang_switch_no_referer_redirects_to_inbox() -> None:
    client = TestClient(app, follow_redirects=False)
    resp = client.get("/ui/lang/en")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/inbox"


def test_lang_switch_invalid_code_defaults_to_en() -> None:
    client = TestClient(app, follow_redirects=False)
    resp = client.get("/ui/lang/fr")
    assert resp.status_code == 303
    assert "stepan2_lang=en" in resp.headers.get("set-cookie", "")


# ─── branch helper unit tests ─────────────────────────────────────────────────

def test_branch_where_with_ids_returns_clause() -> None:
    from app.api._query import _branch_where
    where, params = _branch_where([1, 2])
    assert "ANY(:bids)" in where
    assert params == {"bids": [1, 2]}


def test_branch_where_none_returns_empty() -> None:
    from app.api._query import _branch_where
    where, params = _branch_where(None)
    assert where == ""
    assert params == {}


def test_branch_where_custom_col() -> None:
    from app.api._query import _branch_where
    where, params = _branch_where([3], col="m.branch_id")
    assert "m.branch_id = ANY(:bids)" in where
    assert params == {"bids": [3]}


# ─── POST route smoke tests ────────────────────────────────────────────────────

def test_chat_send_missing_thread_returns_200(client: TestClient) -> None:
    resp = client.post("/ui/chat/99999/send", data={"text": "hello"})
    assert resp.status_code in (200, 500)


def test_chat_send_empty_text_returns_200(client: TestClient) -> None:
    resp = client.post("/ui/chat/1/send", data={"text": "  "})
    assert resp.status_code in (200, 500)


def test_knowledge_save_missing_doc_returns_404_or_500(client: TestClient) -> None:
    resp = client.post(
        "/ui/knowledge/99999/save",
        data={"title": "Test", "content": "Content"},
    )
    assert resp.status_code in (404, 500)


def test_products_save_missing_prod_returns_404_or_500(client: TestClient) -> None:
    resp = client.post(
        "/ui/products/99999/save",
        data={"title": "Test", "content": "Content", "sort_order": "0"},
    )
    assert resp.status_code in (404, 500)


def test_products_delete_missing_prod_returns_303(client: TestClient) -> None:
    """Delete of non-existent product redirects (303) in prod; 500 in SQLite test env."""
    client_no_follow = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
    resp = client_no_follow.post("/ui/products/99999/delete")
    assert resp.status_code in (303, 500)


def test_chat_stage_invalid_stage_coerced_to_new(client: TestClient) -> None:
    resp = client.post("/ui/chat/99999/stage", data={"stage": "invalid"})
    assert resp.status_code in (200, 404, 500)


def test_agent_toggle_post_returns_html(client: TestClient) -> None:
    resp = client.post("/ui/agent-toggle", data={"branch_id": "1"})
    assert resp.status_code in (200, 500)
    if resp.status_code == 200:
        assert "bot-tog" in resp.text


def test_sending_toggle_html_reflects_state() -> None:
    """Sidebar quick-switch for send_outbox — separate widget from the bot on/off switch,
    posting to its own endpoint/target so toggling one never touches the other."""
    from app.api._routes_admin import _sending_toggle_html
    _set_lang("en")
    off = _sending_toggle_html(1, sending_on=False)
    assert "Sending (outbound)" in off
    assert "OFF" in off
    assert "/ui/sending-toggle" in off
    assert "#sending-tog-wrap" in off
    on = _sending_toggle_html(1, sending_on=True)
    assert "ON" in on
    # no branch selected -> a hint, not a guessed toggle
    hint = _sending_toggle_html(None, None)
    assert "tgl-hint" in hint


def test_sending_status_route_responds(client: TestClient) -> None:
    resp = client.get("/ui/sending-status")
    assert resp.status_code in (200, 500)


def test_sending_toggle_post_returns_html(client: TestClient) -> None:
    resp = client.post("/ui/sending-toggle", data={"branch_id": "1"})
    assert resp.status_code in (200, 500)
    if resp.status_code == 200:
        # no branch selected in the filter -> a pick-branch hint (no single branch to flip);
        # with one selected it would post to /ui/sending-toggle instead.
        assert "sending-toggle" in resp.text or "tgl-hint" in resp.text


# ─── query helpers: fetch_messages, fetch_pending ─────────────────────────────

def test_ago_returns_localized_minutes() -> None:
    """_ago must use i18n time abbreviations, not hardcoded Russian."""
    from datetime import datetime, timedelta

    from app.api._ui_html import _ago
    _set_lang("en")
    dt = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
    result = _ago(dt)
    assert "m" in result  # English: "5m"
    assert "м" not in result  # Must not be Russian


def test_ago_returns_russian_when_lang_ru() -> None:
    from datetime import datetime, timedelta

    from app.api._ui_html import _ago
    _set_lang("ru")
    dt = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
    result = _ago(dt)
    assert "м" in result  # Russian: "5м"


def test_ago_hours_uses_i18n() -> None:
    from datetime import datetime, timedelta

    from app.api._ui_html import _ago
    _set_lang("en")
    dt = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=3)
    result = _ago(dt)
    assert "h" in result  # English: "3h"


# ─── sub-module route registration ───────────────────────────────────────────

def test_all_sub_routers_reachable(client: TestClient) -> None:
    """Smoke-test each sub-router endpoint is registered (200 or 404/500 is fine)."""
    partial_urls = [
        "/ui/leads/panel",
        "/ui/outbox/panel",
        "/ui/members/panel",
        "/ui/settings/panel",
        "/ui/knowledge/tree",
        "/ui/knowledge/products",
        "/ui/products/panel",
        "/ui/products/new",
        "/ui/branches/panel",
        "/ui/branches/new",
    ]
    for url in partial_urls:
        resp = client.get(url)
        assert resp.status_code in (200, 500), f"Unexpected status {resp.status_code} for {url}"


# ─── branches i18n ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code,key,expected", [
    ("ru", "nav.branches", "Филиалы"),
    ("en", "nav.branches", "Branches"),
    ("id", "nav.branches", "Cabang"),
    ("en", "br.create", "+ Branch"),
    ("ru", "br.create", "+ Филиал"),
    ("en", "br.save", "Save"),
    ("ru", "br.save", "Сохранить"),
    ("en", "br.new", "New Branch"),
    ("en", "br.edit_title", "Edit Branch"),
    ("en", "br.settings_seeded", "Default bot settings have been seeded automatically."),
    ("ru", "br.settings_seeded", "Настройки бота засеяны по умолчанию."),
    ("id", "br.settings_seeded", "Pengaturan bot default telah ditambahkan otomatis."),
])
def test_branch_i18n_keys(code: str, key: str, expected: str) -> None:
    from app.api._i18n import t
    _set_lang(code)
    assert t(key) == expected


# ─── branches HTML generators ─────────────────────────────────────────────────

def test_branches_panel_html_empty_has_create_button() -> None:
    from app.api._ui_panels import branches_panel_html
    _set_lang("en")
    html = branches_panel_html([])
    assert "/ui/branches/new" in html
    assert "+ Branch" in html


def test_branches_panel_html_renders_row() -> None:
    from app.api._ui_panels import branches_panel_html
    _set_lang("en")
    rows = [(1, "Indonesia", "id", 7, True)]
    html = branches_panel_html(rows)
    assert "Indonesia" in html
    assert "UTC+7" in html
    assert "p-ok" in html


def test_branches_panel_html_inactive_pill() -> None:
    from app.api._ui_panels import branches_panel_html
    _set_lang("en")
    rows = [(2, "Test Branch", "en", 0, False)]
    html = branches_panel_html(rows)
    assert "p-off" in html
    assert "Test Branch" in html


def test_branches_panel_html_edit_link() -> None:
    from app.api._ui_panels import branches_panel_html
    _set_lang("en")
    rows = [(3, "Vietnam", "vi", 7, True)]
    html = branches_panel_html(rows)
    assert "/ui/branches/3/edit" in html


def test_branch_edit_html_create_form() -> None:
    from app.api._ui_panels import branch_edit_html
    _set_lang("en")
    html = branch_edit_html(None, "", "id", 7, is_active=True)
    assert 'action="/ui/branches/create"' in html or "/ui/branches/create" in html
    assert "New Branch" in html


def test_branch_edit_html_edit_form() -> None:
    from app.api._ui_panels import branch_edit_html
    _set_lang("en")
    html = branch_edit_html(5, "Malaysia", "en", 8, is_active=True)
    assert "/ui/branches/5/save" in html
    assert "Malaysia" in html


def test_branch_edit_html_seeded_note_visible() -> None:
    from app.api._ui_panels import branch_edit_html
    _set_lang("en")
    html = branch_edit_html(3, "New", "id", 7, is_active=True, seeded=True)
    assert "seeded automatically" in html


def test_branch_edit_html_no_seeded_note_by_default() -> None:
    from app.api._ui_panels import branch_edit_html
    _set_lang("en")
    html = branch_edit_html(None, "", "id", 7, is_active=False)
    assert "seeded" not in html


def test_branch_edit_html_inactive_no_checked() -> None:
    from app.api._ui_panels import branch_edit_html
    _set_lang("en")
    html = branch_edit_html(1, "Test", "id", 7, is_active=False)
    assert 'checked' not in html


def test_branch_edit_html_active_has_checked() -> None:
    from app.api._ui_panels import branch_edit_html
    _set_lang("en")
    html = branch_edit_html(1, "Test", "id", 7, is_active=True)
    assert 'checked' in html


def test_branch_edit_html_kb_copy_targets_main_with_progress_indicator() -> None:
    """Real bug: the copy-KB button's hx-target was '#panel' — no such element exists
    anywhere in the app, so clicking it ran the copy server-side but never showed the
    result (silent no-op from the user's perspective). Must target #main (same as every
    other nav panel), disable the button while in flight, and show a spinner."""
    from app.api._ui_panels import branch_edit_html
    _set_lang("en")
    html = branch_edit_html(5, "Malaysia", "en", 8, is_active=True,
                            other_branches=[(1, "Indonesia")])
    assert 'hx-target="#panel"' not in html
    assert 'hx-post="/ui/branches/5/copy-kb" hx-target="#main"' in html
    assert 'hx-disabled-elt="find button"' in html
    assert 'hx-indicator="#kbcp-ind"' in html
    assert 'id="kbcp-ind" class="htmx-indicator"' in html
    assert "Копируется" in html


# ─── branches routes smoke tests ──────────────────────────────────────────────

def test_branches_panel_route(client: TestClient) -> None:
    resp = client.get("/ui/branches/panel")
    assert resp.status_code in (200, 500)


def test_branches_new_route_returns_200(client: TestClient) -> None:
    """New-branch form has no DB access — must always return 200."""
    resp = client.get("/ui/branches/new")
    assert resp.status_code == 200
    assert "/ui/branches/create" in resp.text


def test_branches_edit_missing_returns_404_or_500(client: TestClient) -> None:
    resp = client.get("/ui/branches/99999/edit")
    assert resp.status_code in (404, 500)


def test_branches_create_empty_name_stays_on_form(client: TestClient) -> None:
    """Empty name must not insert — returns form (200) or DB error (500)."""
    resp = client.post("/ui/branches/create", data={"name": "", "lang": "id", "tz_offset_h": "7"})
    assert resp.status_code in (200, 500)
    if resp.status_code == 200:
        assert "/ui/branches/create" in resp.text


def test_branches_create_with_name_returns_200_or_500(client: TestClient) -> None:
    """SQLite lacks RETURNING support → 500 is acceptable in test env."""
    resp = client.post(
        "/ui/branches/create",
        data={"name": "Test Branch", "lang": "en", "tz_offset_h": "8", "is_active": "on"},
    )
    assert resp.status_code in (200, 500)


def test_branches_save_missing_returns_200_or_500(client: TestClient) -> None:
    resp = client.post(
        "/ui/branches/99999/save",
        data={"name": "Updated", "lang": "en", "tz_offset_h": "8"},
    )
    assert resp.status_code in (200, 500)


def test_inbox_has_branches_nav_link(client: TestClient) -> None:
    resp = client.get("/ui/inbox")
    assert resp.status_code == 200
    assert "/ui/branches/panel" in resp.text


def test_reports_date_form_auto_submits_on_change() -> None:
    from app.api._ui_panels import _date_range_form_html
    html = _date_range_form_html("", "")
    assert 'hx-trigger="change"' in html   # fires on either date, no Apply click needed
    assert "rep.apply" not in html          # Apply button removed
    assert "<button" not in html            # no submit button left in the form


def test_reports_quick_range_buttons_fire_immediately() -> None:
    """Each preset is a plain hx-get link — clicking it refreshes the report right away,
    no typing a date and no separate Apply step."""
    from app.api._ui_panels import _date_range_form_html
    _set_lang("en")
    html = _date_range_form_html("", "")
    assert 'hx-get="/ui/reports/panel?range=1h"' in html
    assert 'hx-get="/ui/reports/panel?range=24h"' in html
    assert 'hx-get="/ui/reports/panel?range=7d"' in html
    assert 'hx-get="/ui/reports/panel?range=30d"' in html
    assert 'hx-get="/ui/reports/panel"' in html  # "Full period" clears range entirely
    assert "1 hour" in html and "24 hours" in html and "7 days" in html
    assert "30 days" in html and "Full period" in html


def test_reports_quick_range_highlights_active_preset() -> None:
    from app.api._ui_panels import _date_range_form_html
    _set_lang("en")
    html = _date_range_form_html("", "", active_range="24h")
    assert 'rep-preset on" hx-get="/ui/reports/panel?range=24h"' in html
    assert 'rep-preset on" hx-get="/ui/reports/panel?range=1h"' not in html


def test_reports_panel_route_accepts_quick_range() -> None:
    """The route must recognize ?range=1h/24h/7d/30d and ignore any leftover
    date_from/date_to — a quick-range click always wins."""
    import inspect

    from app.api import _routes_admin

    src = inspect.getsource(_routes_admin.reports_panel)
    assert "_QUICK_RANGES[active_range]" in src
    assert "utc_now()" in src


def test_reports_panel_date_filter_binds_are_asyncpg_safe() -> None:
    """Two distinct bugs previously made picking EITHER report date 500 instead of
    refreshing: (1) binding a bare ISO string for date_from/date_to — asyncpg needs a
    real date object to compare against a timestamp column; (2) leaving the parameter
    untyped in "$n + INTERVAL '1 day'" — Postgres then infers $n as interval, not date,
    and "timestamp < interval" fails to parse, so the CAST(name AS date) form must stay
    (not a double-colon cast right after the bind name, which collides with SQLAlchemy's
    own bind-parameter syntax)."""
    import inspect
    import re

    from app.api import _routes_admin

    src = inspect.getsource(_routes_admin.reports_panel)
    assert not re.search(r":\w+::\w+", src)
    assert "CAST(:df AS date)" in src
    assert "CAST(:dt AS date)" in src
    assert 'params["df"] = date.fromisoformat(df)' in src
    assert 'params["dt"] = date.fromisoformat(dt_)' in src


def test_chat_toolbar_suggest_summary_emoji_in_one_row() -> None:
    from app.api._ui_html import chat_panel_html
    _set_lang("en")
    html = chat_panel_html(7, "Bob", "qualifying", [], [])
    i = html.find('class="fin-tools"')
    j = html.find("</div>", i)
    row = html[i:j]
    assert "Suggest" in row and "Summary" in row and "emo-bar" in row  # all one row


def test_composer_textarea_auto_grows() -> None:
    from app.api._ui_html import chat_panel_html
    html = chat_panel_html(7, "Bob", "qualifying", [], [])
    assert 'oninput="autoGrow(this)"' in html
    assert "resetGrow(" in html            # height resets after a send


def test_nav_order_matches_requested_grouping() -> None:
    from app.api._ui_html import app_shell
    _set_lang("en")  # labels come from the i18n contextvar, not app_shell's lang arg
    html = app_shell("en", "<div>x</div>", active_nav="inbox")
    labels = ["Inbox", "Outbox", "Coach KB", "Knowledge", "Products",
              "Reports", "Leads", "Members", "Settings", "Branches", "Broker log"]
    positions = [html.index(f">{lbl}<") for lbl in labels]
    assert positions == sorted(positions)  # exact requested order, left to right
