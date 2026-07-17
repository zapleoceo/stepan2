"""Auth gate: signed session, Telegram-login verification, branch scoping, middleware."""
from __future__ import annotations

import hashlib
import hmac
import os
import time

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.admin._branch import BRANCH_COOKIE, branch_ids_from_request  # noqa: E402
from app.api._auth import SESSION_COOKIE, mint_session, verify_telegram_login  # noqa: E402
from app.api._routes_auth import _login_html  # noqa: E402
from app.api._session import sign, verify  # noqa: E402
from app.api.main import app  # noqa: E402


class _StubSettings:
    """Minimal settings double for toggling the gate in middleware tests."""

    def __init__(self, **kw: object) -> None:
        self.auth_enabled = kw.get("auth_enabled", True)
        self.session_secret = kw.get("session_secret", "testsecret")
        self.secret_key = kw.get("secret_key", "")
        self.tg_bot_token = kw.get("tg_bot_token", "")
        self.tg_login_bot_username = kw.get("tg_login_bot_username", "")
        self.bootstrap_super_admin = kw.get("bootstrap_super_admin", 0)


def _enable(monkeypatch, **kw) -> None:
    monkeypatch.setattr("app.api._auth.settings", lambda: _StubSettings(**kw))


# ─── signed session cookie ────────────────────────────────────────────────────

def test_session_roundtrip() -> None:
    token = sign({"uid": 5, "iat": time.time()}, "sec")
    assert verify(token, "sec", 3600)["uid"] == 5


def test_session_tamper_rejected() -> None:
    body, sig = sign({"uid": 5, "iat": time.time()}, "sec").split(".", 1)
    flipped = body[:-1] + ("A" if body[-1] != "A" else "B")
    assert verify(f"{flipped}.{sig}", "sec", 3600) is None


def test_session_wrong_secret_rejected() -> None:
    assert verify(sign({"uid": 5, "iat": time.time()}, "sec"), "other", 3600) is None


def test_session_expired_rejected() -> None:
    assert verify(sign({"uid": 5, "iat": time.time() - 7200}, "sec"), "sec", 3600) is None


def test_session_garbage_rejected() -> None:
    assert verify("not-a-token", "sec", 3600) is None


# ─── Telegram login verification ──────────────────────────────────────────────

def _tg_hash(data: dict[str, str], token: str) -> str:
    check = "\n".join(sorted(f"{k}={v}" for k, v in data.items()))
    secret = hashlib.sha256(token.encode()).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def test_tg_login_valid() -> None:
    data = {"id": "169510539", "first_name": "Dima", "auth_date": str(int(time.time()))}
    data["hash"] = _tg_hash(data, "TOKEN")
    assert verify_telegram_login(data, "TOKEN") == 169510539


def test_tg_login_bad_hash_rejected() -> None:
    data = {"id": "1", "auth_date": str(int(time.time())), "hash": "deadbeef"}
    assert verify_telegram_login(data, "TOKEN") is None


def test_tg_login_stale_rejected() -> None:
    data = {"id": "1", "auth_date": str(int(time.time()) - 100_000)}
    data["hash"] = _tg_hash(data, "TOKEN")
    assert verify_telegram_login(data, "TOKEN", max_age_s=86400) is None


def test_tg_login_no_token_rejected() -> None:
    assert verify_telegram_login({"id": "1", "hash": "x"}, "") is None


# ─── /api/tg_login callback: the privilege-assignment step ────────────────────

async def _run_tg_login(db_session, monkeypatch, tg_id: int, query: str = ""):
    """Drive the real tg_login route with a stubbed verify + the test DB, return the
    session dict decoded from the Set-Cookie it mints (or None if login was rejected)."""
    import contextlib

    import app.api._routes_auth as ra
    from app.api._auth import SESSION_COOKIE as _SC
    from app.api._auth import read_session

    @contextlib.asynccontextmanager
    async def fake_scope():
        yield db_session

    monkeypatch.setattr(ra, "session_scope", fake_scope)
    monkeypatch.setattr(ra, "verify_telegram_login", lambda *a, **k: tg_id)
    monkeypatch.setattr(ra, "settings", lambda: _StubSettings(tg_bot_token="TOKEN"))  # noqa: S106

    req = Request({"type": "http", "headers": [], "query_string": query.encode(), "state": {}})
    resp = await ra.tg_login(req)
    cookie = resp.headers.get("set-cookie", "")
    if f"{_SC}=" not in cookie:
        return None, resp
    tok = cookie.split(f"{_SC}=", 1)[1].split(";", 1)[0]
    return read_session(_CookieReq(_SC, tok)), resp


