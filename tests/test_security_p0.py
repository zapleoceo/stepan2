"""P0 security fixes: per-thread/channel tenant guards, webhook HMAC signature,
timing-safe verify handshake, CRM SSRF allowlist."""
from __future__ import annotations

import hashlib
import hmac
import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.adapters.db.models import (  # noqa: E402
    Branch,
    Channel,
    ChannelThread,
    KnowledgeDoc,
    Lead,
    Product,
)
from app.admin._branch import (  # noqa: E402
    allowed_branch_ids,
    is_branch_forbidden,
    is_super_admin,
    require_super_admin,
)
from app.api._routes_channels import _channel_branch  # noqa: E402
from app.api._routes_chat import _guarded_branch, chat_bot_toggle  # noqa: E402
from app.api.main import app  # noqa: E402
from app.modules.crm.service import is_safe_webhook_url  # noqa: E402

# ─── act-on permission ignores the view-filter cookie ─────────────────────────

def _req(cookie: str = "", allowed=..., writable=...):
    scope = {"type": "http", "headers": [(b"cookie", f"stepan2_branch={cookie}".encode())]}
    req = Request(scope)
    if allowed is not ...:
        req.state.allowed_branch_ids = allowed
        # Default: a write test mirrors read scope (viewer vs admin is set explicitly via
        # `writable`). Zero-branch member → writable=[] → write routes deny.
        req.state.writable_branch_ids = allowed if writable is ... else writable
    elif writable is not ...:
        req.state.writable_branch_ids = writable
    return req


def test_allowed_branch_ids_ignores_view_cookie_when_no_auth() -> None:
    # auth disabled: cookie filters the *view* but must not restrict *actions*
    assert allowed_branch_ids(_req(cookie="1")) is None       # can manage any branch
    assert allowed_branch_ids(_req(cookie="")) is None
    # scoped user: membership drives permission, not the cookie
    assert allowed_branch_ids(_req(cookie="1", allowed=[7])) == [7]
    assert allowed_branch_ids(_req(cookie="", allowed=None)) is None


# ─── per-thread tenant guard (chat IDOR) ──────────────────────────────────────

async def _thread(s, branch_id: int) -> int:
    ch = Channel(branch_id=branch_id, kind="instagram")
    s.add(ch)
    lead = Lead(branch_id=branch_id)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    s.add(thread)
    await s.flush()
    return thread.id


async def test_guarded_branch_allows_owner_denies_others(db_session) -> None:
    a = Branch(name="A", lang="id")
    b = Branch(name="B", lang="id")
    db_session.add(a)
    db_session.add(b)
    await db_session.flush()
    tid = await _thread(db_session, a.id)

    assert await _guarded_branch(db_session, tid, None) == a.id          # super_admin
    assert await _guarded_branch(db_session, tid, [a.id]) == a.id        # own branch
    assert await _guarded_branch(db_session, tid, [b.id]) is None        # cross-branch IDOR
    assert await _guarded_branch(db_session, tid, [b.id, a.id]) == a.id  # multi allowed
    assert await _guarded_branch(db_session, 999999, [a.id]) is None     # missing


async def test_channel_branch_guard(db_session) -> None:
    a = Branch(name="A", lang="id")
    b = Branch(name="B", lang="id")
    db_session.add(a)
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=a.id, kind="instagram")
    db_session.add(ch)
    await db_session.flush()

    assert await _channel_branch(db_session, ch.id, None) == a.id
    assert await _channel_branch(db_session, ch.id, [a.id]) == a.id
    assert await _channel_branch(db_session, ch.id, [b.id]) is None
    assert await _channel_branch(db_session, 999999, None) is None


# ─── empty allowed list = access to nothing (fail-closed) ────────────────────

def test_is_branch_forbidden_empty_list_denies_everything() -> None:
    assert is_branch_forbidden(1, []) is True
    assert is_branch_forbidden(1, None) is False
    assert is_branch_forbidden(1, [1]) is False
    assert is_branch_forbidden(2, [1]) is True


# ─── super_admin gate (members, branch CRUD, platform kill switch) ────────────

def test_is_super_admin_true_for_none_state_and_super_session() -> None:
    assert is_super_admin(_req()) is True                      # auth disabled → permissive
    assert is_super_admin(_req(allowed=None)) is True           # sess.get("sa") was True
    assert is_super_admin(_req(allowed=[7])) is False           # branch-scoped session
    assert is_super_admin(_req(allowed=[])) is False            # zero-branch member


