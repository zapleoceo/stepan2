"""Broker adapter error paths: HTTP errors, bad JSON bodies, timeouts, per-cap timeouts."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from typing import Any  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.adapters.llm import broker as broker_mod  # noqa: E402


class _FakeResp:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.headers: dict[str, str] = {}

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)  # type: ignore[arg-type]

    @property
    def text(self) -> str:
        return "upstream unavailable"


class _BadJsonResp(_FakeResp):
    def json(self) -> dict[str, Any]:
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


class _FakeClient:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def post(self, *a: object, **k: object) -> _FakeResp:
        return self._resp


class _TimeoutClient(_FakeClient):
    def __init__(self) -> None:
        super().__init__(_FakeResp({}))

    async def post(self, *a: object, **k: object) -> _FakeResp:
        raise httpx.ReadTimeout("read timed out")


def _capture_log(monkeypatch) -> list[dict[str, Any]]:  # noqa: ANN001
    calls: list[dict[str, Any]] = []

    async def _capture(cap, wf, tid, bid, meta, *, ok, error=None):  # noqa: ANN001, ANN002
        calls.append({"cap": cap, "wf": wf, "ok": ok, "err": error, "meta": meta})

    monkeypatch.setattr(broker_mod, "_log_call", _capture)
    return calls


def _llm() -> broker_mod.BrokerLLM:
    return broker_mod.BrokerLLM(base_url="http://x", project_key="k")


@pytest.mark.parametrize("status", [502, 503])
async def test_chat_5xx_raises_and_logs_failure(monkeypatch, status: int) -> None:
    resp = _FakeResp({}, status=status)
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: _FakeClient(resp))
    calls = _capture_log(monkeypatch)
    with pytest.raises(httpx.HTTPStatusError):
        await _llm().chat([{"role": "user", "content": "hi"}], workflow="reply",
                          thread_id=1, branch_id=2)
    assert len(calls) == 1
    assert calls[0]["ok"] is False
    assert calls[0]["err"].startswith(str(status))
    assert "upstream unavailable" in calls[0]["err"]


async def test_chat_200_with_invalid_json_raises_and_logs_failure(monkeypatch) -> None:
    # A 200 with a truncated/invalid JSON body must still write an ok=False broker_log row
    # so the failure is visible on /settings/log (the parse now lives inside the try/except).
    resp = _BadJsonResp({}, status=200)
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: _FakeClient(resp))
    calls = _capture_log(monkeypatch)
    with pytest.raises(ValueError, match="Expecting value"):
        await _llm().chat([{"role": "user", "content": "hi"}])
    assert len(calls) == 1
    assert calls[0]["ok"] is False
    assert calls[0]["err"].startswith("ValueError")


async def test_chat_200_missing_keys_raises_and_logs_failure(monkeypatch) -> None:
    # A 200 whose body parses but lacks required keys (KeyError on d["model"]) is a failed
    # call too — it must be logged, not silently propagated.
    resp = _FakeResp({"text": "hi"}, status=200)  # no model/tokens/provider/cost_usd
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: _FakeClient(resp))
    calls = _capture_log(monkeypatch)
    with pytest.raises(KeyError):
        await _llm().chat([{"role": "user", "content": "hi"}], workflow="reply",
                          thread_id=1, branch_id=2)
    assert len(calls) == 1
    assert calls[0]["ok"] is False


class _SeqClient(_FakeClient):
    """Returns a queued response per post() call and records the capability query param."""

    def __init__(self, resps: list[_FakeResp], caps: list[str]) -> None:
        self._resps = resps
        self._caps = caps

    async def post(self, *a: object, **k: object) -> _FakeResp:
        self._caps.append(k["params"]["capability"])  # type: ignore[index]
        return self._resps.pop(0)


async def test_chat_deep_falls_back_to_smart_on_403(monkeypatch) -> None:
    """chat:deep isn't enabled on the key yet (403) — the call must retry as chat:smart so
    Coach / re-derivation keep working, and the audit row logs the capability actually used."""
    ok = _FakeResp({"text": "hi", "model": "m", "tokens_in": 1, "tokens_out": 1,
                    "provider": "p", "cost_usd": 0.0, "request_id": "r"})
    caps: list[str] = []
    client = _SeqClient([_FakeResp({}, status=403), ok], caps)
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    calls = _capture_log(monkeypatch)
    text, _ = await _llm().chat([{"role": "user", "content": "hi"}], capability="chat:deep",
                                max_tokens=8000, workflow="coach")
    assert text == "hi"
    assert caps == ["chat:deep", "chat:smart"]  # tried deep, fell back to smart
    assert calls[-1]["ok"] is True and calls[-1]["cap"] == "chat:smart"


async def test_chat_read_timeout_raises_and_logs_failure(monkeypatch) -> None:
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: _TimeoutClient())
    calls = _capture_log(monkeypatch)
    with pytest.raises(httpx.ReadTimeout):
        await _llm().chat([{"role": "user", "content": "hi"}], capability="chat:smart",
                          workflow="reply", thread_id=7, branch_id=3)
    assert len(calls) == 1
    assert calls[0]["ok"] is False
    assert calls[0]["err"].startswith("ReadTimeout")
    assert "elapsed_ms" in calls[0]["meta"]


async def test_embed_5xx_raises_and_logs_failure(monkeypatch) -> None:
    resp = _FakeResp({}, status=503)
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: _FakeClient(resp))
    calls = _capture_log(monkeypatch)
    with pytest.raises(httpx.HTTPStatusError):
        await _llm().embed(["a"], branch_id=1)
    assert len(calls) == 1
    assert calls[0]["ok"] is False and calls[0]["cap"] == "embedding"


@pytest.mark.parametrize(
    ("capability", "read_timeout"),
    [("chat:smart", 90.0), ("chat:deep", 600.0), ("chat:fast", 20.0)],
)
async def test_per_capability_timeout_passed_to_client(
    monkeypatch, capability: str, read_timeout: float,
) -> None:
    seen: list[httpx.Timeout] = []
    resp = _FakeResp({"text": "hi", "model": "m", "tokens_in": 1, "tokens_out": 1,
                      "provider": "p", "cost_usd": 0.0, "request_id": "r"})

    def _client(**k: Any) -> _FakeClient:
        seen.append(k["timeout"])
        return _FakeClient(resp)

    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", _client)
    _capture_log(monkeypatch)
    await _llm().chat([{"role": "user", "content": "hi"}], capability=capability)
    assert len(seen) == 1
    assert seen[0].read == read_timeout
    assert seen[0].connect == 5.0
