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
    await session.execute(
        text(
            "INSERT INTO app_setting (branch_id, key, value) VALUES (:b, :k, :v)"
            " ON CONFLICT (branch_id, key) DO UPDATE SET value = excluded.value"
        ),
        {"b": branch_id, "k": _WATERMARK_KEY, "v": when.isoformat()},
    )


async def branch_needs_reindex(session: AsyncSession, branch_id: int) -> bool:
    last = await _last_edit(session, branch_id)
    if last is None:
        return False  # no content to index
    indexed = await _indexed_at(session, branch_id)
    return indexed is None or last > indexed


async def reindex_branch(session: AsyncSession, branch_id: int, llm: LLMPort) -> int:
    """Rebuild the branch index and advance its watermark. Returns chunks stored."""
    started = _utcnow()
    stored = await RagService(session, branch_id, llm).reindex()
    await _set_watermark(session, branch_id, started)
    return stored
