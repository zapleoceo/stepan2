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

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead  # noqa: E402
from app.admin._branch import allowed_branch_ids, is_branch_forbidden  # noqa: E402
from app.api._routes_channels import _channel_branch  # noqa: E402
from app.api._routes_chat import _guarded_branch, chat_bot_toggle  # noqa: E402
from app.api.main import app  # noqa: E402
from app.modules.crm.service import is_safe_webhook_url  # noqa: E402

# ─── act-on permission ignores the view-filter cookie ─────────────────────────

def _req(cookie: str = "", allowed=...):
    scope = {"type": "http", "headers": [(b"cookie", f"stepan2_branch={cookie}".encode())]}
    req = Request(scope)
    if allowed is not ...:
        req.state.allowed_branch_ids = allowed
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