def test_require_super_admin_raises_403_for_scoped_user() -> None:
    from fastapi import HTTPException
    require_super_admin(_req(allowed=None))  # no raise
    try:
        require_super_admin(_req(allowed=[7]))
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("expected HTTPException")


# ─── knowledge/product edit forms leak cross-branch content (IDOR, read-only) ─

async def test_knowledge_edit_denies_cross_branch_read(db_session, monkeypatch) -> None:
    import contextlib

    from app.api._routes_knowledge import knowledge_edit

    a = Branch(name="A", lang="id")
    b = Branch(name="B", lang="id")
    db_session.add_all([a, b])
    await db_session.flush()
    doc = KnowledgeDoc(branch_id=a.id, slug="faq", title="FAQ", content="secret")
    db_session.add(doc)
    await db_session.flush()

    @contextlib.asynccontextmanager
    async def fake_scope():
        yield db_session

    monkeypatch.setattr("app.api._routes_knowledge.session_scope", fake_scope)

    ok = await knowledge_edit(doc.id, _req(allowed=[a.id]))
    assert ok.status_code == 200
    denied = await knowledge_edit(doc.id, _req(allowed=[b.id]))
    assert denied.status_code == 403


async def test_products_edit_denies_cross_branch_read(db_session, monkeypatch) -> None:
    import contextlib

    from app.api._routes_products import products_edit

    a = Branch(name="A", lang="id")
    b = Branch(name="B", lang="id")
    db_session.add_all([a, b])
    await db_session.flush()
    prod = Product(branch_id=a.id, slug="course", title="Course")
    db_session.add(prod)
    await db_session.flush()

    @contextlib.asynccontextmanager
    async def fake_scope():
        yield db_session

    monkeypatch.setattr("app.api._routes_products.session_scope", fake_scope)

    ok = await products_edit(prod.id, _req(allowed=[a.id]))
    assert ok.status_code == 200
    denied = await products_edit(prod.id, _req(allowed=[b.id]))
    assert denied.status_code == 403


# ─── platform-wide bot kill switch is super_admin-only ────────────────────────

async def test_agent_toggle_platform_scope_ignored_for_non_super(db_session, monkeypatch) -> None:
    import contextlib

    from sqlalchemy import text as _text

    from app.api._routes_admin import agent_toggle

    @contextlib.asynccontextmanager
    async def fake_scope():
        yield db_session

    monkeypatch.setattr("app.api._routes_admin.session_scope", fake_scope)

    before = (await db_session.execute(
        _text("SELECT value FROM app_setting WHERE key='agent_enabled_platform'"))).first()

    class _Req:
        cookies: dict = {}
        headers: dict = {}
        state = type("S", (), {"allowed_branch_ids": [7]})()

    await agent_toggle(_Req(), scope="platform", branch_id=7)  # type: ignore[arg-type]
    after = (await db_session.execute(
        _text("SELECT value FROM app_setting WHERE key='agent_enabled_platform'"))).first()
    assert after == before  # a non-super-admin's platform toggle must be a no-op


def test_agent_toggles_html_hides_platform_switch_for_non_super() -> None:
    from app.api.ui import _agent_toggles_html
    html = _agent_toggles_html(1, platform_on=True, branch_on=False, is_super=False)
    assert 'name="scope" value="platform"' not in html
    assert 'name="scope" value="branch"' in html


# ─── coach_say no longer trusts a client-submitted branch_id ──────────────────

def test_coach_say_has_no_client_branch_id_param() -> None:
    import inspect

    from app.api._routes_coach import coach_say
    params = inspect.signature(coach_say).parameters
    assert "branch_id" not in params


# ─── branch CRUD (create/edit/save any branch) is super_admin-only ────────────

def test_branches_router_requires_super_admin() -> None:
    from app.api._routes_branches import router as branches_router
    deps = [d.dependency for d in branches_router.dependencies]
    assert require_super_admin in deps


# ─── SQLAdmin raw dashboard is super_admin-only (privilege-escalation guard) ──

def _cookie_client(sess: dict | None, *, auth_enabled: bool = True) -> TestClient:
    """A TestClient carrying a signed session cookie (or none), with auth enabled so the
    AdminGuardMiddleware path is exercised realistically."""
    import os as _os

    from app.api._auth import SESSION_COOKIE, mint_session
    from app.config import settings as _settings

    _os.environ["STEPAN2_AUTH_ENABLED"] = "true" if auth_enabled else "false"
    _settings.cache_clear()
    c = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    if sess is not None:
        tok = mint_session(
            telegram_id=sess["tg"], user_id=sess["uid"], name=sess.get("nm", ""),
            is_super=sess["sa"], branch_ids=sess.get("br", []),
            writable_branch_ids=sess.get("wr", []),
        )
        c.cookies.set(SESSION_COOKIE, tok)
    return c


