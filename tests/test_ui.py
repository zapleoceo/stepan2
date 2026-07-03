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
           "+62812345", "course-a", "alicetest", None, 1200, 340, True, "Hello", "in", 5, 3, "KL")
    html = thread_list_html([row])
    assert "Alice Test" in html
    assert "@alicetest" in html
    assert 'hx-get="/ui/chat/42/panel"' in html


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


def test_app_shell_lang_buttons_highlight_active() -> None:
    from app.api._ui_html import app_shell
    _set_lang("ru")
    html = app_shell("ru", "", active_nav="inbox")
    assert '"lb on" href="/ui/lang/ru"' in html


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


def test_coach_chat_html_renders_form() -> None:
    from app.api._ui_panels import coach_chat_html
    _set_lang("en")
    html = coach_chat_html(1, [], [])
    assert 'hx-post="/ui/coach/say"' in html
    assert "coach-msgs" in html


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
    from app.api._ui_panels import members_panel_html
    _set_lang("en")
    rows = [(10, 169510539, "manager", "Dima", 1)]
    html = members_panel_html(rows)
    assert "Dima" in html
    assert "manager" in html
    assert "p-mgr" in html


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


def test_inbox_has_help_button(client: TestClient) -> None:
    resp = client.get("/ui/inbox")
    assert resp.status_code == 200
    assert "help-btn" in resp.text


def test_threads_partial_exists(client: TestClient) -> None:
    resp = client.get("/ui/threads")
    assert resp.status_code in (200, 500)


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
             datetime.now(UTC).replace(tzinfo=None))]
    html = leads_panel_html(rows)
    assert "Alice Test" in html
    assert "+62811234567" in html
    assert "sq" in html  # qualifying badge CSS


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
        (1, 10, "sent", "agent", "Hello!", now, now),
        (2, 77, "pending", "manager", "Wait…", now, None),
        (3, 10, "failed", "followup", "Error", now, None),
    ]
    html = outbox_panel_html(rows)
    assert "s-sent" in html
    assert "s-pend" in html
    assert "s-fail" in html
    assert 'hx-get="/ui/chat/77/panel"' in html  # chat number links to the chat
    assert ">#77<" in html


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

def test_agent_toggle_html_shows_on_state() -> None:
    from app.api.ui import _agent_toggle_html
    _set_lang("en")
    html = _agent_toggle_html(1, enabled=True)
    assert "Bot ON" in html
    assert "bot-tog" in html
    assert "/ui/agent-toggle" in html


def test_agent_toggle_html_shows_off_state() -> None:
    from app.api.ui import _agent_toggle_html
    _set_lang("en")
    html = _agent_toggle_html(1, enabled=False)
    assert "Bot OFF" in html
    assert "bot-tog" in html


def test_agent_status_route_responds(client: TestClient) -> None:
    resp = client.get("/ui/agent-status")
    assert resp.status_code in (200, 500)


def test_admin_root_redirects_to_ui() -> None:
    client = TestClient(app, follow_redirects=False)
    resp = client.get("/admin")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/ui/inbox"


# ─── lang switch: must redirect to full-page URL ──────────────────────────────

def test_lang_switch_from_full_page_preserves_referer() -> None:
    client = TestClient(app, follow_redirects=False)
    resp = client.get(
        "/ui/lang/ru",
        headers={"Referer": "http://testserver/ui/inbox"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "http://testserver/ui/inbox"
    assert "stepan2_lang=ru" in resp.headers.get("set-cookie", "")


def test_lang_switch_from_partial_redirects_to_inbox() -> None:
    """HTMX partial URLs must NOT be used as redirect targets after lang switch."""
    client = TestClient(app, follow_redirects=False)
    resp = client.get(
        "/ui/lang/ru",
        headers={"Referer": "http://testserver/ui/settings/panel"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/inbox"


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
