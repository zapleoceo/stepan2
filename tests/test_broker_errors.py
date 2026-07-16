"""Broker adapter — the fully-async job flow (submit POST /v1/jobs → poll GET /v1/jobs/{id})
plus error paths: HTTP errors, bad JSON, missing keys, timeouts, deadline, per-cap budget."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from typing import Any  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.adapters.llm import broker as broker_mod  # noqa: E402
from app.adapters.llm.broker import BrokerUnavailable  # noqa: E402


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


class _JobClient:
    """Fake broker client for the async job flow: scripted submit (post) + poll (get)
    responses. post() records the ?capability= it was called with; get() pops the next
    poll response. Both raise on an exhausted queue so an unexpected extra call is loud."""

    def __init__(
        self, submits: list[_FakeResp], polls: list[_FakeResp] | None = None,
        caps: list[str] | None = None, timeouts: list[Any] | None = None,
    ) -> None:
        self._submits = list(submits)
        self._polls = list(polls or [])
        self.caps = caps if caps is not None else []
        self.timeouts = timeouts

    async def __aenter__(self) -> _JobClient:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def post(self, *a: object, **k: object) -> _FakeResp:
        params = k.get("params") or {}
        if isinstance(params, dict) and "capability" in params:
            self.caps.append(params["capability"])
        return self._submits.pop(0)

    async def get(self, *a: object, **k: object) -> _FakeResp:
        return self._polls.pop(0)


class _TimeoutClient(_JobClient):
    def __init__(self) -> None:
        super().__init__([_FakeResp({})])

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


def _job(job_id: int = 1) -> _FakeResp:
    return _FakeResp({"job_id": job_id, "poll_after_s": 5}, status=202)


def _done(text: str = "hi") -> _FakeResp:
    return _FakeResp({"status": "done", "text": text, "model": "m", "tokens_in": 1,
                      "tokens_out": 1, "provider": "cerebras", "cost_usd": 0.0,
                      "request_id": "r"})


# poll loop sleeps poll_after_s between attempts — no-op it so tests don't actually wait.
@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):  # noqa: ANN001, ANN201
    async def _instant(_seconds):  # noqa: ANN001
        return None
    monkeypatch.setattr(broker_mod.asyncio, "sleep", _instant)


# ── happy path ──────────────────────────────────────────────────────────────

async def test_chat_submits_a_job_and_polls_to_done(monkeypatch) -> None:
    client = _JobClient([_job(1)], [_FakeResp({"status": "pending"}), _done("answer")])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    calls = _capture_log(monkeypatch)
    text, meta = await _llm().chat([{"role": "user", "content": "hi"}],
                                   capability="chat:smart", workflow="reply")
    assert text == "answer"
    assert meta["provider"] == "cerebras" and meta["request_id"] == "r"
    assert client.caps == ["chat:smart"]  # submitted to /v1/jobs?capability=chat:smart
    assert calls[-1]["ok"] is True and calls[-1]["cap"] == "chat:smart"


class _CaptureJobClient(_JobClient):
    """Records the submit POST's params + json body so we can assert the workflow tag."""

    def __init__(self, submits, polls=None):  # noqa: ANN001
        super().__init__(submits, polls)
        self.sent: dict[str, Any] = {}

    async def post(self, *a: object, **k: object) -> _FakeResp:
        self.sent = {"params": k.get("params"), "json": k.get("json")}
        return self._submits.pop(0)


async def test_chat_forwards_workflow_tag_to_the_broker(monkeypatch) -> None:
    """Regression: the workflow label was logged locally but never SENT to the broker, so the
    dashboard grouped every chat under '(none)'. It must ride in both params and the body."""
    client = _CaptureJobClient([_job(1)], [_done("ok")])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    _capture_log(monkeypatch)
    await _llm().chat([{"role": "user", "content": "hi"}],
                      capability="chat:smart", workflow="reply")
    assert client.sent["params"].get("workflow") == "reply"
    assert client.sent["json"].get("workflow") == "reply"


class _CaptureEmbedClient:
    def __init__(self) -> None:
        self.sent: dict[str, Any] = {}

    async def __aenter__(self) -> _CaptureEmbedClient:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def post(self, *a: object, **k: object) -> _FakeResp:
        self.sent = {"params": k.get("params"), "json": k.get("json")}
        return _FakeResp({"embeddings": [[0.1]], "model": "voyage", "tokens_in": 1,
                          "cost_usd": 0.0, "request_id": "r"})