def _clear_auth_env() -> None:
    import os as _os

    from app.config import settings as _settings
    _os.environ.pop("STEPAN2_AUTH_ENABLED", None)
    _settings.cache_clear()


def test_admin_dashboard_blocks_non_super_admin() -> None:
    """SQLAdmin exposes raw CRUD over Membership/Branch/AppSetting — a branch-scoped user
    reaching /admin/membership/list could self-escalate to super_admin. Must be blocked."""
    try:
        # super_admin is let THROUGH the guard (the in-memory test DB has no tables, so
        # SQLAdmin itself 500s — that's fine; the point is the guard didn't bounce it).
        su = _cookie_client({"tg": 1, "uid": 1, "sa": True, "br": []})
        assert su.get("/admin/membership/list").status_code != 303

        scoped = _cookie_client({"tg": 2, "uid": 2, "sa": False, "br": [1]})
        assert scoped.get("/admin/membership/list").status_code == 303  # bounced

        anon = _cookie_client(None)
        assert anon.get("/admin/membership/list").status_code == 303
    finally:
        import os as _os

        from app.config import settings as _settings
        _os.environ.pop("STEPAN2_AUTH_ENABLED", None)
        _settings.cache_clear()


def test_admin_dashboard_closed_when_auth_disabled() -> None:
    """With auth off there is no session at all — /admin must be CLOSED (was wide open)."""
    try:
        c = _cookie_client(None, auth_enabled=False)
        assert c.get("/admin/membership/list").status_code == 303
    finally:
        import os as _os

        from app.config import settings as _settings
        _os.environ.pop("STEPAN2_AUTH_ENABLED", None)
        _settings.cache_clear()


# ─── branch_viewer is read-only: WriteGuardMiddleware blocks mutating POSTs ────

def test_read_support_post_classification() -> None:
    from app.api._auth import _is_read_support_post
    # read-support (a viewer may call these — they don't mutate business data)
    assert _is_read_support_post("/ui/branch-filter") is True
    assert _is_read_support_post("/ui/coach/analyze/7") is True
    assert _is_read_support_post("/ui/chat/7/translate") is True
    assert _is_read_support_post("/ui/chat/7/tr-draft") is True
    assert _is_read_support_post("/ui/chat/7/msg/5/tr") is True
    assert _is_read_support_post("/ui/chat/7/pending/5/tr") is True
    assert _is_read_support_post("/ui/chat/7/load-context") is True
    # genuine writes — NOT read-support
    assert _is_read_support_post("/ui/chat/7/send") is False
    assert _is_read_support_post("/ui/chat/7/msg/5/delete") is False
    assert _is_read_support_post("/ui/knowledge/3/save") is False
    assert _is_read_support_post("/ui/settings/save") is False
    assert _is_read_support_post("/ui/coach/say") is False


def test_write_guard_blocks_viewer_allows_admin_and_super() -> None:
    """A branch_viewer (wr=[]) may not POST a write; branch_admin (wr=[1]) and
    super_admin (sa) may. Uses /ui/chat/1/send — a write route — expecting the guard's
    403 for the viewer, and NOT-403 (the route runs, may 4xx/5xx on the empty test DB)
    for the others."""
    try:
        viewer = _cookie_client({"tg": 3, "uid": 3, "sa": False, "br": [1], "wr": []})
        assert viewer.post("/ui/chat/1/send", data={"text": "hi"}).status_code == 403

        admin = _cookie_client({"tg": 4, "uid": 4, "sa": False, "br": [1], "wr": [1]})
        assert admin.post("/ui/chat/1/send", data={"text": "hi"}).status_code != 403

        su = _cookie_client({"tg": 1, "uid": 1, "sa": True, "br": [], "wr": []})
        assert su.post("/ui/chat/1/send", data={"text": "hi"}).status_code != 403
    finally:
        _clear_auth_env()


def test_write_guard_allows_viewer_read_support_posts() -> None:
    """A viewer must still be able to translate / analyze / load history / switch branch —
    those POSTs are read-support and must NOT be 403'd."""
    try:
        viewer = _cookie_client({"tg": 3, "uid": 3, "sa": False, "br": [1], "wr": []})
        assert viewer.post("/ui/chat/1/translate").status_code != 403
        assert viewer.post("/ui/coach/analyze/1").status_code != 403
        assert viewer.post("/ui/branch-filter", data={"bid": "1"}).status_code != 403
    finally:
        _clear_auth_env()


