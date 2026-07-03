"""Canonical KB structure — the default section skeleton every branch gets.

Mirrors Stepan-1's doc set (persona + playbooks + reference), branch-agnostic. Section
titles and placeholder hints are localized (ru/en/id) and rendered in the interface
language by the editor; the stored content is whatever the admin writes, in ANY language.
`ensure_canonical_docs` creates missing docs and stamps category/order/title on existing
ones without touching their content. Doc/section data lives in canonical_docs.py."""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import KnowledgeDoc, _utcnow
from app.modules.settings.schema import tr as loc

from .canonical_docs import CANONICAL_DOCS, CATEGORIES, CanonDoc, Section
from .repository import KnowledgeRepo

__all__ = [
    "CANONICAL_DOCS", "CATEGORIES", "CanonDoc", "Section", "loc", "canon",
    "ensure_canonical_docs",
]

_BY_SLUG: dict[str, CanonDoc] = {d.slug: d for d in CANONICAL_DOCS}


def canon(slug: str) -> CanonDoc | None:
    return _BY_SLUG.get(slug)


async def ensure_canonical_docs(session: AsyncSession, branch_id: int, lang: str = "en") -> int:
    """Create every canonical doc that's missing and stamp category/order/title on the
    ones that exist (content untouched). Returns how many rows were created."""
    repo = KnowledgeRepo(session, branch_id)
    existing = {d.slug: d for d in await repo.all()}
    created = 0
    for cd in CANONICAL_DOCS:
        title = loc(cd.title, lang)
        doc = existing.get(cd.slug)
        if doc is None:
            await repo.add(KnowledgeDoc(
                branch_id=branch_id, slug=cd.slug, title=title,
                category=cd.category, sort_order=cd.order, content=""))
            created += 1
        else:
            doc.category = cd.category
            doc.sort_order = cd.order
            if not doc.title:
                doc.title = title
            doc.updated_at = _utcnow()
            session.add(doc)
    return created