async def test_embed_forwards_its_kind_as_workflow(monkeypatch) -> None:
    client = _CaptureEmbedClient()
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    _capture_log(monkeypatch)
    await _llm().embed(["x"], kind="embed:index")
    assert client.sent["params"].get("workflow") == "embed:index"
    assert client.sent["json"].get("workflow") == "embed:index"


async def test_describe_image_submits_vision_job_with_multimodal_content(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class _CaptureClient(_JobClient):
        async def post(self, *a: object, **k: object) -> _FakeResp:
            captured["body"] = k.get("json")
            return await super().post(*a, **k)

    client = _CaptureClient([_job(1)], [_done("screenshot harga 1jt")])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    calls = _capture_log(monkeypatch)
    text = await _llm().describe_image(b"\xff\xd8jpegbytes", mime="image/png", branch_id=2)
    assert text == "screenshot harga 1jt"
    assert client.caps == ["vision"]  # submitted to /v1/jobs?capability=vision
    assert calls[-1]["ok"] is True
    content = captured["body"]["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


# ── error paths ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [502, 503])
async def test_chat_submit_5xx_raises_broker_unavailable_and_logs_failure(
    monkeypatch, status: int,
) -> None:
    # A gateway 502/503 is the broker being DOWN → raise BrokerUnavailable so the worker can
    # trip the breaker (an ordinary 4xx/500 stays a plain error — see the test below).
    client = _JobClient([_FakeResp({}, status=status)])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    calls = _capture_log(monkeypatch)
    with pytest.raises(BrokerUnavailable) as ei:
        await _llm().chat([{"role": "user", "content": "hi"}], workflow="reply",
                          thread_id=1, branch_id=2)
    assert isinstance(ei.value.__cause__, httpx.HTTPStatusError)  # original preserved
    assert len(calls) == 1 and calls[0]["ok"] is False
    assert calls[0]["err"].startswith(str(status))
    assert "upstream unavailable" in calls[0]["err"]


async def test_chat_submit_500_stays_a_plain_error_not_broker_unavailable(monkeypatch) -> None:
    # a 500 is a per-request failure, not the gateway being down — must NOT trip the breaker
    client = _JobClient([_FakeResp({}, status=500)])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    _capture_log(monkeypatch)
    with pytest.raises(httpx.HTTPStatusError):
        await _llm().chat([{"role": "user", "content": "hi"}], workflow="reply")


async def test_chat_submit_invalid_json_raises_and_logs_failure(monkeypatch) -> None:
    client = _JobClient([_BadJsonResp({}, status=200)])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    calls = _capture_log(monkeypatch)
    with pytest.raises(ValueError, match="Expecting value"):
        await _llm().chat([{"role": "user", "content": "hi"}])
    assert len(calls) == 1 and calls[0]["ok"] is False
    assert calls[0]["err"].startswith("ValueError")


async def test_chat_submit_missing_job_id_raises_and_logs_failure(monkeypatch) -> None:
    # A 202 whose body lacks job_id is a failed submit (KeyError) — logged, not swallowed.
    client = _JobClient([_FakeResp({"poll_after_s": 5}, status=202)])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    calls = _capture_log(monkeypatch)
    with pytest.raises(KeyError):
        await _llm().chat([{"role": "user", "content": "hi"}], workflow="reply")
    assert len(calls) == 1 and calls[0]["ok"] is False


async def test_chat_poll_error_status_raises_and_logs_failure(monkeypatch) -> None:
    client = _JobClient([_job(3)], [_FakeResp(
        {"status": "error", "error": "no provider available"})])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    calls = _capture_log(monkeypatch)
    with pytest.raises(RuntimeError, match="no provider available"):
        await _llm().chat([{"role": "user", "content": "hi"}], workflow="reply")
    assert calls[-1]["ok"] is False


async def test_chat_submit_read_timeout_raises_and_logs_failure(monkeypatch) -> None:
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: _TimeoutClient())
    calls = _capture_log(monkeypatch)
    # a read timeout reaching the broker is also "gateway down" → BrokerUnavailable
    with pytest.raises(BrokerUnavailable) as ei:
        await _llm().chat([{"role": "user", "content": "hi"}], capability="chat:smart",
                          workflow="reply", thread_id=7, branch_id=3)
    assert isinstance(ei.value.__cause__, httpx.ReadTimeout)
    assert len(calls) == 1 and calls[0]["ok"] is False
    assert calls[0]["err"].startswith("ReadTimeout")
    assert "elapsed_ms" in calls[0]["meta"]


async def test_chat_tolerates_transient_poll_errors_then_succeeds(monkeypatch) -> None:
    """A 502 on a poll must NOT discard a running job — the poll loop retries."""
    class _FlakyPollClient(_JobClient):
        def __init__(self) -> None:
            super().__init__([_job(5)])
            self._poll_calls = 0

        async def get(self, *a: object, **k: object) -> _FakeResp:
            self._poll_calls += 1
            if self._poll_calls == 1:
                raise httpx.ConnectError("transient 502")
            return _done("recovered")

    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: _FlakyPollClient())
    _capture_log(monkeypatch)
    text, _ = await _llm().chat([{"role": "user", "content": "hi"}], workflow="reply")
    assert text == "recovered"


