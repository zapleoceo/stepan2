"""Deploy-infra contract: the init migration builds the schema; the worker is wired."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import settings

_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(db_url: str) -> Config:
    """Alembic config pointed at the repo's migrations; env.py reads the url from settings."""
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_upgrade_head_creates_tables(tmp_path, monkeypatch):
    db_file = tmp_path / "infra.db"
    monkeypatch.setenv("STEPAN2_DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    settings.cache_clear()  # env.py reads settings().database_url — pick up the temp db

    command.upgrade(_alembic_config(f"sqlite+aiosqlite:///{db_file}"), "head")

    engine = create_engine(f"sqlite:///{db_file}")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    settings.cache_clear()

    assert {"branch", "lead", "channel", "outbox", "message"} <= tables
    assert "alembic_version" in tables  # migration actually ran and stamped


def test_kb_structure_migration_downgrades_cleanly(tmp_path, monkeypatch):
    """downgrade() must mirror upgrade()'s existence guards — a downgrade from head must
    not crash with 'column does not exist', and re-upgrading afterwards must still work."""
    db_file = tmp_path / "kb_downgrade.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setenv("STEPAN2_DATABASE_URL", db_url)
    settings.cache_clear()
    cfg = _alembic_config(db_url)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "a1b2c3d4e5f6")
    command.upgrade(cfg, "head")

    settings.cache_clear()


def test_worker_settings_exposes_tasks():
    from app.worker.main import (
        WorkerSettings,
        ingest_active_channels,
        reply_pending,
        send_outbox,
    )

    assert WorkerSettings.functions
    assert {ingest_active_channels, reply_pending, send_outbox} <= set(WorkerSettings.functions)
    assert all(callable(fn) for fn in WorkerSettings.functions)
