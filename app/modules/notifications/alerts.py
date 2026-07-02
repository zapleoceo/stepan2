"""Alert service — one place that records a manager hand-off AND pings the manager.

Persisting the ManagerAlert row and calling the NotifierPort live together so the CRM
record and the ping can never drift apart. Branch isolation comes from BranchScoped; the
notifier is injected (a fake in tests) so the domain stays transport-agnostic."""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import ManagerAlert
from app.adapters.db.repository import BranchScoped
from app.ports.notify import NotifierPort


class AlertService:
    """Records and dispatches manager hand-offs for one branch."""

    def __init__(
        self, session: AsyncSession, branch_id: int, notifier: NotifierPort | None
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self._notifier = notifier
        self._alerts: BranchScoped[ManagerAlert] = BranchScoped(
            session, branch_id, model=ManagerAlert
        )

    async def raise_alert(
        self,
        lead_id: int,
        kind: str,
        summary_en: str,
        summary_ru: str,
        thread_id: int | None = None,
        lead_phone: str | None = None,
    ) -> ManagerAlert:
        """Write the branch-scoped alert row, then ping the manager with the same summaries."""
        alert = await self._alerts.add(
            ManagerAlert(
                branch_id=self.branch_id,
                lead_id=lead_id,
                thread_id=thread_id,
                kind=kind,
                lead_phone=lead_phone,
                summary_en=summary_en,
                summary_ru=summary_ru,
            )
        )
        if self._notifier is not None:  # row is the CRM record; the ping is best-effort
            await self._notifier.notify_manager(
                branch_id=self.branch_id,
                lead_id=lead_id,
                kind=kind,
                summary_en=summary_en,
                summary_ru=summary_ru,
            )
        return alert
