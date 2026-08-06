"""The sender callback: closed by default, deduplicated, and fast.

This endpoint can make Stepan message a real customer in the business's name. Their side sends
no signature today, so the first thing these tests pin is that an UNCONFIGURED endpoint refuses
everything — the state a fresh deploy is in, and the one where an open endpoint would be
handing strangers the ability to forge inbound messages.
"""
from __future__ import annotations

import hashlib
import hmac
import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.adapters.db.models import SenderInbound  # noqa: E402
from app.api._routes_sender import _reset_for_tests, recent, router  # noqa: E402
from app.config import settings  # noqa: E402

_SECRET = "shared-secret-value"  # noqa: S105 — fixture
_BODY = {
    "id": "123456",
    "external_id": "wamid.HBgLNjI4MTIzNDU2Nzg5",
    "from": "6281234567890",
    "message": "Halo kak, mau tanya harga",
    "chanel": "whats-app",
    "project_id": "3",
    "branch_id": "17",
    "conversation_id": "6281234567890",
    "chat_id": "987",
    "from_name": "Budi",
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):  # noqa: ANN001, ANN201
    _reset_for_tests()
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_SECRET", "")
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_IPS", "")
    settings.cache_clear()
    yield
    settings.cache_clear()
    _reset_for_tests()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)



class _Scope:
    """The route's session_scope, pinned to the test session."""

    def __init__(self, s) -> None:  # noqa: ANN001
        self._s = s

    async def __aenter__(self):  # noqa: ANN204
        return self._s

    async def __aexit__(self, *_a) -> bool:
        return False


def _db_client(monkeypatch, db_session) -> TestClient:  # noqa: ANN001
    """A client whose callback writes into the test session. Deduplication now lives in the
    unique index on sender_inbound, so these tests need a real table behind the route."""
    import app.api._routes_sender as rs

    monkeypatch.setattr(rs, "session_scope", lambda: _Scope(db_session))
    return _client()


