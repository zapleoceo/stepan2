"""KB edit history: content changes are journaled; title-only edits are not; restore
re-applies an old version and logs the restore as a new revision."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from sqlalchemy import text  # noqa: E402

from app.adapters.db.models import Branch, KnowledgeDoc  # noqa: E402
from app.modules.knowledge.history import (  # noqa: E402
    list_revisions,
    record_revision,
    restore_revision,
)


async def _branch(s) -> int:
    b = Branch(name="T", lang="en")
    s.add(b)
    await s.flush()
    return b.id


async def test_record_skips_noop_and_lists_newest_first(db_session) -> None:
    bid = await _branch(db_session)
    await record_revision(db_session, branch_id=bid, entity_type="doc", slug="faq",
                          old_content=None, new_content="v1", actor="a")
    await record_revision(db_session, branch_id=bid, entity_type="doc", slug="faq",
                          old_content="v1", new_content="v1", actor="a")  # no-op → skipped
    await record_revision(db_session, branch_id=bid, entity_type="doc", slug="faq",
                          old_content="v1", new_content="v2", actor="b")
    await db_session.flush()

    revs = await list_revisions(db_session, bid, "doc", "faq")
    assert [r[2] for r in revs] == ["v2", "v1"]  # new_content, newest first
    assert revs[0][5] == "b"  # actor of latest


async def test_restore_reapplies_and_logs(db_session) -> None:
    bid = await _branch(db_session)
    db_session.add(KnowledgeDoc(branch_id=bid, slug="faq", title="FAQ", content="current"))
    await db_session.flush()
    await record_revision(db_session, branch_id=bid, entity_type="doc", slug="faq",
                          old_content="current", new_content="golden", actor="a")
    await db_session.flush()
    rev = (await list_revisions(db_session, bid, "doc", "faq"))[0]

    out = await restore_revision(db_session, bid, rev[0], actor="me")
    assert out == ("doc", "faq")
    content = (await db_session.execute(
        text("SELECT content, updated_by FROM knowledge_doc WHERE slug='faq'"))).first()
    assert content[0] == "golden" and content[1] == "me"
    # restore itself is journaled (current → golden)
    assert (await list_revisions(db_session, bid, "doc", "faq"))[0][2] == "golden"
