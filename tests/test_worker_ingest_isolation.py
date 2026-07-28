"""One channel's bad day must never cost another channel its messages.

`_ingest_channel` is where every poll actually lands, and it was entirely uncovered — 33% of
worker/main.py was, and this is the part of it that decides whether a lead's message reaches
us at all. Six ways a single channel can fail, each swallowed on purpose so the loop keeps
going; the failure mode they guard against is the quiet one, where an exception on branch 1's
Instagram silently ends the cycle and branch 8 is never polled at all.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import contextlib  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.worker import main as worker_main  # noqa: E402
from app.worker import wiring  # noqa: E402


class _Channel:
    def __init__(self, *, active: bool = True) -> None:
        self.id = 11
        self.is_active = active
        self.kind = "instagram"


class _Session:
    """Just enough session for the paths under test: `get` returns the channel it was set up
    with, and nothing else is touched before the code under test returns."""

    def __init__(self, channel: _Channel | None) -> None:
        self._channel = channel

    async def get(self, _model, _pk):  # noqa: ANN001, ANN202
        return self._channel


def _patch_session(monkeypatch, session: _Session) -> None:  # noqa: ANN001
    @contextlib.asynccontextmanager
    async def _scope():  # noqa: ANN202
        yield session

    monkeypatch.setattr(worker_main, "session_scope", _scope)


async def test_an_inactive_or_deleted_channel_is_skipped(monkeypatch) -> None:  # noqa: ANN001
    _patch_session(monkeypatch, _Session(None))
    assert await worker_main._ingest_channel(1, 11) == 0

    _patch_session(monkeypatch, _Session(_Channel(active=False)))
    assert await worker_main._ingest_channel(1, 11) == 0


@pytest.mark.parametrize("exc", [NotImplementedError("no port"), KeyError("creds"),
                                RuntimeError("bad config")])
async def test_a_channel_whose_transport_cannot_be_built_is_skipped(monkeypatch, exc) -> None:  # noqa: ANN001
    """A misconfigured connector is a standing condition, not an incident: it must not raise
    into the cycle every two minutes, and it must not stop the channels that DO work."""
    _patch_session(monkeypatch, _Session(_Channel()))

    async def _build(_s, _c):
        raise exc

    monkeypatch.setattr(wiring, "build_channel_port", _build)
    assert await worker_main._ingest_channel(1, 11) == 0


async def test_an_unhealthy_channel_is_not_polled(monkeypatch) -> None:  # noqa: ANN001
    """A checkpointed or expired IG session is frozen until someone re-logs in. Polling it
    anyway is how a soft block becomes a hard one."""
    _patch_session(monkeypatch, _Session(_Channel()))
    polled = False

    class _Port:
        async def fetch_inbound(self):  # noqa: ANN202
            nonlocal polled
            polled = True
            return []

    async def _build(_s, _c):
        return _Port()

    async def _unhealthy(*_a, **_k):
        return False

    monkeypatch.setattr(wiring, "build_channel_port", _build)
    monkeypatch.setattr(worker_main, "_healthy", _unhealthy)

    assert await worker_main._ingest_channel(1, 11) == 0
    assert not polled


async def test_a_failing_fetch_skips_only_that_channel(monkeypatch) -> None:  # noqa: ANN001
    """The transport raises on purpose when it cannot resolve our own account id — better a
    missed poll than misreading our own sends as inbound."""
    _patch_session(monkeypatch, _Session(_Channel()))

    class _Port:
        async def fetch_inbound(self):  # noqa: ANN202
            raise RuntimeError("own ig id unresolvable")

    async def _build(_s, _c):
        return _Port()

    async def _healthy(*_a, **_k):
        return True

    monkeypatch.setattr(wiring, "build_channel_port", _build)
    monkeypatch.setattr(worker_main, "_healthy", _healthy)
    assert await worker_main._ingest_channel(1, 11) == 0


async def test_a_concurrent_run_storing_the_same_rows_is_a_no_op(monkeypatch) -> None:  # noqa: ANN001
    """A slow poll can overrun its cron and overlap the next one. Both runs see the same
    messages; the (channel_id, external_id) unique constraint is what makes the loser
    harmless, and this is the branch that treats it as expected rather than as an error."""
    async def _boom():
        raise IntegrityError("insert", {}, Exception("duplicate key"))

    @contextlib.asynccontextmanager
    async def _scope():  # noqa: ANN202
        await _boom()
        yield None

    monkeypatch.setattr(worker_main, "session_scope", _scope)
    assert await worker_main._ingest_channel(1, 11) == 0


async def test_an_unexpected_failure_is_logged_and_contained(monkeypatch) -> None:  # noqa: ANN001
    """The catch-all. Whatever else goes wrong on this channel, the caller's loop over the
    branch's other channels has to keep running."""
    @contextlib.asynccontextmanager
    async def _scope():  # noqa: ANN202
        raise ValueError("something nobody predicted")
        yield None

    monkeypatch.setattr(worker_main, "session_scope", _scope)
    assert await worker_main._ingest_channel(1, 11) == 0


async def test_one_dead_channel_does_not_stop_its_siblings(monkeypatch) -> None:  # noqa: ANN001
    """The whole point, stated end to end: three channels, the middle one broken, and the
    third still gets polled."""
    seen: list[int] = []

    class _Ch:
        def __init__(self, cid: int) -> None:
            self.id = cid

    async def _one(_branch_id: int, channel_id: int) -> int:
        seen.append(channel_id)
        return 0 if channel_id == 2 else 5  # 2 already swallowed its own failure

    async def _three(_s, _b):
        return [_Ch(1), _Ch(2), _Ch(3)]

    @contextlib.asynccontextmanager
    async def _scope():  # noqa: ANN202
        yield None

    monkeypatch.setattr(worker_main, "session_scope", _scope)
    monkeypatch.setattr(wiring, "active_channels", _three)
    monkeypatch.setattr(worker_main, "_ingest_channel", _one)
    monkeypatch.setattr(worker_main, "_INGEST_JITTER_S", 0)

    assert await worker_main.ingest_branch({}, 1) == 10
    assert seen == [1, 2, 3]
