"""Canonical KB structure: every branch gets the default doc skeleton; re-run is idempotent
and never clobbers existing content."""
from __future__ import annotations

import os
from datetime import datetime

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.adapters.db.models import Branch, KnowledgeDoc  # noqa: E402
from app.modules.knowledge.canonical import (  # noqa: E402
    CANONICAL_DOCS,
    ensure_canonical_docs,
)
from app.modules.knowledge.repository import KnowledgeRepo  # noqa: E402


async def _branch(s, lang="en") -> int:
    b = Branch(name="T", lang=lang)
    s.add(b)
    await s.flush()
    return b.id


async def test_ensure_creates_all_canonical_docs(db_session) -> None:
    bid = await _branch(db_session)
    created = await ensure_canonical_docs(db_session, bid, "en")
    assert created == len(CANONICAL_DOCS)
    docs = {d.slug: d for d in await KnowledgeRepo(db_session, bid).all()}
    # The skeleton is EXACTLY the docs the reply prompt loads — no phantom editors.
    assert set(docs) == {"persona_core", "facts_policy", "facts_market", "objection_playbook"}
    assert docs["persona_core"].category == "persona"
    assert docs["objection_playbook"].category == "playbook"


async def test_ensure_is_idempotent_and_keeps_content(db_session) -> None:
    bid = await _branch(db_session)
    # a pre-existing canonical doc with real content and no category
    db_session.add(KnowledgeDoc(branch_id=bid, slug="facts_policy", title="Facts",
                                content="real answers"))
    await db_session.flush()

    first = await ensure_canonical_docs(db_session, bid, "en")
    second = await ensure_canonical_docs(db_session, bid, "en")
    assert second == 0  # nothing new the second time
    assert first == len(CANONICAL_DOCS) - 1  # facts_policy already existed

    doc = await KnowledgeRepo(db_session, bid).by_slug("facts_policy")
    assert doc.content == "real answers"  # content untouched
    assert doc.category == "facts"        # but category stamped


async def test_ensure_bumps_updated_at_on_existing_doc(db_session) -> None:
    """Stamping category/sort_order on a pre-existing doc must also refresh updated_at —
    otherwise a UI sorted by recency shows a doc as untouched right after this ran."""
    bid = await _branch(db_session)
    old = datetime(2020, 1, 1)
    db_session.add(KnowledgeDoc(branch_id=bid, slug="facts_policy", title="Facts",
                                content="real answers", updated_at=old))
    await db_session.flush()

    await ensure_canonical_docs(db_session, bid, "en")

    doc = await KnowledgeRepo(db_session, bid).by_slug("facts_policy")
    assert doc.updated_at > old
