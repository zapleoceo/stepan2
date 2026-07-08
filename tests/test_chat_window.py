"""Tests for chat-window audit fixes: highlight persistence, IG post URL,
per-chat bot toggle, live append polling, and AI-draft attribution."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from datetime import UTC, datetime, timedelta  # noqa: E402

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
            "+62811", "course-a", "alice", None, 500, 200, True, "Hi", "in", 1, 0, "Jakarta", 0)


def test_thread_list_shows_exact_datetime_not_relative_ago() -> None:
    """Sidebar previously showed a vague '2h ago' style label — must show the explicit
    last-message date+time instead so it's never ambiguous."""
    from app.api._ui_html import set_render_tz, thread_list_html
    _set_lang("en")
    set_render_tz(0)
    row = list(_thread_row(1))
    row[3] = datetime(2026, 7, 3, 14, 5, 0)
    html = thread_list_html([tuple(row)])
    assert "03.07 14:05" in html
    assert "ago" not in html.lower()


def test_thread_list_marks_active_row() -> None:
    from app.api._ui_html import thread_list_html
    _set_lang("en")
    html = thread_list_html([_thread_row(7), _thread_row(9)], active_tid=9)
    # only the matching row carries the "on" class
    assert html.count('class="ti on"') == 1
    assert 'hx-get="/ui/chat/9"' in html  # canonical shareable URL, not the /panel partial


def test_thread_list_no_active_marks_none() -> None:
    from app.api._ui_html import thread_list_html
    _set_lang("en")
    html = thread_list_html([_thread_row(7)], active_tid=None)
    assert 'class="ti on"' not in html


def test_thread_row_preserves_active_filter_in_chat_url() -> None:
    """Opening a chat from a filtered inbox must push a URL that keeps the filter, so a full
    reload rebuilds the same filtered list instead of the whole inbox."""
    from app.api._ui_html import thread_list_html
    _set_lang("en")
    html = thread_list_html([_thread_row(7)], filter_qs="stage=ready")
    assert 'hx-push-url="/ui/chat/7?stage=ready"' in html
    assert 'href="/ui/inbox?stage=ready"' in html
    plain = thread_list_html([_thread_row(7)])            # no filter → plain chat url
    assert 'hx-push-url="/ui/chat/7"' in plain


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


# ─── bubble translate button: only when there is text to translate ────────────

def _msg_row(mid: int, text: str, media_id=None) -> tuple:
    # (id, direction, sent_by, text, ts, llm_info, link_url, preview_url, media_id, media_kind)
    return (mid, "in", "lead", text, datetime.now(UTC).replace(tzinfo=None),
            None, None, None, media_id, "image" if media_id else None)


def test_bubble_shows_translate_button_for_text() -> None:
    from app.api._ui_html import _bubble
    _set_lang("en")
    html = _bubble(_msg_row(1, "halo kak apa kabar"), 10)
    assert "trMsg(1,10)" in html  # 🌐 translate button present on a text bubble


def test_bubble_hides_translate_button_when_no_caption() -> None:
    """A media-only bubble has no bt-{mid} text node, so the 🌐 button would no-op — it must
    not be rendered at all (root cause of 'bubble translate sometimes does nothing')."""
    from app.api._ui_html import _MEDIA_PH, _bubble
    _set_lang("en")
    placeholder = next(iter(_MEDIA_PH))
    html = _bubble(_msg_row(2, placeholder, media_id=99), 10)
    assert "trMsg(2,10)" not in html  # no translate button on a caption-less media bubble


# ─── funnel filter → shareable /ui/inbox?stage=X URL ──────────────────────────

def test_funnel_html_highlights_active_and_pushes_inbox_url() -> None:
    from app.api._ui_html import funnel_html
    _set_lang("en")
    html = funnel_html({"dormant": 5, "new": 2, "qualifying": 3}, active_stage="dormant",
                       bot_on=7)
    assert 'hx-push-url="/ui/inbox?stage=dormant"' in html  # shareable full-page URL
    assert 'hx-get="/ui/threads?stage=dormant"' in html     # fast partial swap into #tl
    # the dormant step is the active one
    i = html.find("stage=dormant")
    seg = html[max(0, i - 140):i]
    assert "fstep on" in seg
    # summary line: bot-on count + in-funnel count (new+qualifying = 5, dormant excluded)
    assert "bot on" in html and ">7<" in html
    assert "in funnel" in html and ">5<" in html


