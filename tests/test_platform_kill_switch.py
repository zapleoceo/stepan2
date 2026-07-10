"""_platform_agent_on — the reader that gates reply/followup/send/deletions/refresh/backfill.
Every other test monkeypatches it away; this pins the actual SQL read + truthy parse + the
default-ON-when-no-row branch, so a regression that inverts the default (silently stopping —
or failing to stop — the whole platform's bot) fails here."""
from __future__ import annotations

from app.adapters.db.models import AppSetting
from app.worker.main import _platform_agent_on

_KEY = "agent_enabled_platform"


async def test_default_on_when_no_row(db_session) -> None:
    assert await _platform_agent_on(db_session) is True


async def test_explicit_false_stops_the_platform(db_session) -> None:
    db_session.add(AppSetting(branch_id=None, key=_KEY, value="false"))
    await db_session.flush()
    assert await _platform_agent_on(db_session) is False


async def test_truthy_values_keep_it_on(db_session) -> None:
    for val in ("true", "1", "yes"):
        row = AppSetting(branch_id=None, key=_KEY, value=val)
        db_session.add(row)
        await db_session.flush()
        assert await _platform_agent_on(db_session) is True, val
        await db_session.delete(row)
        await db_session.flush()


async def test_a_branch_scoped_row_does_not_gate_the_platform(db_session) -> None:
    # The switch is the branch_id IS NULL row only — a per-branch 'off' must not read as a
    # platform stop (or one branch's setting would freeze everyone).
    from app.adapters.db.models import Branch

    b = Branch(name="B", lang="id")
    db_session.add(b)
    await db_session.flush()
    db_session.add(AppSetting(branch_id=b.id, key=_KEY, value="false"))
    await db_session.flush()
    assert await _platform_agent_on(db_session) is True
