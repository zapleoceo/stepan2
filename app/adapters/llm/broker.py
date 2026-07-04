"""Broker LLM adapter — the ONLY LLM path. Implements LLMPort over AIbroker HTTP.

No provider keys in this app: the broker holds them and returns cost_usd, so per-branch
budgeting uses the real broker price. Every call (reply/translate/embed/suggest) is logged
to broker_log — the single write point — for the /settings/log audit page."""
from __future__ import annotations

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
_SLOW_CAPS = frozenset({"chat:smart", "chat:edit"})


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
    ) -> tuple[str, dict[str, Any]]:
        body: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if require_json_schema:
            body["response_format"] = {"type": "json_object"}
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=_chat_timeout(capability)) as c:
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
