"""Channel adapters mapped against FAKE transports — no httpx/instagrapi needed.

Proves the hexagonal seam: each adapter turns raw transport dicts into InboundMessage /
SendResult / SessionStatus, so swapping the real transport never touches the adapter."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.adapters.channels import (
    REGISTRY,
    InstagramAdapter,
    MetaBusinessAdapter,
    WhatsAppAdapter,
)
from app.domain.enums import ChannelKind, SessionStatus
from app.ports.channel import InboundMessage, SendResult


class _Boom(Exception):
    """Transport-layer failure used to assert send_text degrades to SendResult(ok=False)."""


class FakeIGTransport:
    def __init__(self, *, health: str = "ok", raise_on_send: bool = False) -> None:
        self._health = health
        self._raise = raise_on_send

    async def fetch_threads(self) -> list[dict[str, Any]]:
        return [
            {
                "thread_id": 111,
                "sender_id": 42,
                "text": "hi from ig",
                "timestamp": datetime(2026, 6, 1, tzinfo=UTC),
                "ad_product": "vibe_coding",
            }
        ]

    async def send_direct(self, thread_id: str, text: str) -> dict[str, Any]:
        if self._raise:
            raise _Boom("ig down")
        return {"item_id": "ig_item_9"}

    async def account_health(self) -> str:
        return self._health


class FakeWATransport:
    def __init__(self, *, state: str = "open", raise_on_send: bool = False) -> None:
        self._state = state
        self._raise = raise_on_send

    async def fetch_messages(self) -> list[dict[str, Any]]:
        return [
            {
                "remote_jid": "628@s.whatsapp.net",
                "sender_id": "628@s.whatsapp.net",
                "text": "hi from wa",
                "message_timestamp": 1_750_000_000,
            }
        ]

    async def send_message(self, remote_jid: str, text: str) -> dict[str, Any]:
        if self._raise:
            raise _Boom("evolution down")
        return {"key": {"id": "wa_msg_7"}}

    async def connection_state(self) -> str:
        return self._state


class FakeGraphTransport:
    def __init__(self, *, valid: bool = True, raise_on_send: bool = False) -> None:
        self._valid = valid
        self._raise = raise_on_send

    async def fetch_conversations(self) -> list[dict[str, Any]]:
        return [
            {
                "thread_id": "t_55",
                "from_id": "user_88",
                "message": "hi from mbs",
                "created_time": "2026-06-01T10:00:00+0000",
                "referral_product": "data_science",
            }
        ]

    async def send_message(self, recipient_id: str, text: str) -> dict[str, Any]:
        if self._raise:
            raise _Boom("graph down")
        return {"message_id": "mbs_msg_3"}

    async def token_debug(self) -> dict[str, Any]:
        return {"is_valid": self._valid, "window_open": True}


# --- Instagram -------------------------------------------------------------

async def test_instagram_fetch_maps_to_inbound() -> None:
    adapter = InstagramAdapter(FakeIGTransport(), handle="@itstep")
    msgs = await adapter.fetch_inbound()
    assert msgs == [
        InboundMessage(
            external_thread_id="111",
            sender_id="42",
            text="hi from ig",
            occurred_at=datetime(2026, 6, 1),
            product_hint="vibe_coding",
        )
    ]


def test_ig_timestamp_is_naive_utc() -> None:
    """IG sends epoch microseconds; occurred_at must be naive UTC or asyncpg rejects the
    INSERT into a TIMESTAMP WITHOUT TIME ZONE column (the first live-ingest crash)."""
    from app.adapters.channels.instagram import _as_dt

    dt = _as_dt(1_750_000_000_000_000)  # microseconds
    assert dt.tzinfo is None
    assert dt == datetime.fromtimestamp(1_750_000_000, tz=UTC).replace(tzinfo=None)


async def test_instagram_send_maps_to_send_result() -> None:
    adapter = InstagramAdapter(FakeIGTransport(), handle="@itstep")
    assert await adapter.send_text("111", "yo") == SendResult(
        ok=True, external_message_id="ig_item_9"
    )


async def test_instagram_send_failure_is_not_ok() -> None:
    adapter = InstagramAdapter(FakeIGTransport(raise_on_send=True), handle="@itstep")
    res = await adapter.send_text("111", "yo")
    assert res.ok is False
    assert res.external_message_id is None
    assert "ig down" in (res.error or "")


@pytest.mark.parametrize(
    ("health", "expected"),
    [
        ("ok", SessionStatus.ACTIVE),
        ("challenge", SessionStatus.CHALLENGE),
        ("dead", SessionStatus.EXPIRED),
    ],
)
async def test_instagram_session_status(health: str, expected: SessionStatus) -> None:
    adapter = InstagramAdapter(FakeIGTransport(health=health), handle="@itstep")
    assert await adapter.session_status() is expected


# --- WhatsApp --------------------------------------------------------------

async def test_whatsapp_fetch_maps_to_inbound() -> None:
    adapter = WhatsAppAdapter(FakeWATransport(), instance="id_branch")
    msgs = await adapter.fetch_inbound()
    assert len(msgs) == 1
    m = msgs[0]
    assert m.external_thread_id == "628@s.whatsapp.net"
    assert m.text == "hi from wa"
    assert m.occurred_at == datetime.fromtimestamp(1_750_000_000, tz=UTC).replace(tzinfo=None)


async def test_whatsapp_send_maps_to_send_result() -> None:
    adapter = WhatsAppAdapter(FakeWATransport(), instance="id_branch")
    assert await adapter.send_text("628@s.whatsapp.net", "yo") == SendResult(
        ok=True, external_message_id="wa_msg_7"
    )


async def test_whatsapp_send_failure_is_not_ok() -> None:
    adapter = WhatsAppAdapter(FakeWATransport(raise_on_send=True), instance="id_branch")
    res = await adapter.send_text("628@s.whatsapp.net", "yo")
    assert res.ok is False
    assert "evolution down" in (res.error or "")


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("open", SessionStatus.ACTIVE),
        ("connecting", SessionStatus.CHALLENGE),
        ("close", SessionStatus.EXPIRED),
    ],
)
async def test_whatsapp_session_status(state: str, expected: SessionStatus) -> None:
    adapter = WhatsAppAdapter(FakeWATransport(state=state), instance="id_branch")
    assert await adapter.session_status() is expected


# --- Meta Business ---------------------------------------------------------

async def test_meta_business_fetch_maps_to_inbound() -> None:
    adapter = MetaBusinessAdapter(FakeGraphTransport(), account_id="page_1")
    msgs = await adapter.fetch_inbound()
    assert msgs == [
        InboundMessage(
            external_thread_id="t_55",
            sender_id="user_88",
            text="hi from mbs",
            occurred_at=datetime(2026, 6, 1, 10, 0),
            product_hint="data_science",
        )
    ]


async def test_meta_business_send_maps_to_send_result() -> None:
    adapter = MetaBusinessAdapter(FakeGraphTransport(), account_id="page_1")
    assert await adapter.send_text("user_88", "yo") == SendResult(
        ok=True, external_message_id="mbs_msg_3"
    )


async def test_meta_business_send_failure_is_not_ok() -> None:
    adapter = MetaBusinessAdapter(FakeGraphTransport(raise_on_send=True), account_id="page_1")
    res = await adapter.send_text("user_88", "yo")
    assert res.ok is False
    assert "graph down" in (res.error or "")


@pytest.mark.parametrize(
    ("valid", "expected"),
    [
        (True, SessionStatus.ACTIVE),
        (False, SessionStatus.CHALLENGE),
    ],
)
async def test_meta_business_session_status(valid: bool, expected: SessionStatus) -> None:
    adapter = MetaBusinessAdapter(FakeGraphTransport(valid=valid), account_id="page_1")
    assert await adapter.session_status() is expected


# --- Persistence (channel_create insert) -----------------------------------

async def test_channel_row_persists_with_created_at(db_session) -> None:
    """channel.created_at is NOT NULL in Postgres with no server default; the create
    route must go through the model so default_factory fills it (raw INSERT omitted it
    and 500'd on prod, while SQLite silently allowed NULL)."""
    from app.adapters.db.models import Branch, Channel

    b = Branch(name="M", lang="id")
    db_session.add(b)
    await db_session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind("instagram"), handle="@x", is_active=True)
    db_session.add(ch)
    await db_session.flush()
    await db_session.refresh(ch)
    assert ch.id is not None
    assert ch.created_at is not None