class _CookieReq:
    def __init__(self, name: str, tok: str) -> None:
        self.cookies = {name: tok}


async def test_tg_login_mints_super_admin_session(db_session, monkeypatch) -> None:
    from app.adapters.db.models import Membership, User
    from app.domain.enums import Role

    u = User(telegram_id=169510539, name="Dima")
    db_session.add(u)
    await db_session.flush()
    db_session.add(Membership(user_id=u.id, branch_id=None, role=Role.SUPER_ADMIN))
    await db_session.flush()

    sess, resp = await _run_tg_login(db_session, monkeypatch, 169510539)
    assert sess is not None
    assert sess["sa"] is True           # super_admin claim
    assert sess["br"] == []             # no branch confinement
    assert sess["uid"] == u.id
    # The cookie must ride a 200 HTML page, NOT a 3xx: mobile in-app WebViews drop a
    # Set-Cookie on a redirect, which looped phone logins back to /login forever.
    assert resp.status_code == 200
    body = resp.body.decode()
    assert "/ui/inbox" in body and "location.replace" in body


async def test_tg_login_viewer_gets_no_write(db_session, monkeypatch) -> None:
    from app.adapters.db.models import Branch, Membership, User
    from app.domain.enums import Role

    b = Branch(name="Jakarta", lang="id")
    db_session.add(b)
    await db_session.flush()
    u = User(telegram_id=555, name="Staff")
    db_session.add(u)
    await db_session.flush()
    db_session.add(Membership(user_id=u.id, branch_id=b.id, role=Role.BRANCH_VIEWER))
    await db_session.flush()

    sess, _ = await _run_tg_login(db_session, monkeypatch, 555)
    assert sess is not None
    assert sess["sa"] is False          # NOT super_admin
    assert sess["br"] == [b.id]         # can READ their branch
    assert sess["wr"] == []             # branch_viewer → NO write anywhere


async def test_tg_login_admin_gets_write_on_own_branch(db_session, monkeypatch) -> None:
    from app.adapters.db.models import Branch, Membership, User
    from app.domain.enums import Role

    b = Branch(name="Jakarta", lang="id")
    db_session.add(b)
    await db_session.flush()
    u = User(telegram_id=556, name="Manager")
    db_session.add(u)
    await db_session.flush()
    db_session.add(Membership(user_id=u.id, branch_id=b.id, role=Role.BRANCH_ADMIN))
    await db_session.flush()

    sess, _ = await _run_tg_login(db_session, monkeypatch, 556)
    assert sess is not None
    assert sess["sa"] is False
    assert sess["br"] == [b.id]
    assert sess["wr"] == [b.id]         # branch_admin → write on own branch


async def test_tg_login_unknown_user_rejected(db_session, monkeypatch) -> None:
    """A verified Telegram id with no User/Membership row must NOT get a session — the
    privilege-assignment step must fail closed for strangers."""
    sess, resp = await _run_tg_login(db_session, monkeypatch, 424242)
    assert sess is None
    assert resp.status_code == 403


# ─── identity-aware branch scoping ────────────────────────────────────────────

def _req(cookie: str | None = None, allowed: object = "__unset__") -> Request:
    headers = [(b"cookie", f"{BRANCH_COOKIE}={cookie}".encode())] if cookie else []
    req = Request({"type": "http", "headers": headers, "state": {}})
    if allowed != "__unset__":
        req.state.allowed_branch_ids = allowed
    return req


def test_scope_auth_off_cookie_drives() -> None:
    assert branch_ids_from_request(_req(cookie="1,3")) == [1, 3]
    assert branch_ids_from_request(_req()) is None


def test_scope_super_admin_sees_all() -> None:
    assert branch_ids_from_request(_req(allowed=None)) is None
    assert branch_ids_from_request(_req(cookie="2", allowed=None)) == [2]


def test_scope_user_cannot_exceed_allowed() -> None:
    assert branch_ids_from_request(_req(cookie="1,7", allowed=[1])) == [1]
    assert branch_ids_from_request(_req(cookie="7", allowed=[1])) == [1]
    assert branch_ids_from_request(_req(allowed=[1])) == [1]


