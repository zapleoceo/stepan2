"""AppSetting repository — batch load with branch override of platform values."""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import AppSetting


class SettingRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def load_all(self, branch_id: int) -> dict[str, str]:
        """Load settings for branch; branch-specific values override platform-wide (NULL)."""
        result = await self._s.execute(
            select(AppSetting).where(
                or_(AppSetting.branch_id == branch_id, AppSetting.branch_id.is_(None))
            )
        )
        platform: dict[str, str] = {}
        branch: dict[str, str] = {}
        for row in result.scalars().all():
            if row.branch_id is None:
                platform[row.key] = row.value
            else:
                branch[row.key] = row.value
        return {**platform, **branch}