class _FakeIGClient:
    def __init__(self) -> None:
        self.last_json = {"two_factor_info": {"two_factor_identifier": "abc"}}
        self.challenge_code_handler = None
        self.login_calls: list[tuple] = []
        self.resolved: Any = None
        self.handler_code: str | None = None

    def login(self, username=None, password=None, verification_code=""):
        self.login_calls.append((username, password, verification_code))
        return True

    def challenge_resolve(self, last_json):
        self.resolved = last_json
        self.handler_code = self.challenge_code_handler("user", None)  # type: ignore[misc]
        return True


def test_ig_2fa_code_re_logs_in_not_challenge_resolve() -> None:
    """2FA in instagrapi is resolved by re-login with verification_code — NOT by
    challenge_resolve (which takes last_json, not a code)."""
    from app.api._routes_channels import _resolve_ig_code

    cl = _FakeIGClient()
    _resolve_ig_code(cl, {"kind": "2fa", "username": "u", "password": "p"}, "123456")
    assert cl.login_calls == [("u", "p", "123456")]
    assert cl.resolved is None


def test_ig_challenge_code_drives_challenge_resolve() -> None:
    """Email/SMS challenge feeds the code through challenge_code_handler, then
    challenge_resolve(last_json) — no re-login."""
    from app.api._routes_channels import _resolve_ig_code

    cl = _FakeIGClient()
    _resolve_ig_code(cl, {"kind": "challenge"}, "654321")
    assert cl.resolved is cl.last_json
    assert cl.handler_code == "654321"
    assert cl.login_calls == []


