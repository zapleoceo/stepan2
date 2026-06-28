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

    # Admin/super-admin bootstrap (Telegram id of the first super_admin)
    bootstrap_super_admin: int = Field(default=0)

    debug: bool = Field(default=False)


@lru_cache
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
