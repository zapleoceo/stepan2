"""Notifier port — manager hand-off / alerts. Telegram is one adapter; the domain
doesn't know the transport."""
from __future__ import annotations

from typing import Protocol


class NotifierPort(Protocol):
    async def notify_manager(
        self,
        *,
        branch_id: int,
        lead_id: int,
        kind: str,           # ready_deal | ready_openhouse | needs_manager
        summary_en: str,
        summary_ru: str,
        link: str | None = None,
    ) -> None:
        ...