class _RaisingIGClient:
    """Fake instagrapi client whose login() raises a given exception once, then succeeds
    (simulating a retry after the operator resolves whatever instagrapi asked for)."""

    def __init__(self, exc: Exception | None, last_json: dict | None = None) -> None:
        self._exc = exc
        self.login_calls: list[tuple] = []
        self.last_json = last_json or {}  # instagrapi stashes the 2FA response here

    def login(self, username=None, password=None, verification_code=""):
        self.login_calls.append((username, password, verification_code))
        if self._exc is not None:
            exc, self._exc = self._exc, None  # only raise on the FIRST call
            raise exc
        return True

    def get_settings(self) -> dict:
        return {"fake": "session"}


def test_is_manual_challenge_matches_instagrapi_own_marker() -> None:
    """instagrapi's ChallengeRequired._message_for_payload prefixes exactly the
    unresolvable-by-code cases (Bloks redirect / auth-platform / native flow) with
    'Manual verification required' — that's the only reliable signal to key off."""
    from app.api._routes_channels import _is_manual_challenge

    assert _is_manual_challenge(Exception(
        "Manual verification required via Instagram native challenge flow. ..."))
    assert _is_manual_challenge(Exception(
        "Manual verification required via Instagram Bloks redirect checkpoint. ..."))
    assert not _is_manual_challenge(Exception(
        "Instagram returned a legacy challenge flow. Configure challenge_code_handler..."))
    assert not _is_manual_challenge(Exception("some unrelated error"))
    # real report, 2026-07-08: this exact TwoFactorRequired message (raised on the 2FA-CODE
    # submit, from _login_with_bloks_two_factor's missing-context branch) has no "Manual
    # verification required" prefix at all — the second marker below is what catches it.
    assert _is_manual_challenge(Exception(
        "Instagram rejected the legacy two-factor login endpoint and may require a newer "
        "Bloks-based two-factor verification flow, but the response did not include "
        "two_step_verification_context required for the Bloks two-factor fallback. "
        "Complete verification in the Instagram app or capture a fresh login response "
        "with the current app flow."))