async def test_chat_gives_up_after_budget(monkeypatch) -> None:
    """A job stuck in 'pending' must raise TimeoutError once the poll budget elapses."""
    from app.config import settings
    monkeypatch.setattr(settings(), "llm_read_timeout_s", 0.0)
    client = _JobClient([_job(9)], [_FakeResp({"status": "pending"})])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    _capture_log(monkeypatch)
    with pytest.raises(TimeoutError, match="still pending"):
        await _llm().chat([{"role": "user", "content": "hi"}], workflow="reply")


# ── chat_deep (same job flow + 403→smart fallback) ──────────────────────────

async def test_chat_deep_submits_polls_and_returns_done(monkeypatch) -> None:
    client = _JobClient([_job(42)], [_FakeResp({"status": "pending"}), _done("deep answer")])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    calls = _capture_log(monkeypatch)
    text, meta = await _llm().chat_deep([{"role": "user", "content": "hi"}], workflow="coach")
    assert text == "deep answer"
    assert client.caps == ["chat:deep"]
    assert calls[-1]["ok"] is True and calls[-1]["cap"] == "chat:deep"


async def test_chat_deep_falls_back_to_smart_on_403(monkeypatch) -> None:
    """Key lacks llm:deep → the deep submit 403s and chat_deep re-submits as chat:smart."""
    caps: list[str] = []
    # 1st submit (deep) → 403; 2nd submit (smart fallback) → job; then poll → done.
    client = _JobClient([_FakeResp({}, status=403), _job(2)], [_done("hi")], caps=caps)
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    calls = _capture_log(monkeypatch)
    text, _ = await _llm().chat_deep([{"role": "user", "content": "hi"}], max_tokens=8000,
                                     workflow="coach")
    assert text == "hi"
    assert caps == ["chat:deep", "chat:smart"]  # deep 403, then the smart resubmit
    assert calls[-1]["ok"] is True and calls[-1]["cap"] == "chat:smart"


async def test_chat_deep_gives_up_after_deadline(monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings(), "llm_read_timeout_deep_s", 0.0)
    client = _JobClient([_job(9)], [_FakeResp({"status": "pending"})])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    _capture_log(monkeypatch)
    with pytest.raises(TimeoutError, match="still pending"):
        await _llm().chat_deep([{"role": "user", "content": "hi"}], workflow="coach")


# ── embed stays synchronous ─────────────────────────────────────────────────

async def test_embed_5xx_raises_and_logs_failure(monkeypatch) -> None:
    client = _JobClient([_FakeResp({}, status=503)])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    calls = _capture_log(monkeypatch)
    with pytest.raises(httpx.HTTPStatusError):
        await _llm().embed(["a"], branch_id=1)
    assert len(calls) == 1
    assert calls[0]["ok"] is False and calls[0]["cap"] == "embedding"


# ── budget selection + client timeout ───────────────────────────────────────

def test_poll_budget_per_capability() -> None:
    from app.config import settings
    s = settings()
    assert broker_mod._poll_budget_s("chat:deep") == s.llm_read_timeout_deep_s
    assert broker_mod._poll_budget_s("chat:smart") == s.llm_read_timeout_slow_s
    assert broker_mod._poll_budget_s("vision") == s.llm_read_timeout_slow_s
    assert broker_mod._poll_budget_s("chat:fast") == s.llm_read_timeout_s
    assert broker_mod._poll_budget_s("translate") == s.llm_read_timeout_s


