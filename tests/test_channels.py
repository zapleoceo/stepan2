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
            occurred_at=datetime(2026, 6, 1, tzinfo=UTC),
            product_hint="vibe_coding",
        )
    ]


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
    assert m.occurred_at == datetime.fromtimestamp(1_750_000_000, tz=UTC)


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
            occurred_at=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
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


# --- Registry --------------------------------------------------------------

def test_registry_maps_every_kind_to_its_adapter() -> None:
    assert REGISTRY == {
        ChannelKind.INSTAGRAM: InstagramAdapter,
        ChannelKind.WHATSAPP: WhatsAppAdapter,
        ChannelKind.META_BUSINESS: MetaBusinessAdapter,
    }
    for kind, cls in REGISTRY.items():
        assert cls.kind is kind  # class advertises the kind it is registered under