async def test_attempt_ig_login_manual_challenge_shows_retry_not_code_field() -> None:
    """A native/Bloks challenge (no code possible) must route to kind='manual' and store
    the SAME client for a later no-code retry — not the code-input 'challenge' kind."""
    # instagrapi's ChallengeRequired is imported lazily inside the function under test;
    # raising the REAL class here (instagrapi is a hard dependency, see pyproject.toml)
    # exercises the exact except clause instead of a stand-in.
    from instagrapi.exceptions import ChallengeRequired

    from app.api._routes_channels import _attempt_ig_login, _ig_flows

    cl = _RaisingIGClient(ChallengeRequired(
        "Manual verification required via Instagram native challenge flow."))
    resp = await _attempt_ig_login(cl, ch_id=42, user="u", pw="p", fid="fid-manual")
    body = resp.body.decode()
    assert 'name="code"' not in body  # no code field — a code can't resolve this
    # nothing to click either: approving in the app is the whole job, so we poll for them
    assert "/ui/channels/42/ig/verify" in body and "hx-trigger=" in body
    assert _ig_flows["fid-manual"]["kind"] == "manual"
    assert _ig_flows["fid-manual"]["client"] is cl
    _ig_flows.pop("fid-manual", None)


async def test_attempt_ig_login_code_based_challenge_shows_code_field() -> None:
    """A regular email/SMS challenge (no 'Manual verification required' marker) must
    still show the code-input form — only the unresolvable case skips it."""
    from instagrapi.exceptions import ChallengeRequired

    from app.api._routes_channels import _attempt_ig_login, _ig_flows

    cl = _RaisingIGClient(ChallengeRequired("Instagram returned a legacy challenge flow."))
    resp = await _attempt_ig_login(cl, ch_id=42, user="u", pw="p", fid="fid-challenge")
    body = resp.body.decode()
    assert 'name="code"' in body
    assert _ig_flows["fid-challenge"]["kind"] == "challenge"
    _ig_flows.pop("fid-challenge", None)


async def test_attempt_ig_login_totp_2fa_shows_code_field() -> None:
    """A TOTP/SMS 2FA (two_factor_info flags a code method) shows the code-entry field."""
    from instagrapi.exceptions import TwoFactorRequired

    from app.api._routes_channels import _attempt_ig_login, _ig_flows

    cl = _RaisingIGClient(TwoFactorRequired("2FA required"),
                          last_json={"two_factor_info": {"totp_two_factor_on": True}})
    resp = await _attempt_ig_login(cl, ch_id=42, user="u", pw="p", fid="fid-2fa")
    body = resp.body.decode()
    assert 'name="code"' in body
    assert _ig_flows["fid-2fa"] == {"client": cl, "channel_id": 42, "kind": "2fa",
                                    "username": "u", "password": "p"}
    _ig_flows.pop("fid-2fa", None)


async def test_attempt_ig_login_device_approval_auto_completes_with_no_code_and_no_button() -> None:
    """A device-approval push (two_factor_info flags NO code method) must NOT demand a code —
    and must not demand a click either. The operator approves in the Instagram app; the panel
    finishes the login on its own (the itstep.kl bug: a push-approval login kept asking for a
    code that never comes, and then for a button press that just restarted the whole thing)."""
    from instagrapi.exceptions import TwoFactorRequired

    from app.api._routes_channels import _attempt_ig_login, _ig_flows

    cl = _RaisingIGClient(TwoFactorRequired("2FA required"),
                          last_json={"two_factor_info": {"totp_two_factor_on": False,
                                                         "sms_two_factor_on": False}})
    resp = await _attempt_ig_login(cl, ch_id=42, user="u", pw="p", fid="fid-dev")
    body = resp.body.decode()
    assert 'name="code"' not in body                       # no code to type on a push approval
    assert 'hx-post="/ui/channels/42/ig/verify"' in body   # it retries by itself…
    assert 'hx-trigger="load delay:' in body               # …on a timer, no click required
    assert '"attempt":"1"' in body                         # and carries the back-off counter
    assert _ig_flows["fid-dev"]["kind"] == "device"
    assert _ig_flows["fid-dev"]["password"] == "p"  # noqa: S105 — kept for the no-code relogin
    _ig_flows.pop("fid-dev", None)


