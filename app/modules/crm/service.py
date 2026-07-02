"""CrmSyncService — push unsynced manager alerts to the branch CRM webhook.

manager_alert.synced_at is the sync watermark (NULL = pending, mirrors S1's Apix
pattern but push-based). Gated by settings: crm_enabled + crm_webhook_url. A failed
POST leaves the row unsynced; the next tick retries."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import ManagerAlert
from app.modules.settings.service import get_settings

logger = logging.getLogger(__name__)


class CrmTransport(Protocol):
    async def post_alert(self, url: str, payload: dict[str, Any]) -> bool: ...


class CrmSyncService:
    """Sync one branch's pending alerts; returns how many were confirmed synced."""

    def __init__(
        self, session: AsyncSession, branch_id: int, transport: CrmTransport
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.transport = transport

    async def sync_pending(self, limit: int = 20) -> int:
        cfg = await get_settings(self.session, self.branch_id)
        url = (cfg.crm_webhook_url or "").strip()
        if not cfg.crm_enabled or not url.startswith("http"):
            return 0
        rows = await self._pending(limit)
        synced = 0
        for alert in rows:
            if await self.transport.post_alert(url, _payload(alert)):
                alert.synced_at = datetime.now(UTC).replace(tzinfo=None)
                self.session.add(alert)
                synced += 1
        if synced:
            await self.session.flush()
            logger.info("crm sync branch=%d: %d/%d alerts", self.branch_id, synced, len(rows))
        return synced

    async def _pending(self, limit: int) -> list[ManagerAlert]:
        q = (
            select(ManagerAlert)
            .where(
                ManagerAlert.branch_id == self.branch_id,
                ManagerAlert.synced_at.is_(None),  # type: ignore[union-attr]
            )
            .order_by(ManagerAlert.id)
            .limit(limit)
        )
        return list((await self.session.exec(q)).all())


def _payload(alert: ManagerAlert) -> dict[str, Any]:
    """Flat JSON the CRM side can map without knowing our schema."""
    return {
        "id": alert.id,
        "branch_id": alert.branch_id,
        "lead_id": alert.lead_id,
        "thread_id": alert.thread_id,
        "kind": alert.kind,
        "actor": alert.actor,
        "lead_phone": alert.lead_phone,
        "summary_en": alert.summary_en,
        "summary_ru": alert.summary_ru,
        "created_at": alert.created_at.isoformat(),
    }
