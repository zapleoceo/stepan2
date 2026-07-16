"""Circuit breaker for a down chat broker: the broker classifies a gateway-down failure,
a failed reply job trips the breaker, and the reply/follow-up fan-out then skips a tick
instead of stampeding a broker that is known to be down."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import time  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.adapters.llm.broker import _is_gateway_down  # noqa: E402
from app.worker import (
    breaker,  # noqa: E402
    wiring,  # noqa: E402
)
from app.worker import main as worker_main  # noqa: E402

# ─── gateway-down classification: back off the fleet vs a per-request error ───

def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://broker/v1/jobs")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req))


@pytest.mark.parametrize("code", [502, 503, 504])
def test_gateway_status_codes_are_broker_down(code: int) -> None:
    assert _is_gateway_down(_status_error(code)) is True


@pytest.mark.parametrize("code", [400, 401, 403, 429, 500])
def test_other_status_codes_are_not_broker_down(code: int) -> None:
    # a per-request problem must NOT freeze the whole fleet
    assert _is_gateway_down(_status_error(code)) is False


def test_connection_errors_are_broker_down() -> None:
    assert _is_gateway_down(httpx.ConnectError("refused")) is True
    assert _is_gateway_down(httpx.ReadTimeout("slow")) is True


def test_per_request_errors_are_not_broker_down() -> None:
    # a single job timing out on its budget, or a job status=error, is this request's problem
    assert _is_gateway_down(TimeoutError("chat:smart job still pending")) is False
    assert _is_gateway_down(RuntimeError("job failed: bad params")) is False


# ─── breaker state in redis ───

class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.enqueued: list[tuple] = []

    async def set(self, key, value, ex=None):  # noqa: ANN001
        self.store[key] = value

    async def get(self, key):  # noqa: ANN001
        return self.store.get(key)

    async def enqueue_job(self, fn, *args, _job_id=None, **kw):  # noqa: ANN001, ANN002, ANN003
        self.enqueued.append((fn, args, _job_id))
        return object()


async def test_trip_opens_then_reports_remaining_and_closes() -> None:
    r = _FakeRedis()
    assert await breaker.seconds_open(r) == 0.0            # closed by default
    await breaker.trip(r, cooldown_s=30)
    open_s = await breaker.seconds_open(r)
    assert 0 < open_s <= 30                                 # open, counting down
    # a deadline in the past reads as closed (fail-safe)
    r.store[breaker._KEY] = str(time.time() - 1)
    assert await breaker.seconds_open(r) == 0.0


async def test_malformed_breaker_value_reads_as_closed() -> None:
    r = _FakeRedis()
    r.store[breaker._KEY] = "not-a-number"
    assert await breaker.seconds_open(r) == 0.0             # never freeze on a bad read


# ─── the fan-out honours the breaker ───

class _Branch:
    def __init__(self, bid: int) -> None:
        self.id = bid


async def test_fan_out_skips_when_breaker_open(monkeypatch) -> None:
    async def _branches(_s):
        return [_Branch(1), _Branch(7)]

    monkeypatch.setattr(wiring, "active_branches", _branches)
    r = _FakeRedis()
    await breaker.trip(r, cooldown_s=60)

    n = await worker_main._fan_out_per_branch(
        {"redis": r}, "reply_pending_branch", gate_broker=True)

    assert n == 0
    assert r.enqueued == []                                 # nothing fanned out into a dead broker


async def test_fan_out_proceeds_when_breaker_closed(monkeypatch) -> None:
    async def _branches(_s):
        return [_Branch(1), _Branch(7)]

    monkeypatch.setattr(wiring, "active_branches", _branches)
    r = _FakeRedis()  # never tripped

    n = await worker_main._fan_out_per_branch(
        {"redis": r}, "reply_pending_branch", gate_broker=True)

    assert n == 2
    assert [c[2] for c in r.enqueued] == ["reply_pending_branch:1", "reply_pending_branch:7"]


async def test_ungated_fan_out_ignores_the_breaker(monkeypatch) -> None:
    # ingest/send never call the chat broker, so they must run even while it is down
    async def _branches(_s):
        return [_Branch(1)]

    monkeypatch.setattr(wiring, "active_branches", _branches)
    r = _FakeRedis()
    await breaker.trip(r, cooldown_s=60)

    n = await worker_main._fan_out_per_branch({"redis": r}, "ingest_branch")  # gate_broker=False
    assert n == 1
