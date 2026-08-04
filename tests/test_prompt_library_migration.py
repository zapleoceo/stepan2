"""The migration's DATA steps, run against a database shaped like production.

tests/test_infra.py proves the schema migrates and downgrades; it runs on an EMPTY database,
so the two statements that actually decide something — which branches move to the composer,
and branch 7's fresh start — never execute there. They are the risky part: one of them writes
to a live tenant's rows.

The fixture reproduces the five production branches by SHAPE (document names and content
sizes, read off the server 2026-08-04) with harmless text.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import importlib.util  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

import app.adapters.db.models  # noqa: E402,F401 — register tables on the metadata

_MIGRATION = Path(__file__).parents[1] / "migrations" / "versions" / \
    "20260804_1000_plib000001_prompt_library.py"


def _load_migration():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("plib000001", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# branch id, name, doc slugs, per-doc content size, active product size — production 04.08.2026.
_PRODUCTION_SHAPE = (
    (1, "Indonesia", ("persona_core", "facts_policy", "facts_market", "objection_playbook"),
     14000, 34319),
    (7, "TEST", ("persona_core", "faq", "playbook_close", "sales_mastery"), 8800, 1566),
    (8, "ClodeCouch", ("persona_core", "facts_policy", "facts_market", "objection_playbook"),
     14000, 33868),
    (9, "Malaysia", ("persona_core", "faq", "playbook_close", "playbook_qualify"),
     24400, 65761),
)


@pytest.fixture
def conn():  # noqa: ANN201
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with engine.begin() as c:
        for bid, name, slugs, doc_size, product_size in _PRODUCTION_SHAPE:
            c.execute(text("INSERT INTO branch (id, name, lang, tz_offset_h, is_active, "
                           "created_at) VALUES (:id, :n, 'id', 7, 1, :ts)"),
                      {"id": bid, "n": name, "ts": datetime.now(UTC).replace(tzinfo=None)})
            for slug in slugs:
                c.execute(text("INSERT INTO knowledge_doc (branch_id, slug, title, content, "
                               "sort_order, in_prompt, updated_at) VALUES (:b, :s, :s, :c, 0, "
                               "1, :ts)"),
                          {"b": bid, "s": slug, "c": "x" * doc_size,
                           "ts": datetime.now(UTC).replace(tzinfo=None)})
            c.execute(text("INSERT INTO product (branch_id, slug, title, content, is_active, "
                           "sort_order, kind, updated_at) VALUES (:b, 'legacy_course', "
                           "'Legacy course', :c, 1, 0, 'course', :ts)"),
                      {"b": bid, "c": "y" * product_size,
                       "ts": datetime.now(UTC).replace(tzinfo=None)})
    with engine.begin() as c:
        yield c
    engine.dispose()


def _pipelines(c) -> dict[int, str]:  # noqa: ANN001
    return {r[0]: r[1] for r in c.execute(text(
        "SELECT branch_id, value FROM app_setting WHERE key = 'prompt_pipeline'")).all()}


def test_only_a_truncated_branch_that_fits_moves_to_composer(conn) -> None:  # noqa: ANN001
    m = _load_migration()
    m._switch_truncated_branches(conn)  # noqa: SLF001
    moved = _pipelines(conn)
    # 1 is the live branch and is excluded by id; 8 holds only slugs the legacy list loads;
    # 9's 97 600 + 65 761 chars do not fit the 104 000 budget, so switching it would push its
    # whole catalogue out of the tail — a bigger silent loss than the one being fixed.
    assert moved == {7: "composer"}


def test_the_live_branch_is_excluded_even_when_it_qualifies(conn) -> None:  # noqa: ANN001
    """Branch 1 is held back by id, not by luck. Today its four documents happen to be four
    the legacy list loads — so the exclusion is untestable unless the branch is given a
    document that WOULD qualify it, which is exactly the situation a KB edit could create on
    any afternoon. It still must not move: 37 000 live messages migrate in their own step.

    It stays legacy by ABSENCE of a row, not by a written 'legacy' — the setting defaults to
    legacy, and no row is the strongest form of untouched."""
    conn.execute(text("INSERT INTO knowledge_doc (branch_id, slug, title, content, sort_order,"
                      " in_prompt, updated_at) VALUES (1, 'stories', 'stories', :c, 0, 1, :ts)"),
                 {"c": "x" * 8000, "ts": datetime.now(UTC).replace(tzinfo=None)})
    m = _load_migration()
    m._switch_truncated_branches(conn)  # noqa: SLF001
    assert 1 not in _pipelines(conn)
    assert conn.execute(text(
        "SELECT count(*) FROM knowledge_doc WHERE branch_id = 1 AND in_prompt = 0")).scalar() == 0


def test_the_fresh_start_replaces_only_branch_seven(conn) -> None:  # noqa: ANN001
    m = _load_migration()
    now = datetime.now(UTC).replace(tzinfo=None)
    m._seed_library(conn, now)  # noqa: SLF001
    m._fresh_start(conn, now)  # noqa: SLF001

    live = conn.execute(text(
        "SELECT slug FROM knowledge_doc WHERE branch_id = 7 AND in_prompt = 1")).scalars().all()
    assert set(live) == set(m._DOC_SLUGS)  # noqa: SLF001 — persona + method, from the library
    # The former client's documents are still there, only out of scope: downgrade is one flag.
    assert conn.execute(text(
        "SELECT count(*) FROM knowledge_doc WHERE branch_id = 7 AND slug = 'sales_mastery' "
        "AND in_prompt = 0")).scalar() == 1
    active = conn.execute(text(
        "SELECT slug FROM product WHERE branch_id = 7 AND is_active = 1")).scalars().all()
    assert set(active) == set(m._CATALOGUE_SLUGS)  # noqa: SLF001
    layers = conn.execute(text(
        "SELECT layer FROM branch_prompt_source WHERE branch_id = 7")).scalars().all()
    assert set(layers) == {"persona", "method", "catalogue"}

    for other in (1, 8, 9):
        assert conn.execute(text(
            "SELECT count(*) FROM knowledge_doc WHERE branch_id = :b AND in_prompt = 0"),
            {"b": other}).scalar() == 0
        assert conn.execute(text(
            "SELECT count(*) FROM product WHERE branch_id = :b AND is_active = 0"),
            {"b": other}).scalar() == 0


def test_the_fresh_start_is_a_no_op_where_branch_seven_is_something_else(conn) -> None:  # noqa: ANN001
    """The name guard: on another deployment branch 7 is a real tenant, not the leftover."""
    conn.execute(text("UPDATE branch SET name = 'Vietnam' WHERE id = 7"))
    m = _load_migration()
    now = datetime.now(UTC).replace(tzinfo=None)
    m._seed_library(conn, now)  # noqa: SLF001
    m._fresh_start(conn, now)  # noqa: SLF001
    assert conn.execute(text(
        "SELECT count(*) FROM knowledge_doc WHERE branch_id = 7 AND in_prompt = 0")).scalar() == 0
    assert conn.execute(text(
        "SELECT count(*) FROM branch_prompt_source")).scalar() == 0
