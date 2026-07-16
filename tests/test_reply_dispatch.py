"""reply_pending is now a DISPATCHER: it enqueues one generate_one_reply ARQ job per awaiting
thread, deduped by _job_id=reply:{thread_id}, and does no broker work itself."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.adapters.db.models import Branch  # noqa: E402
from app.modules.settings.service import _parse  # noqa: E402
from app.worker import main as worker_main  # noqa: E402
from app.worker import wiring  # noqa: E402


class _FakeRedis:
    """Captures enqueue_job calls; returns None for a job_id already 'in flight' (ARQ dedup).
    Also implements the sorted-set ops generate_one_reply uses for its concurrency cap."""

    def __init__(self, inflight: set[str] | None = None, zcard: int = 1) -> None:
        self.calls: list[tuple] = []
        self._inflight = inflight or set()
        self._zcard = zcard

    async def enqueue_job(self, fn, *args, _job_id=None, **kw):  # noqa: ANN001, ANN002, ANN003
        self.calls.append((fn, args, _job_id))
        if _job_id in self._inflight:
            return None  # a job for this thread is already queued/running
        return object()  # a fresh Job

    async def zremrangebyscore(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        return 0

    async def zadd(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        return 1

    async def zcard(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        return self._zcard

    async def zrem(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        return 1

    async def get(self, *a, **k):  # noqa: ANN002, ANN003, ANN201 — breaker read (never tripped here)
        return None


async def _wire(monkeypatch, b, thread_ids):  # noqa: ANN001
    async def _platform_on(_s):
        return True

    async def _branches(_s):
        return [b]

    async def _settings(_s, _bid):
        return _parse({"agent_enabled_global": "true"})

    async def _awaiting(_s, _bid, limit=None):  # noqa: ANN001
        return list(thread_ids)

    monkeypatch.setattr(worker_main, "_platform_agent_on", _platform_on)
    monkeypatch.setattr(wiring, "active_branches", _branches)
    monkeypatch.setattr(worker_main, "get_settings", _settings)
    monkeypatch.setattr(wiring, "threads_awaiting_reply", _awaiting)


async def test_dispatches_one_job_per_awaiting_thread(db_session, monkeypatch) -> None:
    b = Branch(name="Q", lang="id")
    db_session.add(b)
    await db_session.flush()
    await _wire(monkeypatch, b, [10, 11])
    redis = _FakeRedis()

    n = await worker_main.reply_pending_branch({"redis": redis}, b.id)

    assert n == 2
    assert redis.calls == [
        ("generate_one_reply", (b.id, 10), "reply:10"),
        ("generate_one_reply", (b.id, 11), "reply:11"),
    ]


async def test_deduped_thread_is_not_counted(db_session, monkeypatch) -> None:
    b = Branch(name="Q", lang="id")
    db_session.add(b)
    await db_session.flush()
    await _wire(monkeypatch, b, [10, 11])
    redis = _FakeRedis(inflight={"reply:10"})  # thread 10 already has a job in flight

    n = await worker_main.reply_pending_branch({"redis": redis}, b.id)

    assert n == 1  # only thread 11 was newly enqueued; 10 was deduped
    assert len(redis.calls) == 2  # both attempted, one returned None


async def test_platform_kill_switch_stops_dispatch(db_session, monkeypatch) -> None:
    async def _off(_s):
        return False

    monkeypatch.setattr(worker_main, "_platform_agent_on", _off)
    redis = _FakeRedis()
    assert await worker_main.reply_pending({"redis": redis}) == 0
    assert redis.calls == []  # nothing enqueued when the platform switch is OFF


async def test_generate_one_reply_uses_the_generous_broker_budget(monkeypatch) -> None:
    """The whole point of the per-thread job: each reply waits the generous
    reply_broker_budget_s (not the old 90s tick cap). Lock that it's threaded into ReplyService."""
    from app.config import settings

    captured: dict = {}

    class _CaptureReply:
        def __init__(self, *a, broker_budget_s=None, **kw) -> None:  # noqa: ANN002, ANN003
            captured["budget"] = broker_budget_s

        async def decide(self, _tid):  # noqa: ANN202
            return None

    @asynccontextmanager
    async def _scope():
        yield object()

    async def _on(_s):
        return True

    async def _lock(_s, _t):
        return True

    async def _settings(_s, _b):
        return _parse({"agent_enabled_global": "true"})

    async def _kb(_s, b):  # noqa: ANN202
        return b

    monkeypatch.setattr(worker_main, "session_scope", _scope)
    monkeypatch.setattr(worker_main, "_platform_agent_on", _on)
    monkeypatch.setattr(worker_main, "BrokerLLM", lambda *a, **k: object())
    monkeypatch.setattr(wiring, "try_lock_thread", _lock)
    monkeypatch.setattr(worker_main, "get_settings", _settings)
    monkeypatch.setattr(worker_main, "effective_kb_branch", _kb)
    monkeypatch.setattr(worker_main, "_build_notifier", lambda _c: None)
    monkeypatch.setattr(worker_main, "KnowledgeService", lambda *a, **k: object())
    monkeypatch.setattr(worker_main, "ReplyService", _CaptureReply)

    await worker_main.generate_one_reply({"redis": _FakeRedis()}, 1, 42)
    assert captured["budget"] == settings().reply_broker_budget_s


async def test_generate_one_reply_skips_when_over_concurrency_cap(monkeypatch) -> None:
    """Over the slow-reply concurrency cap → return without touching the DB/broker (leaves
    worker slots for ingest/send); the thread is re-dispatched next tick."""
    from app.config import settings

    async def _must_not_run(*_a, **_k):
        raise AssertionError("must not reach the lock/broker when over the concurrency cap")

    monkeypatch.setattr(wiring, "try_lock_thread", _must_not_run)
    over = settings().reply_max_concurrency + 1
    result = await worker_main.generate_one_reply({"redis": _FakeRedis(zcard=over)}, 1, 42)
    assert result is False
