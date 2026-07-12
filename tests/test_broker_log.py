"""broker_log: every broker call is recorded; the /settings/log page reads & renders it."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from datetime import UTC, datetime, timedelta  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402

from app.adapters.db.models import Branch, BrokerLog  # noqa: E402
from app.adapters.llm import broker as broker_mod  # noqa: E402
from app.api._query import (  # noqa: E402
    fetch_branch_tz,
    fetch_broker_log,
    fetch_turn_histogram,
    log_window_keys,
)
from app.api._ui_panels import broker_log_panel_html  # noqa: E402

_NOW = datetime.now(UTC).replace(tzinfo=None)


class _FakeResp:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.headers: dict[str, str] = {}

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("boom", request=None, response=self)  # type: ignore[arg-type]

    @property
    def text(self) -> str:
        return "err-body"


class _FakeClient:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def post(self, *a: object, **k: object) -> _FakeResp:
        return self._resp


class _JobFakeClient(_FakeClient):
    """Async job flow: post() returns a job submit, get() returns the done result."""

    def __init__(self, submit: _FakeResp, done: _FakeResp) -> None:
        super().__init__(submit)
        self._done = done

    async def get(self, *a: object, **k: object) -> _FakeResp:
        return self._done


@pytest.mark.asyncio
async def test_chat_logs_ok_row(monkeypatch) -> None:
    submit = _FakeResp({"job_id": 1, "poll_after_s": 1}, status=202)
    done = _FakeResp({"status": "done", "text": "hi", "model": "x/gpt", "tokens_in": 10,
                      "tokens_out": 4, "provider": "openai", "cost_usd": 0.0,
                      "request_id": "req-1"})
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient",
                        lambda **k: _JobFakeClient(submit, done))

    async def _instant(_s):  # noqa: ANN001, ANN202
        return None
    monkeypatch.setattr(broker_mod.asyncio, "sleep", _instant)
    calls: list[dict[str, Any]] = []

    async def _capture(cap, wf, tid, bid, meta, *, ok, error=None):  # noqa: ANN001, ANN002
        calls.append({"cap": cap, "wf": wf, "tid": tid, "bid": bid, "ok": ok, "err": error})

    monkeypatch.setattr(broker_mod, "_log_call", _capture)
    out, meta = await broker_mod.BrokerLLM(base_url="http://x", project_key="k").chat(
        [{"role": "user", "content": "hi"}], capability="chat:smart",
        workflow="reply", thread_id=42, branch_id=7)
    assert out == "hi"
    assert calls == [{"cap": "chat:smart", "wf": "reply", "tid": 42, "bid": 7,
                      "ok": True, "err": None}]


@pytest.mark.asyncio
async def test_chat_logs_failure_row(monkeypatch) -> None:
    resp = _FakeResp({}, status=400)
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: _FakeClient(resp))
    calls: list[dict[str, Any]] = []

    async def _capture(cap, wf, tid, bid, meta, *, ok, error=None):  # noqa: ANN001, ANN002
        calls.append({"ok": ok, "err": error})

    monkeypatch.setattr(broker_mod, "_log_call", _capture)
    with pytest.raises(Exception):  # noqa: B017, PT011 — HTTPStatusError bubbles up
        await broker_mod.BrokerLLM(base_url="http://x", project_key="k").chat(
            [{"role": "user", "content": "hi"}], workflow="reply", thread_id=1)
    assert len(calls) == 1 and calls[0]["ok"] is False


async def test_fetch_and_render_broker_log(db_session) -> None:
    for i in range(3):
        db_session.add(BrokerLog(
            request_id=f"r{i}", branch_id=7, thread_id=100 + i, kind="reply",
            capability="chat:smart", provider="p", model="vendor/model-x",
            tokens_in=100, tokens_out=20, cost_usd=0.0, latency_ms=1500, ok=True,
            created_at=_NOW - timedelta(minutes=i)))
    db_session.add(BrokerLog(  # a failure in another branch — must be filtered out for b7
        branch_id=9, kind="translate", capability="chat:fast", ok=False,
        error="429 too many", created_at=_NOW))
    await db_session.flush()

    rows, total = await fetch_broker_log(db_session, [7], page=0, size=50)
    assert total == 3 and len(rows) == 3
    assert rows[0].request_id == "r2"  # highest id (last inserted) first

    all_rows, all_total = await fetch_broker_log(db_session, None, page=0, size=50)
    assert all_total == 4  # owner sees the other branch too

    html = broker_log_panel_html(list(all_rows), 0, 50, all_total)
    assert "model-x" in html                 # short model name, vendor stripped
    assert "free" in html                    # zero cost → free
    assert "1.5s" in html                     # latency in seconds
    assert "fail" in html                     # failure row flagged


async def test_log_renders_all_rows_in_the_viewers_tz(db_session) -> None:
    """Owner viewing the log sees every row in THEIR OWN tz (set_render_tz, from the browser),
    regardless of which branch the call belongs to — one viewer offset, not per-branch."""
    from app.api._ui_html import set_render_tz

    jakarta = Branch(name="Jakarta", tz_offset_h=7)
    utc_branch = Branch(name="UTC", tz_offset_h=0)
    db_session.add_all([jakarta, utc_branch])
    await db_session.flush()

    when = datetime(2026, 7, 3, 10, 0, 0)  # same UTC instant for both calls
    db_session.add(BrokerLog(branch_id=jakarta.id, kind="reply", capability="chat:smart",
                             ok=True, created_at=when))
    db_session.add(BrokerLog(branch_id=utc_branch.id, kind="reply", capability="chat:smart",
                             ok=True, created_at=when))
    await db_session.flush()

    rows, total = await fetch_broker_log(db_session, None, page=0, size=50)
    tz_by_branch = await fetch_branch_tz(db_session, [jakarta.id, utc_branch.id])
    set_render_tz(3)  # viewer at UTC+3

    html = broker_log_panel_html(list(rows), 0, 50, total, tz_by_branch)
    # both rows (Jakarta call + UTC-branch call) render at the VIEWER's +3, not each branch's tz
    assert html.count("07-03 13:00:00") == 2  # 10:00 UTC + 3h, for BOTH rows
    assert "17:00:00" not in html             # no per-branch (+7) shift anymore
    set_render_tz(0)


async def test_log_groups_one_threads_calls_into_a_turn_with_end_to_end(db_session) -> None:
    """The calls of one reply (embed + chat + guard verify + a regen) all share a thread and
    happen within seconds — they render as ONE turn block with an end-to-end wall-clock."""
    base = _NOW
    # one turn on thread 500: 4 calls spanning ~12s (0s..10s start + 2s last latency)
    for i, (off, lat) in enumerate([(0, 300), (3, 5000), (8, 200), (10, 2000)]):
        db_session.add(BrokerLog(
            request_id=f"t{i}", branch_id=7, thread_id=500, kind="reply",
            capability="chat:smart", model="v/m", tokens_in=100, tokens_out=20,
            cost_usd=0.0, latency_ms=lat, ok=True, created_at=base + timedelta(seconds=off)))
    # a SEPARATE turn on the same thread, an hour later → its own group
    db_session.add(BrokerLog(
        request_id="later", branch_id=7, thread_id=500, kind="reply", capability="chat:fast",
        model="v/m", ok=True, latency_ms=800, created_at=base + timedelta(hours=1)))
    await db_session.flush()

    rows, total = await fetch_broker_log(db_session, [7], page=0, size=50)
    html = broker_log_panel_html(list(rows), 0, 50, total)
    assert "4 calls" in html            # the burst grouped as one turn
    assert "end-to-end" in html         # aggregated wall-clock shown
    assert "12.0s" in html              # 10s last-start + 2s latency − 0s first-start
    assert html.count("🧵") == 1        # only the multi-call turn gets a header, not the singleton


async def test_turn_histogram_sums_end_to_end_per_bucket(db_session) -> None:
    """The header histogram buckets each turn's end-to-end wall-clock. Two turns in-window on
    one thread → their spans sum into the total; an out-of-window call is excluded."""
    now = _NOW
    # turn A: 2 calls spanning 10s, ~30 min ago
    a_base = now - timedelta(minutes=30)
    for i, off in enumerate([0, 8]):
        db_session.add(BrokerLog(
            request_id=f"a{i}", branch_id=7, thread_id=800, kind="reply", capability="chat:smart",
            ok=True, latency_ms=2000, created_at=a_base + timedelta(seconds=off)))
    # turn B: 1 call, ~5 min ago (its own turn — far from A)
    db_session.add(BrokerLog(
        request_id="b0", branch_id=7, thread_id=800, kind="reply", capability="chat:fast",
        ok=True, latency_ms=3000, created_at=now - timedelta(minutes=5)))
    # out of the 1h window — excluded
    db_session.add(BrokerLog(
        request_id="old", branch_id=7, thread_id=800, kind="reply", capability="chat:fast",
        ok=True, latency_ms=9000, created_at=now - timedelta(hours=3)))
    await db_session.flush()

    buckets, turns, _since, span_s = await fetch_turn_histogram(db_session, [7], "1h")
    assert turns == 2                       # A and B; the 3h-old call is outside the window
    # A span = 8s start + 2s latency = 10s; B span = 3s → total 13s
    assert round(sum(buckets)) == 13
    assert span_s == 3600 / 24              # 1h window / 24 buckets = 150s per bucket
    assert "1h" in log_window_keys() and "7d" in log_window_keys()
