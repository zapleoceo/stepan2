"""SettingsProvider — 30 s TTL in-process cache over app_setting.

Values survive worker restarts (stored in DB). The 30 s cache avoids per-task
DB hits; call invalidate(branch_id) after any admin write to flush immediately.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from sqlmodel.ext.asyncio.session import AsyncSession

from app.domain.clock import branch_now

from .repository import SettingRepo
from .schema import defaults as _schema_defaults

# Single source of truth lives in schema.py; defaults derive from it (DRY).
_DEFAULTS: dict[str, str] = _schema_defaults()

_TTL = 30.0
_cache: dict[int, tuple[BranchSettings, float]] = {}
_lock = asyncio.Lock()


@dataclass(frozen=True)
class BranchSettings:
    agent_enabled: bool
    hourly_cap: int
    daily_cap: int
    quiet_start: int
    quiet_end: int
    reply_delay_min_s: int
    reply_delay_max_s: int
    tz_offset_h: int
    tg_group_id: str
    followup_enabled: bool
    followup_schedule_h: list[int]
    knowledge_backend: str
    tech_search_enabled: bool
    tech_usecase_enabled: bool
    daily_budget_usd: float
    crm_enabled: bool
    crm_webhook_url: str
    meta_pixel_id: str
    meta_capi_token: str

    def is_quiet_hour(self) -> bool:
        """True if the local branch time is inside the quiet window."""
        now_h = branch_now(self.tz_offset_h).hour
        if self.quiet_start > self.quiet_end:  # e.g. 22→08 wraps midnight
            return now_h >= self.quiet_start or now_h < self.quiet_end
        return self.quiet_start <= now_h < self.quiet_end


async def get_settings(session: AsyncSession, branch_id: int) -> BranchSettings:
    """Return cached settings; re-fetches from DB when the 30 s TTL has expired."""
    now = time.monotonic()
    cached = _cache.get(branch_id)
    if cached and now - cached[1] < _TTL:
        return cached[0]
    async with _lock:
        cached = _cache.get(branch_id)
        if cached and now - cached[1] < _TTL:
            return cached[0]
        raw = await SettingRepo(session).load_all(branch_id)
        s = _parse(raw)
        _cache[branch_id] = (s, now)
        return s


def invalidate(branch_id: int) -> None:
    """Drop the cached settings for a branch (e.g. after an admin write)."""
    _cache.pop(branch_id, None)


# ── internal helpers ──────────────────────────────────────────────────────────

def _b(raw: dict[str, str], key: str) -> bool:
    return raw.get(key, _DEFAULTS.get(key, "false")).lower() in ("true", "1", "yes")


def _i(raw: dict[str, str], key: str) -> int:
    default = _DEFAULTS.get(key, "0")
    try:
        return int(raw.get(key, default))
    except ValueError:
        try:
            return int(default)
        except ValueError:
            return 0


def _f(raw: dict[str, str], key: str) -> float:
    default = _DEFAULTS.get(key, "0")
    try:
        return float(raw.get(key, default) or default)
    except ValueError:
        try:
            return float(default)
        except ValueError:
            return 0.0


def _parse_schedule(raw: dict[str, str]) -> list[int]:
    val = raw.get("followup_schedule_h", _DEFAULTS.get("followup_schedule_h", "1,4,24,120"))
    try:
        return sorted({int(h.strip()) for h in val.split(",") if h.strip()})
    except ValueError:
        return [1, 4, 24, 120]  # must match schema default followup_schedule_h


def _parse(raw: dict[str, str]) -> BranchSettings:
    return BranchSettings(
        agent_enabled=_b(raw, "agent_enabled_global"),
        hourly_cap=_i(raw, "hourly_cap"),
        daily_cap=_i(raw, "daily_cap"),
        quiet_start=_i(raw, "quiet_start"),
        quiet_end=_i(raw, "quiet_end"),
        reply_delay_min_s=_i(raw, "reply_delay_min_s"),
        reply_delay_max_s=_i(raw, "reply_delay_max_s"),
        tz_offset_h=_i(raw, "tz_offset_h"),
        tg_group_id=raw.get("tg_group_id", _DEFAULTS.get("tg_group_id", "")),
        followup_enabled=_b(raw, "followup_enabled"),
        followup_schedule_h=_parse_schedule(raw),
        knowledge_backend=raw.get("knowledge_backend", "direct"),
        tech_search_enabled=_b(raw, "tech_search_enabled"),
        tech_usecase_enabled=_b(raw, "tech_usecase_enabled"),
        daily_budget_usd=_f(raw, "daily_budget_usd"),
        crm_enabled=_b(raw, "crm_enabled"),
        crm_webhook_url=raw.get("crm_webhook_url", ""),
        meta_pixel_id=raw.get("meta_pixel_id", ""),
        meta_capi_token=raw.get("meta_capi_token", ""),
    )
