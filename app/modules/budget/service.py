"""BudgetService — per-branch daily LLM cost ledger + gate.

record() accumulates the broker's cost_usd per branch-local day; over_budget() answers
"may this branch spend more today?" against the daily_budget_usd setting (0 = off).
Accumulation is a single atomic INSERT … ON CONFLICT DO UPDATE — no select-then-insert
race and (critically) no rollback on a caller-owned session."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.settings.service import get_settings

_UPSERT = (
    "INSERT INTO llm_spend (branch_id, day, used_usd, calls)"
    " VALUES (:b, :d, :c, 1)"
    " ON CONFLICT (branch_id, day) DO UPDATE"
    " SET used_usd = used_usd + excluded.used_usd, calls = calls + 1"
)


def _branch_today(tz_offset_h: int) -> date:
    return (datetime.now(UTC) + timedelta(hours=tz_offset_h)).date()


class BudgetService:
    """Track and gate LLM spend for one branch."""

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        self.session = session
        self.branch_id = branch_id

    async def record(self, cost_usd: float) -> None:
        """Add one call's cost to today's ledger row — atomic upsert, never rolls back."""
        if cost_usd <= 0:
            return
        day = await self._today()
        await self.session.execute(
            text(_UPSERT), {"b": self.branch_id, "d": day, "c": cost_usd}
        )
        await self.session.flush()

    async def spent_today(self) -> float:
        """Today's accumulated spend — raw read so it reflects the latest upsert."""
        day = await self._today()
        val = (
            await self.session.execute(
                text("SELECT used_usd FROM llm_spend WHERE branch_id=:b AND day=:d"),
                {"b": self.branch_id, "d": day},
            )
        ).scalar()
        return float(val) if val is not None else 0.0

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


def _to_float(raw: object) -> float:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
