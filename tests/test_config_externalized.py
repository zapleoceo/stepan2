"""Operational tuning constants are externalized to Settings, each with a description,
and the modules read from Settings rather than baked-in literals."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.config import Settings, settings  # noqa: E402

# fields that existed before this change (no description requirement imposed retroactively)
_LEGACY = {
    "database_url", "redis_url", "broker_url", "broker_project_key", "secret_key",
    "ig_proxy", "tg_bot_token", "bootstrap_super_admin", "auth_enabled", "session_secret",
    "tg_login_bot_username", "debug",
}


def test_every_new_setting_has_a_description() -> None:
    """The user asked for each externalized parameter to carry a description of its meaning."""
    missing = [
        name for name, f in Settings.model_fields.items()
        if name not in _LEGACY and not (f.description or "").strip()
    ]
    assert not missing, f"settings without a description: {missing}"


def test_operational_knobs_are_present_with_sane_defaults() -> None:
    s = settings()
    # the caps that must stay under the worker job timeout
    assert s.worker_job_timeout_s == 120
    assert s.reply_batch_cap == 10 and s.send_batch_cap == 15 and s.deletion_thread_cap == 3
    assert s.awaiting_reply_max_age_days == 3
    assert s.broker_log_retention_days == 30
    # anti-ban pacing
    assert s.bubble_gap_s == 6 and s.max_bubbles == 3
    assert s.seen_delay_min_s < s.seen_delay_max_s
    # llm cost knobs
    assert s.max_context_msgs == 40 and s.rag_top_k == 12 and s.translate_max_tokens == 1500
    # slow LLM timeout must stay under the worker job timeout or a chat call gets killed
    assert s.llm_read_timeout_slow_s < s.worker_job_timeout_s


def test_modules_read_from_settings_not_literals() -> None:
    from app.modules.conversation import reply
    from app.modules.conversation.repository import _MAX_CONTEXT_MSGS
    from app.modules.knowledge.rag import _TOP_K
    assert reply._BUBBLE_GAP_S == settings().bubble_gap_s
    assert reply._MAX_BUBBLES == settings().max_bubbles
    assert _MAX_CONTEXT_MSGS == settings().max_context_msgs
    assert _TOP_K == settings().rag_top_k


def test_public_url_default_is_empty() -> None:
    # the tenant-specific default was removed; it must be set per environment
    assert Settings.model_fields["public_url"].default == ""


def test_followup_schedule_fallback_matches_schema_default() -> None:
    from app.modules.settings.service import _parse_schedule
    # a garbage value must fall back to the documented schema default, not the old 4,24,72
    assert _parse_schedule({"followup_schedule_h": "not-numbers"}) == [1, 4, 24, 120]
