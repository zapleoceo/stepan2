"""Notifier port — manager alerts into a group. Telegram (forum topics) is one adapter;
the domain doesn't know the transport. Two primitives: open a per-lead topic and send a
message into it (or into the group's General when topic_id is None)."""
from __future__ import annotations

from typing import Literal, Protocol

SendStatus = Literal["ok", "topic_gone", "failed"]


class NotifierPort(Protocol):
    async def create_topic(self, *, name: str) -> int | None:
        """Open a forum topic named `name`; return its id (message_thread_id) or None."""
        ...

    async def send(self, *, text: str, topic_id: int | None = None) -> SendStatus:
        """Send `text` into topic_id (or General when None). 'topic_gone' when the topic
        was deleted (caller should recreate + resend), 'failed' on any other error."""
        ...
