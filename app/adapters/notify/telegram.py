"""Telegram notifier — per-lead forum topics in a branch's Telegram group.

Implements app.ports.notify.NotifierPort. The group must be a forum (topics enabled) and
the bot an admin with 'Manage topics'. httpx is imported lazily so the module loads
without the dep and unit tests inject a fake. Transport failure degrades gracefully: the
alert row is already persisted by the caller, so a missed ping must never raise."""
from __future__ import annotations

import logging
from typing import Any

from app.ports.notify import SendStatus

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org"
# Telegram errors that mean the forum topic no longer exists → recreate + resend.
_TOPIC_GONE = ("thread not found", "topic_deleted", "topic deleted",
               "message thread not found", "topic_closed", "topic id invalid")
# Forum-topic icon sticker ids (from getForumTopicIconStickers) keyed by emoji — a topic
# icon must be a custom_emoji_id from this fixed Telegram set, not an arbitrary emoji.
_ICON_EMOJI_ID = {
    "🔥": "5312241539987020022",   # ready_deal — hot lead / enrolling
    "📆": "5433614043006903194",   # ready_openhouse — event RSVP
    "❓": "5377316857231450742",   # needs_manager — open question
    "💰": "5350452584119279096",
    "📈": "5350305691942788490",
}


class TelegramNotifier:
    """Implements app.ports.notify.NotifierPort over the Telegram Bot API (forum topics)."""

    def __init__(self, *, bot_token: str, group_chat_id: int, base_url: str = _API) -> None:
        self._token = bot_token
        self._chat_id = group_chat_id
        self._base = base_url.rstrip("/")

    async def create_topic(self, *, name: str, icon_emoji: str | None = None) -> int | None:
        payload: dict[str, Any] = {"chat_id": self._chat_id, "name": name[:128]}
        icon_id = _ICON_EMOJI_ID.get(icon_emoji or "")
        if icon_id:
            payload["icon_custom_emoji_id"] = icon_id
        data = await self._call("createForumTopic", payload)
        if data and data.get("ok"):
            return int(data["result"]["message_thread_id"])
        return None

    async def send(self, *, text: str, topic_id: int | None = None) -> SendStatus:
        payload: dict[str, Any] = {
            "chat_id": self._chat_id, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }
        if topic_id is not None:
            payload["message_thread_id"] = topic_id
        data = await self._call("sendMessage", payload)
        if data and data.get("ok"):
            return "ok"
        desc = (data or {}).get("description", "").lower() if data else ""
        if topic_id is not None and any(tok in desc for tok in _TOPIC_GONE):
            return "topic_gone"
        return "failed"

    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """POST to the Bot API; return the parsed body (even on HTTP 4xx so callers can read
        `description`), or None on a transport error. Never raises — a missed ping is not fatal."""
        import httpx  # lazy: keep httpx out of the import path so the module loads without it

        try:
            url = f"{self._base}/bot{self._token}/{method}"
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(url, json=payload)
            return response.json()
        except Exception as exc:  # missed ping is non-fatal; the alert row already exists
            logger.warning("telegram %s failed (chat=%s): %s", method, self._chat_id, exc)
            return None