def test_funnel_html_is_one_line_with_icons_and_hover_labels() -> None:
    from app.api._ui_html import funnel_html
    _set_lang("en")
    html = funnel_html({"new": 2}, bot_on=1)
    assert 'class="fnl"' in html and "fstep" in html
    assert 'title="new"' in html            # full stage label on hover
    assert "🔍" in html or "✨" in html      # stage icons present
    assert "fpill" not in html              # old wrap-of-chips design gone


def test_funnel_html_has_two_rows_metrics_then_stages() -> None:
    """Row 1 = total/bot-on/in-funnel headline metrics; row 2 = the per-stage chips."""
    from app.api._ui_html import funnel_html
    _set_lang("en")
    html = funnel_html({"new": 2}, bot_on=1)
    assert html.count('<div class="fnl">') == 2
    row1_end = html.index("</div>")
    row1 = html[: row1_end]
    assert "📥" in row1 and "🤖" in row1 and "🎯" in row1
    assert "🔍" not in row1 and "✨" not in row1  # stage chips are NOT in row 1


def test_bot_on_and_in_funnel_metrics_are_not_clickable() -> None:
    """They're aggregates, not a single stage — clicking them would have no filter target."""
    from app.api._ui_html import funnel_html
    _set_lang("en")
    html = funnel_html({"new": 2}, bot_on=1)
    i = html.find("bot on")
    seg = html[max(0, i - 60): i + 10]
    assert 'class="fstep info"' in seg
    assert "hx-get" not in seg


def test_funnel_row2_has_a_clickable_blocked_chip() -> None:
    """is_blocked is a lead flag, not a funnel stage — without this chip a blocked lead was
    completely unfindable anywhere in the UI."""
    from app.api._ui_html import funnel_html
    _set_lang("en")
    html = funnel_html({"new": 2}, blocked=3)
    assert 'hx-get="/ui/threads?stage=blocked"' in html
    assert 'hx-push-url="/ui/inbox?stage=blocked"' in html
    assert "🚫" in html
    assert ">3<" in html


# ─── manual product change: header <select> + history line ────────────────────

def test_chat_header_renders_product_select_when_products_given() -> None:
    from app.api._ui_html import chat_header_html
    _set_lang("en")
    html = chat_header_html(7, "Bob", "new", product_slug="vibe",
                            products=[("vibe", "Vibe Coding"), ("py", "Python")])
    assert 'hx-post="/ui/chat/7/product"' in html
    assert 'name="product"' in html
    assert '<option value="vibe" selected>Vibe Coding</option>' in html
    assert '<option value="">' in html  # the "no product" option


def test_needs_block_renders_the_already_translated_profile() -> None:
    """chat_header_html receives a pre-translated NeedsProfile (the route does the async
    translation) and just displays it — no parsing/translation happens at render time."""
    from app.api._ui_html import chat_header_html
    from app.modules.conversation.needs import NeedsProfile
    _set_lang("en")
    profile = NeedsProfile(jobs=["learn coding"], pains=["afraid to fail"], gains=["stable job"])
    html = chat_header_html(7, "Bob", "new", needs=profile)
    assert "learn coding" in html and "afraid to fail" in html and "stable job" in html
    assert "🎯" in html and "⚠️" in html and "✨" in html


def test_needs_block_empty_when_nothing_captured() -> None:
    from app.api._ui_html import chat_header_html
    _set_lang("en")
    assert "nd-box" not in chat_header_html(7, "Bob", "new", needs=None)


def test_event_bubble_shows_product_change_detail() -> None:
    from datetime import UTC, datetime

    from app.api._ui_html import _event_bubble
    _set_lang("en")
    row = (1, "log", "product_changed", "vibe → py", "Dima",
           datetime.now(UTC).replace(tzinfo=None))
    html = _event_bubble(row)
    assert "Product changed" in html
    assert "vibe → py" in html  # the old→new detail is shown


# ─── stage-event display anchoring (decision written before the reply is sent) ──

