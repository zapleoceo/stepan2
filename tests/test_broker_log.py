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
from app.api._query import fetch_branch_tz, fetch_broker_log  # noqa: E402
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


@pytest.mark.asyncio
async def test_chat_logs_ok_row(monkeypatch) -> None:
    resp = _FakeResp({"text": "hi", "model": "x/gpt", "tokens_in": 10, "tokens_out": 4,
                      "provider": "openai", "cost_usd": 0.0, "request_id": "req-1"})
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: _FakeClient(resp))
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


async def test_log_renders_each_branch_in_its_own_local_time(db_session) -> None:
    """Owner viewing the log across branches must see each row in ITS branch's tz, not a
    single global offset — a Jakarta (+7) call and a UTC (+0) call side by side."""
    jakarta = Branch(name="Jakarta", tz_offset_h=7)
    utc_branch = Branch(name="UTC", tz_offset_h=0)
    db_session.add_all([jakarta, utc_branch])
    await db_session.flush()

    when = datetime(2026, 7, 3, 10, 0, 0)
    db_session.add(BrokerLog(branch_id=jakarta.id, kind="reply", capability="chat:smart",
                             ok=True, created_at=when))
    db_session.add(BrokerLog(branch_id=utc_branch.id, kind="reply", capability="chat:smart",
                             ok=True, created_at=when))
    await db_session.flush()

    rows, total = await fetch_broker_log(db_session, None, page=0, size=50)
    tz_by_branch = await fetch_branch_tz(db_session, [jakarta.id, utc_branch.id])
    assert tz_by_branch == {jakarta.id: 7, utc_branch.id: 0}

    html = broker_log_panel_html(list(rows), 0, 50, total, tz_by_branch)
    assert "07-03 17:00:00" in html  # Jakarta: 10:00 UTC + 7h
    assert "07-03 10:00:00" in html  # UTC branch: unshifted
