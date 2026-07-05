"""Typed configuration from environment (Pydantic Settings) — no hand-rolled parsing.

Secrets (DB, broker key, Fernet key) come from env only; never hardcoded, never in VCS.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STEPAN2_", env_file=".env", extra="ignore")

    database_url: str = Field(..., description="postgresql+asyncpg://…")
    redis_url: str = Field(default="redis://localhost:6379", description="ARQ broker")

    # LLM — broker only (no provider keys live here or anywhere in this app)
    broker_url: str = Field(default="", description="AIbroker base URL")
    broker_project_key: str = Field(default="", description="X-Project-Key for AIbroker")

    # Fernet key for encrypting channel session secrets at rest
    secret_key: str = Field(default="", description="Fernet key for session/secret encryption")

    # Instagram private-API proxy (same geo as login — avoids checkpoint). Empty = none.
    ig_proxy: str = Field(default="", description="proxy URL for instagrapi transport")

    # Telegram bot token for manager alerts (BotFather token — never in VCS)
    tg_bot_token: str = Field(default="", description="Telegram bot token for manager pings")
    public_url: str = Field(default="",
                            description="public base URL (e.g. https://host) for chat "
                                        "deep-links in manager Telegram alerts; empty = no link")

    # Admin/super-admin bootstrap (Telegram id of the first super_admin)
    bootstrap_super_admin: int = Field(default=0)

    # Auth gate — opt-in so the code can ship before the login bot/domain are wired.
    auth_enabled: bool = Field(default=False, description="enforce session auth on /ui + /admin")
    # HMAC secret for the session cookie; falls back to secret_key when empty.
    session_secret: str = Field(default="")
    # Bot username for the Telegram Login widget (domain bound via BotFather).
    tg_login_bot_username: str = Field(default="")
    # Bearer token for the MCP connector's /mcp API. Empty → the API is disabled (403).
    mcp_secret: str = Field(default="", description="Bearer token gating the /mcp lead-ops API")
    # Separate token(s) for the READ-ONLY reader MCP at /reader (dialogs + analysis). Kept
    # apart from mcp_secret so a reviewer's read access can't move the funnel.
    mcp_read_secret: str = Field(
        default="", description="Bearer token(s) gating the read-only /reader MCP")

    # CRM read link (the gate that stops Stepan re-touching a lead a manager already owns).
    crm_read_timeout_s: float = Field(default=8.0, description="per-request CRM read timeout")
    crm_state_ttl_s: int = Field(
        default=300, description="a cached CRM state newer than this is trusted; older → the "
                                "pre-send point-check refetches live")

    # ── Worker cadence & batch caps ─────────────────────────────────────────────
    # These are pinned around worker_job_timeout_s: a tick must finish inside it or ARQ
    # kills+retries it, and a retry can re-pick a thread whose lock already released →
    # duplicate replies / duplicate real IG sends (both happened in prod). Tune together.
    worker_max_jobs: int = Field(
        default=10, description="max concurrent ARQ jobs (higher = more parallel IG sessions "
                                "and DB load)")
    worker_job_timeout_s: int = Field(
        default=120, description="per-job kill deadline; every batch cap below is sized to "
                                 "finish inside this")
    ingest_jitter_s: float = Field(
        default=12.0, description="random 0..N s delay before each IG inbox poll so calls don't "
                                  "hit on a machine-regular tick (anti-ban)")
    reply_batch_cap: int = Field(
        default=10, description="threads decided per reply_pending tick; too high overruns the "
                                "job timeout → retry duplicates")
    send_batch_cap: int = Field(
        default=15, description="outbox rows sent per send_outbox tick; too high overruns the "
                                "timeout → duplicate real IG sends")
    deletion_thread_cap: int = Field(
        default=3, description="threads processed per unsend tick (each IG revoke is slow, "
                               "~40-90s)")
    awaiting_reply_max_age_days: int = Field(
        default=3, description="a thread whose last inbound is older than this is NOT auto-"
                               "replied — stops a re-enabled branch mass-blasting old backlog")
    broker_log_retention_days: int = Field(
        default=30, description="broker_log rows older than this are pruned daily (disk vs "
                                "audit history)")

    # ── Anti-ban message pacing ─────────────────────────────────────────────────
    bubble_gap_s: int = Field(
        default=6, description="seconds between the split bubbles of one reply (human typing "
                               "cadence)")
    max_bubbles: int = Field(
        default=3, description="max messages one reply is split into; 4+ rapid DMs raises spam "
                               "detection risk")
    seen_delay_min_s: float = Field(
        default=2.0, description="min pause after marking a chat seen before sending (fake "
                                 "human read time)")
    seen_delay_max_s: float = Field(
        default=5.0, description="max pause after marking a chat seen before sending")
    soft_block_retry_min: int = Field(
        default=15, description="minutes to back off a channel after a soft block (rate limit / "
                                "challenge) before retrying sends")

    # ── LLM cost / quality knobs ────────────────────────────────────────────────
    llm_read_timeout_s: float = Field(
        default=20.0, description="HTTP read timeout for normal broker calls (translate/embed/"
                                  "suggest)")
    llm_read_timeout_slow_s: float = Field(
        default=90.0, description="HTTP read timeout for chat:smart/chat:edit (long JSON, "
                                  "provider fallback); keep < worker_job_timeout_s")
    llm_read_timeout_deep_s: float = Field(
        default=600.0, description="HTTP read timeout for chat:deep (full-context analysis, "
                                   "the model may think for minutes); background/batch ONLY, "
                                   "never a live reply handler")
    max_context_msgs: int = Field(
        default=40, description="dialog messages fed to the reply LLM — the main per-reply "
                                "token-cost bound")
    rag_top_k: int = Field(
        default=12, description="knowledge chunks retrieved per reply; higher = better recall "
                                "but more tokens/cost")
    translate_max_tokens: int = Field(
        default=1500, description="output token budget for a per-bubble translation (Cyrillic "
                                  "is token-heavy)")

    # ── Pinned external API versions (bump without a code deploy) ────────────────
    meta_graph_version: str = Field(
        default="v18.0", description="Facebook Graph API version for Meta CAPI Lead events")
    ig_graph_version: str = Field(
        default="v21.0", description="Instagram Graph API version (Meta Business channel "
                                     "base URL fallback)")

    debug: bool = Field(default=False)


@lru_cache
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
