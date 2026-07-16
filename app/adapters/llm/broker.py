"""Broker LLM adapter — the ONLY LLM path. Implements LLMPort over AIbroker HTTP.

No provider keys in this app: the broker holds them and returns cost_usd, so per-branch
budgeting uses the real broker price. Every call (reply/translate/embed/suggest) is logged
to broker_log — the single write point — for the /settings/log audit page.

Chat is FULLY ASYNC (2026-07-09): every chat capability goes through the broker's job
queue — POST /v1/jobs?capability=X returns a job id, then we poll GET /v1/jobs/{id} until
done. A slow provider no longer holds a synchronous connection open past Cloudflare's /
nginx's proxy timeout (the 504 class of failure). The public API is unchanged —
chat()/chat_deep() still return (text, meta); callers don't know it polls underneath.
embed()/transcribe() stay synchronous (fast, and the broker has no job endpoint for them)."""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.config import settings

_log = logging.getLogger(__name__)


class BrokerUnavailable(Exception):
    """The broker GATEWAY is unreachable (502/503/504 or a connection error) — the broker
    itself is down, not this one request being malformed. Subclasses Exception so existing
    `except Exception` callers still catch it, but lets the worker distinguish "the broker is
    down, back the whole fleet off" from an ordinary per-request failure and trip the breaker."""


_GATEWAY_DOWN_STATUS = frozenset({502, 503, 504})


