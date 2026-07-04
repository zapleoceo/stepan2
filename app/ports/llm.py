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
    ) -> tuple[str, dict[str, Any]]:
        """Returns (text, meta). meta carries cost_usd / model / tokens for budgeting.
        workflow/thread_id/branch_id are audit context for broker_log (optional)."""
        ...

    async def embed(
        self, texts: list[str], *, thread_id: int | None = None,
        branch_id: int | None = None, kind: str = "embed",
    ) -> list[list[float]]:
        ...
