"""KB sharing between branches: link (effective_kb_branch), copy, and reindex-skip."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from sqlalchemy import func, select  # noqa: E402

from app.adapters.db.models import Branch, KnowledgeDoc, Product  # noqa: E402
from app.modules.knowledge.source import copy_kb, effective_kb_branch  # noqa: E402


async def _src(s) -> int:
    b = Branch(name="Source", lang="id")
    s.add(b)
    await s.flush()
    s.add(KnowledgeDoc(branch_id=b.id, slug="persona_core", content="You are Stepan."))
    s.add(KnowledgeDoc(branch_id=b.id, slug="playbook_price", content="Price 13M."))
    s.add(Product(branch_id=b.id, slug="vibe", title="Vibe", content="13 juta", is_active=True))
    await s.flush()
    return b.id


async def _empty(s) -> int:
    b = Branch(name="Clone", lang="id")
    s.add(b)
    await s.flush()
    return b.id


async def test_effective_branch_self_then_linked(db_session) -> None:
    src = await _src(db_session)
    dst = await _empty(db_session)
    assert await effective_kb_branch(db_session, dst) == dst   # unlinked → itself

    b = await db_session.get(Branch, dst)
    b.kb_source_branch_id = src
    db_session.add(b)
    await db_session.flush()
    assert await effective_kb_branch(db_session, dst) == src   # linked → source



async def test_copy_kb_clones_and_replaces(db_session) -> None:
    src = await _src(db_session)
    dst = await _empty(db_session)
    # give dst a stale doc that copy must replace
    db_session.add(KnowledgeDoc(branch_id=dst, slug="old", content="stale"))
    await db_session.flush()

    n = await copy_kb(db_session, dst, src)
    assert n == 3  # 2 docs + 1 product

    doc_slugs = set((await db_session.execute(
        select(KnowledgeDoc.slug).where(KnowledgeDoc.branch_id == dst))).scalars().all())
    assert doc_slugs == {"persona_core", "playbook_price"}   # stale 'old' gone, source's in
    prods = (await db_session.execute(
        select(func.count()).select_from(Product).where(Product.branch_id == dst))).scalar()
    assert prods == 1
    # a copy is independent — no live link created
    assert await effective_kb_branch(db_session, dst) == dst


async def test_copy_to_self_is_noop(db_session) -> None:
    src = await _src(db_session)
    assert await copy_kb(db_session, src, src) == 0
    n = (await db_session.execute(
        select(func.count()).select_from(KnowledgeDoc).where(
            KnowledgeDoc.branch_id == src))).scalar()
    assert n == 2  # untouched
