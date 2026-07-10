"""SettingsProvider — 30 s TTL in-process cache over app_setting.

Values survive worker restarts (stored in DB). The 30 s cache avoids per-task
DB hits; call invalidate(branch_id) after any admin write to flush immediately.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch
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
    tech_search_enabled: bool
    tech_usecase_enabled: bool
    daily_budget_usd: float
    crm_enabled: bool
    crm_webhook_url: str
    meta_pixel_id: str
    meta_capi_token: str
    # defaulted + last so adding them doesn't break callers that build BranchSettings
    # positionally (dataclass requires defaulted fields after non-defaulted ones).
    crm_read_enabled: bool = False
    crm_state_url: str = ""
    crm_read_secret: str = ""
    # 'hybrid' → route cheap turns to chat:fast, keep chat:smart for money moments;
    # 'off' → always chat:smart (pre-optimisation behaviour). See conversation.routing.
    reply_routing: str = "hybrid"
    # Comma-list of stages that keep the strong model under hybrid routing (operator-tunable).
    smart_stages: str = "presenting,objection,ready"
    # Reply-guard against fabrication: 'full' (deterministic URL check + LLM grounding
    # verify on risky replies), 'urls' (deterministic only), 'off'. See conversation.guard.
    reply_guard: str = "full"
    # Trunk country code for phones mined from a lead's free-text message (see
    # leads.phone.extract_phone). Default Indonesia "62"; set per branch so a non-Indonesian
    # branch doesn't stamp its leads' local numbers as +62.
    phone_country_code: str = "62"
    # Independent from agent_enabled: agent_enabled gates whether Stepan SCANS incoming and
    # QUEUES a reply (reply_pending/schedule_followups); this gates whether send_outbox
    # actually DRAINS the queue. Off = the queue just accumulates, nothing goes out — the
    # lever to pull when the channel/account is soft-blocked but you still want to keep
    # capturing incoming while sending is paused. See worker.main.send_outbox.
    sending_enabled: bool = True
    # Single Meta connector (App ID, Business ID, Ad Account ID, Page ID, Pixel ID) plus one
    # System User token scoped for ads + pixel + page/IG messaging — replaces the older split
    # meta_capi_token/meta_ads_token pair, kept above for backward compatibility.
    meta_app_id: str = ""
    fb_account_id: str = ""
    meta_page_id: str = ""
    meta_system_user_token: str = ""

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
        parsed = _parse(raw)
        branch = await session.get(Branch, branch_id)
        if branch is not None:  # timezone lives on the branch, not in app_setting
            parsed = replace(parsed, tz_offset_h=branch.tz_offset_h)
        _cache[branch_id] = (parsed, now)
        return parsed


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
        tz_offset_h=int(raw.get("tz_offset_h") or 7),  # overridden from the branch row
        tg_group_id=raw.get("tg_group_id", _DEFAULTS.get("tg_group_id", "")),
        followup_enabled=_b(raw, "followup_enabled"),
        followup_schedule_h=_parse_schedule(raw),
        tech_search_enabled=_b(raw, "tech_search_enabled"),
        tech_usecase_enabled=_b(raw, "tech_usecase_enabled"),
        daily_budget_usd=_f(raw, "daily_budget_usd"),
        crm_enabled=_b(raw, "crm_enabled"),
        crm_webhook_url=raw.get("crm_webhook_url", ""),
        crm_read_enabled=_b(raw, "crm_read_enabled"),
        crm_state_url=raw.get("crm_state_url", ""),
        crm_read_secret=raw.get("crm_read_secret", ""),
        meta_pixel_id=raw.get("meta_pixel_id", ""),
        meta_capi_token=raw.get("meta_capi_token", ""),
        reply_routing=raw.get("reply_routing", "hybrid"),
        smart_stages=raw.get("smart_stages", "presenting,objection,ready"),
        reply_guard=raw.get("reply_guard", "full"),
        phone_country_code=raw.get("phone_country_code", "62"),
        sending_enabled=_b(raw, "sending_enabled"),
        meta_app_id=raw.get("meta_app_id", ""),
        fb_account_id=raw.get("fb_account_id", ""),
        meta_page_id=raw.get("meta_page_id", ""),
        meta_system_user_token=raw.get("meta_system_user_token", ""),
    )
