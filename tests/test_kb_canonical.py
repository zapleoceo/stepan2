"""Canonical KB structure: every branch gets the default doc skeleton; re-run is idempotent
and never clobbers existing content."""
from __future__ import annotations

import os

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
    assert "persona_core" in docs and docs["persona_core"].category == "persona"
    assert "playbook_price" in docs and docs["playbook_price"].category == "playbook"


async def test_ensure_is_idempotent_and_keeps_content(db_session) -> None:
    bid = await _branch(db_session)
    # a pre-existing doc with real content and no category (like the branch-7 seed)
    db_session.add(KnowledgeDoc(branch_id=bid, slug="faq", title="FAQ", content="real answers"))
    await db_session.flush()

    first = await ensure_canonical_docs(db_session, bid, "en")
    second = await ensure_canonical_docs(db_session, bid, "en")
    assert second == 0  # nothing new the second time
    assert first == len(CANONICAL_DOCS) - 1  # faq already existed

    faq = await KnowledgeRepo(db_session, bid).by_slug("faq")
    assert faq.content == "real answers"  # content untouched
    assert faq.category == "reference"    # but category stamped
