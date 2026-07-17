"""Test fixtures: env defaults + in-memory SQLite session with all tables."""
import os

from cryptography.fernet import Fernet

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402

import app.adapters.db.models  # noqa: E402,F401 — register tables on metadata


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    # The settings resolver caches by (branch_id, channel_id) for 30 s. Every test builds a
    # fresh in-memory DB where branch ids restart at 1, so without a reset a value cached by one
    # test (e.g. sending_enabled=false) leaks into the next test reusing branch 1 within the TTL.
    # Also drop the process-global @lru_cache on app.config.settings(): a test that monkeypatches
    # settings in ONE module (e.g. test_hiw's app.api._auth.settings) leaves the real lru_cached
    # instance live for any code path reading it elsewhere, so under pytest-randomly's shuffle an
    # auth-gate test would intermittently see a stale settings object (order-dependent flake).
    from app.config import settings as _global_settings
    from app.modules.settings.service import _cache
    _cache.clear()
    _global_settings.cache_clear()
    yield
    _cache.clear()
    _global_settings.cache_clear()


@pytest_asyncio.fixture
async def db_session():
    # StaticPool pins the engine to ONE in-memory connection. Without it the pool can hand
    # create_all and the session different connections — each its own fresh, empty ":memory:"
    # DB — so the session intermittently sees "no such table: branch" (a flaky failure that
    # surfaced under pytest-randomly's ordering). One connection = one schema, always.
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()