def test_stage_event_anchors_to_the_reply_it_caused() -> None:
    """A needs_manager/stage-change row is written the instant the decision is made —
    before the humanize-delayed reply that triggered it actually sends. The system line
    must display AFTER that reply, not before it, or the transcript reads backwards."""
    from app.api._ui_html import _anchor_event_ts

    decided_at = datetime(2026, 7, 7, 11, 25, 44)
    sent_at = datetime(2026, 7, 7, 11, 26, 33)  # ~49s later, after the humanize delay
    assert _anchor_event_ts(decided_at, [sent_at]) == sent_at


def test_stage_event_keeps_own_timestamp_beyond_the_anchor_window() -> None:
    from app.api._ui_html import _anchor_event_ts

    decided_at = datetime(2026, 7, 7, 11, 25, 44)
    far_reply = decided_at + timedelta(minutes=10)  # e.g. a soft-block retry, way later
    assert _anchor_event_ts(decided_at, [far_reply]) == decided_at


def test_stage_event_keeps_own_timestamp_with_no_following_reply() -> None:
    """A manual stage change (no bot reply attached) shows at its own time."""
    from app.api._ui_html import _anchor_event_ts

    decided_at = datetime(2026, 7, 7, 11, 25, 44)
    earlier_reply = decided_at - timedelta(minutes=1)
    assert _anchor_event_ts(decided_at, [earlier_reply]) == decided_at


def test_merge_feed_orders_stage_line_after_its_reply() -> None:
    from app.api._ui_html import _merge_feed

    decided_at = datetime(2026, 7, 7, 11, 25, 44)
    sent_at = datetime(2026, 7, 7, 11, 26, 33)
    msg = (24376, "out", "agent", "Oke Kak, jadwalnya...", sent_at, None,
           None, None, None, None, False)
    event = (5575, "stage", "manager", "presenting", "bot", decided_at)
    html = _merge_feed([msg], [event], tid=1, lead_seen_at=None)
    assert html.index("Oke Kak") < html.index("sys-log")  # reply bubble renders first


# ─── manager alert deep-link ──────────────────────────────────────────────────

def test_alert_body_includes_header_deep_link_and_lang_blocks() -> None:
    from app.modules.notifications.alerts import AlertService
    svc = AlertService(None, 1, None)  # _compose is pure — no session/notifier touched
    body = svc._compose(1732, "Budi", "id", "ringkasan", "alasan", "сводка", "причина")
    assert "чат #1732" in body and "Budi" in body       # header line
    assert "Bahasa:" in body and "Ru:" in body          # per-language summary labels
    assert "/ui/chat/1732" in body and "open chat" in body
    # branch-language block precedes the Russian one
    assert body.index("alasan") < body.index("причина")


def test_manual_stage_alert_only_for_ready_and_manager() -> None:
    from app.api._routes_chat import _MANUAL_ALERT_KIND
    assert _MANUAL_ALERT_KIND == {"ready": "ready_deal", "manager": "needs_manager"}


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
    assert "ON" in html and "OFF" in html          # both segments of the toggle
    assert 'class="bot-tog on"' in html            # knob on the ON side
    assert 'hx-post="/ui/chat/3/bot-toggle"' in html


def test_chat_bot_pill_reflects_off_state() -> None:
    from app.api._ui_html import chat_bot_pill_html
    _set_lang("en")
    html = chat_bot_pill_html(3, enabled=False)
    assert 'class="bot-tog off"' in html            # knob on the OFF side


def test_chat_header_includes_bot_pill() -> None:
    from app.api._ui_html import chat_header_html
    _set_lang("en")
    html = chat_header_html(8, "Bob", "new", agent_enabled=False)
    assert "OFF" in html
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


@pytest.mark.asyncio
async def test_manager_note_saves_and_appears_in_header(db_session) -> None:
    """Per-lead override note (2026-07-08): a manager writes it once via this route, and
    it must both persist on the lead row AND render back in the header HTML — the reply
    pipeline reads it straight off lead.manager_note (see prompt.manager_note_block)."""
    from sqlalchemy import text as _text

    import app.api._routes_chat as rc
    from app.api._routes_chat import chat_manager_note

    await _seed_thread(db_session)
    orig = rc.session_scope
    rc.session_scope = lambda: _Scope(db_session)
    try:
        html = await chat_manager_note(1, _Req(), note="not ready yet — needs budget")  # type: ignore[arg-type]
    finally:
        rc.session_scope = orig

    stored = (await db_session.execute(
        _text("SELECT manager_note FROM lead WHERE id = 1")
    )).scalar()
    assert stored == "not ready yet — needs budget"
    assert "not ready yet" in html.body.decode()


