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
    ) -> tuple[str, dict[str, Any]]:
        """Returns (text, meta). meta carries cost_usd / model / tokens for budgeting."""
        ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...
