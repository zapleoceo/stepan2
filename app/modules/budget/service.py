"""BudgetService — per-branch daily LLM cost ledger + gate.

record() accumulates the broker's cost_usd per branch-local day; over_budget() answers
"may this branch spend more today?" against the daily_budget_usd setting (0 = off).
Single worker process → select-then-upsert is race-safe enough; a duplicate-day insert
loses to the unique constraint and is retried as an update."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import LlmSpend
from app.modules.settings.service import get_settings


def _branch_today(tz_offset_h: int) -> date:
    return (datetime.now(UTC) + timedelta(hours=tz_offset_h)).date()


class BudgetService:
    """Track and gate LLM spend for one branch."""

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        self.session = session
        self.branch_id = branch_id

    async def record(self, cost_usd: float) -> None:
        """Add one call's cost to today's ledger row (creates it on first call)."""
        if cost_usd < 0:
            return
        day = await self._today()
        row = await self._row(day)
        if row is None:
            row = LlmSpend(branch_id=self.branch_id, day=day)
            try:
                self.session.add(row)
                await self.session.flush()
            except IntegrityError:  # lost a same-day insert race — fall back to update
                await self.session.rollback()
                row = await self._row(day)
                if row is None:  # pragma: no cover — rollback removed it; re-create
                    row = LlmSpend(branch_id=self.branch_id, day=day)
                    self.session.add(row)
        row.used_usd = (row.used_usd or 0.0) + cost_usd
        row.calls = (row.calls or 0) + 1
        self.session.add(row)
        await self.session.flush()

    async def spent_today(self) -> float:
        row = await self._row(await self._today())
        return row.used_usd if row is not None else 0.0

    async def over_budget(self) -> bool:
        """True when today's spend reached daily_budget_usd (setting ≤ 0 = gate off)."""
        cfg = await get_settings(self.session, self.branch_id)
        limit = _to_float(getattr(cfg, "daily_budget_usd", 0))
        if limit <= 0:
            return False
        return await self.spent_today() >= limit

    async def _today(self) -> date:
        cfg = await get_settings(self.session, self.branch_id)
        return _branch_today(cfg.tz_offset_h)

    async def _row(self, day: date) -> LlmSpend | None:
        q = select(LlmSpend).where(
            LlmSpend.branch_id == self.branch_id, LlmSpend.day == day
        )
        return (await self.session.exec(q)).first()


def _to_float(raw: object) -> float:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