@pytest.mark.asyncio
async def test_manager_note_blank_clears_it(db_session) -> None:
    from sqlalchemy import text as _text

    import app.api._routes_chat as rc
    from app.api._routes_chat import chat_manager_note

    await _seed_thread(db_session)
    await db_session.execute(
        _text("UPDATE lead SET manager_note = 'old note' WHERE id = 1"))
    await db_session.commit()
    orig = rc.session_scope
    rc.session_scope = lambda: _Scope(db_session)
    try:
        await chat_manager_note(1, _Req(), note="   ")  # type: ignore[arg-type]
    finally:
        rc.session_scope = orig

    stored = (await db_session.execute(
        _text("SELECT manager_note FROM lead WHERE id = 1")
    )).scalar()
    assert stored is None


@pytest.mark.asyncio
async def test_manager_note_updates_log_chronology_not_just_overwrite(db_session) -> None:
    """The lead row only holds the CURRENT note (each save overwrites it) — chronology
    across updates must survive in ThreadLog, same mechanism as context-clear/product-change
    log lines, so a manager can see the history of notes left on this lead over time."""
    from sqlalchemy import text as _text

    import app.api._routes_chat as rc
    from app.api._routes_chat import chat_manager_note

    await _seed_thread(db_session)
    orig = rc.session_scope
    rc.session_scope = lambda: _Scope(db_session)
    try:
        await chat_manager_note(1, _Req(), note="first note")  # type: ignore[arg-type]
        await chat_manager_note(1, _Req(), note="updated note")  # type: ignore[arg-type]
        await chat_manager_note(1, _Req(), note="")  # type: ignore[arg-type]
    finally:
        rc.session_scope = orig

    rows = (await db_session.execute(
        _text("SELECT kind, detail FROM thread_log WHERE thread_id = 1 ORDER BY id")
    )).all()
    assert [r[0] for r in rows] == [
        "manager_note_set", "manager_note_set", "manager_note_cleared"]
    assert rows[0][1] == "first note"
    assert rows[1][1] == "updated note"
    assert rows[2][1] is None
    # the lead row itself only reflects the LATEST state (cleared) — history lives in the log
    current = (await db_session.execute(
        _text("SELECT manager_note FROM lead WHERE id = 1")
    )).scalar()
    assert current is None


# ─── captured-needs auto-translate on chat panel load (cached) ────────────────

class _CountingTranslateLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        import json as _json
        self.calls += 1
        user_msg = messages[-1]["content"]
        lines = [ln.split(". ", 1)[1] for ln in user_msg.splitlines() if ln.strip()]
        return _json.dumps([f"RU:{ln}" for ln in lines]), {"model": "fake", "cost_usd": 0.0}

    async def embed(self, texts):  # noqa: ANN001, ANN201
        return [[0.0] for _ in texts]


@pytest.mark.asyncio
async def test_needs_panel_load_never_calls_broker(db_session) -> None:
    """Loading the chat panel must render instantly from cache (untranslated fallback text
    is fine) — it must NEVER block on a broker call. The panel instead ships an hx-get so
    the browser lazily fetches the real translation after the page is already visible."""
    import app.api._routes_chat as rc
    from app.adapters.db.models import Branch, ChannelThread, Lead

    b = Branch(id=1, name="B", lang="id", tz_offset_h=7, is_active=True)
    lead = Lead(id=1, branch_id=1, display_name="Alice", agent_enabled=True,
                needs='{"jobs":["belajar coding"],"pains":[],"gains":[],'
                     '"discovery_complete":false}')
    db_session.add_all([b, lead, ChannelThread(id=1, lead_id=1, channel_id=1,
                                                external_thread_id="x1")])
    await db_session.commit()

    orig_scope, orig_llm = rc.session_scope, rc.BrokerLLM
    llm = _CountingTranslateLLM()
    rc.session_scope = lambda: _Scope(db_session)
    rc.BrokerLLM = lambda: llm
    _set_lang("ru")
    try:
        html = await rc._build_chat_panel(db_session, 1, None)
        assert llm.calls == 0  # panel render must never touch the broker
        assert "belajar coding" in html  # untranslated fallback shown immediately
        assert 'hx-get="/ui/chat/1/needs"' in html  # lazy-load hook for the real translation
        assert 'hx-trigger="load"' in html
    finally:
        rc.session_scope, rc.BrokerLLM = orig_scope, orig_llm