async def test_unexpected_login_failure_is_logged_not_only_shown_on_screen(caplog) -> None:
    """A failure that is neither 2FA nor challenge used to go ONLY into the red box on the
    operator's screen, so the server logs showed a bare '[400] POST /accounts/login/' with no
    reason and a broken connect could not be diagnosed without asking them to read it out."""
    import logging

    from app.api._routes_channels import _attempt_ig_login

    cl = _RaisingIGClient(RuntimeError("Please wait a few minutes before you try again."))
    with caplog.at_level(logging.WARNING):
        resp = await _attempt_ig_login(cl, ch_id=42, user="u", pw="secret", fid="fid-err")  # noqa: S106
    assert "Please wait a few minutes" in caplog.text
    assert "RuntimeError" in caplog.text            # the exception TYPE, not just the message
    assert "secret" not in caplog.text              # never the password
    assert "Please wait a few minutes" in resp.body.decode()   # still shown to the operator


def test_device_poll_backs_off_and_stops_instead_of_hammering_login() -> None:
    """Every poll is a REAL Instagram login call, so the gap must grow and the polling must
    stop — repeated logins are a checkpoint/ban vector. Past the cap the operator gets a
    button back rather than an endless background login loop."""
    import re

    from app.api._i18n import _lang
    from app.api._ui_panels import _IG_POLL_DELAYS, _ch_ig_form

    _lang.set("en")
    assert list(_IG_POLL_DELAYS) == sorted(_IG_POLL_DELAYS)      # never tightens
    assert _IG_POLL_DELAYS[0] >= 5                                # never a fast tick

    delays = []
    for att in range(len(_IG_POLL_DELAYS)):
        html = _ch_ig_form(5, step="2fa", flow_id="f", kind="device", username="u", attempt=att)
        delays.append(int(re.search(r'hx-trigger="load delay:(\d+)s"', html).group(1)))
    assert delays == list(_IG_POLL_DELAYS)

    # past the cap: no more auto-retry, a manual button instead
    end = _ch_ig_form(5, step="2fa", flow_id="f", kind="device", username="u",
                      attempt=len(_IG_POLL_DELAYS))
    assert "hx-trigger=" not in end
    assert "<button" in end and 'name="flow_id"' in end


async def test_ig_verify_device_kind_relogins_without_a_code() -> None:
    """Clicking Continue on the device-approval step re-attempts login on the SAME client with
    no code (after the user approved on their phone) — and on success saves the session."""
    from starlette.requests import Request

    from app.api._routes_channels import _ig_flows, ig_login_verify

    # first attempt raises device-approval; the SAME client succeeds on the retry
    cl = _RaisingIGClient(None, last_json={"two_factor_info": {}})
    _ig_flows["fid-dev2"] = {"client": cl, "channel_id": 7, "kind": "device",
                             "username": "u", "password": "p"}  # noqa: S106 — test dummy

    async def _fake_branch(_s, _ch, _allowed):
        return 7

    import app.api._routes_channels as rc
    _orig = rc._channel_branch
    rc._channel_branch = _fake_branch

    class _Scope:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *_a):
            return False

    _orig_scope = rc.session_scope
    rc.session_scope = lambda: _Scope()
    # _ig_save writes to the DB; stub it to isolate the relogin behaviour
    _orig_save = rc._ig_save

    async def _fake_save(ch_id, dump):
        from fastapi.responses import HTMLResponse
        return HTMLResponse("saved")

    rc._ig_save = _fake_save
    try:
        req = Request({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                       "headers": []})
        resp = await ig_login_verify(7, req, flow_id="fid-dev2", code="", skip_code="")
        assert resp.body.decode() == "saved"               # relogin succeeded → session saved
        assert cl.login_calls[-1][2] == ""                 # retried with NO verification code
    finally:
        rc._channel_branch, rc.session_scope, rc._ig_save = _orig, _orig_scope, _orig_save
        _ig_flows.pop("fid-dev2", None)


