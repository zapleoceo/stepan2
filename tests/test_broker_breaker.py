"""Circuit breaker for a down chat broker: the broker classifies a gateway-down failure,
a failed reply job trips the breaker, and the reply/follow-up fan-out then skips a tick
instead of stampeding a broker that is known to be down."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.adapters.llm.broker import _is_gateway_down  # noqa: E402
from app.worker import breaker, wiring  # noqa: E402
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


# ─── guard state in redis: recovery reopens it, not a timer ───

class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.enqueued: list[tuple] = []

    async def set(self, key, value, ex=None):  # noqa: ANN001
        self.store[key] = value

    async def get(self, key):  # noqa: ANN001
        return self.store.get(key)

    async def delete(self, key):  # noqa: ANN001
        self.store.pop(key, None)

    async def enqueue_job(self, fn, *args, _job_id=None, **kw):  # noqa: ANN001, ANN002, ANN003
        self.enqueued.append((fn, args, _job_id))
        return object()


async def test_trip_opens_and_clear_reopens_immediately() -> None:
    r = _FakeRedis()
    assert await breaker.is_open(r) is False               # closed by default
    await breaker.trip(r)
    assert await breaker.is_open(r) is True                 # open until recovery
    await breaker.clear(r)                                  # a successful call → reopen NOW
    assert await breaker.is_open(r) is False               # no waiting out a cooldown


async def test_clear_is_idempotent_when_not_tripped() -> None:
    r = _FakeRedis()
    await breaker.clear(r)                                  # must not raise
    assert await breaker.is_open(r) is False


async def test_is_open_is_false_when_redis_read_fails() -> None:
    class _Broken:
        async def get(self, _k):  # noqa: ANN001, ANN202
            raise RuntimeError("redis blip")

    assert await breaker.is_open(_Broken()) is False        # never freeze on a bad read


# ─── proactive fan-out (follow-ups) skips while the guard is open ───

class _Branch:
    def __init__(self, bid: int) -> None:
        self.id = bid


async def test_followup_fan_out_skips_when_guard_open(monkeypatch) -> None:
    async def _branches(_s):
        return [_Branch(1), _Branch(7)]

    monkeypatch.setattr(wiring, "active_branches", _branches)
    r = _FakeRedis()
    await breaker.trip(r)

    n = await worker_main._fan_out_per_branch(
        {"redis": r}, "schedule_followups_branch", gate_broker=True)

    assert n == 0 and r.enqueued == []                      # proactive work waits for recovery


async def test_followup_fan_out_runs_when_guard_closed(monkeypatch) -> None:
    async def _branches(_s):
        return [_Branch(1), _Branch(7)]

    monkeypatch.setattr(wiring, "active_branches", _branches)
    r = _FakeRedis()  # never tripped

    n = await worker_main._fan_out_per_branch(
        {"redis": r}, "schedule_followups_branch", gate_broker=True)
    assert n == 2


async def test_reply_fan_out_never_skips_it_sends_a_canary_instead(monkeypatch) -> None:
    # the reply path must NOT full-skip on a down broker — it fans out and each branch sends a
    # canary (see the reply_pending_branch test), so a healed broker is detected within a tick
    async def _branches(_s):
        return [_Branch(1)]

    monkeypatch.setattr(wiring, "active_branches", _branches)
    r = _FakeRedis()
    await breaker.trip(r)

    n = await worker_main._fan_out_per_branch({"redis": r}, "reply_pending_branch")
    assert n == 1                                           # gate_broker not set → not skipped
