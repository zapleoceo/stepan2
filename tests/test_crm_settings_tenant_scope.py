"""A CRM link belongs to one tenant: never inherited from the platform tier, always
editable by the branch, and the platform rows that existed move onto branch 1.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

from alembic.migration import MigrationContext  # noqa: E402
from alembic.operations import Operations  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.adapters.db.models import AppSetting, Branch  # noqa: E402
from app.api._ui_settings import settings_form_html  # noqa: E402
from app.modules.settings import schema as S  # noqa: E402
from app.modules.settings.service import get_settings, invalidate  # noqa: E402

_URL = "https://mcp.example/mcp/crm?token=secret"
_TENANT_KEYS = ("crm_mcp_url", "crm_mcp_city_alias",
                "crm_rescue_enabled", "crm_writeback_enabled")


async def _branch(s, name: str = "Malaysia") -> int:
    b = Branch(name=name, lang="ms")
    s.add(b)
    await s.flush()
    invalidate(b.id)
    return b.id


# ── the resolver ────────────────────────────────────────────────────────────────

async def test_platform_crm_row_does_not_reach_a_branch(db_session) -> None:
    bid = await _branch(db_session)
    db_session.add(AppSetting(branch_id=None, key="crm_mcp_url", value=_URL))
    db_session.add(AppSetting(branch_id=None, key="crm_mcp_city_alias", value="jakarta"))
    await db_session.flush()
    invalidate(bid)
    cfg = await get_settings(db_session, bid)
    assert cfg.crm_mcp_url == ""
    assert cfg.crm_mcp_city_alias == ""


async def test_the_branchs_own_crm_row_is_used(db_session) -> None:
    bid = await _branch(db_session)
    db_session.add(AppSetting(branch_id=bid, key="crm_mcp_url", value=_URL))
    await db_session.flush()
    invalidate(bid)
    assert (await get_settings(db_session, bid)).crm_mcp_url == _URL


async def test_a_genuinely_platform_wide_key_still_inherits(db_session) -> None:
    """Control: only the CRM keys stop inheriting — the platform tier itself still works."""
    bid = await _branch(db_session)
    db_session.add(AppSetting(branch_id=None, key="tg_group_id", value="-1009999"))
    await db_session.flush()
    invalidate(bid)
    assert (await get_settings(db_session, bid)).tg_group_id == "-1009999"


# ── the branch can now edit it in-product ───────────────────────────────────────

def test_crm_connection_fields_are_in_the_schema() -> None:
    for key in _TENANT_KEYS:
        assert S.field_for(key) is not None, f"{key} has no editor"
    assert S.field_for("crm_mcp_url").kind == "secret"  # URL carries a bearer token


def test_crm_fields_render_and_the_default_alias_names_no_tenant() -> None:
    html = settings_form_html({}, "en")
    assert "CRM MCP server" in html and "CRM city alias" in html
    assert S.field_for("crm_mcp_city_alias").default == ""


# ── the migration ───────────────────────────────────────────────────────────────

def _load_migration():  # noqa: ANN202
    """Load the revision file by path — its name is not a Python identifier."""
    path = (Path(__file__).resolve().parents[1] / "migrations" / "versions"
            / "20260804_0900_crmtnt00001_crm_settings_per_branch.py")
    spec = importlib.util.spec_from_file_location("_mig_crmtnt00001", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _run_migration(session) -> None:  # noqa: ANN001
    mig = _load_migration()

    def _apply(sync_conn) -> None:  # noqa: ANN001
        with Operations.context(MigrationContext.configure(connection=sync_conn)):
            mig.upgrade()

    await (await session.connection()).run_sync(_apply)


async def _platform_keys(session) -> set[str]:  # noqa: ANN001
    rows = (await session.execute(
        text("SELECT key FROM app_setting WHERE branch_id IS NULL"))).all()
    return {r[0] for r in rows}


async def test_migration_moves_the_platform_rows_onto_branch_one(db_session) -> None:
    bid = await _branch(db_session, "Indonesia")
    assert bid == 1, "the migration targets branch 1 by id"
    db_session.add(AppSetting(branch_id=None, key="crm_mcp_url", value=_URL))
    db_session.add(AppSetting(branch_id=None, key="crm_mcp_city_alias", value="jakarta"))
    await db_session.flush()
    await _run_migration(db_session)
    invalidate(bid)
    cfg = await get_settings(db_session, bid)
    assert cfg.crm_mcp_url == _URL          # branch 1 reads exactly what it read before
    assert cfg.crm_mcp_city_alias == "jakarta"
    assert await _platform_keys(db_session) == set()


async def test_migration_keeps_an_existing_branch_value(db_session) -> None:
    """Branch 1 already has crm_read_enabled=true; the platform row must not overwrite it,
    and must not collide with the uq_setting_scope unique index either."""
    bid = await _branch(db_session, "Indonesia")
    db_session.add(AppSetting(branch_id=bid, key="crm_read_enabled", value="true"))
    db_session.add(AppSetting(branch_id=None, key="crm_read_enabled", value="false"))
    await db_session.flush()
    await _run_migration(db_session)
    invalidate(bid)
    assert (await get_settings(db_session, bid)).crm_read_enabled is True
    assert await _platform_keys(db_session) == set()


async def test_migration_is_a_noop_without_branch_one(db_session) -> None:
    db_session.add(AppSetting(branch_id=None, key="crm_mcp_url", value=_URL))
    await db_session.flush()
    await _run_migration(db_session)  # fresh install: no branch 1 to move anything onto
    assert await _platform_keys(db_session) == {"crm_mcp_url"}