def test_credential_panel_shows_session_after_connect() -> None:
    """Active session → connected view (not a blank login form again); otherwise form."""
    from app.api._i18n import _lang
    from app.api._ui_panels import channel_credential_html

    _lang.set("en")
    active = channel_credential_html(5, "instagram", "active")
    assert "Session active" in active
    assert "/ui/channels/5/form" in active  # reconnect button
    assert 'name="password"' not in active  # login form not re-shown

    fresh = channel_credential_html(5, "instagram", "none")
    assert 'name="password"' in fresh  # entry form shown when not connected


def test_ig_form_step1_shows_login_fields_and_collapsed_json() -> None:
    from app.api._i18n import _lang
    from app.api._ui_panels import _ch_ig_form

    _lang.set("en")
    html = _ch_ig_form(5)
    assert "Step 1 of 2" in html
    assert 'name="username"' in html and 'name="password"' in html
    # session-JSON import is a collapsed advanced option, not competing with the main path
    assert "<details" in html and 'name="session_json"' in html
    assert "Advanced" in html


def test_ig_form_step2_2fa_shows_2fa_specific_copy() -> None:
    """Real bug: a genuine 2FA prompt and an unrelated Instagram security CHALLENGE used to
    render as the exact same bare 'Code 2FA' field — turning off 2FA didn't stop the
    prompt because it wasn't 2FA. kind='2fa' must show 2FA-specific copy."""
    from app.api._i18n import _lang
    from app.api._ui_panels import _ch_ig_form

    _lang.set("en")
    html = _ch_ig_form(5, step="2fa", flow_id="abc", kind="2fa", username="itstep.ph")
    assert "Step 2 of 2" in html
    assert "@itstep.ph" in html
    assert "2FA Code" in html
    assert "authenticator app" in html
    assert "NOT a two-factor code" not in html
    assert 'value="abc"' in html
    assert "Start over" in html


def test_ig_form_step2_challenge_shows_challenge_specific_copy() -> None:
    """kind='challenge' must clearly say this ISN'T 2FA and point at email/SMS instead."""
    from app.api._i18n import _lang
    from app.api._ui_panels import _ch_ig_form

    _lang.set("en")
    html = _ch_ig_form(5, step="2fa", flow_id="xyz", kind="challenge", username="itstep.ph")
    assert "Step 2 of 2" in html
    assert "@itstep.ph" in html
    assert "Verification code" in html
    assert "NOT a two-factor code" in html
    assert "email or phone" in html
    assert "2FA Code" not in html


def test_ig_form_step2_never_puts_disabled_elt_or_indicator_on_the_form() -> None:
    """Real, empirically-confirmed htmx 1.9.12 bug: hx-disabled-elt="find button" and/or
    hx-indicator="find .htmx-indicator" on a <form> silently swallows the click of any
    OTHER descendant with its own independent hx-get/hx-post — no console error, the
    request just never leaves the browser. This broke 'Start over' and the app-confirm
    button (real report, 2026-07-09: clicking either did nothing). Every button in the
    step-2 forms (both 'manual' and '2fa'/'challenge') must carry hx-disabled-elt/
    hx-indicator on ITSELF instead of relying on the form to supply them."""
    import re

    from app.api._i18n import _lang
    from app.api._ui_panels import _ch_ig_form

    _lang.set("en")
    for html in (
        _ch_ig_form(5, step="2fa", flow_id="abc", kind="2fa", username="u"),
        _ch_ig_form(5, step="2fa", flow_id="abc", kind="challenge", username="u"),
        # the no-code kinds auto-poll first and only fall back to a form at the cap — cover
        # both, so the form that DOES appear there is still held to the same rule
        _ch_ig_form(5, step="2fa", flow_id="abc", kind="manual", username="u"),
        _ch_ig_form(5, step="2fa", flow_id="abc", kind="manual", username="u", attempt=99),
        _ch_ig_form(5, step="2fa", flow_id="abc", kind="device", username="u", attempt=99),
    ):
        form_match = re.search(r"<form\b[^>]*>", html)
        if form_match is not None:
            form_tag = form_match.group(0)
            assert "hx-disabled-elt" not in form_tag, form_tag
            assert "hx-indicator" not in form_tag, form_tag
        # every <button> must carry its own hx-disabled-elt
        for btn_tag in re.findall(r"<button\b[^>]*>", html):
            assert "hx-disabled-elt=\"this\"" in btn_tag, btn_tag