async def test_job_client_uses_short_http_timeout(monkeypatch) -> None:
    """Individual submit/poll calls use the short _JOB_HTTP_TIMEOUT (read=30) — the
    per-capability value is the overall poll BUDGET now, not one request's read timeout."""
    seen: list[httpx.Timeout] = []
    client = _JobClient([_job(1)], [_done("x")])

    def _client(**k: Any) -> _JobClient:
        seen.append(k["timeout"])
        return client

    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", _client)
    _capture_log(monkeypatch)
    await _llm().chat([{"role": "user", "content": "hi"}], capability="chat:smart")
    assert len(seen) == 1
    assert seen[0].read == 30.0 and seen[0].connect == 5.0


async def test_done_job_with_no_text_is_ok_empty_not_a_failure(monkeypatch) -> None:
    """A done job that returns no text (e.g. tiny max_tokens) is a successful empty reply —
    it must NOT become a KeyError that discards the billed cost/tokens and logs a failure."""
    done_no_text = _FakeResp({"status": "done", "model": "m", "tokens_in": 3,
                              "tokens_out": 0, "provider": "cerebras", "cost_usd": 0.004,
                              "request_id": "r"})
    client = _JobClient([_job(1)], [done_no_text])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    calls = _capture_log(monkeypatch)
    text, meta = await _llm().chat([{"role": "user", "content": "hi"}], workflow="reply")
    assert text == ""                       # empty, not a crash
    assert meta["cost_usd"] == 0.004        # cost preserved, not discarded
    assert calls[-1]["ok"] is True          # logged as success, not failure


async def test_done_job_with_null_text_is_ok_empty(monkeypatch) -> None:
    done_null = _FakeResp({"status": "done", "text": None, "model": "m", "provider": "p",
                           "cost_usd": 0.0, "request_id": "r"})
    client = _JobClient([_job(1)], [done_null])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    _capture_log(monkeypatch)
    text, _ = await _llm().chat([{"role": "user", "content": "hi"}], workflow="reply")
    assert text == ""


async def test_poll_gives_up_after_too_many_transient_errors(monkeypatch) -> None:
    """More than _POLL_MAX_ERRORS consecutive poll errors = a flapping gateway → give up as
    BrokerUnavailable (don't poll forever) so the fleet backs off, not just this job."""
    class _AlwaysFlakyPoll(_JobClient):
        def __init__(self) -> None:
            super().__init__([_job(1)])

        async def get(self, *a: object, **k: object) -> _FakeResp:
            raise httpx.ConnectError("always down")

    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: _AlwaysFlakyPoll())
    calls = _capture_log(monkeypatch)
    with pytest.raises(BrokerUnavailable):
        await _llm().chat([{"role": "user", "content": "hi"}], workflow="reply")
    assert calls[-1]["ok"] is False


# ── transcribe (voice STT — single POST /v1/transcribe, no job poll) ──────────

async def test_transcribe_success_returns_text_and_logs_ok(monkeypatch) -> None:
    calls = _capture_log(monkeypatch)
    client = _JobClient([_FakeResp({"text": "halo dunia", "model": "w", "provider": "p",
                                    "cost_usd": 0.0, "request_id": "r"})])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    out = await _llm().transcribe(b"audiobytes", mime="audio/mp4")
    assert out == "halo dunia"
    assert calls[-1]["ok"] is True and calls[-1]["cap"] == "audio"


async def test_transcribe_falls_back_to_transcript_key(monkeypatch) -> None:
    _capture_log(monkeypatch)
    client = _JobClient([_FakeResp({"transcript": " hi there ", "request_id": "r"})])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    assert await _llm().transcribe(b"x", mime="audio/mp4") == "hi there"


async def test_transcribe_5xx_raises_and_logs_failure(monkeypatch) -> None:
    calls = _capture_log(monkeypatch)
    client = _JobClient([_FakeResp({}, status=503)])
    monkeypatch.setattr(broker_mod.httpx, "AsyncClient", lambda **k: client)
    with pytest.raises(httpx.HTTPStatusError):
        await _llm().transcribe(b"x", mime="audio/mp4")
    assert calls[-1]["ok"] is False and calls[-1]["cap"] == "audio"
