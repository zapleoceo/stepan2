"""The API→worker queue hop, which shipped with no coverage at all.

Everything else in the webhook path is tested with `enqueue` monkeypatched away, so a wrong
DSN parse, a pool built per request or a swallowed Redis failure would first be seen in
production as a 503 storm from the endpoint Meta disables for answering slowly.
"""
from __future__ import annotations

import asyncio

import pytest

from app.adapters import queue


class _FakeJob:
    pass


class _FakePool:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.calls: list[tuple] = []
        self.closed = 0
        self.duplicate = False

    async def enqueue_job(self, name, *args, _job_id=None):
        self.calls.append((name, args, _job_id))
        return None if self.duplicate else _FakeJob()

    async def aclose(self) -> None:
        self.closed += 1


@pytest.fixture(autouse=True)
async def _clean_pool():
    await queue.reset_pool()
    yield
    await queue.reset_pool()


def _wire(monkeypatch, made: list, *, delay: float = 0.0) -> None:
    async def _create_pool(redis_settings):
        if delay:
            await asyncio.sleep(delay)
        pool = _FakePool(f"{redis_settings.host}:{redis_settings.port}")
        made.append(pool)
        return pool

    monkeypatch.setattr("arq.create_pool", _create_pool)


async def test_the_pool_is_built_once_and_reused(monkeypatch) -> None:
    """One pool for the process lifetime: arq's create_pool opens a connection pool, and
    building one per webhook would spend more time on the socket than on the job."""
    made: list[_FakePool] = []
    _wire(monkeypatch, made)

    first, second = await queue.get_pool(), await queue.get_pool()

    assert first is second
    assert len(made) == 1


async def test_concurrent_first_calls_do_not_build_two_pools(monkeypatch) -> None:
    """Meta delivers in bursts, so the very first requests of a fresh container arrive
    together. Without the double-check inside the lock each of them opens its own pool and
    every one but the last is leaked."""
    made: list[_FakePool] = []
    _wire(monkeypatch, made, delay=0.02)

    pools = await asyncio.gather(*(queue.get_pool() for _ in range(5)))

    assert len({id(p) for p in pools}) == 1
    assert len(made) == 1


async def test_the_dsn_from_settings_is_what_the_pool_connects_to(monkeypatch) -> None:
    """A silently mis-parsed redis_url means jobs queued into a Redis the worker never reads —
    an endpoint that acks 200 and ingests nothing."""
    made: list[_FakePool] = []
    _wire(monkeypatch, made)
    monkeypatch.setattr(queue, "settings", lambda: _Settings("redis://example.test:6399/2"))

    pool = await queue.get_pool()

    assert pool.dsn == "example.test:6399"


async def test_enqueue_reports_a_job_already_in_flight(monkeypatch) -> None:
    """arq returns None when the job id is taken. The webhook logs that as Meta redelivering,
    so a caller that mistook it for success would report a queued job that never existed."""
    made: list[_FakePool] = []
    _wire(monkeypatch, made)

    assert await queue.enqueue("job", 1, [], job_id="metahook:1:m_a") is True
    made[0].duplicate = True
    assert await queue.enqueue("job", 1, [], job_id="metahook:1:m_a") is False
    assert made[0].calls[0] == ("job", (1, []), "metahook:1:m_a")


async def test_a_redis_failure_is_raised_not_swallowed(monkeypatch) -> None:
    """By design: the webhook must answer 503 so Meta keeps the delivery on its retry
    schedule. A swallowed error would ack 200 for a message that exists nowhere."""
    async def _boom(_redis_settings):
        raise OSError("connection refused")

    monkeypatch.setattr("arq.create_pool", _boom)

    with pytest.raises(OSError, match="connection refused"):
        await queue.enqueue("job", 1, [])


async def test_reset_pool_closes_the_connection_and_lets_the_next_call_reconnect(
    monkeypatch,
) -> None:
    """Wired into the API lifespan. Without it the sockets are dropped on the floor at
    shutdown and a Redis restart leaves the process holding a pool it can never replace."""
    made: list[_FakePool] = []
    _wire(monkeypatch, made)
    first = await queue.get_pool()

    await queue.reset_pool()
    second = await queue.get_pool()

    assert first.closed == 1
    assert second is not first


async def test_reset_pool_on_a_process_that_never_queued_anything_is_a_no_op() -> None:
    """The lifespan calls it unconditionally, including on an API container whose webhook
    was never hit."""
    await queue.reset_pool()


class _Settings:
    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url
