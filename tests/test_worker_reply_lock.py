"""Advisory-lock guard against the reply_pending race: two overlapping worker ticks must
not both call the LLM for the same thread (real incident: thread 1585 was billed twice,
47s apart, because the NOT-EXISTS pending guard alone left a TOCTOU gap)."""
from __future__ import annotations

from contextlib import asynccontextmanager

from app.worker import main as worker_main
from app.worker import wiring


async def test_try_lock_thread_is_noop_off_postgres(db_session) -> None:
    """Sqlite (tests, and any non-Postgres deploy) always acquires — no locking needed."""
    assert await wiring.try_lock_thread(db_session, 1) is True
    assert await wiring.try_lock_thread(db_session, 1) is True  # repeatable, not a real lock


async def test_reply_thread_skips_without_calling_llm_when_lock_denied(monkeypatch) -> None:
    """When another tick already holds the thread's lock, _reply_thread must return False
    before ever calling the LLM — that's the whole point of checking the lock first."""

    class _NeverCalledLLM:
        async def chat(self, *_a, **_kw):
            raise AssertionError("LLM must not be called when the lock is not acquired")

    @asynccontextmanager
    async def _fake_scope():
        yield object()  # never touched — try_lock_thread is faked below and short-circuits

    async def _denied(_session, _thread_id) -> bool:
        return False

    monkeypatch.setattr(worker_main, "session_scope", _fake_scope)
    monkeypatch.setattr(wiring, "try_lock_thread", _denied)

    result = await worker_main._reply_thread(1, 42, _NeverCalledLLM())
    assert result is False
