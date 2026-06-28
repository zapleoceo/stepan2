"""Test fixtures: env defaults + in-memory SQLite session with all tables."""
import os

from cryptography.fernet import Fernet

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402

import app.adapters.db.models  # noqa: E402,F401 — register tables on metadata


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()
