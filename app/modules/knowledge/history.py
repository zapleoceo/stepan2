"""KB edit history — every content change on a doc/product is journaled to
knowledge_revision (who / what / when), viewable and restorable. Written from the app
layer (portable to SQLite tests, and the actor is known here)."""
from __future__ import annotations

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import KnowledgeRevision, _utcnow


async def record_revision(
    session: AsyncSession, *, branch_id: int | None, entity_type: str, slug: str,
    old_content: str | None, new_content: str, actor: str | None,
) -> None:
    """Append a revision iff the content actually changed (title-only edits are ignored)."""
    if (old_content or "") == (new_content or ""):
        return
    session.add(KnowledgeRevision(
        branch_id=branch_id, entity_type=entity_type, slug=slug,
        old_content=old_content, new_content=new_content,
        old_len=len(old_content) if old_content is not None else None,
        new_len=len(new_content or ""), actor=actor,
    ))


async def list_revisions(
    session: AsyncSession, branch_id: int | None, entity_type: str, slug: str, limit: int = 50,
) -> list:
    rows = await session.execute(
        text(
            "SELECT id, old_content, new_content, old_len, new_len, actor, created_at"
            " FROM knowledge_revision"
            " WHERE entity_type=:et AND slug=:slug AND (:bid IS NULL OR branch_id=:bid)"
            " ORDER BY id DESC LIMIT :lim"
        ),
        {"et": entity_type, "slug": slug, "bid": branch_id, "lim": limit},
    )
    return list(rows.all())


async def restore_revision_scoped(
    session: AsyncSession, rev_id: int, *, writable: list[int] | None, actor: str | None,
) -> tuple[str, tuple[str, str] | None]:
    """Restore a revision into ITS OWN branch, once the caller is shown to have WRITE there.

    Returns ("ok", (entity_type, slug)) | ("missing", None) | ("forbidden", None) so the
    route can answer 404 and 403 honestly. Callers used to pass `writable[0]` as the scope,
    which for a multi-branch admin scoped every restore to whichever branch sorted first —
    silently "not found" for the branch they were actually looking at, and one membership
    change away from restoring into the wrong tenant.
    """
    row = (await session.execute(
        text("SELECT branch_id FROM knowledge_revision WHERE id=:id"), {"id": rev_id})).first()
    if row is None:
        return "missing", None
    owner = row[0]
    if writable is not None:
        # owner is None on a revision written before revisions carried a branch: there is
        # nothing to check it against, so only a write-anywhere caller may touch one.
        if owner is None or owner not in writable:
            return "forbidden", None
    out = await restore_revision(session, owner, rev_id, actor)
    return ("ok", out) if out is not None else ("missing", None)


async def restore_revision(
    session: AsyncSession, branch_id: int | None, rev_id: int, actor: str | None,
) -> tuple[str, str] | None:
    """Re-apply a revision's new_content to its live doc/product. Returns (entity_type, slug)
    so the caller can reindex/redirect, or None if the revision is missing/out of scope."""
    rev = (await session.execute(
        text(
            "SELECT entity_type, slug, new_content FROM knowledge_revision"
            " WHERE id=:id AND (:bid IS NULL OR branch_id=:bid)"
        ),
        {"id": rev_id, "bid": branch_id},
    )).first()
    if rev is None:
        return None
    entity_type, slug, content = rev[0], rev[1], rev[2] or ""
    # entity_type comes from our own row, mapped to a whitelisted table name (not user input)
    # updated_at MUST bump here: a raw UPDATE bypasses the ORM's onupdate, and a restore that
    # leaves the old timestamp misreports when the doc last changed (edit-recency in the KB UI
    # and history ordering) — the KB itself is loaded whole every reply, so a restore is live
    # at once with no index to refresh.
    if entity_type == "product":
        sel = ("SELECT content FROM product WHERE slug=:slug"
               " AND (:bid IS NULL OR branch_id=:bid)")
        upd = ("UPDATE product SET content=:c, updated_by=:a, updated_at=:ts"
               " WHERE slug=:slug AND (:bid IS NULL OR branch_id=:bid)")
    else:
        sel = ("SELECT content FROM knowledge_doc WHERE slug=:slug"
               " AND (:bid IS NULL OR branch_id=:bid)")
        upd = ("UPDATE knowledge_doc SET content=:c, updated_by=:a, updated_at=:ts"
               " WHERE slug=:slug AND (:bid IS NULL OR branch_id=:bid)")
    old = (await session.execute(text(sel), {"slug": slug, "bid": branch_id})).first()
    await session.execute(
        text(upd), {"c": content, "a": actor, "slug": slug, "bid": branch_id,
                    "ts": _utcnow()})
    await record_revision(session, branch_id=branch_id, entity_type=entity_type, slug=slug,
                          old_content=old[0] if old else None, new_content=content, actor=actor)
    return entity_type, slug
