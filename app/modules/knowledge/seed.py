"""Seed a branch with a knowledge base from JSON (e.g. Indonesia pulled from Stepan-1).

Pure data load through BranchScoped — knowledge/products only, no chats/PII."""
from __future__ import annotations

import json
from pathlib import Path

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, KnowledgeDoc, Product
from app.modules.knowledge.repository import KnowledgeRepo, ProductRepo

_SEED_DIR = Path(__file__).parent / "seeds"


def _load(filename: str) -> list[dict]:
    return json.loads((_SEED_DIR / filename).read_text(encoding="utf-8").strip() or "[]")


async def seed_branch(
    session: AsyncSession, *, name: str, lang: str,
    docs_file: str, products_file: str,
) -> int:
    """Create a branch and load its KB from seed files. Returns the new branch id."""
    branch = Branch(name=name, lang=lang)
    session.add(branch)
    await session.flush()
    assert branch.id is not None

    kr = KnowledgeRepo(session, branch.id)
    for d in _load(docs_file):
        await kr.add(KnowledgeDoc(slug=d["slug"], title=d.get("title"),
                                  content=d.get("content", "")))
    pr = ProductRepo(session, branch.id)
    for p in _load(products_file):
        await pr.add(Product(slug=p["slug"], title=p["title"],
                             content=p.get("content", ""),
                             is_active=p.get("is_active", True),
                             sort_order=p.get("sort_order", 0)))
    return branch.id


async def seed_indonesia(session: AsyncSession) -> int:
    """First branch — Indonesia (knowledge pulled from Stepan-1)."""
    return await seed_branch(
        session, name="Indonesia", lang="id",
        docs_file="indonesia_docs.json", products_file="indonesia_products.json")
