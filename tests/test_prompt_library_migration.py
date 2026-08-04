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
import re  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine, event, text  # noqa: E402
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


# branch id, name, doc slugs, per-doc content size, active product size — production 04.08.2026,
# read back off the server. All FIVE live branches: 10 is here because both this suite and
# docs/prompt-library.md make a claim about it (Philippines does not qualify), and a claim no
# fixture carries is a claim nobody checked.
_PRODUCTION_SHAPE = (
    (1, "Indonesia", ("persona_core", "facts_policy", "facts_market", "objection_playbook"),
     14000, 34319),
    (7, "TEST", ("persona_core", "faq", "playbook_close", "sales_mastery"), 8800, 1566),
    (8, "ClodeCouch", ("persona_core", "facts_policy", "facts_market", "objection_playbook"),
     14000, 33868),
    (9, "Malaysia", ("persona_core", "faq", "playbook_close", "playbook_qualify"),
     24400, 65761),
    (10, "Philippines", ("persona_core", "faq", "playbook_close", "playbook_qualify"),
     24400, 65804),
)


def _seed_production_shape(c) -> None:  # noqa: ANN001
    ts = datetime.now(UTC).replace(tzinfo=None)
    for bid, name, slugs, doc_size, product_size in _PRODUCTION_SHAPE:
        c.execute(text("INSERT INTO branch (id, name, lang, tz_offset_h, is_active, "
                       "created_at) VALUES (:id, :n, 'id', 7, :on, :ts)"),
                  {"id": bid, "n": name, "on": True, "ts": ts})
        for slug in slugs:
            c.execute(text("INSERT INTO knowledge_doc (branch_id, slug, title, content, "
                           "sort_order, in_prompt, updated_at) VALUES (:b, :s, :s, :c, 0, "
                           ":on, :ts)"),
                      {"b": bid, "s": slug, "c": "x" * doc_size, "on": True, "ts": ts})
        c.execute(text("INSERT INTO product (branch_id, slug, title, content, is_active, "
                       "sort_order, kind, updated_at) VALUES (:b, 'legacy_course', "
                       "'Legacy course', :c, :on, 0, 'course', :ts)"),
                  {"b": bid, "c": "y" * product_size, "on": True, "ts": ts})


@pytest.fixture
def conn():  # noqa: ANN201
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with engine.begin() as c:
        _seed_production_shape(c)
    with engine.begin() as c:
        yield c
    engine.dispose()


@pytest.fixture
def recorded_conn():  # noqa: ANN201
    """Like `conn`, but every statement the migration executes is captured verbatim.

    The listener is attached AFTER the fixture rows are in, so what comes back is the
    migration's own SQL and nothing else."""
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with engine.begin() as c:
        _seed_production_shape(c)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001, ARG001
        statements.append(statement)

    with engine.begin() as c:
        yield c, statements
    engine.dispose()


def _pipelines(c) -> dict[int, str]:  # noqa: ANN001
    return {r[0]: r[1] for r in c.execute(text(
        "SELECT branch_id, value FROM app_setting WHERE key = 'prompt_pipeline'")).all()}


def test_only_a_truncated_branch_that_fits_moves_to_composer(conn) -> None:  # noqa: ANN001
    m = _load_migration()
    m._switch_truncated_branches(conn)  # noqa: SLF001
    moved = _pipelines(conn)
    # 1 is the live branch and is excluded by id; 8 holds only slugs the legacy list loads;
    # 9 and 10's 97 600 + ~65 800 chars do not fit the 104 000 budget, so switching them would
    # push their whole catalogue out of the tail — a bigger silent loss than the one fixed.
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

    for other in (1, 8, 9, 10):
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