def _signed(body: dict, secret: str) -> dict[str, str]:
    """The header their side would send: HMAC-SHA256 of the exact bytes posted."""
    from urllib.parse import urlencode  # noqa: PLC0415

    raw = urlencode(body).encode()
    return {"X-Sender-Signature":
            "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()}


async def test_an_unconfigured_endpoint_refuses_everything(monkeypatch, db_session) -> None:
    """The state of a fresh deploy. Accepting here would let anyone post a fake inbound and
    have Stepan answer a real customer as the business."""
    assert _db_client(monkeypatch, db_session).post(
        "/api/v1/sender/inbound-callback", data=_BODY).status_code == 403


async def test_a_correct_signature_is_accepted(  # noqa: ANN001
    monkeypatch, db_session,
) -> None:
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_SECRET", _SECRET)
    settings.cache_clear()

    r = _db_client(monkeypatch, db_session).post("/api/v1/sender/inbound-callback", data=_BODY,
                       headers=_signed(_BODY, _SECRET))

    assert r.status_code == 200
    assert len(recent()) == 1


async def test_a_signature_over_different_bytes_is_rejected(  # noqa: ANN001
    monkeypatch, db_session,
) -> None:
    """Signing something else and reusing the header is the obvious forgery — the HMAC has to
    cover the body that actually arrived."""
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_SECRET", _SECRET)
    settings.cache_clear()
    header = _signed({**_BODY, "message": "something else entirely"}, _SECRET)

    r = _db_client(monkeypatch, db_session).post(
        "/api/v1/sender/inbound-callback", data=_BODY, headers=header)

    assert r.status_code == 403
    assert recent() == []


async def test_a_bearer_token_is_accepted_for_a_side_that_cannot_sign(  # noqa: ANN001
    monkeypatch, db_session,
) -> None:
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_SECRET", _SECRET)
    settings.cache_clear()

    r = _db_client(monkeypatch, db_session).post("/api/v1/sender/inbound-callback", data=_BODY,
                       headers={"Authorization": f"Bearer {_SECRET}"})

    assert r.status_code == 200


async def test_a_wrong_bearer_token_is_rejected(  # noqa: ANN001
    monkeypatch, db_session,
) -> None:
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_SECRET", _SECRET)
    settings.cache_clear()

    r = _db_client(monkeypatch, db_session).post("/api/v1/sender/inbound-callback", data=_BODY,
                       headers={"Authorization": "Bearer not-the-secret"})

    assert r.status_code == 403


async def test_the_allowlist_lets_their_side_test_before_signing_exists(  # noqa: ANN001
    monkeypatch, db_session,
) -> None:
    """The interim: their UrlCallback sends no header at all today."""
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_IPS", "10.0.0.7, testclient")
    settings.cache_clear()

    r = _db_client(monkeypatch, db_session).post("/api/v1/sender/inbound-callback", data=_BODY)

    assert r.status_code == 200


async def test_an_address_not_on_the_list_is_refused(  # noqa: ANN001
    monkeypatch, db_session,
) -> None:
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_IPS", "10.0.0.7")
    settings.cache_clear()

    assert _db_client(monkeypatch, db_session).post("/api/v1/sender/inbound-callback",
                          data=_BODY).status_code == 403


async def test_a_repeated_message_is_ignored_but_still_answered_200(  # noqa: ANN001
    monkeypatch, db_session,
) -> None:
    """Their retries and ours must not fight: a duplicate is accepted and dropped, never 4xx.
    Answering a lead twice is the visible failure this prevents."""
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_IPS", "testclient")
    settings.cache_clear()
    c = _db_client(monkeypatch, db_session)

    first = c.post("/api/v1/sender/inbound-callback", data=_BODY)
    second = c.post("/api/v1/sender/inbound-callback", data=_BODY)

    assert (first.status_code, second.status_code) == (200, 200)
    assert len(recent()) == 1  # recorded once


async def test_a_different_message_from_the_same_lead_is_not_a_duplicate(  # noqa: ANN001
    monkeypatch, db_session,
) -> None:
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_IPS", "testclient")
    settings.cache_clear()
    c = _db_client(monkeypatch, db_session)

    c.post("/api/v1/sender/inbound-callback", data=_BODY)
    c.post("/api/v1/sender/inbound-callback",
           data={**_BODY, "external_id": "wamid.SECOND", "message": "berapa lama kursusnya?"})

    assert len(recent()) == 2


async def test_a_payload_without_an_external_id_is_accepted_and_flagged(  # noqa: ANN001
    monkeypatch, db_session,
) -> None:
    """Without it we cannot tell a retry from a new message. Refusing would lose the message
    entirely — their side does not retry — so accept it and make it visible instead."""
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_IPS", "testclient")
    settings.cache_clear()
    body = {k: v for k, v in _BODY.items() if k != "external_id"}

    r = _db_client(monkeypatch, db_session).post("/api/v1/sender/inbound-callback", data=body)

    assert r.status_code == 200
    assert recent()[0]["no_external_id"] is True


async def test_the_fields_we_agreed_on_survive_and_the_rest_is_dropped(  # noqa: ANN001
    monkeypatch, db_session,
) -> None:
    """Their side posts the whole row; the contract is the subset. The phone in `from` is the
    field the whole integration turns on — the same lead writes to us on Instagram too, and
    the two only merge into one lead if the number matches."""
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_IPS", "testclient")
    settings.cache_clear()

    _db_client(monkeypatch, db_session).post("/api/v1/sender/inbound-callback",
                   data={**_BODY, "status": "delivered", "provider_response_json": "{}",
                         "user_photo": "http://x/y.jpg"})

    got = recent()[0]
    assert got["from"] == "6281234567890"
    assert got["conversation_id"] == "6281234567890"
    assert got["chat_id"] == "987"
    assert "status" not in got
    assert "provider_response_json" not in got
    assert "user_photo" not in got


async def test_the_open_switch_accepts_an_unauthenticated_call(  # noqa: ANN001
    monkeypatch, db_session,
) -> None:
    """The integration window: their side tests delivery before we agree on a secret.

    Safe only because this endpoint records and nothing else — a forged payload starts no
    reply, reaches no customer and spends no model call."""
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_OPEN", "true")
    settings.cache_clear()

    r = _db_client(monkeypatch, db_session).post("/api/v1/sender/inbound-callback", data=_BODY)

    assert r.status_code == 200
    assert len(recent()) == 1


async def test_open_is_off_unless_someone_turns_it_on(monkeypatch, db_session) -> None:
    """A temporary hole has to be an explicit act, never a default. A fresh deploy, a restored
    config or a new environment must all come up closed."""
    assert settings().sender_callback_open is False


async def test_open_still_deduplicates(  # noqa: ANN001
    monkeypatch, db_session,
) -> None:
    """Being open does not make it careless: answering a lead twice is what the customer sees,
    and that guard must not depend on how the caller authenticated."""
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_OPEN", "true")
    settings.cache_clear()
    c = _db_client(monkeypatch, db_session)

    c.post("/api/v1/sender/inbound-callback", data=_BODY)
    c.post("/api/v1/sender/inbound-callback", data=_BODY)

    assert len(recent()) == 1


def test_the_callback_path_is_public_to_the_session_middleware() -> None:
    """Their side posts with no session cookie and never will have one.

    Public here does NOT mean unauthenticated — the route runs its own check and refuses
    everything when nothing is configured. But it has to REACH that check: behind the session
    middleware the POST returned 401 before our code saw it, which reads to the other side as
    "your callback is broken" rather than "you are not authorised". Caught on the live server
    minutes after deploying, by posting to it."""
    from app.api._auth import _is_public

    assert _is_public("/api/v1/sender/inbound-callback")
    # And the prefix must not open the rest of the API.
    assert not _is_public("/api/v1/leads")
    assert not _is_public("/ui/settings/panel")


def _open_client(monkeypatch, db_session) -> TestClient:  # noqa: ANN001
    """Auth is not what these tests are about; direction is."""
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_OPEN", "true")
    settings.cache_clear()
    return _db_client(monkeypatch, db_session)


async def test_a_managers_own_message_is_stored_as_outgoing(  # noqa: ANN001
    monkeypatch, db_session,
) -> None:
    """Their side can switch on callbacks for OUTGOING messages so we can see a human take
    over — including the echo of a manager typing in the WhatsApp app (Victor's spec, §4).

    It must be kept, since "a manager is engaged" is what should later stand the bot down —
    but kept as OUTGOING. Stored without that mark it is shaped exactly like a lead's message,
    and once replies are on Stepan would answer the manager: second voice in a conversation a
    human already owns, in front of the customer.

    Their echoes carry their own external_id, so this does not collide with the lead's turn —
    which is why the direction, not the key, is what has to carry the distinction.
    """
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_IPS", "testclient")
    settings.cache_clear()
    c = _db_client(monkeypatch, db_session)
    out = {**_BODY, "external_id": "wamid.ECHO", "message": "Halo kak, saya manager",
           "type_send": "out"}
    lead = {**_BODY, "external_id": "wamid.LEAD", "message": "berapa harganya?"}

    assert c.post("/api/v1/sender/inbound-callback", data=out).status_code == 200
    assert c.post("/api/v1/sender/inbound-callback", data=lead).status_code == 200

    rows = (await db_session.execute(select(SenderInbound))).scalars().all()
    by_id = {r.external_id: r.direction for r in rows}
    assert by_id == {"wamid.ECHO": "out", "wamid.LEAD": "in"}


async def test_an_inbound_message_is_still_taken_when_the_field_says_in(  # noqa: ANN001
    monkeypatch, db_session,
) -> None:
    body = {**_BODY, "external_id": "wamid.IN1", "message": "Halo, mau tanya",
            "type_send": "in"}

    assert _open_client(monkeypatch, db_session).post(
        "/api/v1/sender/inbound-callback", data=body).status_code == 200
    assert recent()[0]["message"] == "Halo, mau tanya"


async def test_a_callback_without_the_field_is_still_an_inbound(  # noqa: ANN001
    monkeypatch, db_session,
) -> None:
    """Today their side sends inbound only and no direction field at all. Absence must keep
    meaning "a lead wrote", or turning the option on would become the only way to work."""
    body = {**_BODY, "external_id": "wamid.IN2", "message": "masih ada tempat?"}

    assert _open_client(monkeypatch, db_session).post(
        "/api/v1/sender/inbound-callback", data=body).status_code == 200
    assert recent()[0]["message"] == "masih ada tempat?"
