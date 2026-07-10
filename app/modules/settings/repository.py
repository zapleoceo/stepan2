"""AppSetting repository — three-tier load merging platform → branch → connector."""
from __future__ import annotations

from sqlalchemy import or_, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import AppSetting

# One portable upsert for every app_setting write. The conflict target matches the
# COALESCE-based unique index (uq_setting_scope), so it upserts correctly at all three
# tiers — platform (NULL,NULL), branch (bid,NULL), connector (bid,cid) — on SQLite and
# Postgres alike. Centralised here so no route hand-rolls ON CONFLICT SQL (DRY).
_UPSERT = text(
    "INSERT INTO app_setting (branch_id, channel_id, key, value) VALUES (:b, :c, :k, :v)"
    " ON CONFLICT (COALESCE(branch_id, 0), COALESCE(channel_id, 0), key)"
    " DO UPDATE SET value = excluded.value"
)


class SettingRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert(
        self, key: str, value: str, *, branch_id: int | None = None,
        channel_id: int | None = None,
    ) -> None:
        """Set one setting at the given scope, inserting or updating in place."""
        await self._s.execute(
            _UPSERT, {"b": branch_id, "c": channel_id, "k": key, "v": value})

    async def load_all(self, branch_id: int, channel_id: int | None = None) -> dict[str, str]:
        """Merged settings by increasing precision: platform (branch_id NULL) → branch
        (branch_id, channel_id NULL) → connector (branch_id, channel_id).

        Without channel_id this is the branch view (connector tier skipped). With it, a
        per-connector override wins over the branch value, which wins over the platform value.
        """
        result = await self._s.execute(
            select(AppSetting).where(
                or_(AppSetting.branch_id == branch_id, AppSetting.branch_id.is_(None))
            )
        )
        platform: dict[str, str] = {}
        branch: dict[str, str] = {}
        connector: dict[str, str] = {}
        for row in result.scalars().all():
            if row.branch_id is None:
                platform[row.key] = row.value
            elif row.channel_id is None:
                branch[row.key] = row.value
            elif row.channel_id == channel_id:
                connector[row.key] = row.value
        return {**platform, **branch, **connector}