def test_write_guard_passthrough_when_auth_disabled() -> None:
    """Auth off (dev/local) → no enforcement, writes pass (matches every other guard)."""
    try:
        c = _cookie_client(None, auth_enabled=False)
        assert c.post("/ui/chat/1/send", data={"text": "hi"}).status_code != 403
    finally:
        _clear_auth_env()


def test_write_guard_denies_pre_wr_cookie_fail_closed() -> None:
    """A non-super session minted before the 'wr' claim existed has no writable branches →
    writes are denied until re-login (fail closed, not grandfathered-open)."""
    try:
        from app.api._auth import SESSION_COOKIE, mint_session

        # simulate an old cookie: mint without writable_branch_ids at all
        old = _cookie_client(None)
        tok = mint_session(telegram_id=9, user_id=9, name="Old", is_super=False,
                           branch_ids=[1])  # no writable_branch_ids kwarg → wr=[]
        old.cookies.set(SESSION_COOKIE, tok)
        assert old.post("/ui/chat/1/send", data={"text": "hi"}).status_code == 403
    finally:
        _clear_auth_env()


def test_writable_branch_helpers() -> None:
    from app.admin._branch import is_branch_write_forbidden, writable_branch_ids
    assert writable_branch_ids(_req()) is None                 # no state → super/all
    assert writable_branch_ids(_req(allowed=None)) is None      # (allowed unused here)
    assert is_branch_write_forbidden(1, None) is False          # super writes anywhere
    assert is_branch_write_forbidden(1, []) is True             # viewer writes nowhere
    assert is_branch_write_forbidden(1, [1]) is False           # admin of branch 1
    assert is_branch_write_forbidden(2, [1]) is True            # not admin of branch 2


async def test_mixed_role_writes_own_admin_branch_not_viewer_branch(
    db_session, monkeypatch,
) -> None:
    """A user who is branch_admin of B1 and branch_viewer of B2 (br=[1,2], wr=[1]) may
    write to a B1 thread but NOT a B2 thread — even though they can READ both."""
    import contextlib

    from app.api._routes_chat import chat_bot_toggle

    b1 = Branch(name="B1", lang="id")
    b2 = Branch(name="B2", lang="id")
    db_session.add_all([b1, b2])
    await db_session.flush()
    t1 = await _thread(db_session, b1.id)
    t2 = await _thread(db_session, b2.id)

    @contextlib.asynccontextmanager
    async def fake_scope():
        yield db_session

    monkeypatch.setattr("app.api._routes_chat.session_scope", fake_scope)
    from sqlalchemy import text as _text

    # reads both branches (allowed=[1,2]) but writes only B1 (writable=[1])
    mixed = _req(allowed=[b1.id, b2.id], writable=[b1.id])

    async def _enabled(tid_thread) -> bool:
        row = (await db_session.execute(
            _text("SELECT l.agent_enabled FROM channel_thread ct"
                  " JOIN lead l ON l.id=ct.lead_id WHERE ct.id=:t"), {"t": tid_thread})).first()
        return bool(row[0])

    before_b2 = await _enabled(t2)
    await chat_bot_toggle(t2, mixed)               # write to viewer-branch → blocked
    assert await _enabled(t2) == before_b2          # unchanged

    before_b1 = await _enabled(t1)
    await chat_bot_toggle(t1, mixed)               # write to admin-branch → allowed
    assert await _enabled(t1) != before_b1          # flipped


# ─── coach analyze must not read another branch's chat + KB (IDOR) ────────────

async def test_coach_analyze_denies_cross_branch(db_session, monkeypatch) -> None:
    import contextlib

    from app.api._routes_coach import coach_analyze

    a = Branch(name="A", lang="id")
    b = Branch(name="B", lang="id")
    db_session.add_all([a, b])
    await db_session.flush()
    tid = await _thread(db_session, a.id)

    called = {"n": 0}

    async def fake_analyze(*args, **kwargs):
        called["n"] += 1
        return "GRADED"

    @contextlib.asynccontextmanager
    async def fake_scope():
        yield db_session

    monkeypatch.setattr("app.api._routes_coach.session_scope", fake_scope)
    monkeypatch.setattr("app.api._routes_coach.analyze_chat", fake_analyze)

    denied = await coach_analyze(tid, _req(allowed=[b.id]))
    assert denied.body == b""            # cross-branch → nothing rendered
    assert called["n"] == 0              # and the LLM/KB read never ran

    ok = await coach_analyze(tid, _req(allowed=[a.id]))
    assert b"GRADED" in ok.body
    assert called["n"] == 1


