"""LLM port — the only way the domain talks to a model. Implemented by the broker
adapter. No provider keys anywhere in this app; the broker holds them and returns
cost_usd so budgeting is by real broker price, per branch."""
from __future__ import annotations

from typing import Any, Protocol


class LLMPort(Protocol):
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
        """Returns (text, meta). meta carries cost_usd / model / tokens for budgeting.
        workflow/thread_id/branch_id are audit context for broker_log (optional).
        read_timeout_s overrides the total poll budget for this one call (optional).

        The broker adapter runs every chat capability over the async job queue (submit +
        poll) so a slow provider can't 504 a held connection — the caller never sees the
        polling, just (text, meta)."""
        ...

    async def embed(
        self, texts: list[str], *, thread_id: int | None = None,
        branch_id: int | None = None, kind: str = "embed",
    ) -> list[list[float]]:
        ...

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
        """chat:deep — full-context/reasoning capability, same async job queue as chat()
        (submit /v1/jobs + poll). nemotron's real latency (up to ~8 min) far exceeds
        Cloudflare's/nginx's proxy timeouts, so a blocking call could never carry the
        result. Same (text, meta) shape as chat(); falls back to chat:smart when the
        project key lacks the llm:deep scope."""
        ...
