"""Telegram notifier — manager hand-off pings to a branch's Telegram group.

Implements app.ports.notify.NotifierPort. The group id is injected per branch (no
globals); httpx is imported lazily so the module loads without the dep and unit tests
inject a fake send instead. Transport failure degrades gracefully — the alert row is
already persisted by the caller, so a missed ping must never raise."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org"


class TelegramNotifier:
    """Implements app.ports.notify.NotifierPort over the Telegram Bot API."""

    def __init__(self, *, bot_token: str, group_chat_id: int, base_url: str = _API) -> None:
        self._token = bot_token
        self._chat_id = group_chat_id
        self._base = base_url.rstrip("/")

    async def notify_manager(
        self,
        *,
        branch_id: int,
        lead_id: int,
        kind: str,
        summary_en: str,
        summary_ru: str,
    ) -> None:
        text = _render(kind=kind, lead_id=lead_id, summary_en=summary_en, summary_ru=summary_ru)
        await self._send(text)

    async def _send(self, text: str) -> dict[str, Any] | None:
        """POST to sendMessage; on any transport error log a warning and return None."""
        import httpx  # lazy: keep httpx out of the import path so the module loads without it

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{self._base}/bot{self._token}/sendMessage",
                    json={"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"},
                )
            response.raise_for_status()
        except Exception as exc:  # missed ping is non-fatal; the alert row already exists
            logger.warning("telegram notify failed (chat=%s): %s", self._chat_id, exc)
            return None
        return response.json()


def _render(*, kind: str, lead_id: int, summary_en: str, summary_ru: str) -> str:
    """Bilingual EN/RU manager message — one block per language under a kind header."""
    return (
        f"<b>{kind}</b> · lead #{lead_id}\n\n"
        f"EN: {summary_en}\n"
        f"RU: {summary_ru}"
    )