async def test_needs_lazy_endpoint_translates_and_caches(db_session) -> None:
    """The lazy /needs route is where the actual broker call happens — first hit translates
    and caches on lead.needs_tr, a second hit is a pure cache read (no re-bill)."""
    from sqlalchemy import text as _text

    import app.api._routes_chat as rc
    from app.adapters.db.models import Branch, ChannelThread, Lead

    b = Branch(id=1, name="B", lang="id", tz_offset_h=7, is_active=True)
    lead = Lead(id=1, branch_id=1, display_name="Alice", agent_enabled=True,
                needs='{"jobs":["belajar coding"],"pains":[],"gains":[],'
                     '"discovery_complete":false}')
    db_session.add_all([b, lead, ChannelThread(id=1, lead_id=1, channel_id=1,
                                                external_thread_id="x1")])
    await db_session.commit()

    orig_scope, orig_llm = rc.session_scope, rc.BrokerLLM
    llm = _CountingTranslateLLM()
    rc.session_scope = lambda: _Scope(db_session)
    rc.BrokerLLM = lambda: llm
    req = _Req()
    req.cookies = {"stepan2_lang": "ru"}  # chat_needs_lazy reads lang from the request, not
    # the ContextVar set by _set_lang, since it (unlike _build_chat_panel) calls apply_lang
    try:
        html1 = await rc.chat_needs_lazy(1, req)
        assert "RU:belajar coding" in html1.body.decode()
        assert llm.calls == 1

        html2 = await rc.chat_needs_lazy(1, req)
        assert "RU:belajar coding" in html2.body.decode()
        assert llm.calls == 1  # cache hit — no second broker call
    finally:
        rc.session_scope, rc.BrokerLLM = orig_scope, orig_llm

    cached = (await db_session.execute(
        _text("SELECT needs_tr FROM lead WHERE id = 1"))).scalar()
    assert cached and "belajar coding" in cached and "RU:belajar coding" in cached


# ─── item 4: live append polling (since-route) ────────────────────────────────

def test_messages_html_embeds_poll_sentinel() -> None:
    from app.api._ui_html import messages_html
    _set_lang("en")
    row = (5, "in", "lead", "Hello", datetime.now(UTC).replace(tzinfo=None),
           None, None, None, None, None)
    html = messages_html([row], [], 4)
    assert 'id="poll-4"' in html
    assert 'hx-get="/ui/chat/4/since/5/0/0"' in html  # cursor at last id (msg/stage/log)


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
    assert 'hx-get="/ui/chat/4/since/11/0/0"' in html  # cursor advanced to newest


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
    assert 'hx-get="/ui/chat/4/since/9/0/0"' in html


def test_since_route_responds(client: TestClient) -> None:
    resp = client.get("/ui/chat/99999/since/0/0/0")
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
    assert _fmt_time(dt) == "03.07 12:00:00"
    set_render_tz(0)
    assert _fmt_time(dt) == "03.07 05:00:00"


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
    shown = [(r[3], bool(r[10])) for r in rows]  # (text, excluded)
    # both stay visible; the pre-clear one is greyed (excluded), the post-clear one is live
    assert shown == [("msg0", True), ("msg1", False)]


async def test_clear_boundary_matches_llm_dialog_cutoff(db_session) -> None:
    """A message timestamped exactly at context_cleared_at is marked excluded (greyed) in
    the chat window the same way MessageRepo.dialog() drops it from the LLM prompt — so the
    manager's greyed view and the bot's context agree on the boundary."""
    from datetime import UTC, datetime

    from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
    from app.api._query import fetch_messages
    from app.domain.enums import ChannelKind
    from app.modules.conversation.repository import MessageRepo

    cutoff = datetime.now(UTC).replace(tzinfo=None)
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id)
    db_session.add_all([ch, lead])
    await db_session.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-2",
                       context_cleared_at=cutoff)
    db_session.add(th)
    await db_session.flush()
    db_session.add(Message(branch_id=b.id, thread_id=th.id, channel_id=ch.id,
                           external_id="m-boundary", direction="in", sent_by="lead",
                           text="on-the-tick", occurred_at=cutoff))
    await db_session.flush()

    rows = await fetch_messages(db_session, th.id)
    dialog = await MessageRepo(db_session, b.id).dialog(th.id, since=cutoff)
    assert len(rows) == 1 and bool(rows[0][10]) is True  # shown but greyed (excluded)
    assert dialog == []  # … and dropped from the LLM prompt, same boundary


