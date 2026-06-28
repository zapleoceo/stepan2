"""Broker LLM adapter — the ONLY LLM path. Implements LLMPort over AIbroker HTTP.

No provider keys in this app: the broker holds them and returns cost_usd, so per-branch
budgeting uses the real broker price."""
from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


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
    ) -> tuple[str, dict[str, Any]]:
        body: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if require_json_schema:
            body["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{self._url}/v1/chat",
                params={"capability": capability},
                headers={"X-Project-Key": self._key},
                json=body,
            )
        r.raise_for_status()
        d = r.json()
        meta = {
            "model": d["model"],
            "tokens_in": d["tokens_in"],
            "tokens_out": d["tokens_out"],
            "provider": d["provider"],
            "cost_usd": d["cost_usd"],
        }
        return d["text"], meta

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{self._url}/v1/embed",
                params={"provider": "voyage"},
                headers={"X-Project-Key": self._key},
                json={"input": texts},
            )
        r.raise_for_status()
        return r.json()["embeddings"]
