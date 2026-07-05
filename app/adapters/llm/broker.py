"""Broker LLM adapter — the ONLY LLM path. Implements LLMPort over AIbroker HTTP.

No provider keys in this app: the broker holds them and returns cost_usd, so per-branch
budgeting uses the real broker price. Every call (reply/translate/embed/suggest) is logged
to broker_log — the single write point — for the /settings/log audit page."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.config import settings

_log = logging.getLogger(__name__)

# chat:smart (lead replies) and chat:edit (Coach) return a large JSON and the broker may
# fall back across providers — they need a long read timeout. Fast caps fail fast so one
# stuck call doesn't wedge the worker. Mirrors Stepan-1's broker_client timeouts.
_DEFAULT_TIMEOUT = httpx.Timeout(
    connect=5.0, read=settings().llm_read_timeout_s, write=10.0, pool=5.0)
_SLOW_TIMEOUT = httpx.Timeout(
    connect=5.0, read=settings().llm_read_timeout_slow_s, write=10.0, pool=5.0)
_SLOW_CAPS = frozenset({"chat:smart"})

# chat:deep is submit+poll now (2026-07-05, see BrokerLLM.chat_deep) — the broker itself
# made /v1/chat?capability=chat:deep return 400 unconditionally, because a single blocking
# HTTP call can't reliably carry a result that sometimes takes ~8 minutes (past Cloudflare's
# and the broker's own nginx proxy timeouts). llm_read_timeout_deep_s is now the total
# polling budget, not one HTTP request's read timeout — each individual submit/poll call
# uses the short _DEFAULT_TIMEOUT.


def _chat_timeout(capability: str) -> httpx.Timeout:
    return _SLOW_TIMEOUT if capability in _SLOW_CAPS else _DEFAULT_TIMEOUT


class BrokerLLM:
    """Implements app.ports.llm.LLMPort."""

    def __init__(self, base_url: str | None = None, project_key: str | None = None) -> None:
        s = settings()
        self._url = (base_url or s.broker_url).rstrip("/")
        self._key = project_key or s.broker_project_key

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
        timeout = _chat_timeout(capability)
        if read_timeout_s is not None:
            timeout = httpx.Timeout(connect=5.0, read=read_timeout_s, write=10.0, pool=5.0)
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(
                    f"{self._url}/v1/chat",
                    params={"capability": capability},
                    headers={"X-Project-Key": self._key},
                    json=body,
                )
            r.raise_for_status()
            # A 200 with a truncated body or missing keys is still a failed call — parse
            # inside the try so JSONDecodeError/KeyError also write an ok=false audit row.
            d = r.json()
            meta = {
                "model": d["model"],
                "tokens_in": d["tokens_in"],
                "tokens_out": d["tokens_out"],
                "provider": d["provider"],
                "cost_usd": d["cost_usd"],
                "elapsed_ms": int((time.perf_counter() - start) * 1000),
                "request_id": d.get("request_id") or d.get("id") or r.headers.get("x-request-id"),
            }
            reply_text = d["text"]
        except Exception as exc:
            await _log_call(capability, workflow or "chat", thread_id, branch_id,
                            {"elapsed_ms": int((time.perf_counter() - start) * 1000)},
                            ok=False, error=_err_text(exc))
            raise
        await _log_call(capability, workflow or "chat", thread_id, branch_id, meta, ok=True)
        return reply_text, meta

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
        """chat:deep — submit + poll (see module docstring for why). Falls back to
        chat:smart on 403 (project key doesn't have the llm:deep scope yet), same as
        the old inline fallback used to do for chat:deep through chat().

        require_json_schema is accepted for signature parity with chat() but NOT sent
        to the broker — /v1/deep doesn't take response_format at all (nemotron isn't
        JSON-reliable). Callers that need JSON out of chat:deep must already tolerate a
        markdown-fenced or slightly malformed body (propose_edit already does)."""
        body: dict[str, Any] = {
            "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
        }
        start = time.perf_counter()
        capability = "chat:deep"
        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as c:
                r = await c.post(
                    f"{self._url}/v1/deep",
                    headers={"X-Project-Key": self._key},
                    json=body,
                )
                if r.status_code == 403:
                    # llm:deep not granted on this project key yet — fall back to
                    # chat:smart so Coach keeps working; auto-upgrades the day the
                    # broker grants the scope.
                    return await self.chat(
                        messages, capability="chat:smart",
                        require_json_schema=require_json_schema,
                        max_tokens=min(max_tokens, 2000), temperature=temperature,
                        workflow=workflow, thread_id=thread_id, branch_id=branch_id,
                    )
                r.raise_for_status()
                job = r.json()
                job_id, poll_after_s = job["job_id"], job.get("poll_after_s") or 5

                deadline = start + settings().llm_read_timeout_deep_s
                while True:
                    await asyncio.sleep(poll_after_s)
                    pr = await c.get(
                        f"{self._url}/v1/deep/{job_id}",
                        headers={"X-Project-Key": self._key},
                    )
                    pr.raise_for_status()
                    d = pr.json()
                    if d["status"] == "done":
                        break
                    if d["status"] == "error":
                        raise RuntimeError(f"chat:deep job {job_id} failed: {d.get('error')}")
                    if time.perf_counter() > deadline:
                        raise TimeoutError(
                            f"chat:deep job {job_id} still pending after "
                            f"{settings().llm_read_timeout_deep_s:.0f}s"
                        )
                    poll_after_s = d.get("poll_after_s") or poll_after_s

            meta = {
                "model": d["model"],
                "tokens_in": d["tokens_in"],
                "tokens_out": d["tokens_out"],
                "provider": d["provider"],
                "cost_usd": d["cost_usd"],
                "elapsed_ms": int((time.perf_counter() - start) * 1000),
                "request_id": d.get("request_id"),
            }
            reply_text = d["text"]
        except Exception as exc:
            await _log_call(capability, workflow or "chat", thread_id, branch_id,
                            {"elapsed_ms": int((time.perf_counter() - start) * 1000)},
                            ok=False, error=_err_text(exc))
            raise
        await _log_call(capability, workflow or "chat", thread_id, branch_id, meta, ok=True)
        return reply_text, meta

    async def transcribe(
        self, audio: bytes, *, mime: str = "audio/mp4",
        thread_id: int | None = None, branch_id: int | None = None,
    ) -> str:
        """Speech-to-text for a voice message via the broker's /v1/transcribe. Returns the
        transcript text ('' if the broker returns none). Raises on transport/scope errors
        (the caller keeps the placeholder + retries) — needs the project key's llm:audio scope."""
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as c:
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
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as c:
                r = await c.post(
                    f"{self._url}/v1/embed",
                    params={"provider": "voyage"},
                    headers={"X-Project-Key": self._key},
                    json={"input": texts},
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
