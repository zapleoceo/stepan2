"""Cloning a library entry into a branch — the copy that makes a prompt the branch's own.

A clone is a COPY, never a reference. From the moment it lands the branch edits its own rows:
the library cannot reach back into a live prompt, and a branch's edits cannot leak into the
library. That is the entire point of the split — a shared method that every branch pointed at
would be the Indonesian contract again, one constant, one market, everybody's prompt.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import (
    BranchPromptSource,
    KnowledgeDoc,
    Product,
    PromptLibraryItem,
)
from app.domain.clock import utc_now
from app.modules.knowledge.repository import KnowledgeRepo, ProductRepo

from .composer import METHOD_CATEGORY, PERSONA_CATEGORY
from .library_seed import CATALOGUE, METHOD, PERSONA

logger = logging.getLogger(__name__)

_CATEGORY_OF = {PERSONA: PERSONA_CATEGORY, METHOD: METHOD_CATEGORY}


class CloneConflict(Exception):
    """The branch already holds a row the clone would overwrite. Raised rather than merged:
    the branch's copy is the one that has been edited by the people who sell there."""


@dataclass(frozen=True)
class CloneResult:
    layer: str
    created: int
    retired: int


async def library_item(
    session: AsyncSession, kind: str, slug: str, version: str | None = None,
) -> PromptLibraryItem | None:
    """One library entry; without a version, the highest-sorting published one for that slug."""
    q = select(PromptLibraryItem).where(
        PromptLibraryItem.kind == kind, PromptLibraryItem.slug == slug)
    if version is not None:
        q = q.where(PromptLibraryItem.version == version)
    rows = list((await session.execute(q)).scalars())
    published = [r for r in rows if r.status == "published"] or rows
    return max(published, key=lambda r: _ver_key(r.version)) if published else None


def _ver_key(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in (v or "0").split("."))
    except ValueError:
        return (0,)


async def clone_into_branch(
    session: AsyncSession, branch_id: int, item: PromptLibraryItem, *,
    replace_existing: bool = False, overwrite: bool = False,
) -> CloneResult:
    """Copy one library entry into a branch and record where it came from.

    `replace_existing` takes the branch's CURRENT rows of that layer out of the prompt —
    in_prompt=false for docs, is_active=false for products. Nothing is deleted, so the switch
    is reversible by flipping those flags back; that is what makes the fresh-start case
    ("inherit nothing from the previous occupant of this branch") a safe operation rather
    than a data loss.

    `overwrite` is required to re-clone onto a slug the branch already holds — taking a newer
    library version, and discarding whatever the branch wrote on top of the old one."""
    if item.kind in _CATEGORY_OF:
        result = await _clone_doc(session, branch_id, item, replace_existing, overwrite)
    elif item.kind == CATALOGUE:
        result = await _clone_catalogue(session, branch_id, item, replace_existing, overwrite)
    else:
        raise ValueError(f"unknown library kind: {item.kind}")
    await _record_source(session, branch_id, item)
    await session.flush()
    logger.info("promptlib clone branch=%d layer=%s from=%s@%s created=%d retired=%d",
                branch_id, result.layer, item.slug, item.version,
                result.created, result.retired)
    return result


async def _clone_doc(
    session: AsyncSession, branch_id: int, item: PromptLibraryItem,
    replace_existing: bool, overwrite: bool,
) -> CloneResult:
    category = _CATEGORY_OF[item.kind]
    repo = KnowledgeRepo(session, branch_id)
    docs = await repo.all()
    retired = 0
    if replace_existing:
        for doc in docs:
            if doc.slug != item.slug and doc.category == category and doc.in_prompt:
                doc.in_prompt = False
                session.add(doc)
                retired += 1
    existing = next((d for d in docs if d.slug == item.slug), None)
    if existing is not None and not overwrite:
        raise CloneConflict(
            f"branch {branch_id} already has doc '{item.slug}' — pass overwrite to replace it")
    if existing is not None:
        existing.title, existing.content = item.title, item.body
        existing.category, existing.in_prompt = category, True
        session.add(existing)
        return CloneResult(item.kind, 0, retired)
    await repo.add(KnowledgeDoc(
        branch_id=branch_id, slug=item.slug, title=item.title, category=category,
        content=item.body, in_prompt=True, sort_order=0 if item.kind == PERSONA else 50))
    return CloneResult(item.kind, 1, retired)


async def _clone_catalogue(
    session: AsyncSession, branch_id: int, item: PromptLibraryItem,
    replace_existing: bool, overwrite: bool,
) -> CloneResult:
    cards = json.loads(item.body or "[]")
    if not isinstance(cards, list):
        raise ValueError(f"catalogue '{item.slug}' body is not a JSON list of cards")
    repo = ProductRepo(session, branch_id)
    incoming = {c["slug"] for c in cards}
    retired = 0
    if replace_existing:
        for p in await repo.active():
            if p.slug not in incoming:
                p.is_active = False
                session.add(p)
                retired += 1
    created = 0
    for card in cards:
        existing = await repo.by_slug(card["slug"])
        if existing is not None and not overwrite:
            raise CloneConflict(
                f"branch {branch_id} already has product '{card['slug']}' — pass overwrite")
        if existing is not None:
            existing.title, existing.content = card["title"], card.get("content", "")
            existing.is_active = True
            session.add(existing)
            continue
        await repo.add(Product(
            branch_id=branch_id, slug=card["slug"], title=card["title"],
            content=card.get("content", ""), kind=card.get("kind", "course"),
            sort_order=int(card.get("sort_order", 0)), is_active=True))
        created += 1
    return CloneResult(item.kind, created, retired)


async def _record_source(
    session: AsyncSession, branch_id: int, item: PromptLibraryItem,
) -> None:
    row = await session.get(BranchPromptSource, (branch_id, item.kind))
    if row is None:
        row = BranchPromptSource(branch_id=branch_id, layer=item.kind)
    row.library_item_id = item.id
    row.library_slug = item.slug
    row.library_version = item.version
    row.cloned_at = utc_now()
    session.add(row)
