"""One-off: force a full RAG reindex of every branch after the broker's default voyage
embedding model changed (voyage-3 -> voyage-4, 2026-07-07). Same 1024 dims, but a different
vector space -- old voyage-3 chunks would silently score wrong against new voyage-4 query
vectors (retrieve()'s only guard is a length check, which voyage-4 also passes at 1024).

reindex_branch() ignores the content-staleness watermark and always rebuilds, so this is
safe to run even when no doc/product was edited -- it's the "model changed" case, not the
"content changed" case the watcher already handles.

Run in the container:  python -m scripts.reembed_all_branches
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.adapters.db.models import Branch
from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.modules.knowledge.reindex import reindex_branch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("reembed_all_branches")


async def main() -> None:
    llm = BrokerLLM()
    async with session_scope() as session:
        branch_ids = (await session.execute(select(Branch.id))).scalars().all()
    log.info("reindexing %d branches", len(branch_ids))
    for branch_id in branch_ids:
        async with session_scope() as session:
            stored = await reindex_branch(session, branch_id, llm)
        log.info("branch=%d reindexed: %d chunks", branch_id, stored)
    log.info("done")


asyncio.run(main())
