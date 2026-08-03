"""API smoke tests — app must import and serve /healthz with no live DB/redis.
conftest sets STEPAN2_DATABASE_URL (sqlite) + STEPAN2_SECRET_KEY before import."""
import hashlib
import hmac
import json
import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402

BRANCH_ID = 7
VERIFY_TOKEN = f"verify-{BRANCH_ID}-" + "ABCDEF123456"  # test fixture, not a real secret
APP_SECRET = "app-secret-" + "XYZ789"  # test fixture, not a real secret
os.environ[f"STEPAN2_META_VERIFY_TOKEN_{BRANCH_ID}"] = VERIFY_TOKEN
os.environ[f"STEPAN2_META_APP_SECRET_{BRANCH_ID}"] = APP_SECRET


def _signed(payload: dict) -> tuple[bytes, dict[str, str]]:
    """Body + valid X-Hub-Signature-256 header for the test branch's app secret."""
    body = json.dumps(payload).encode()
    sig = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_healthz_ok(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["service"] == "stepan2"


def test_meta_verify_echoes_challenge_on_match(client: TestClient) -> None:
    resp = client.get(
        f"/webhooks/meta/{BRANCH_ID}",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "1234567890",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "1234567890"


def test_meta_verify_rejects_bad_token(client: TestClient) -> None:
    resp = client.get(
        f"/webhooks/meta/{BRANCH_ID}",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "1234567890",
        },
    )
    assert resp.status_code == 403


def test_meta_verify_rejects_unknown_branch(client: TestClient) -> None:
    resp = client.get(
        "/webhooks/meta/999999",
        params={"hub.verify_token": "anything", "hub.challenge": "x"},
    )
    assert resp.status_code == 403


def _message_payload(*mids: str) -> dict:
    return {
        "object": "page",
        "entry": [{
            "id": "PAGE1",
            "messaging": [
                {"sender": {"id": f"PSID{i}"}, "recipient": {"id": "PAGE1"},
                 "timestamp": 1_754_200_000_000,
                 "message": {"mid": mid, "text": "halo"}}
                for i, mid in enumerate(mids)
            ],
        }],
    }


@pytest.fixture
def queued(monkeypatch) -> list[tuple]:
    """Capture what the handler hands to the worker instead of talking to a real Redis."""
    calls: list[tuple] = []

    async def _fake(job_name: str, *args: object, job_id: str | None = None) -> bool:
        calls.append((job_name, args, job_id))
        return True

    monkeypatch.setattr("app.api.webhooks.enqueue", _fake)
    return calls


def test_meta_ingest_enqueues_the_messages_it_acks(
    client: TestClient, queued: list[tuple],
) -> None:
    """The handler used to count the entries, answer 200 and DISCARD the payload — its own
    docstring claimed ingest was enqueued downstream and nothing was. Every DM to an official
    connector was invisible until the next poll."""
    body, headers = _signed(_message_payload("m_aaa", "m_bbb"))
    resp = client.post(f"/webhooks/meta/{BRANCH_ID}", content=body, headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {"accepted": 2}
    [(job_name, args, job_id)] = queued
    assert job_name == "ingest_meta_webhook"
    assert args[0] == BRANCH_ID
    assert [e["mid"] for e in args[1]] == ["m_aaa", "m_bbb"]
    # Meta retries a delivery it thinks failed; a stable job id makes the retry a no-op.
    assert job_id == f"metahook:{BRANCH_ID}:m_aaa"


def test_meta_ingest_answers_503_when_the_queue_is_unreachable(
    client: TestClient, monkeypatch,
) -> None:
    """A 200 would end Meta's retries for a message we never stored. 503 keeps the delivery on
    their retry schedule, which is the only durability this endpoint has."""
    async def _boom(*_a: object, **_kw: object) -> bool:
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.api.webhooks.enqueue", _boom)
    body, headers = _signed(_message_payload("m_aaa"))
    resp = client.post(f"/webhooks/meta/{BRANCH_ID}", content=body, headers=headers)
    assert resp.status_code == 503


def test_meta_ingest_queues_nothing_for_an_event_with_no_message(
    client: TestClient, queued: list[tuple],
) -> None:
    """Delivery/read receipts ride the same subscription. They are acked and dropped, not
    counted — `accepted` now means messages queued for ingest."""
    payload = {"object": "page", "entry": [
        {"id": "PAGE1", "messaging": [{"sender": {"id": "1"}, "delivery": {"watermark": 1}}]}]}
    body, headers = _signed(payload)
    resp = client.post(f"/webhooks/meta/{BRANCH_ID}", content=body, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 0}
    assert queued == []


def test_meta_ingest_empty_payload(client: TestClient) -> None:
    body, headers = _signed({})
    resp = client.post(f"/webhooks/meta/{BRANCH_ID}", content=body, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 0}


def test_meta_ingest_unsigned_rejected(client: TestClient) -> None:
    resp = client.post(f"/webhooks/meta/{BRANCH_ID}", json={"entry": []})
    assert resp.status_code == 403