def test_the_gate_measures_the_assembled_prompt_not_the_raw_rows(conn) -> None:  # noqa: ANN001
    """A branch under the ceiling on content and over it once assembled must NOT be switched.

    The composer wraps every block in a header ("[persona x lang=id]") and joins with a blank
    line, so a branch of many small documents pays thousands of characters the row lengths do
    not show. Switching it would hand it exactly the silent catalogue loss the gate exists to
    prevent — on its first reply, and only for the leads asking about the dropped course.

    200 docs x 500 chars = 100 000 raw, comfortably under 104 000; assembled it is ~105 800."""
    ts = datetime.now(UTC).replace(tzinfo=None)
    conn.execute(text("INSERT INTO branch (id, name, lang, tz_offset_h, is_active, created_at)"
                      " VALUES (11, 'Nearly', 'id', 7, :on, :ts)"), {"on": True, "ts": ts})
    for i in range(200):
        conn.execute(text("INSERT INTO knowledge_doc (branch_id, slug, title, content, "
                          "sort_order, in_prompt, updated_at) VALUES (11, :s, :s, :c, 0, "
                          ":on, :ts)"),
                     {"s": f"doc_{i:03d}", "c": "x" * 500, "on": True, "ts": ts})
    raw = conn.execute(text(
        "SELECT sum(length(content)) FROM knowledge_doc WHERE branch_id = 11")).scalar()
    m = _load_migration()
    assert raw < m._CTX_BUDGET_ON_THE_DAY  # noqa: SLF001 — the near miss the old gate waved through
    m._switch_truncated_branches(conn)  # noqa: SLF001
    assert 11 not in _pipelines(conn)


def test_the_downgrade_puts_branch_sevens_own_catalogue_back(conn) -> None:  # noqa: ANN001
    """is_active survives the downgrade (in_prompt does not — that column is dropped), so it
    is the one flag the reversal actually has to restore."""
    m = _load_migration()
    now = datetime.now(UTC).replace(tzinfo=None)
    m._seed_library(conn, now)  # noqa: SLF001
    m._fresh_start(conn, now)  # noqa: SLF001
    m._undo_fresh_start(conn)  # noqa: SLF001
    active = conn.execute(text(
        "SELECT slug FROM product WHERE branch_id = 7 AND is_active")).scalars().all()
    assert active == ["legacy_course"]
    assert conn.execute(text(
        "SELECT count(*) FROM knowledge_doc WHERE branch_id = 7 "
        "AND slug IN ('neutral_consultant', 'consultative_chat_sales')")).scalar() == 0


_BOOL_COLUMNS = frozenset({"in_prompt", "is_active"})
_ASSIGNMENT = re.compile(r"\b(in_prompt|is_active)\s*=\s*([^\s,)]+)", re.IGNORECASE)
_INSERT_VALUES = re.compile(
    r"INSERT\s+INTO\s+\w+\s*\(([^)]*)\)\s*VALUES\s*\(([^)]*)\)", re.IGNORECASE | re.DOTALL)


def _integer_in_a_boolean_column(sql: str) -> str | None:
    for match in _ASSIGNMENT.finditer(sql):
        if match.group(2) in {"0", "1"}:
            return f"{match.group(1)} = {match.group(2)}"
    for match in _INSERT_VALUES.finditer(sql):
        columns = [c.strip().lower() for c in match.group(1).split(",")]
        values = [v.strip() for v in match.group(2).split(",")]
        if len(columns) != len(values):
            continue
        for column, value in zip(columns, values, strict=True):
            if column in _BOOL_COLUMNS and value in {"0", "1"}:
                return f"{column} <- {value}"
    return None


def test_no_boolean_column_is_ever_given_an_integer(recorded_conn) -> None:  # noqa: ANN001
    """The portability trap this whole suite is otherwise blind to.

    knowledge_doc.in_prompt and product.is_active are Boolean. SQLite — which every migration
    test and test_infra's head-to-head run on — accepts `SET in_prompt = 0` without complaint.
    Postgres registers int4->bool as an EXPLICIT-only cast and aborts the statement with
    "column is of type boolean but expression is of type integer". Deploy runs migrate-first,
    so on production that abort is the release, not one row.

    So read back the SQL the migration actually executes and refuse an integer literal in a
    boolean position. Bound Python bools render as a placeholder here and as `true`/`false` on
    Postgres, which is the fix as well as the check."""
    c, statements = recorded_conn
    m = _load_migration()
    now = datetime.now(UTC).replace(tzinfo=None)
    m._seed_library(c, now)  # noqa: SLF001
    m._switch_truncated_branches(c)  # noqa: SLF001
    m._fresh_start(c, now)  # noqa: SLF001
    m._undo_fresh_start(c)  # noqa: SLF001

    assert any("in_prompt" in s for s in statements), "the fixture stopped exercising the flag"
    offenders = [(s, bad) for s in statements if (bad := _integer_in_a_boolean_column(s))]
    assert not offenders, f"integer written to a boolean column: {offenders}"
