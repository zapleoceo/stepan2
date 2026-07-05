"""Knowledge-base sharing between branches.

A branch may either LINK to another branch's KB (reads persona/products/docs/RAG live
from the source — one source of truth) or COPY it (a one-time clone, then independent).
`effective_kb_branch` resolves which branch's KB a given branch actually reads from.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch


async def effective_kb_branch(session: AsyncSession, branch_id: int) -> int:
    """The branch whose KB `branch_id` reads from — its link source, or itself. One hop
    only (a source must be a real KB branch, never itself linked; the UI enforces this)."""
    branch = await session.get(Branch, branch_id)
    if branch is not None and branch.kb_source_branch_id:
        return branch.kb_source_branch_id
    return branch_id


async def copy_kb(session: AsyncSession, dst_branch_id: int, src_branch_id: int) -> int:
    """Clone src's knowledge docs + products into dst (replacing dst's own). One-time
    snapshot — dst stays independent afterwards. Returns docs + products copied."""
    if dst_branch_id == src_branch_id:
        return 0
    await session.execute(
        text("DELETE FROM knowledge_doc WHERE branch_id=:d"), {"d": dst_branch_id})
    await session.execute(
        text("DELETE FROM product WHERE branch_id=:d"), {"d": dst_branch_id})
    docs = (await session.execute(text(
        "INSERT INTO knowledge_doc (branch_id, slug, title, category, sort_order, content,"
        " updated_at, updated_by)"
        " SELECT :d, slug, title, category, sort_order, content, updated_at, updated_by"
        " FROM knowledge_doc WHERE branch_id=:s"), {"d": dst_branch_id, "s": src_branch_id}))
    prods = (await session.execute(text(
        "INSERT INTO product (branch_id, slug, title, content, is_active, kind, sort_order,"
        " updated_at, updated_by)"
        " SELECT :d, slug, title, content, is_active, kind, sort_order, updated_at, updated_by"
        " FROM product WHERE branch_id=:s"), {"d": dst_branch_id, "s": src_branch_id}))
    return (docs.rowcount or 0) + (prods.rowcount or 0)