def _is_gateway_down(exc: Exception) -> bool:
    """True when exc means the broker gateway is unreachable — a 502/503/504 or a
    connect/read/pool timeout — as opposed to a per-request error (bad body, job status=error,
    a single slow job hitting its budget), which must NOT freeze the fleet."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _GATEWAY_DOWN_STATUS
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                            httpx.PoolTimeout, httpx.RemoteProtocolError))

# Vision prompt for a lead's image (screenshot of a price/schedule, a payment proof, a
# product photo, a competitor's offer, …). Read any text verbatim and describe the rest
# concisely — this becomes the lead's message the reply model then answers.
_VISION_PROMPT = (
    "Deskripsikan gambar ini secara ringkas dalam Bahasa Indonesia. Jika ada teks di "
    "dalamnya (harga, jadwal, tangkapan layar, bukti transfer), tuliskan teksnya apa "
    "adanya. Jangan menebak yang tidak terlihat.")

# A fresh AsyncClient per public call (submit_and_poll / transcribe / embed) is deliberate,
# not an oversight: the connection IS reused across the many polls of one job (they share the
# same `c`), which is where keep-alive matters. A single long-lived shared client would save
# only one TLS handshake per multi-second LLM job while adding real risk — its pool binds to
# the event loop that created it, and this runs across per-job ARQ worker loops that come and
# go. Per-call keeps lifecycle trivial and correct; the saving isn't worth the shared state.
# Individual submit/poll HTTP calls are quick — a short timeout. The OVERALL wait for a job
# is bounded by the per-capability poll budget below, not by one request's read timeout.
_JOB_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
# Sync-only calls (embed/transcribe) still use a plain read timeout.
_SYNC_TIMEOUT = httpx.Timeout(
    connect=5.0, read=settings().llm_read_timeout_s, write=10.0, pool=5.0)
# vision runs in the background media backfill (not a live reply), and free vision providers
# are spiky — give it the slow budget so a slow caption isn't cut off at the fast ceiling.
_SLOW_CAPS = frozenset({"chat:smart", "vision"})
# A transient poll failure (502/timeout) must not discard a job that's still running —
# tolerate this many consecutive poll errors before giving up.
_POLL_MAX_ERRORS = 5


def _poll_budget_s(capability: str) -> float:
    """Total time to wait for a job of this capability before giving up (TimeoutError).
    Not a single-request timeout — the overall submit+poll budget. chat:deep reasons for
    minutes; chat:smart gets the slow budget; everything else the normal one."""
    s = settings()
    if capability == "chat:deep":
        return s.llm_read_timeout_deep_s
    if capability in _SLOW_CAPS:
        return s.llm_read_timeout_slow_s
    return s.llm_read_timeout_s


class BrokerLLM:
    """Implements app.ports.llm.LLMPort."""

    def __init__(self, base_url: str | None = None, project_key: str | None = None) -> None:
        s = settings()
        self._url = (base_url or s.broker_url).rstrip("/")
        self._key = project_key or s.broker_project_key
        self.calls = 0  # broker requests made by THIS instance (one per reply job) — retries incl.

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        capability: str = "chat:fast",
        require_json_schema: bool = False,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        workflow: str | None = None,
        thread_id: int | None = None,
        branch_id: int | None = None,
        read_timeout_s: float | None = None,
    ) -> tuple[str, dict[str, Any]]:
        body: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if require_json_schema:
            body["response_format"] = {"type": "json_object"}
        # read_timeout_s (a caller override, e.g. translate) becomes the total poll budget.
        budget = read_timeout_s if read_timeout_s is not None else _poll_budget_s(capability)
        return await self._submit_and_poll(
            body, capability=capability, workflow=workflow,
            thread_id=thread_id, branch_id=branch_id, budget_s=budget)

    async def chat_deep(
        self,
        messages: list[dict[str, Any]],
        *,
        require_json_schema: bool = False,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        workflow: str | None = None,
        thread_id: int | None = None,
        branch_id: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """chat:deep — full-context/reasoning capability, same async job flow as chat().
        Falls back to chat:smart on a 403 (the project key doesn't have the llm:deep scope
        yet) so Coach keeps working; auto-upgrades the day the scope is granted.

        require_json_schema is accepted for signature parity but NOT sent — nemotron isn't
        JSON-reliable, so callers already tolerate a fenced/loose body (propose_edit does)."""
        body: dict[str, Any] = {
            "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
        }

        async def _fallback_to_smart() -> tuple[str, dict[str, Any]]:
            return await self.chat(
                messages, capability="chat:smart",
                require_json_schema=require_json_schema,
                max_tokens=min(max_tokens, 2000), temperature=temperature,
                workflow=workflow, thread_id=thread_id, branch_id=branch_id,
            )

        return await self._submit_and_poll(
            body, capability="chat:deep", workflow=workflow,
            thread_id=thread_id, branch_id=branch_id,
            budget_s=_poll_budget_s("chat:deep"), on_403=_fallback_to_smart)

    async def _submit_and_poll(
        self,
        body: dict[str, Any],
        *,
        capability: str,
        workflow: str | None,
        thread_id: int | None,
        branch_id: int | None,
        budget_s: float,
        on_403: Callable[[], Awaitable[tuple[str, dict[str, Any]]]] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Submit a chat job and poll it to completion — the single async chat path.

        on_403 lets chat_deep fall back to chat:smart when the key lacks the scope. A
        transient poll error is tolerated up to _POLL_MAX_ERRORS in a row; the whole wait is
        bounded by budget_s. Writes exactly one broker_log row (ok on done, error otherwise),
        same shape as before so the audit page / request_id lookups are unchanged."""
        self.calls += 1
        start = time.perf_counter()
        # Tag the call with the scenario so the broker dashboard can break spend down per
        # workflow (reply / followup / coach / translate / guard / …) instead of one "(none)"
        # blob. The broker reads `workflow` from either the query params or the body; send both.
        params = {"capability": capability}
        if workflow:
            params["workflow"] = workflow
            body["workflow"] = workflow
        try:
            async with httpx.AsyncClient(timeout=_JOB_HTTP_TIMEOUT) as c:
                r = await c.post(
                    f"{self._url}/v1/jobs",
                    params=params,
                    headers={"X-Project-Key": self._key},
                    json=body,
                )
                if r.status_code == 403 and on_403 is not None:
                    return await on_403()
                r.raise_for_status()
                job = r.json()
                job_id = job["job_id"]
                poll_after_s = job.get("poll_after_s") or 2
                d = await self._poll_job(c, job_id, capability, start + budget_s, poll_after_s)

            meta = {
                "model": d.get("model"),
                "tokens_in": d.get("tokens_in") or 0,
                "tokens_out": d.get("tokens_out") or 0,
                "provider": d.get("provider"),
                "cost_usd": d.get("cost_usd") or 0,
                "elapsed_ms": int((time.perf_counter() - start) * 1000),
                "request_id": d.get("request_id") or d.get("id"),
            }
            # A done job may legitimately have empty/absent text (e.g. a tiny max_tokens) —
            # treat it as "" (a successful zero-length reply), NOT a KeyError that would
            # discard the cost/tokens already in meta and mislabel a billed call as failed.
            reply_text = d.get("text") or ""
        except Exception as exc:
            await _log_call(capability, workflow or "chat", thread_id, branch_id,
                            {"elapsed_ms": int((time.perf_counter() - start) * 1000)},
                            ok=False, error=_err_text(exc))
            if _is_gateway_down(exc) and not isinstance(exc, BrokerUnavailable):
                raise BrokerUnavailable(str(exc)[:200]) from exc
            raise
        await _log_call(capability, workflow or "chat", thread_id, branch_id, meta, ok=True)
        return reply_text, meta

    async def _poll_job(
        self, c: httpx.AsyncClient, job_id: Any, capability: str,
        deadline: float, poll_after_s: float,
    ) -> dict[str, Any]:
        """Poll GET /v1/jobs/{id} until done; raise on error/timeout. Polls IMMEDIATELY
        first (a fast job may already be done — no needless initial wait), then sleeps
        poll_after_s between subsequent polls."""
        poll_errors = 0
        while True:
            try:
                pr = await c.get(
                    f"{self._url}/v1/jobs/{job_id}",
                    headers={"X-Project-Key": self._key},
                )
                pr.raise_for_status()
                d = pr.json()
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                # A transient poll error (502/timeout) must NOT discard a running job.
                poll_errors += 1
                if poll_errors > _POLL_MAX_ERRORS:
                    # Repeated poll failures = the gateway is flapping, not this job being
                    # slow → signal the breaker so the fleet backs off.
                    raise BrokerUnavailable(
                        f"{capability} job {job_id}: {poll_errors} poll errors") from exc
                _log.warning("%s poll error %d/%d job=%s: %s",
                             capability, poll_errors, _POLL_MAX_ERRORS, job_id, exc)
                if time.perf_counter() > deadline:
                    raise TimeoutError(
                        f"{capability} job {job_id} unresolved (poll errors)") from exc
                await asyncio.sleep(poll_after_s)
                continue
            poll_errors = 0  # a clean poll resets the run
            status = d.get("status")
            if status == "done":
                return d
            if status == "error":
                raise RuntimeError(f"{capability} job {job_id} failed: {d.get('error')}")
            if time.perf_counter() > deadline:
                raise TimeoutError(f"{capability} job {job_id} still pending after budget")
            poll_after_s = d.get("poll_after_s") or poll_after_s
            await asyncio.sleep(poll_after_s)

    async def transcribe(
        self, audio: bytes, *, mime: str = "audio/mp4",
        thread_id: int | None = None, branch_id: int | None = None,
    ) -> str:
        """Speech-to-text for a voice message via the broker's /v1/transcribe. Returns the
        transcript text ('' if the broker returns none). Raises on transport/scope errors
        (the caller keeps the placeholder + retries) — needs the project key's llm:audio scope."""
        self.calls += 1
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=_SYNC_TIMEOUT) as c:
                r = await c.post(
                    f"{self._url}/v1/transcribe", params={"workflow": "voice"},
                    headers={"X-Project-Key": self._key},
                    files={"file": ("voice.mp4", audio, mime)},
                )
            r.raise_for_status()
            d = r.json()
            meta = {
                "model": d.get("model"), "provider": d.get("provider"),
                "cost_usd": d.get("cost_usd") or 0,
                "elapsed_ms": int((time.perf_counter() - start) * 1000),
                "request_id": d.get("request_id") or d.get("id"),
            }
        except Exception as exc:
            await _log_call("audio", "transcribe", thread_id, branch_id,
                            {"elapsed_ms": int((time.perf_counter() - start) * 1000)},
                            ok=False, error=_err_text(exc))
            raise
        await _log_call("audio", "transcribe", thread_id, branch_id, meta, ok=True)
        return (d.get("text") or d.get("transcript") or "").strip()

    async def describe_image(
        self, image: bytes, *, mime: str = "image/jpeg", prompt: str | None = None,
        thread_id: int | None = None, branch_id: int | None = None,
    ) -> str:
        """Vision caption for a lead's image via the 'vision' capability (async job queue,
        multimodal content). Returns a short description ('' if none). Raises on
        transport/scope errors (caller keeps the placeholder) — needs the llm:vision scope."""
        data_uri = f"data:{mime};base64,{base64.b64encode(image).decode()}"
        content = [
            {"type": "text", "text": prompt or _VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]
        text, _meta = await self._submit_and_poll(
            {"messages": [{"role": "user", "content": content}],
             "max_tokens": 300, "temperature": 0.2},
            capability="vision", workflow="vision",
            thread_id=thread_id, branch_id=branch_id, budget_s=_poll_budget_s("vision"))
        return (text or "").strip()

    async def embed(
        self,
        texts: list[str],
        *,
        thread_id: int | None = None,
        branch_id: int | None = None,
        kind: str = "embed",
    ) -> list[list[float]]:
        if not texts:
            return []
        self.calls += 1
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=_SYNC_TIMEOUT) as c:
                r = await c.post(
                    f"{self._url}/v1/embed",
                    params={"provider": "voyage", "workflow": kind},  # per-scenario dashboard tag
                    headers={"X-Project-Key": self._key},
                    json={"input": texts, "workflow": kind},
                )
            r.raise_for_status()
            d = r.json()
            embeddings = d["embeddings"]
            meta = {
                "model": d.get("model"), "provider": d.get("provider"),
                "tokens_in": d.get("tokens_in") or 0, "cost_usd": d.get("cost_usd") or 0,
                "elapsed_ms": int((time.perf_counter() - start) * 1000),
                "request_id": d.get("request_id") or d.get("id"),
            }
        except Exception as exc:
            await _log_call("embedding", kind, thread_id, branch_id,
                            {"elapsed_ms": int((time.perf_counter() - start) * 1000)},
                            ok=False, error=_err_text(exc))
            raise
        await _log_call("embedding", kind, thread_id, branch_id, meta, ok=True)
        return embeddings


def _err_text(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{exc.response.status_code} {exc.response.text[:200]}"
    return f"{type(exc).__name__}: {exc}"[:200]


async def _log_call(
    capability: str, workflow: str, thread_id: int | None, branch_id: int | None,
    meta: dict[str, Any], *, ok: bool, error: str | None = None,
) -> None:
    """Write one broker_log row. Fail-safe: a logging error must NEVER break a reply."""
    try:
        from app.adapters.db.models import BrokerLog
        from app.adapters.db.session import session_scope
        rid = meta.get("request_id")
        async with session_scope() as s:
            s.add(BrokerLog(
                request_id=str(rid) if rid is not None else None,
                branch_id=branch_id, thread_id=thread_id,
                kind=workflow, capability=capability,
                provider=meta.get("provider"), model=meta.get("model"),
                tokens_in=meta.get("tokens_in") or 0,
                tokens_out=meta.get("tokens_out") or 0,
                cost_usd=meta.get("cost_usd") or 0.0,
                latency_ms=meta.get("elapsed_ms"), ok=ok, error=error,
            ))
    except Exception as exc:  # noqa: BLE001 — logging must never break a reply
        _log.warning("broker_log write failed: %s", str(exc)[:120])
