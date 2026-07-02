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


def test_meta_ingest_counts_entries(client: TestClient) -> None:
    payload = {
        "object": "page",
        "entry": [
            {"id": "100", "messaging": [{"sender": {"id": "1"}}]},
            {"id": "101", "messaging": [{"sender": {"id": "2"}}]},
        ],
    }
    body, headers = _signed(payload)
    resp = client.post(f"/webhooks/meta/{BRANCH_ID}", content=body, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 2}


def test_meta_ingest_empty_payload(client: TestClient) -> None:
    body, headers = _signed({})
    resp = client.post(f"/webhooks/meta/{BRANCH_ID}", content=body, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 0}


def test_meta_ingest_unsigned_rejected(client: TestClient) -> None:
    resp = client.post(f"/webhooks/meta/{BRANCH_ID}", json={"entry": []})
    assert resp.status_code == 403
