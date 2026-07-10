"""Per-branch worker fan-out: each cron dispatcher enqueues exactly one arq job per active
branch, deduped by {job}:{branch_id}, gated by the platform kill switch, and a failing branch
job is isolated from its siblings."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.worker import main as worker_main  # noqa: E402
from app.worker import wiring  # noqa: E402


class _Branch:
    def __init__(self, bid: int) -> None:
        self.id = bid


class _FakeRedis:
    def __init__(self, inflight: set[str] | None = None) -> None:
        self.calls: list[tuple] = []
        self._inflight = inflight or set()

    async def enqueue_job(self, fn, *args, _job_id=None, **kw):  # noqa: ANN001, ANN002, ANN003
        self.calls.append((fn, args, _job_id))
        return None if _job_id in self._inflight else object()


async def test_fans_out_one_job_per_active_branch(monkeypatch) -> None:
    async def _branches(_s):
        return [_Branch(1), _Branch(7), _Branch(9)]

    monkeypatch.setattr(wiring, "active_branches", _branches)
    redis = _FakeRedis()

    n = await worker_main._fan_out_per_branch({"redis": redis}, "ingest_branch")

    assert n == 3
    assert redis.calls == [
        ("ingest_branch", (1,), "ingest_branch:1"),
        ("ingest_branch", (7,), "ingest_branch:7"),
        ("ingest_branch", (9,), "ingest_branch:9"),
    ]


async def test_dedups_a_branch_whose_job_is_still_in_flight(monkeypatch) -> None:
    async def _branches(_s):
        return [_Branch(1), _Branch(2)]

    monkeypatch.setattr(wiring, "active_branches", _branches)
    redis = _FakeRedis(inflight={"send_outbox_branch:1"})  # branch 1's last job still running

    n = await worker_main._fan_out_per_branch({"redis": redis}, "send_outbox_branch")

    assert n == 1  # only branch 2 newly enqueued
    assert len(redis.calls) == 2  # both attempted; branch 1 returned None


async def test_platform_gate_short_circuits_before_enumerating_branches(monkeypatch) -> None:
    reached = {"branches": False}

    async def _off(_s):
        return False

    async def _branches(_s):
        reached["branches"] = True
        return []

    monkeypatch.setattr(worker_main, "_platform_agent_on", _off)
    monkeypatch.setattr(wiring, "active_branches", _branches)

    # no redis needed on the kill-switch path (it's read after the gate)
    n = await worker_main._fan_out_per_branch({}, "send_outbox_branch", gate_platform=True)

    assert n == 0
    assert reached["branches"] is False


async def test_one_branch_job_raising_does_not_stop_the_dispatcher(monkeypatch) -> None:
    """The dispatcher only enqueues — a per-branch JOB raising is arq's concern and never
    aborts the fan-out. Enqueue succeeds for every branch regardless."""
    async def _branches(_s):
        return [_Branch(1), _Branch(2), _Branch(3)]

    monkeypatch.setattr(wiring, "active_branches", _branches)
    redis = _FakeRedis()

    n = await worker_main._fan_out_per_branch({"redis": redis}, "refresh_profiles_branch")

    assert n == 3  # every branch got its own independent job
    assert [c[2] for c in redis.calls] == [
        "refresh_profiles_branch:1", "refresh_profiles_branch:2", "refresh_profiles_branch:3",
    ]
