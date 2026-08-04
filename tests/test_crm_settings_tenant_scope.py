"""A CRM link belongs to one tenant: never inherited from the platform tier, always
editable by the branch, and the platform rows that existed move onto the branch that
actually uses that CRM — never onto an id the migration merely assumed.
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


async def _crm_owner(session, bid: int) -> None:  # noqa: ANN001
    """What makes a branch the CRM's owner on production: the read gate switched on."""
    session.add(AppSetting(branch_id=bid, key="crm_read_enabled", value="true"))
    await session.flush()


async def test_migration_moves_the_platform_rows_onto_the_owning_branch(db_session) -> None:
    bid = await _branch(db_session, "Indonesia")
    await _crm_owner(db_session, bid)
    db_session.add(AppSetting(branch_id=None, key="crm_mcp_url", value=_URL))
    db_session.add(AppSetting(branch_id=None, key="crm_mcp_city_alias", value="jakarta"))
    await db_session.flush()
    await _run_migration(db_session)
    invalidate(bid)
    cfg = await get_settings(db_session, bid)
    assert cfg.crm_mcp_url == _URL          # the owner reads exactly what it read before
    assert cfg.crm_mcp_city_alias == "jakarta"
    assert await _platform_keys(db_session) == set()


async def test_migration_does_not_hand_the_token_to_an_unrelated_branch(db_session) -> None:
    """The whole point, on a restored snapshot or a staging copy: branch 1 exists but is
    somebody else, so the credentials must NOT land on it. TENANT_ONLY_KEYS already makes
    the rows inert; moving them onto a stranger would not be recoverable."""
    bid = await _branch(db_session, "SomeoneElse")
    assert bid == 1, "the id the migration used to hardcode"
    db_session.add(AppSetting(branch_id=None, key="crm_mcp_url", value=_URL))
    await db_session.flush()
    await _run_migration(db_session)
    invalidate(bid)
    assert (await get_settings(db_session, bid)).crm_mcp_url == ""
    assert await _platform_keys(db_session) == {"crm_mcp_url"}


async def test_migration_will_not_choose_between_two_crm_owners(db_session) -> None:
    first = await _branch(db_session, "Indonesia")
    second = await _branch(db_session, "Malaysia")
    await _crm_owner(db_session, first)
    await _crm_owner(db_session, second)
    db_session.add(AppSetting(branch_id=None, key="crm_mcp_url", value=_URL))
    await db_session.flush()
    await _run_migration(db_session)
    assert await _platform_keys(db_session) == {"crm_mcp_url"}


async def test_migration_keeps_an_existing_branch_value(db_session) -> None:
    """The owner already has crm_read_enabled=true; the platform row must not overwrite it,
    and must not collide with the uq_setting_scope unique index either."""
    bid = await _branch(db_session, "Indonesia")
    await _crm_owner(db_session, bid)
    db_session.add(AppSetting(branch_id=None, key="crm_read_enabled", value="false"))
    await db_session.flush()
    await _run_migration(db_session)
    invalidate(bid)
    assert (await get_settings(db_session, bid)).crm_read_enabled is True
    assert await _platform_keys(db_session) == set()


async def test_migration_is_a_noop_on_a_fresh_install(db_session) -> None:
    db_session.add(AppSetting(branch_id=None, key="crm_mcp_url", value=_URL))
    await db_session.flush()
    await _run_migration(db_session)  # no branch talks to a CRM: nothing to move it onto
    assert await _platform_keys(db_session) == {"crm_mcp_url"}


async def test_migration_never_deletes_a_row_it_would_not_re_parent(db_session) -> None:
    """DELETE and UPDATE must share their predicate. A connector-tier platform row is
    nonsense today, but deleting one the UPDATE would skip is unrecoverable — downgrade()
    cannot put it back."""
    bid = await _branch(db_session, "Indonesia")
    await _crm_owner(db_session, bid)
    db_session.add(AppSetting(branch_id=bid, key="crm_mcp_url", value=_URL))
    db_session.add(AppSetting(branch_id=None, channel_id=4, key="crm_mcp_url", value=_URL))
    await db_session.flush()
    await _run_migration(db_session)
    rows = (await db_session.execute(text(
        "SELECT channel_id FROM app_setting WHERE branch_id IS NULL AND key = 'crm_mcp_url'"
    ))).all()
    assert [r[0] for r in rows] == [4]
