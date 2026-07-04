"""BudgetService — per-branch daily LLM cost ledger + gate.

record() accumulates the broker's cost_usd per branch-local day; over_budget() answers
"may this branch spend more today?" against the daily_budget_usd setting (0 = off).
Accumulation is a single atomic INSERT … ON CONFLICT DO UPDATE — no select-then-insert
race and (critically) no rollback on a caller-owned session."""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domain.clock import branch_today
from app.modules.settings.service import get_settings

# The table name MUST qualify the value-side reference (llm_spend.used_usd), not just
# the SET target — Postgres raises "column reference is ambiguous" for a bare `used_usd`
# on the right of `=` because both the target row and `excluded` expose that name. SQLite
# (unit tests) accepts the unqualified form fine, so this broke silently in prod only:
# real incident — every call with cost_usd > 0 raised inside record(), which propagated
# out of decide() and silently discarded an already-paid-for LLM reply.
_UPSERT = (
    "INSERT INTO llm_spend (branch_id, day, used_usd, calls)"
    " VALUES (:b, :d, :c, 1)"
    " ON CONFLICT (branch_id, day) DO UPDATE"
    " SET used_usd = llm_spend.used_usd + excluded.used_usd,"
    " calls = llm_spend.calls + 1"
)


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
        return branch_today(cfg.tz_offset_h)


def _to_float(raw: object) -> float:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
