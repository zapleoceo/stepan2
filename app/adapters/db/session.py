"""Async engine + session (SQLModel/SQLAlchemy). One DB; isolation is by branch_id."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        cfg = settings()
        # SQLite (tests) uses a StaticPool-style default and rejects pool_size kwargs — only
        # pass pool sizing to a real server engine (Postgres). The worker runs many
        # concurrent jobs, each nesting session_scope opens, so the pool must clear
        # worker_max_jobs + API load or checkout blocks then TimeoutErrors under load.
        kwargs: dict = {"pool_pre_ping": True}
        if not cfg.database_url.startswith("sqlite"):
            kwargs |= {
                "pool_size": cfg.db_pool_size,
                "max_overflow": cfg.db_max_overflow,
                "pool_timeout": cfg.db_pool_timeout_s,
            }
        _engine = create_async_engine(cfg.database_url, **kwargs)
        _sessionmaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    return _engine


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Commit on success, rollback on error. The only way to get a session."""
    engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise
