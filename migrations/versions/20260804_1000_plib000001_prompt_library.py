"""Prompt library, per-branch prompt independence, and branch 7's fresh start.

Three things land together because separately none of them is usable:

1. `knowledge_doc.in_prompt` — WHICH documents go into the prompt becomes branch data. Until
   now it was a hardcoded tuple of slugs in knowledge.service; branch 7 held eight documents
   and ~35 000 characters of its own material under names that tuple had never heard of, and
   its prompt fingerprinted at 16 810 characters against branch 1's 105 846. Defaults true.

2. `prompt_library_item` / `branch_prompt_source` — a versioned library of personas, selling
   methods and catalogues, and a record of what each branch cloned. Cloning COPIES; a branch
   edits its own rows afterwards and the library never reaches back into a live prompt.

3. The switch to the composer pipeline for the branches the hardcoded list was truncating,
   and branch 7 rebuilt from the library so it inherits nothing from the previous occupant.

Branch 1 is excluded BY ID and stays on the legacy assembler: 37 000 live messages behind a
pinned fingerprint, migrated in its own step and never as a side effect of this one. By id and
not by name, because a rename in the branch panel must not be able to arm this. Branch 8 is
not selected either — every document it holds is one the legacy list already loads, and it is
branch 1's sim proxy, so it must keep assembling the same way branch 1 does.

The seed text is IMPORTED from app.modules.promptlib.library_seed rather than copied in here.
That is the opposite of the usual rule for migrations, and deliberate: this is content, not
logic, the insert is keyed on (kind, slug, version) so a later edit to the module ships as a
NEW version instead of rewriting a row somebody has already cloned from, and two copies of
four kilobytes of prose would silently disagree within a month.

Reversible: nothing is deleted on the way in. Branch 7's own rows are only flagged out of the
prompt, and downgrade flags them back.

Every statement has to be portable BOTH ways: the migration suite runs head-to-head on SQLite
while production is Postgres, so no now(), no ANY(), no btrim() — and, the trap that is
invisible on SQLite, never an integer in a boolean column. `knowledge_doc.in_prompt` and
`product.is_active` are Boolean; SQLite takes `= 1` happily, Postgres registers int4→bool as
an EXPLICIT-only cast and aborts with "column is of type boolean but expression is of type
integer". Deploy runs migrate-first, so that abort is the whole release. Every flag below is
therefore a bound Python bool, and
tests/test_prompt_library_migration.py::test_no_boolean_column_is_ever_given_an_integer reads
back the SQL this module actually executes to keep it that way.

Revision ID: plib000001
Revises: dossbf00001
Create Date: 2026-08-04 10:00:00
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from app.modules.promptlib.library_seed import SEED_ITEMS

revision = "plib000001"
down_revision = "dossbf00001"
branch_labels = None
depends_on = None

# The slugs the legacy assembler can load, exactly as knowledge.service listed them on the day
# this ran. A branch holding a document outside this set was being truncated in silence, and
# that is the condition for moving it onto the composer.
_LEGACY_SLUGS = (
    "persona_core", "persona", "facts_policy", "facts_market",
    "payment_policy", "policy_prohibitions", "objection_playbook",
)

# The one deployment-specific data fix in here: the TEST branch, whose knowledge base still
# belonged to a client that left. Guarded on the name too, so on any other deployment where
# branch 7 is something real this is a no-op.
_FRESH_START_BRANCH = 7
_FRESH_START_NAME = "TEST"

# settings.free_context_char_budget as it stood on the day this ran. Frozen here rather than
# imported: what a branch fitted into is a fact about this migration, and a later raise of the
# ceiling must not retroactively change which branches this switched.
_CTX_BUDGET_ON_THE_DAY = 104000

# What composer.compose_context WRAPS each block in, on top of the content: a header line
# ("[persona <slug> lang=xx]\n" / "[method <slug>]\n" / "[<slug>]\n" — 20 chars of furniture
# around the slug at worst) plus the "\n\n" the blocks are joined with. Counting raw content
# alone would let a branch pass the gate at 103 900 and then lose catalogue cards on its very
# first reply — the exact silent loss the gate exists to prevent. Product blocks also carry
# the title on its own line, which is why they are measured separately.
_BLOCK_OVERHEAD = 22

_CATALOGUE_SLUGS = tuple(
    card["slug"]
    for item in SEED_ITEMS if item["kind"] == "catalogue"
    for card in json.loads(item["body"] or "[]")
)
_DOC_SLUGS = tuple(i["slug"] for i in SEED_ITEMS if i["kind"] != "catalogue")


def upgrade() -> None:
    op.add_column("knowledge_doc", sa.Column(
        "in_prompt", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.create_table(
        "prompt_library_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False, index=True),
        sa.Column("slug", sa.String(), nullable=False, index=True),
        sa.Column("version", sa.String(), nullable=False, server_default="1.0"),
        sa.Column("title", sa.String(), nullable=False, server_default=""),
        sa.Column("lang", sa.String(), nullable=False, server_default="en"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="published"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("kind", "slug", "version", name="uq_plib_kind_slug_version"),
    )
    op.create_table(
        "branch_prompt_source",
        sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), primary_key=True),
        sa.Column("layer", sa.String(), primary_key=True),
        sa.Column("library_item_id", sa.Integer(),
                  sa.ForeignKey("prompt_library_item.id"), nullable=True),
        sa.Column("library_slug", sa.String(), nullable=False, server_default=""),
        sa.Column("library_version", sa.String(), nullable=False, server_default=""),
        sa.Column("cloned_at", sa.DateTime(), nullable=False),
    )
    conn = op.get_bind()
    now = datetime.now(UTC).replace(tzinfo=None)
    _seed_library(conn, now)
    _switch_truncated_branches(conn)
    _fresh_start(conn, now)


def downgrade() -> None:
    conn = op.get_bind()
    _undo_fresh_start(conn)
    conn.execute(sa.text("DELETE FROM app_setting WHERE key = 'prompt_pipeline'"))
    op.drop_table("branch_prompt_source")
    op.drop_table("prompt_library_item")
    op.drop_column("knowledge_doc", "in_prompt")


def _undo_fresh_start(conn: sa.engine.Connection) -> None:
    """Branch 7 back to what it carried: the cloned rows go, its own products come back on.

    Its documents need no un-flagging — in_prompt is dropped three statements later, so
    restoring it would be work nobody can observe. is_active is a column that SURVIVES the
    downgrade, so that one genuinely has to be put back."""
    b = _FRESH_START_BRANCH
    conn.execute(_expanding(
        "DELETE FROM knowledge_doc WHERE branch_id = :b AND slug IN :slugs", "slugs"),
        {"b": b, "slugs": list(_DOC_SLUGS)})
    conn.execute(_expanding(
        "UPDATE product SET is_active = :on WHERE branch_id = :b AND slug NOT IN :slugs",
        "slugs"), {"b": b, "on": True, "slugs": list(_CATALOGUE_SLUGS)})
    conn.execute(_expanding(
        "DELETE FROM product WHERE branch_id = :b AND slug IN :slugs", "slugs"),
        {"b": b, "slugs": list(_CATALOGUE_SLUGS)})


def _expanding(sql: str, name: str) -> sa.TextClause:
    """`IN :name` over a Python list, without the Postgres-only ANY()."""
    return sa.text(sql).bindparams(sa.bindparam(name, expanding=True))


def _seed_library(conn: sa.engine.Connection, now: datetime) -> None:
    for item in SEED_ITEMS:
        conn.execute(sa.text(
            "INSERT INTO prompt_library_item (kind, slug, version, title, lang, summary, body, "
            "status, created_at, updated_at) VALUES (:kind, :slug, :version, :title, :lang, "
            ":summary, :body, 'published', :ts, :ts) ON CONFLICT DO NOTHING"),
            {**item, "ts": now})


def _switch_truncated_branches(conn: sa.engine.Connection) -> None:
    """Composer for every branch that is being truncated AND whose whole material fits.

    The fit test is not fussiness. Composer sends everything the branch marked, and the
    assembler drops from the tail when the budget is exceeded — the catalogue. Branches 9 and
    10 carry ~98k of documents and ~66k of product cards against a 104k ceiling, so switching
    them blind would trade one silent loss for a bigger one. They stay on legacy until
    somebody turns in_prompt off on the playbooks they no longer use; the setting is one
    dropdown in the branch panel.

    The sums mirror what the composer actually assembles, not what the rows contain: only
    in-prompt documents with real content, and every block's header and separator counted —
    see _BLOCK_OVERHEAD."""
    conn.execute(_expanding(
        "INSERT INTO app_setting (branch_id, channel_id, key, value) "
        "SELECT b.id, NULL, 'prompt_pipeline', 'composer' FROM branch b "
        "WHERE b.id <> 1 "
        "AND EXISTS (SELECT 1 FROM knowledge_doc d WHERE d.branch_id = b.id "
        "            AND d.slug NOT IN :legacy AND length(trim(d.content)) > 0) "
        "AND ((SELECT coalesce(sum(length(d.content) + length(d.slug) + :pad), 0) "
        "        FROM knowledge_doc d WHERE d.branch_id = b.id AND d.in_prompt "
        "          AND length(trim(d.content)) > 0) "
        "   + (SELECT coalesce(sum(length(p.content) + length(p.slug) "
        "                          + length(p.title) + :pad), 0) FROM product p "
        "        WHERE p.branch_id = b.id AND p.is_active "
        "          AND length(trim(p.content)) > 0)) < :budget "
        "AND NOT EXISTS (SELECT 1 FROM app_setting s WHERE s.branch_id = b.id "
        "                AND s.channel_id IS NULL AND s.key = 'prompt_pipeline')", "legacy"),
        {"legacy": list(_LEGACY_SLUGS), "budget": _CTX_BUDGET_ON_THE_DAY,
         "pad": _BLOCK_OVERHEAD})


def _fresh_start(conn: sa.engine.Connection, now: datetime) -> None:
    """Branch 7 rebuilt from the library: everything it carried leaves the prompt (kept in
    the table, flags only), and a persona, a method and a catalogue are cloned in."""
    row = conn.execute(sa.text("SELECT id FROM branch WHERE id = :b AND name = :n"),
                       {"b": _FRESH_START_BRANCH, "n": _FRESH_START_NAME}).first()
    if row is None:
        return
    bid = _FRESH_START_BRANCH
    conn.execute(sa.text("UPDATE knowledge_doc SET in_prompt = :off WHERE branch_id = :b"),
                 {"b": bid, "off": False})
    conn.execute(sa.text("UPDATE product SET is_active = :off WHERE branch_id = :b"),
                 {"b": bid, "off": False})
    for item in SEED_ITEMS:
        lib = conn.execute(sa.text(
            "SELECT id FROM prompt_library_item WHERE kind = :kind AND slug = :slug "
            "AND version = :version"),
            {"kind": item["kind"], "slug": item["slug"], "version": item["version"]}).first()
        if lib is None:
            continue
        if item["kind"] == "catalogue":
            _clone_catalogue(conn, bid, item["body"], now)
        else:
            _clone_doc(conn, bid, item, now)
        conn.execute(sa.text(
            "INSERT INTO branch_prompt_source (branch_id, layer, library_item_id, "
            "library_slug, library_version, cloned_at) VALUES (:b, :layer, :lib, :slug, "
            ":version, :ts) ON CONFLICT DO NOTHING"),
            {"b": bid, "layer": item["kind"], "lib": lib[0],
             "slug": item["slug"], "version": item["version"], "ts": now})


def _clone_doc(conn: sa.engine.Connection, branch_id: int, item: dict[str, str],
               now: datetime) -> None:
    category = "persona" if item["kind"] == "persona" else "method"
    conn.execute(sa.text(
        "INSERT INTO knowledge_doc (branch_id, slug, title, category, sort_order, content, "
        "in_prompt, updated_at) VALUES (:b, :slug, :title, :cat, :ord, :body, :on, :ts) "
        "ON CONFLICT (branch_id, slug) DO NOTHING"),
        {"b": branch_id, "slug": item["slug"], "title": item["title"], "cat": category,
         "ord": 0 if category == "persona" else 50, "body": item["body"], "on": True,
         "ts": now})


def _clone_catalogue(conn: sa.engine.Connection, branch_id: int, body: str,
                     now: datetime) -> None:
    for card in json.loads(body or "[]"):
        conn.execute(sa.text(
            "INSERT INTO product (branch_id, slug, title, content, is_active, sort_order, "
            "kind, updated_at) VALUES (:b, :slug, :title, :content, :on, :ord, :kind, :ts) "
            "ON CONFLICT (branch_id, slug) DO NOTHING"),
            {"b": branch_id, "slug": card["slug"], "title": card["title"],
             "content": card.get("content", ""), "ord": int(card.get("sort_order", 0)),
             "kind": card.get("kind", "course"), "on": True, "ts": now})
