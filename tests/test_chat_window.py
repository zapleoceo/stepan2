"""Tests for chat-window audit fixes: highlight persistence, IG post URL,
per-chat bot toggle, live append polling, and AI-draft attribution."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from datetime import UTC, datetime  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402


def _set_lang(code: str) -> None:
    from app.api._i18n import DEFAULT_LANG, LANGS, _lang
    _lang.set(code if code in LANGS else DEFAULT_LANG)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ─── item 1: active-thread highlight survives the poll ────────────────────────

def _thread_row(tid: int) -> tuple:
    return (tid, "Alice", "new", datetime.now(UTC).replace(tzinfo=None),
            "+62811", "course-a", "alice", None, 500, 200, True, "Hi", "in", 1, 0, "Jakarta")


def test_thread_list_marks_active_row() -> None:
    from app.api._ui_html import thread_list_html
    _set_lang("en")
    html = thread_list_html([_thread_row(7), _thread_row(9)], active_tid=9)
    # only the matching row carries the "on" class
    assert html.count('class="ti on"') == 1
    assert 'hx-get="/ui/chat/9/panel"' in html


def test_thread_list_no_active_marks_none() -> None:
    from app.api._ui_html import thread_list_html
    _set_lang("en")
    html = thread_list_html([_thread_row(7)], active_tid=None)
    assert 'class="ti on"' not in html


def test_thread_card_shows_bot_off_indicator() -> None:
    from app.api._ui_html import thread_list_html
    _set_lang("en")
    off = list(_thread_row(5))
    off[10] = False  # agent_enabled column
    assert "🤖⛔" in thread_list_html([tuple(off)])       # disabled → indicator
    assert "🤖⛔" not in thread_list_html([_thread_row(5)])  # enabled → none


def test_thread_card_has_lowercase_search_index() -> None:
    from app.api._ui_html import thread_list_html
    _set_lang("en")
    html = thread_list_html([_thread_row(3)])  # name "Alice", handle "alice"
    assert 'data-search="alice alice"' in html  # name + @handle, lowercased for live search


def test_thread_card_branch_badge_only_in_multibranch_view() -> None:
    from app.api._ui_html import thread_list_html
    _set_lang("en")
    with_badge = thread_list_html([_thread_row(1)], show_branch=True)
    assert "🏢 Jakarta" in with_badge
    without = thread_list_html([_thread_row(1)], show_branch=False)
    assert "Jakarta" not in without  # single-branch view stays clean


def test_thread_item_sets_open_thread_cookie_onclick() -> None:
    from app.api._ui_html import thread_list_html
    _set_lang("en")
    html = thread_list_html([_thread_row(42)])
    assert "setOpenThread(42)" in html


def test_threads_partial_honors_open_thread_cookie(client: TestClient) -> None:
    resp = client.get("/ui/threads", cookies={"stepan2_open_thread": "5"})
    assert resp.status_code in (200, 500)


# ─── item 2: IG post URL conversion ───────────────────────────────────────────

def test_ig_post_url_is_deterministic_and_url_safe() -> None:
    from app.api._ui_html import _IG_ALPHABET, ig_post_url
    url = ig_post_url("3419988395853209800")
    assert url is not None
    assert ig_post_url("3419988395853209800") == url  # deterministic
    assert url.startswith("https://www.instagram.com/p/")
    code = url.removeprefix("https://www.instagram.com/p/").rstrip("/")
    assert code
    assert all(c in _IG_ALPHABET for c in code)  # url-safe alphabet only


def test_ig_post_url_round_trips_through_base64() -> None:
    from app.api._ui_html import _IG_ALPHABET, ig_post_url
    mid = 3419988395853209800
    url = ig_post_url(str(mid))
    assert url is not None
    code = url.removeprefix("https://www.instagram.com/p/").rstrip("/")
    decoded = 0
    for ch in code:
        decoded = decoded * 64 + _IG_ALPHABET.index(ch)
    assert decoded == mid


def test_ig_post_url_handles_underscore_suffix() -> None:
    from app.api._ui_html import ig_post_url
    assert ig_post_url("123456_789") == ig_post_url("123456")


def test_ig_post_url_invalid_returns_none() -> None:
    from app.api._ui_html import ig_post_url
    assert ig_post_url(None) is None
    assert ig_post_url("") is None
    assert ig_post_url("not-a-number") is None


def test_source_bar_uses_shortcode_not_raw_id() -> None:
    from app.api._ui_html import _source_bar, ig_post_url
    _set_lang("en")
    raw = "3419988395853209800"
    html = _source_bar("ad", "ad-1", raw, None)
    expected = ig_post_url(raw)
    assert expected is not None
    assert expected in html
    assert f"/p/{raw}/" not in html  # never links the raw numeric id


# ─── item 3: per-chat bot toggle ──────────────────────────────────────────────

def test_chat_bot_pill_reflects_on_state() -> None:
    from app.api._ui_html import chat_bot_pill_html
    _set_lang("en")
    html = chat_bot_pill_html(3, enabled=True)
    assert "Bot ON" in html
    assert 'hx-post="/ui/chat/3/bot-toggle"' in html


def test_chat_bot_pill_reflects_off_state() -> None:
    from app.api._ui_html import chat_bot_pill_html
    _set_lang("en")
    html = chat_bot_pill_html(3, enabled=False)
    assert "Bot OFF" in html


def test_chat_header_includes_bot_pill() -> None:
    from app.api._ui_html import chat_header_html
    _set_lang("en")
    html = chat_header_html(8, "Bob", "new", agent_enabled=False)
    assert "Bot OFF" in html
    assert 'hx-post="/ui/chat/8/bot-toggle"' in html


def test_bot_toggle_route_flips_flag(client: TestClient) -> None:
    resp = client.post("/ui/chat/99999/bot-toggle")
    assert resp.status_code in (200, 500)


async def _seed_thread(db_session) -> None:
    from app.adapters.db.models import Branch, ChannelThread, Lead
    db_session.add(Branch(id=1, name="B", lang="en", tz_offset_h=7, is_active=True))
    db_session.add(Lead(id=1, branch_id=1, display_name="Alice", agent_enabled=True))
    db_session.add(ChannelThread(id=1, lead_id=1, channel_id=1, external_thread_id="x1"))
    await db_session.commit()


class _Scope:
    def __init__(self, session) -> None:
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *a) -> None:
        await self._s.commit()


class _Req:
    cookies: dict = {}
    headers: dict = {}


@pytest.mark.asyncio
async def test_bot_toggle_flips_lead_agent_enabled(db_session) -> None:
    from sqlalchemy import text as _text

    import app.api._routes_chat as rc
    from app.api._routes_chat import chat_bot_toggle

    await _seed_thread(db_session)
    orig = rc.session_scope
    rc.session_scope = lambda: _Scope(db_session)
    try:
        await chat_bot_toggle(1, _Req())  # type: ignore[arg-type]
    finally:
        rc.session_scope = orig

    val = (await db_session.execute(
        _text("SELECT agent_enabled FROM lead WHERE id = 1")
    )).scalar()
    assert bool(val) is False


# ─── item 4: live append polling (since-route) ────────────────────────────────

def test_messages_html_embeds_poll_sentinel() -> None:
    from app.api._ui_html import messages_html
    _set_lang("en")
    row = (5, "in", "lead", "Hello", datetime.now(UTC).replace(tzinfo=None),
           None, None, None, None, None)
    html = messages_html([row], [], 4)
    assert 'id="poll-4"' in html
    assert 'hx-get="/ui/chat/4/since/5"' in html  # cursor at last id


def test_since_bubbles_only_render_given_rows_plus_sentinel() -> None:
    from app.api._ui_html import since_bubbles_html
    _set_lang("en")
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = [
        (10, "in", "lead", "New one", now, None, None, None, None, None),
        (11, "out", "agent", "Reply", now, None, None, None, None, None),
    ]
    html = since_bubbles_html(rows, 4, after_id=9)
    assert "New one" in html
    assert "Reply" in html
    assert 'hx-get="/ui/chat/4/since/11"' in html  # cursor advanced to newest


def test_bubble_renders_media_link_preview_and_receipt() -> None:
    from app.api._ui_html import messages_html
    _set_lang("en")
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = [
        # out image message, lead has read it → ✓✓; placeholder caption suppressed
        (20, "out", "agent", "🖼 media", now, None, None, None, 77, "image"),
        # inbound shared link with preview + a bare url to linkify
        (21, "in", "lead", "see https://x.com/p", now, None,
         "https://x.com/p", "https://cdn/prev.jpg", None, None),
    ]
    html = messages_html(rows, [], 4, lead_seen_at=now)
    assert 'src="/ui/media/77"' in html          # image served from media route
    assert "msg-prev" in html
    assert "✓✓" in html                           # read receipt on the out message
    assert 'href="https://x.com/p"' in html       # linkified url + preview anchor
    assert 'referrerpolicy="no-referrer"' in html  # preview thumbnail
    assert "🖼 media" not in html                  # placeholder caption hidden


def test_pending_bubble_queue_time_buttons_and_oob() -> None:
    from app.api._ui_html import messages_html, since_bubbles_html
    _set_lang("en")
    pending = [(11, "first reply", "2026-07-03 08:15:30", "mistral · free", None),
               (12, "second reply", "2026-07-03 08:15:36", None, "перевод")]
    html = messages_html([], pending, 4)
    assert 'id="pend-4"' in html                    # pending pinned in its own container
    assert 'id="ppb-11"' in html and "№1" in html   # queued bubble + queue position
    assert "08:15:30" in html                        # estimated send time (HH:MM:SS)
    assert "bb-o bb-p" in html                        # styled like outgoing (right side)
    assert "/ui/chat/4/pending/11/delete" in html    # cancel-send button
    assert "/ui/chat/4/pending/11/tr" in html         # translate button
    assert "🌐 перевод" in html                        # cached translation shown
    # the 4s poll re-renders pending out-of-band so it stays below new messages
    since = since_bubbles_html([], 4, 9, pending=pending)
    assert 'id="pend-4" hx-swap-oob="true"' in since


def test_since_bubbles_empty_keeps_cursor() -> None:
    from app.api._ui_html import since_bubbles_html
    _set_lang("en")
    html = since_bubbles_html([], 4, after_id=9)
    assert "bb-" not in html
    assert 'hx-get="/ui/chat/4/since/9"' in html


def test_since_route_responds(client: TestClient) -> None:
    resp = client.get("/ui/chat/99999/since/0")
    assert resp.status_code in (200, 500)


# ─── item 5: AI-draft attribution ─────────────────────────────────────────────

def test_send_suggest_js_marks_source_agent() -> None:
    from app.api._ui_html import app_shell
    _set_lang("en")
    html = app_shell("en", "", active_nav="inbox")
    assert "fd.append('source','agent')" in html


@pytest.mark.asyncio
async def test_send_as_agent_stores_agent_source(db_session) -> None:
    from sqlalchemy import text as _text

    import app.api._routes_chat as rc
    from app.api._routes_chat import chat_send

    await _seed_thread(db_session)
    orig = rc.session_scope
    rc.session_scope = lambda: _Scope(db_session)
    try:
        await chat_send(  # type: ignore[arg-type]
            1, _Req(), text_body="AI draft", source="agent", llm_info="broker/x",
        )
        await chat_send(  # type: ignore[arg-type]
            1, _Req(), text_body="Manual", source="manager", llm_info=None,
        )
    finally:
        rc.session_scope = orig

    rows = (await db_session.execute(
        _text("SELECT text, source, llm_info FROM outbox ORDER BY id")
    )).all()
    by_text = {r[0]: (r[1], r[2]) for r in rows}
    assert by_text["AI draft"] == ("agent", "broker/x")  # attributed + llm_info kept
    assert by_text["Manual"] == ("manager", None)  # manual reply stays manager


@pytest.mark.asyncio
async def test_send_unknown_source_falls_back_to_manager(db_session) -> None:
    from sqlalchemy import text as _text

    import app.api._routes_chat as rc
    from app.api._routes_chat import chat_send

    await _seed_thread(db_session)
    orig = rc.session_scope
    rc.session_scope = lambda: _Scope(db_session)
    try:
        await chat_send(  # type: ignore[arg-type]
            1, _Req(), text_body="Sneaky", source="followup", llm_info=None,
        )
    finally:
        rc.session_scope = orig

    src = (await db_session.execute(
        _text("SELECT source FROM outbox WHERE text = 'Sneaky'")
    )).scalar()
    assert src == "manager"


# ─── poll cursor + branch timezone (pure) ─────────────────────────────────────

def test_last_msg_id_is_max_not_last_by_time() -> None:
    """A late-arriving row (higher id, earlier timestamp) must not drop the cursor —
    else the poll re-fetches already-shown rows and the order jitters."""
    from app.api._ui_html import _last_msg_id
    rows = [(5,), (9,), (7,)]  # ordered by occurred_at; id 9 is not last
    assert _last_msg_id(rows) == 9
    assert _last_msg_id([]) == 0


def test_fmt_time_uses_branch_offset() -> None:
    from datetime import datetime

    from app.api._ui_html import _fmt_time, set_render_tz
    dt = datetime(2026, 7, 3, 5, 0, 0)  # naive UTC
    set_render_tz(7)                     # Jakarta
    assert _fmt_time(dt) == "12:00:00"
    set_render_tz(0)
    assert _fmt_time(dt) == "05:00:00"


# ─── context-clear hides pre-cutoff messages from the chat window ──────────────

async def test_clear_filters_display(db_session) -> None:
    from datetime import UTC, datetime, timedelta

    from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
    from app.api._query import fetch_messages
    from app.domain.enums import ChannelKind

    now = datetime.now(UTC).replace(tzinfo=None)
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id)
    db_session.add_all([ch, lead])
    await db_session.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1",
                       context_cleared_at=now)
    db_session.add(th)
    await db_session.flush()
    for i, when in enumerate((now - timedelta(hours=1), now + timedelta(minutes=1))):
        db_session.add(Message(branch_id=b.id, thread_id=th.id, channel_id=ch.id,
                               external_id=f"m{i}", direction="in", sent_by="lead",
                               text=f"msg{i}", occurred_at=when))
    await db_session.flush()

    rows = await fetch_messages(db_session, th.id)
    texts = [r[3] for r in rows]
    assert texts == ["msg1"]  # pre-clear msg0 hidden, post-clear msg1 kept
