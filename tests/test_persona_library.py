"""Persona library: seed, section parsing, per-branch selection / favorites / addendum,
and route wiring. Additive feature — asserts it does NOT touch the reply path."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from fastapi.testclient import TestClient  # noqa: E402

from app.adapters.db.models import Branch  # noqa: E402
from app.api.main import app  # noqa: E402
from app.modules.persona import service as P  # noqa: E402
from tests.test_infra import _alembic_config  # noqa: E402


def test_sections_parses_headings() -> None:
    secs = P.sections("## Voice & tone\nbe warm\n\n## Closing\nsoft close")
    assert [s[0] for s in secs] == ["Voice & tone", "Closing"]
    assert secs[0][1] == "voice-tone" and secs[0][2] == "be warm"


async def _branch(s) -> int:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    return b.id


async def test_the_library_ships_empty_and_holds_only_what_branches_import(db_session) -> None:
    """No built-in persona since S6. The one that used to be here, "website-demo", was a
    browsable copy of the landing chat's hardcoded prompt — two texts for one agent, already
    drifting. The site is a branch now and its persona is a prompt-library row it cloned.

    Nothing in the app may re-seed this table: a built-in reappearing is the duplicate coming
    back."""
    assert not hasattr(P, "SEED_PERSONAS")
    assert not hasattr(P, "ensure_seeded")
    assert await P.list_personas(db_session) == []


def test_the_migration_takes_the_old_website_demo_row_off_the_grid(tmp_path, monkeypatch):
    """Deleting the seeder does not delete its rows.

    `ensure_seeded` ran on every persona-library page view, so every install where anybody
    opened that page holds "website-demo". The test above runs on an empty database and is
    therefore silent about exactly the case that matters: after deploy the operator's library
    would show the stale "Stepan (website demo)" v1.2 next to the S6 prompt-library entry the
    site now really sells with — the drifted duplicate this release exists to remove.

    So this one seeds the row the way production has it, at the revision production is at, and
    upgrades over it. Retired rather than deleted: `list_personas` filters on `published`, so
    the card goes away, while an operator who rewrote that text keeps it."""
    from alembic import command
    from sqlalchemy import create_engine, text

    from app.config import settings

    db_file = tmp_path / "persona.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setenv("STEPAN2_DATABASE_URL", db_url)
    settings.cache_clear()
    cfg = _alembic_config(db_url)

    command.upgrade(cfg, "plib000001")  # where a live install stands before this release
    engine = create_engine(f"sqlite:///{db_file}")
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO persona (slug, name, version, lang, country, summary, content,"
                " changelog, author_name, author_contact, status, created_at, updated_at)"
                " VALUES ('website-demo', 'Stepan (website demo)', '1.2', 'en', '', '', 'x',"
                " '', 'Zapleo', 'https://t.me/zapleosoft', 'published', '2026-07-01',"
                " '2026-07-01')"))
            conn.execute(text(
                "INSERT INTO persona (slug, name, version, lang, country, summary, content,"
                " changelog, author_name, author_contact, status, created_at, updated_at)"
                " VALUES ('indonesia-persona', 'Indonesia', '1.0', 'id', '', '', 'y',"
                " '', 'Zapleo', 'https://t.me/zapleosoft', 'published', '2026-07-01',"
                " '2026-07-01')"))

        command.upgrade(cfg, "head")

        with engine.begin() as conn:
            rows = dict(conn.execute(text("SELECT slug, status FROM persona")).all())
    finally:
        engine.dispose()
        settings.cache_clear()

    assert rows == {"website-demo": "retired", "indonesia-persona": "published"}


async def test_an_imported_branch_persona_is_listed(db_session) -> None:
    from app.adapters.db.models import KnowledgeDoc

    bid = await _branch(db_session)
    db_session.add(KnowledgeDoc(branch_id=bid, slug="persona_core", category="persona",
                                content="## Voice\nwarm"))
    await db_session.flush()
    await P.import_from_branch(db_session, bid, "Indonesia persona", lang="id", country="ID")

    listed = await P.list_personas(db_session)
    assert {p.slug for p in listed} == {"indonesia-persona"}
    assert all(p.author_name and p.author_contact for p in listed)


async def test_select_favorite_and_addendum_roundtrip(db_session) -> None:
    from app.adapters.db.models import KnowledgeDoc

    bid = await _branch(db_session)
    db_session.add(KnowledgeDoc(branch_id=bid, slug="persona_core", category="persona",
                                content="## Voice\nwarm"))
    await db_session.flush()
    await P.import_from_branch(db_session, bid, "Indonesia persona", lang="id", country="ID")
    pid = (await P.list_personas(db_session))[0].id

    # nothing selected → draft
    active, add, favs = await P.branch_state(db_session, bid)
    assert active is None and add == {} and favs == set()

    await P.set_active(db_session, bid, pid)
    await P.toggle_favorite(db_session, bid, pid)
    await P.save_addendum(db_session, bid, "closing", "always mention 0% instalment")
    active, add, favs = await P.branch_state(db_session, bid)
    assert active == pid and pid in favs
    assert add["closing"] == "always mention 0% instalment"

    # adoption reflects the selection + favorite
    adopt = await P.adoption(db_session)
    assert adopt[pid] == (1, 1)

    # un-favorite + clear the addendum
    assert (await P.toggle_favorite(db_session, bid, pid)) is False
    await P.save_addendum(db_session, bid, "closing", "")
    _a, add2, favs2 = await P.branch_state(db_session, bid)
    assert favs2 == set() and "closing" not in add2


def test_library_panel_renders_cards_stats_and_author() -> None:
    from types import SimpleNamespace

    from app.api._i18n import _lang
    from app.api._ui_personas import personas_panel_html
    _lang.set("en")

    def _p(pid: int, name: str) -> SimpleNamespace:
        return SimpleNamespace(id=pid, name=name, version="1.0", summary="s.",
                               lang="en", country="", author_name="Zapleo",
                               author_contact="https://t.me/zapleosoft")

    personas = [_p(1, "Stepan (website demo)"), _p(2, "Indonesia persona")]
    html = personas_panel_html(
        personas, adopt={1: (2, 3)}, active_id=1, fav_ids={2},
        can_write=True, active_name="Stepan (website demo)")
    assert "pa-grid" in html and "Stepan (website demo)" in html
    assert "2 branches · 3" in html                   # adoption stat rendered
    assert "t.me/zapleosoft" in html                  # contact-author link
    assert "/ui/personas/2/favorite" in html          # favorite toggle present
    assert 'class="pa-use active"' in html            # the active persona is marked in-use


async def test_import_from_branch_bundles_all_kb_and_versions(db_session) -> None:
    from app.adapters.db.models import KnowledgeDoc
    bid = await _branch(db_session)
    db_session.add(KnowledgeDoc(branch_id=bid, slug="persona_core", category="persona",
                                content="## Voice\nwarm"))
    db_session.add(KnowledgeDoc(branch_id=bid, slug="playbook_close", category="playbook",
                                content="close on value"))
    await db_session.flush()

    p1 = await P.import_from_branch(db_session, bid, "Indonesia persona", lang="id", country="ID")
    assert p1.version == "1.0" and p1.country == "ID" and p1.lang == "id"
    # bundles EVERY non-product KB doc, not just persona_core
    assert "## persona_core" in p1.content and "warm" in p1.content
    assert "## playbook_close" in p1.content and "close on value" in p1.content

    assert p1.changelog                                   # first import gets a default note
    # re-import mints the next version + records a 'what changed' note
    p2 = await P.import_from_branch(db_session, bid, "Indonesia persona",
                                    changelog="tightened the closing playbook")
    assert p2.slug == p1.slug and p2.version == "1.1"
    assert p2.changelog == "tightened the closing playbook"

    # the library grid shows only the LATEST version per slug…
    listed = await P.list_personas(db_session)
    same = [p for p in listed if p.slug == p1.slug]
    assert len(same) == 1 and same[0].version == "1.1"
    # …while the full, readable history is newest-first with the changelog notes
    hist = await P.versions_of(db_session, p1.slug)
    assert [h.version for h in hist] == ["1.1", "1.0"]
    assert hist[0].changelog == "tightened the closing playbook"


def test_personas_route_is_wired() -> None:
    # DB-touching route: the app engine isn't migrated in the unit harness, so 200 (schema
    # present) or 500 (not) both prove the route is mounted; the logic is covered above.
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/ui/personas").status_code in (200, 500)


def test_write_without_a_single_branch_is_refused() -> None:
    # auth off in tests → super_admin/all, no single branch → selecting is refused (not a crash)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/ui/personas/1/use")
    assert resp.status_code == 400