# ─── item 5: technical/system log lines in the chat window ────────────────────

async def _log_world(db_session):
    from app.adapters.db.models import Branch, Channel, ChannelThread, Lead
    from app.domain.enums import ChannelKind

    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id)
    db_session.add_all([ch, lead])
    await db_session.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-log")
    db_session.add(th)
    await db_session.flush()
    return b.id, th.id


async def test_fetch_thread_events_merges_stage_and_log_by_time(db_session) -> None:
    from datetime import UTC, datetime, timedelta

    from app.adapters.db.models import StageEvent, ThreadLog
    from app.api._query import fetch_thread_events

    bid, tid = await _log_world(db_session)
    t0 = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(StageEvent(branch_id=bid, lead_id=1, thread_id=tid,
                              from_stage="new", to_stage="qualifying",
                              actor="bot", created_at=t0))
    db_session.add(ThreadLog(branch_id=bid, thread_id=tid, kind="context_cleared",
                             actor="Dima", created_at=t0 + timedelta(seconds=5)))
    await db_session.flush()

    rows = await fetch_thread_events(db_session, tid)
    assert [r[1] for r in rows] == ["stage", "log"]  # time-ordered: stage first, then log


async def test_fetch_thread_events_cursor_excludes_seen_rows(db_session) -> None:
    from app.adapters.db.models import StageEvent, ThreadLog
    from app.api._query import fetch_thread_events

    bid, tid = await _log_world(db_session)
    db_session.add(StageEvent(branch_id=bid, lead_id=1, thread_id=tid,
                              from_stage="new", to_stage="qualifying", actor="bot"))
    db_session.add(ThreadLog(branch_id=bid, thread_id=tid, kind="context_cleared",
                             actor="Dima"))
    await db_session.flush()

    rows = await fetch_thread_events(db_session, tid)
    stage_id = next(r[0] for r in rows if r[1] == "stage")
    log_id = next(r[0] for r in rows if r[1] == "log")

    fresh = await fetch_thread_events(db_session, tid, after_stage_id=stage_id,
                                      after_log_id=log_id)
    assert fresh == []  # both already-seen rows excluded by their own cursor


def test_event_bubble_renders_stage_change_and_context_clear() -> None:
    from datetime import UTC, datetime

    from app.api._ui_html import _event_bubble

    _set_lang("en")
    now = datetime.now(UTC).replace(tzinfo=None)
    stage_row = (1, "stage", "qualifying", "new", "bot", now)
    log_row = (2, "log", "context_cleared", None, "Dima", now)
    assert "Stage: new → qualifying" in _event_bubble(stage_row)
    assert "Context cleared" in _event_bubble(log_row)
    assert "Dima" in _event_bubble(log_row)


def test_merge_feed_interleaves_messages_and_events_by_time() -> None:
    from datetime import UTC, datetime, timedelta

    from app.api._ui_html import _merge_feed

    _set_lang("en")
    t0 = datetime.now(UTC).replace(tzinfo=None)
    msg = (5, "in", "lead", "hi", t0, None, None, None, None, None)
    evt = (1, "log", "context_cleared", None, "Dima", t0 + timedelta(seconds=1))
    html = _merge_feed([msg], [evt], 4, None)
    assert html.index("hi") < html.index("Context cleared")  # chronological order preserved


async def test_chat_clear_route_writes_and_shows_log_line(db_session) -> None:
    from sqlmodel import select

    import app.api._routes_chat as rc
    from app.adapters.db.models import ThreadLog
    from app.api._routes_chat import chat_clear

    _, tid = await _log_world(db_session)
    orig = rc.session_scope
    rc.session_scope = lambda: _Scope(db_session)
    try:
        resp = await chat_clear(tid, _Req())  # type: ignore[arg-type]
    finally:
        rc.session_scope = orig

    assert "Context cleared" in resp.body.decode()
    row = (await db_session.exec(
        select(ThreadLog).where(ThreadLog.thread_id == tid))).first()
    assert row is not None and row.kind == "context_cleared" and row.actor == "manager"
