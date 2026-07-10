"""Typed configuration from environment (Pydantic Settings) — no hand-rolled parsing.

Secrets (DB, broker key, Fernet key) come from env only; never hardcoded, never in VCS.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger("stepan2.config")


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
    # Branch staff for the bootstrap script — a JSON list of
    # {"tg": <id>, "name": "...", "role": "branch_admin|branch_viewer"}. Kept in env, not
    # VCS, so employee Telegram ids/names aren't committed. Empty → seed only the owner.
    bootstrap_staff_json: str = Field(
        default="",
        description='JSON list of branch staff for the bootstrap script: '
                    '[{"tg": id, "name": "...", "role": "branch_admin|branch_viewer"}]; '
                    "kept in env not VCS so employee ids/names aren't committed")

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
    # DB connection pool — sized above worker_max_jobs (each job nests several session_scope
    # opens) plus API concurrency, so a busy tick can't exhaust the pool and raise
    # TimeoutError on checkout. SQLAlchemy defaults (5 + 10 overflow) were too tight.
    db_pool_size: int = Field(
        default=20, description="SQLAlchemy pool_size (persistent connections)")
    db_max_overflow: int = Field(
        default=20, description="SQLAlchemy max_overflow (burst connections above pool_size)")
    db_pool_timeout_s: float = Field(
        default=30.0, description="seconds to wait for a pooled connection before TimeoutError")
    # ── Per-thread reply jobs ───────────────────────────────────────────────────
    # reply_pending only DISPATCHES: it enqueues one generate_one_reply job per awaiting
    # thread (deduped by _job_id=reply:{thread_id}). Each job polls the broker to completion
    # on its OWN timeout, so a slow generation no longer gets killed by the shared tick budget
    # (the head-of-line + timeout→retry→double-bill problem of the old batch loop).
    reply_broker_budget_s: float = Field(
        default=150.0, description="how long ONE reply waits for the broker per chat call before "
                                   "giving up — generous (vs the old 90s) so a slow provider "
                                   "completes instead of timing out and re-billing on retry")
    reply_job_timeout_s: int = Field(
        default=420, description="per-reply ARQ job kill deadline; must exceed the worst single "
                                 "thread: initial chat:smart + a guard regen (each up to "
                                 "reply_broker_budget_s) + a chat:fast verify")
    reply_dispatch_cap: int = Field(
        default=20, description="max threads the dispatcher enqueues per tick; excess over the "
                                "concurrency cap fast-return and re-dispatch next tick")
    reply_max_concurrency: int = Field(
        default=6, description="max SLOW reply jobs running at once — kept below worker_max_jobs "
                               "so a burst can't fill every worker slot and starve ingest/send")
    worker_job_timeout_s: int = Field(
        default=240, description="per-job kill deadline; every batch cap below is sized to "
                                 "finish inside this. Must comfortably clear a single thread's "
                                 "WORST case: one llm_read_timeout_slow_s (90s) initial call "
                                 "plus a guard regen also at 90s ceiling — 120s used to be "
                                 "tight enough that a broker running near its own timeout "
                                 "ceiling got this job killed mid-flight and retried, and the "
                                 "retry re-picked a thread whose advisory lock had already "
                                 "released → duplicate reply/send (2026-07-07)")
    ingest_jitter_s: float = Field(
        default=12.0, description="random 0..N s delay before each IG inbox poll so calls don't "
                                  "hit on a machine-regular tick (anti-ban)")
    reply_batch_cap: int = Field(
        default=5, description="threads decided per reply_pending tick; too high overruns the "
                               "job timeout → retry duplicates. Halved alongside the raised "
                               "worker_job_timeout_s so a run of several guard-triggered "
                               "(near-worst-case) threads in one tick still finishes with room "
                               "to spare, not just the average case")
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
    outbox_max_soft_block_attempts: int = Field(
        default=8, description="give up and mark 'failed' after this many soft-block retries "
                               "(~8 × soft_block_retry_min ≈ 2h) — a permanent block used to "
                               "retry forever and never surface to a human")

    # ── LLM cost / quality knobs ────────────────────────────────────────────────
    llm_read_timeout_s: float = Field(
        default=70.0, description="HTTP read timeout for normal broker calls (chat:fast/translate/"
                                  "embed/suggest); the broker can be spiky, 20s cut off simple "
                                  "replies — waiting longer is cheaper than dropping the answer. "
                                  "Kept a margin above the broker's own chat:fast/chat:smart "
                                  "server-side ceiling (60s as of 2026-07-07) — at an equal "
                                  "value this raced a raw httpx.ReadTimeout against the broker's "
                                  "own clean error response, so a stuck call surfaced as an "
                                  "unstructured transport abort instead of a retriable HTTP "
                                  "status, with no time left for FAST→SMART escalation")
    llm_read_timeout_slow_s: float = Field(
        default=90.0, description="HTTP read timeout for chat:smart/chat:edit (long JSON, "
                                  "provider fallback); keep < worker_job_timeout_s")
    llm_read_timeout_deep_s: float = Field(
        default=600.0, description="HTTP read timeout for chat:deep (full-context analysis, "
                                   "the model may think for minutes); background/batch ONLY, "
                                   "never a live reply handler")
    max_context_msgs: int = Field(
        default=30, description="dialog messages fed to the reply LLM — the main per-reply "
                                "token-cost bound (was 40; a chat rarely needs more than "
                                "~15 turns back to stay coherent, and the focus+RAG blocks "
                                "carry the facts, not the raw history depth)")
    rag_top_k: int = Field(
        default=8, description="knowledge chunks retrieved per reply; higher = better recall "
                               "but more tokens/cost (was 12 — the focused product's own "
                               "chunks are now excluded via exclude_slug, so fewer slots are "
                               "needed to cover the OTHER docs/products a query might touch)")
    dialog_char_budget: int = Field(
        default=8000, description="char bound on the dialog history fed to the reply LLM, on "
                                  "top of max_context_msgs — trims the oldest tail of a wordy "
                                  "thread (the top-10 longest threads carried 5-13k chars in "
                                  "their newest 30 messages) while the newest turns stay "
                                  "verbatim for the dedup/don't-repeat checks")
    knowledge_context_char_budget: int = Field(
        default=16000, description="char ceiling on the assembled KB context (persona + focus "
                                   "card + catalog + RAG chunks) — lowest-ranked RAG chunks "
                                   "are dropped to fit. Past ~30k chars the cheap JSON-mode "
                                   "providers stop returning valid JSON at all (verified live "
                                   "on deepseek), so an oversized context buys empty "
                                   "responses, not recall")
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

    def validate_runtime(self) -> None:
        """Fail-fast at boot on config that would otherwise break silently at first use.

        Raises for hard invariants (a misconfig that guarantees a broken request later);
        logs a WARNING for soft ones (a disabled-but-shippable feature). Called once on
        both the API app and the worker startup."""
        if self.auth_enabled and not (self.session_secret or self.secret_key):
            raise ValueError(
                "STEPAN2_AUTH_ENABLED=true but neither STEPAN2_SESSION_SECRET nor "
                "STEPAN2_SECRET_KEY is set — session cookies cannot be signed")
        if self.bootstrap_staff_json.strip():
            try:
                staff = json.loads(self.bootstrap_staff_json)
            except json.JSONDecodeError as e:
                raise ValueError(f"STEPAN2_BOOTSTRAP_STAFF_JSON is not valid JSON: {e}") from e
            if not isinstance(staff, list):
                raise ValueError("STEPAN2_BOOTSTRAP_STAFF_JSON must be a JSON list of staff")
        if not self.auth_enabled:
            _log.warning("STEPAN2_AUTH_ENABLED is not true — the /ui admin surface and every "
                         "state-changing POST are served with NO authentication. Fine for local "
                         "dev; NEVER run a public deployment this way.")
        if not self.broker_url:
            _log.warning("STEPAN2_BROKER_URL is empty — all LLM calls will fail; "
                         "no replies/translations/embeddings until set")
        if not self.secret_key:
            _log.warning("STEPAN2_SECRET_KEY is empty — channel session secrets cannot be "
                         "encrypted/decrypted; adding a channel with a secret will fail")


@lru_cache
def settings() -> Settings:
    """Process-wide singleton: env is read ONCE, at first call, and frozen for the life of
    the process. Changing an environment variable requires a restart of the API/worker to
    take effect — these are deploy-time knobs, not runtime-tunable. Per-branch settings that
    DO change at runtime live in the DB (BranchSettings), not here."""
    return Settings()  # type: ignore[call-arg]
