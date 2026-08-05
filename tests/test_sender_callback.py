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


def _signed(body: dict, secret: str) -> dict[str, str]:
    """The header their side would send: HMAC-SHA256 of the exact bytes posted."""
    from urllib.parse import urlencode  # noqa: PLC0415

    raw = urlencode(body).encode()
    return {"X-Sender-Signature":
            "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()}


def test_an_unconfigured_endpoint_refuses_everything() -> None:
    """The state of a fresh deploy. Accepting here would let anyone post a fake inbound and
    have Stepan answer a real customer as the business."""
    assert _client().post("/api/v1/sender/inbound-callback", data=_BODY).status_code == 403


def test_a_correct_signature_is_accepted(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_SECRET", _SECRET)
    settings.cache_clear()

    r = _client().post("/api/v1/sender/inbound-callback", data=_BODY,
                       headers=_signed(_BODY, _SECRET))

    assert r.status_code == 200
    assert len(recent()) == 1


def test_a_signature_over_different_bytes_is_rejected(monkeypatch) -> None:  # noqa: ANN001
    """Signing something else and reusing the header is the obvious forgery — the HMAC has to
    cover the body that actually arrived."""
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_SECRET", _SECRET)
    settings.cache_clear()
    header = _signed({**_BODY, "message": "something else entirely"}, _SECRET)

    r = _client().post("/api/v1/sender/inbound-callback", data=_BODY, headers=header)

    assert r.status_code == 403
    assert recent() == []


def test_a_bearer_token_is_accepted_for_a_side_that_cannot_sign(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_SECRET", _SECRET)
    settings.cache_clear()

    r = _client().post("/api/v1/sender/inbound-callback", data=_BODY,
                       headers={"Authorization": f"Bearer {_SECRET}"})

    assert r.status_code == 200


def test_a_wrong_bearer_token_is_rejected(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_SECRET", _SECRET)
    settings.cache_clear()

    r = _client().post("/api/v1/sender/inbound-callback", data=_BODY,
                       headers={"Authorization": "Bearer not-the-secret"})

    assert r.status_code == 403


def test_the_allowlist_lets_their_side_test_before_signing_exists(monkeypatch) -> None:  # noqa: ANN001
    """The interim: their UrlCallback sends no header at all today."""
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_IPS", "10.0.0.7, testclient")
    settings.cache_clear()

    r = _client().post("/api/v1/sender/inbound-callback", data=_BODY)

    assert r.status_code == 200


def test_an_address_not_on_the_list_is_refused(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_IPS", "10.0.0.7")
    settings.cache_clear()

    assert _client().post("/api/v1/sender/inbound-callback",
                          data=_BODY).status_code == 403


def test_a_repeated_message_is_ignored_but_still_answered_200(monkeypatch) -> None:  # noqa: ANN001
    """Their retries and ours must not fight: a duplicate is accepted and dropped, never 4xx.
    Answering a lead twice is the visible failure this prevents."""
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_IPS", "testclient")
    settings.cache_clear()
    c = _client()

    first = c.post("/api/v1/sender/inbound-callback", data=_BODY)
    second = c.post("/api/v1/sender/inbound-callback", data=_BODY)

    assert (first.status_code, second.status_code) == (200, 200)
    assert len(recent()) == 1  # recorded once


def test_a_different_message_from_the_same_lead_is_not_a_duplicate(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_IPS", "testclient")
    settings.cache_clear()
    c = _client()

    c.post("/api/v1/sender/inbound-callback", data=_BODY)
    c.post("/api/v1/sender/inbound-callback",
           data={**_BODY, "external_id": "wamid.SECOND", "message": "berapa lama kursusnya?"})

    assert len(recent()) == 2


def test_a_payload_without_an_external_id_is_accepted_and_flagged(monkeypatch) -> None:  # noqa: ANN001
    """Without it we cannot tell a retry from a new message. Refusing would lose the message
    entirely — their side does not retry — so accept it and make it visible instead."""
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_IPS", "testclient")
    settings.cache_clear()
    body = {k: v for k, v in _BODY.items() if k != "external_id"}

    r = _client().post("/api/v1/sender/inbound-callback", data=body)

    assert r.status_code == 200
    assert recent()[0]["no_external_id"] is True


def test_the_fields_we_agreed_on_survive_and_the_rest_is_dropped(monkeypatch) -> None:  # noqa: ANN001
    """Their side posts the whole row; the contract is the subset. The phone in `from` is the
    field the whole integration turns on — the same lead writes to us on Instagram too, and
    the two only merge into one lead if the number matches."""
    monkeypatch.setenv("STEPAN2_SENDER_CALLBACK_IPS", "testclient")
    settings.cache_clear()

    _client().post("/api/v1/sender/inbound-callback",
                   data={**_BODY, "status": "delivered", "provider_response_json": "{}",
                         "user_photo": "http://x/y.jpg"})

    got = recent()[0]
    assert got["from"] == "6281234567890"
    assert got["conversation_id"] == "6281234567890"
    assert got["chat_id"] == "987"
    assert "status" not in got
    assert "provider_response_json" not in got
    assert "user_photo" not in got
