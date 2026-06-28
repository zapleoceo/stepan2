"""Alembic environment — async, schema owned by SQLModel.metadata.

URL comes from app.config (never hardcoded); importing app.adapters.db.models registers
every table on the shared metadata so autogenerate sees the full schema."""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

import app.adapters.db.models  # noqa: F401 — registers tables on SQLModel.metadata
from app.config import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def _url() -> str:
    """Single source for the DB url — env via app.config, never the ini file."""
    return settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL without a DBAPI connection (`alembic upgrade --sql`)."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


def run_migrations_online() -> None:
    """Run migrations against a live async engine via run_sync."""
    asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