async def test_guarded_branch_denies_zero_branch_member(db_session) -> None:
    a = Branch(name="A", lang="id")
    db_session.add(a)
    await db_session.flush()
    tid = await _thread(db_session, a.id)

    assert await _guarded_branch(db_session, tid, []) is None


async def test_bot_toggle_denied_for_zero_branch_member(db_session, monkeypatch) -> None:
    import contextlib

    a = Branch(name="A", lang="id")
    db_session.add(a)
    await db_session.flush()
    tid = await _thread(db_session, a.id)

    @contextlib.asynccontextmanager
    async def fake_scope():
        yield db_session

    monkeypatch.setattr("app.api._routes_chat.session_scope", fake_scope)
    from sqlalchemy import text as _text

    before = (await db_session.execute(
        _text("SELECT agent_enabled FROM lead"))).scalar()
    await chat_bot_toggle(tid, _req(allowed=[]))
    after = (await db_session.execute(
        _text("SELECT agent_enabled FROM lead"))).scalar()
    assert after == before  # guard blocked the flip


# ─── ad_id must not break out of the copy-to-clipboard handler (XSS) ──────────

def test_source_bar_ad_id_not_in_inline_js_string() -> None:
    from app.api._ui_html import _source_bar
    payload = "');alert(document.cookie);//"
    html = _source_bar("ad", payload, None, None)
    # value copied via this.dataset, never interpolated into an inline JS string, so the
    # raw quote-breakout sequence can't appear unescaped anywhere in the output.
    assert "writeText('" not in html
    assert "this.dataset.clip" in html
    assert "');alert" not in html          # the ' is escaped to &#x27; — no breakout


# ─── CRM SSRF allowlist ───────────────────────────────────────────────────────

def test_ssrf_allowlist_blocks_internal_and_http() -> None:
    assert is_safe_webhook_url("https://hooks.example.com/crm") is True
    assert is_safe_webhook_url("http://hooks.example.com/crm") is False   # not https
    assert is_safe_webhook_url("https://localhost/x") is False
    assert is_safe_webhook_url("https://127.0.0.1/x") is False
    assert is_safe_webhook_url("https://169.254.169.254/latest/meta") is False
    assert is_safe_webhook_url("https://10.0.0.5/x") is False
    assert is_safe_webhook_url("https://192.168.1.1/x") is False
    assert is_safe_webhook_url("https://internalhost/x") is False        # no dot
    assert is_safe_webhook_url("") is False


# ─── webhook signature + handshake ────────────────────────────────────────────

def test_webhook_post_rejects_unsigned_and_forged(monkeypatch) -> None:
    monkeypatch.setenv("STEPAN2_META_APP_SECRET_1", "s3cr3t")
    client = TestClient(app, raise_server_exceptions=False)
    body = b'{"entry":[{"id":"1"}]}'

    # no signature → 403
    assert client.post("/webhooks/meta/1", content=body).status_code == 403
    # forged signature → 403
    assert client.post(
        "/webhooks/meta/1", content=body,
        headers={"X-Hub-Signature-256": "sha256=deadbeef"},
    ).status_code == 403
    # valid signature → accepted
    sig = hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/webhooks/meta/1", content=body,
        headers={"X-Hub-Signature-256": f"sha256={sig}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 1}


def test_webhook_unconfigured_branch_rejected(monkeypatch) -> None:
    monkeypatch.delenv("STEPAN2_META_APP_SECRET_2", raising=False)
    client = TestClient(app, raise_server_exceptions=False)
    body = b'{"entry":[]}'
    sig = hmac.new(b"whatever", body, hashlib.sha256).hexdigest()
    resp = client.post(
        "/webhooks/meta/2", content=body,
        headers={"X-Hub-Signature-256": f"sha256={sig}"},
    )
    assert resp.status_code == 403  # fail-closed: no secret configured


def test_webhook_verify_handshake(monkeypatch) -> None:
    monkeypatch.setenv("STEPAN2_META_VERIFY_TOKEN_1", "verifyme")
    client = TestClient(app, raise_server_exceptions=False)
    ok = client.get("/webhooks/meta/1", params={
        "hub.mode": "subscribe", "hub.verify_token": "verifyme", "hub.challenge": "42",
    })
    assert ok.status_code == 200 and ok.text == "42"
    bad = client.get("/webhooks/meta/1", params={
        "hub.verify_token": "wrong", "hub.challenge": "42",
    })
    assert bad.status_code == 403