def test_ig_form_2fa_step_offers_skip_code_shortcut() -> None:
    """Instagram can fire the 2FA code prompt AND an in-app push for the same login
    attempt at once — if the operator already approved the push, they shouldn't have to
    type a code first just to reach the eventual manual retry."""
    from app.api._i18n import _lang
    from app.api._ui_panels import _ch_ig_form

    _lang.set("en")
    html = _ch_ig_form(5, step="2fa", flow_id="abc", kind="2fa", username="itstep.ph")
    assert "Already confirmed in the app" in html
    assert '"skip_code":"1"' in html


def test_ig_form_challenge_step_has_no_skip_code_shortcut() -> None:
    """The shortcut retries via plain re-login (needs the stored password) — a code-based
    email/SMS challenge flow never stores one, so it must not offer this button at all."""
    from app.api._i18n import _lang
    from app.api._ui_panels import _ch_ig_form

    _lang.set("en")
    html = _ch_ig_form(5, step="2fa", flow_id="xyz", kind="challenge", username="itstep.ph")
    assert "Already confirmed in the app" not in html


async def test_ig_login_verify_skip_code_retries_without_resolving_code(
    monkeypatch,
) -> None:
    """skip_code=1 must bypass _resolve_ig_code entirely and go straight to a plain
    re-login attempt on the same client — the code field's value is irrelevant."""
    from app.api import _routes_channels as routes_mod
    from app.api._routes_channels import _ig_flows, ig_login_verify

    cl = _RaisingIGClient(None)  # succeeds immediately
    _ig_flows["fid-skip"] = {"client": cl, "channel_id": 7, "kind": "2fa",
                             "username": "u", "password": "p"}

    async def _fake_channel_branch(_session, _ch_id, _allowed):
        return 1

    async def _fake_ig_save(ch_id, dump):
        from fastapi.responses import HTMLResponse
        return HTMLResponse(f"saved:{ch_id}:{dump}")

    monkeypatch.setattr(routes_mod, "_channel_branch", _fake_channel_branch)
    monkeypatch.setattr(routes_mod, "_ig_save", _fake_ig_save)

    class _Req:
        cookies: dict = {}
        headers: dict = {}

    resp = await ig_login_verify(7, _Req(), flow_id="fid-skip", code="", skip_code="1")
    assert cl.login_calls == [("u", "p", "")]  # plain retry, no verification_code applied
    assert "saved:7" in resp.body.decode()
    _ig_flows.pop("fid-skip", None)


# --- Registry --------------------------------------------------------------

# --- InstagrapiTransport own-id resolution ----------------------------------

class _FakeInstagrapiClient:
    """Simulates a Client rebuilt from set_settings() — user_id unset (login() never ran)."""

    def __init__(self) -> None:
        self.user_id = None


def test_transport_prefers_stored_ds_user_id_over_unset_client_user_id() -> None:
    """A client rebuilt from a stored session dump (the only path — we never call login()
    again) leaves client.user_id unset. Before the fix, own_id fell back to None and our
    own sent items were misread as direction='in', corrupting the dialog history with the
    bot's replies posing as the lead. authorization_data.ds_user_id (restored by
    set_settings()) must be used instead so our own items are always tagged 'out'."""
    from app.adapters.channels.transports import InstagrapiTransport

    transport = InstagrapiTransport(
        username="acc",
        session_settings={"authorization_data": {"ds_user_id": "76431725497"}},
    )
    transport._client = _FakeInstagrapiClient()  # bypass build_ig_client/instagrapi import
    import asyncio

    async def _run():
        from unittest.mock import patch

        with patch(
            "app.adapters.channels.ig_parse.item_content",
            return_value={"text": "hi", "link_url": None, "preview_url": None,
                          "media_url": None, "media_kind": None},
        ):
            with patch(
                "app.adapters.channels.transports._paged_threads",
                return_value=[{
                    "thread_id": "t1", "is_group": False,
                    "users": [{"pk": "76431725497"}, {"pk": "lead9"}],
                    "items": [{"item_type": "text", "text": "hi", "item_id": "it1",
                               "user_id": "76431725497", "timestamp": 1}],
                }],
            ):
                return await transport.fetch_threads()

    rows = asyncio.run(_run())
    assert rows[0]["direction"] == "out"