def test_scope_no_membership_sees_nothing() -> None:
    assert branch_ids_from_request(_req(allowed=[])) == [-1]


# ─── middleware gate ──────────────────────────────────────────────────────────

def test_gate_disabled_by_default_allows_access() -> None:
    resp = TestClient(app, raise_server_exceptions=False).get("/ui/inbox")
    assert resp.status_code == 200


def test_gate_enabled_redirects_anonymous_remembering_the_destination(monkeypatch) -> None:
    _enable(monkeypatch)
    client = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
    resp = client.get("/hiw?lang=uk")
    assert resp.status_code == 303
    # the gate carries where they were headed so login can land them back there
    assert resp.headers["location"] == "/login?next=%2Fhiw%3Flang%3Duk"


def test_gate_never_makes_next_point_at_an_auth_endpoint(monkeypatch) -> None:
    # a bounce off /api/... (or /login itself) must not build /login?next=/login… — plain /login
    _enable(monkeypatch)
    client = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
    resp = client.get("/api/whatever")
    assert resp.status_code == 303 and resp.headers["location"] == "/login"


def test_gate_enabled_htmx_gets_hx_redirect(monkeypatch) -> None:
    _enable(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/ui/threads", headers={"HX-Request": "true"})
    assert resp.status_code == 401
    assert resp.headers.get("HX-Redirect") == "/login"


def test_gate_enabled_valid_session_allows(monkeypatch) -> None:
    _enable(monkeypatch)
    token = mint_session(telegram_id=1, user_id=1, name="x", is_super=True, branch_ids=[])
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/ui/inbox", cookies={SESSION_COOKIE: token})
    assert resp.status_code == 200


def test_gate_enabled_healthz_public(monkeypatch) -> None:
    _enable(monkeypatch)
    assert TestClient(app, raise_server_exceptions=False).get("/healthz").status_code == 200


def test_gate_enabled_marketing_pages_public(monkeypatch) -> None:
    # /whats-new was redirecting to /login with auth on — it's a public marketing page and
    # must render for anonymous visitors and crawlers, like / and /privacy.
    _enable(monkeypatch)
    client = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
    for path in ("/", "/whats-new", "/privacy", "/robots.txt", "/sitemap.xml", "/og.svg"):
        assert client.get(path).status_code == 200, path


# ─── login page ───────────────────────────────────────────────────────────────

def test_login_html_with_bot_has_widget() -> None:
    html = _login_html("mybot")
    assert "telegram-widget" in html
    assert "mybot" in html


def test_login_html_without_bot_warns() -> None:
    assert "BotFather" in _login_html("")


def test_login_widget_carries_next_into_the_auth_url() -> None:
    html = _login_html("mybot", "/hiw?lang=uk")
    assert 'data-auth-url="/api/tg_login?next=%2Fhiw%3Flang%3Duk"' in html


def test_safe_next_blocks_open_redirect_and_auth_endpoints() -> None:
    from app.api._routes_auth import _safe_next
    assert _safe_next("/hiw") == "/hiw"
    assert _safe_next("/ui/reports?x=1") == "/ui/reports?x=1"
    assert _safe_next("//evil.com") == "/ui/inbox"          # protocol-relative
    assert _safe_next("https://evil.com") == "/ui/inbox"    # absolute URL
    assert _safe_next("/login") == "/ui/inbox"              # no login→login bounce
    assert _safe_next("/api/tg_login") == "/ui/inbox"
    assert _safe_next("") == "/ui/inbox"


async def test_tg_login_returns_user_to_the_next_destination(db_session, monkeypatch) -> None:
    from app.adapters.db.models import Membership, User
    from app.domain.enums import Role

    u = User(telegram_id=169510539, name="Dima")
    db_session.add(u)
    await db_session.flush()
    db_session.add(Membership(user_id=u.id, branch_id=None, role=Role.SUPER_ADMIN))
    await db_session.flush()

    _, resp = await _run_tg_login(db_session, monkeypatch, 169510539, query="next=%2Fhiw")
    assert resp.status_code == 200
    body = resp.body.decode()
    assert "/hiw" in body                                   # landed back where they were headed
    assert "/ui/inbox" not in body
