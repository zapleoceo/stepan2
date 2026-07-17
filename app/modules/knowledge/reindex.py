"""Reindex orchestration + the watcher's staleness check.

A branch is stale when any doc/product was edited after its last successful index. The
watermark lives in app_setting (key `rag_indexed_at`); the worker's watcher reindexes
stale branches on a cadence, and edits/restores can force a reindex directly."""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import _utcnow
from app.ports.llm import LLMPort

from .rag import RagService

logger = logging.getLogger(__name__)

_WATERMARK_KEY = "rag_indexed_at"


def _as_dt(value: object) -> datetime | None:
    """Coerce a scalar (datetime on Postgres, ISO string on SQLite raw text) to datetime."""
    if value is None or isinstance(value, datetime):
        return value  # type: ignore[return-value]
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


async def _last_edit(session: AsyncSession, branch_id: int) -> datetime | None:
    return _as_dt((await session.execute(
        text(
            "SELECT max(t) FROM ("
            " SELECT max(updated_at) AS t FROM knowledge_doc WHERE branch_id=:b"
            " UNION ALL SELECT max(updated_at) FROM product WHERE branch_id=:b) x"
        ),
        {"b": branch_id},
    )).scalar())


async def _indexed_at(session: AsyncSession, branch_id: int) -> datetime | None:
    value = (await session.execute(
        text("SELECT value FROM app_setting WHERE branch_id=:b AND key=:k"),
        {"b": branch_id, "k": _WATERMARK_KEY},
    )).scalar()
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


async def _set_watermark(session: AsyncSession, branch_id: int, when: datetime) -> None:
    from app.modules.settings.repository import SettingRepo  # noqa: PLC0415 (avoid cycle)

    await SettingRepo(session).upsert(_WATERMARK_KEY, when.isoformat(), branch_id=branch_id)


async def branch_needs_reindex(session: AsyncSession, branch_id: int) -> bool:
    from .source import effective_kb_branch  # noqa: PLC0415 (avoid import cycle)
    if await effective_kb_branch(session, branch_id) != branch_id:
        return False  # linked branch — reads the source's index, has none of its own
    last = await _last_edit(session, branch_id)
    if last is None:
        return False  # no content to index
    indexed = await _indexed_at(session, branch_id)
    return indexed is None or last > indexed


async def reindex_branch(session: AsyncSession, branch_id: int, llm: LLMPort) -> int:
    """Rebuild the branch index and advance its watermark. Returns chunks stored.

    The watermark is max(updated_at) of the content as read at the START of the rebuild —
    the same values `_last_edit` compares against — NEVER the worker's wall clock. The two
    clocks live in different processes (UI edits stamp updated_at with Postgres NOW(), the
    worker's _utcnow() is Python), and any skew between them let the wall-clock watermark
    run AHEAD of real edits, after which `branch_needs_reindex` returned False forever and
    a stale index lived on silently (live incident, 2026-07-17: a KB edit never reached
    RAG until a manual force)."""
    covered = await _last_edit(session, branch_id)
    rag = RagService(session, branch_id, llm)
    stored = await rag.reindex()
    if rag.incomplete:
        # transient embed failure dropped chunks — leave the watermark so the next tick
        # retries; advancing it would lock in a partial index (silent KB-context loss).
        logger.warning("reindex branch=%d incomplete (%d chunks) — watermark not advanced,"
                       " will retry", branch_id, stored)
        return stored
    await _set_watermark(session, branch_id, covered or _utcnow())
    return stored