def test_transport_raises_when_own_id_unresolvable() -> None:
    """If neither ds_user_id nor client.user_id is available, the transport must FAIL the
    poll rather than default every item to direction='in' — that silent default filed 1401
    of our own sent messages as inbound lead messages in prod. Raising skips the poll (the
    worker logs + retries) instead of writing corrupt rows."""
    import asyncio

    import pytest

    from app.adapters.channels.transports import InstagrapiTransport

    transport = InstagrapiTransport(username="acc", session_settings={})  # no ds_user_id
    transport._client = _FakeInstagrapiClient()  # client.user_id is None too

    with pytest.raises(RuntimeError, match="cannot resolve own IG user id"):
        asyncio.run(transport.fetch_threads())


def test_transport_skips_item_with_no_user_id() -> None:
    """An item carrying no user_id can't be attributed to anyone; guessing 'in' is exactly
    how our own messages got mislabeled. It must be skipped, not stored as inbound."""
    import asyncio
    from unittest.mock import patch

    from app.adapters.channels.transports import InstagrapiTransport

    transport = InstagrapiTransport(
        username="acc",
        session_settings={"authorization_data": {"ds_user_id": "999"}},
    )
    transport._client = _FakeInstagrapiClient()

    def _echo(item):
        return {"text": item.get("text", ""), "link_url": None, "preview_url": None,
                "media_url": None, "media_kind": None}

    async def _run():
        with patch(
            "app.adapters.channels.ig_parse.item_content", side_effect=_echo,
        ), patch(
            "app.adapters.channels.transports._paged_threads",
            return_value=[{
                "thread_id": "t1", "is_group": False,
                "users": [{"pk": "999"}, {"pk": "lead9"}],
                "items": [
                    {"item_type": "text", "text": "ours", "item_id": "a",
                     "user_id": "999", "timestamp": 1},
                    {"item_type": "text", "text": "orphan", "item_id": "b",
                     "user_id": "", "timestamp": 2},  # no sender → must be skipped
                    {"item_type": "text", "text": "theirs", "item_id": "c",
                     "user_id": "lead9", "timestamp": 3},
                ],
            }],
        ):
            return await transport.fetch_threads()

    rows = asyncio.run(_run())
    texts = {r["text"]: r["direction"] for r in rows}
    assert texts == {"ours": "out", "theirs": "in"}  # orphan dropped, others correct


# --- Registry ----------------------------------------------------------------


def test_registry_maps_every_kind_to_its_adapter() -> None:
    assert REGISTRY == {
        ChannelKind.INSTAGRAM: InstagramAdapter,
        ChannelKind.WHATSAPP: WhatsAppAdapter,
        ChannelKind.META_BUSINESS: MetaBusinessAdapter,
    }
    for kind, cls in REGISTRY.items():
        assert cls.kind is kind  # class advertises the kind it is registered under


async def test_two_factor_classification_is_logged_at_a_readable_level(caplog) -> None:
    """The API runs at WARNING, so an INFO line here would never be read. Without this the
    classification is invisible and a code box shown for a push approval (or the reverse)
    can only be diagnosed by asking the operator what is on their screen."""
    import logging

    from app.api._routes_channels import _two_factor_kind

    class _Cl:
        last_json = {"two_factor_info": {"totp_two_factor_on": True, "sms_two_factor_on": False}}

    with caplog.at_level(logging.WARNING):
        assert _two_factor_kind(_Cl()) == "2fa"
    assert "classified as 2fa" in caplog.text
    assert "totp=True" in caplog.text
