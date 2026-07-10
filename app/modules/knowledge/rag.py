"""RAG index for one branch — chunk → embed (broker) → store, and cosine retrieval.

Embeddings are stored as JSON arrays and scored in Python, so the same code runs on the
SQLite test DB and Postgres prod. Persona docs stay out of the index (they're always in
the prompt directly); everything else — playbooks, faq, stories, product cards — is
retrieved by similarity. Reindex is build-then-swap: delete the branch's chunks, rebuild."""
from __future__ import annotations

import json
import logging
import math

from sqlalchemy import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import KnowledgeChunk
from app.config import settings
from app.ports.llm import LLMPort

from .chunking import chunk_sections
from .repository import KnowledgeRepo, ProductRepo

logger = logging.getLogger(__name__)

# Docs injected/used directly, never chunked into the retrieval index: persona is
# identity (always injected); guard_verify is the reply-guard's checker prompt.
PERSONA_SLUGS = frozenset({"persona", "persona_core", "guard_verify"})
_EMBED_BATCH = 24  # broker/voyage rejects large embed batches (502) — keep well under ~40
_TOP_K = settings().rag_top_k

_Source = tuple[str, str, str, str]        # (source_type, slug, title, content)
_ChunkRow = tuple[str, str, str, int, str]  # (source_type, slug, title, seq, text)


class RagService:
    def __init__(self, session: AsyncSession, branch_id: int, llm: LLMPort) -> None:
        self.session = session
        self.branch_id = branch_id
        self.llm = llm
        self.docs = KnowledgeRepo(session, branch_id)
        self.products = ProductRepo(session, branch_id)
        self.incomplete = False  # True after reindex() if any chunk couldn't be embedded

    async def _sources(self) -> list[_Source]:
        """(source_type, slug, title, content) for everything indexable in this branch."""
        out: list[tuple[str, str, str, str]] = []
        for d in await self.docs.all():
            if d.slug in PERSONA_SLUGS or not (d.content or "").strip():
                continue
            out.append(("doc", d.slug, d.title or d.slug, d.content))
        for p in await self.products.active():
            body = f"{p.title}\n{p.content}".strip()
            if body:
                out.append(("product", p.slug, p.title, body))
        return out

    async def reindex(self) -> int:
        """Rebuild the whole branch index; returns the number of chunks stored. Sets
        `self.incomplete` when a transient embed failure dropped chunks — the caller must
        NOT advance the watermark then, or a partial index locks in (chat-1-index bug)."""
        rows = self._chunk_rows(await self._sources())
        await self.session.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.branch_id == self.branch_id))
        stored = await self._embed_and_store(rows)
        self.incomplete = stored < len(rows)
        logger.info("rag reindex branch=%d: %d/%d chunks%s", self.branch_id, stored,
                    len(rows), " (INCOMPLETE)" if self.incomplete else "")
        return stored

    async def reindex_source(self, source_type: str, slug: str) -> int:
        """Reindex a single doc/product (delete its chunks, rebuild). 0 if it has no
        indexable content (e.g. a persona doc or an inactive product)."""
        await self.session.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.branch_id == self.branch_id,
                KnowledgeChunk.source_type == source_type,
                KnowledgeChunk.source_slug == slug,
            ))
        rows = [r for r in self._chunk_rows(await self._sources())
                if r[0] == source_type and r[1] == slug]
        return await self._embed_and_store(rows)

    @staticmethod
    def _chunk_rows(sources: list[_Source]) -> list[_ChunkRow]:
        rows: list[_ChunkRow] = []
        for source_type, slug, title, content in sources:
            for seq, text in enumerate(chunk_sections(content)):
                rows.append((source_type, slug, title, seq, text))
        return rows

    async def _embed_and_store(self, rows: list[_ChunkRow]) -> int:
        stored = 0
        for i in range(0, len(rows), _EMBED_BATCH):
            stored += await self._store_batch(rows[i:i + _EMBED_BATCH])
        return stored

    async def _store_batch(self, batch: list[_ChunkRow]) -> int:
        """Embed a batch; on a broker size-rejection split it in half and retry."""
        if not batch:
            return 0
        try:
            vectors = await self.llm.embed(
                [r[4] for r in batch], branch_id=self.branch_id, kind="embed:index")
        except Exception:
            if len(batch) == 1:
                logger.warning("rag: dropping unembeddable chunk %s/%s",
                               batch[0][0], batch[0][1])
                return 0
            mid = len(batch) // 2
            return await self._store_batch(batch[:mid]) + await self._store_batch(batch[mid:])
        for (source_type, slug, title, seq, text), vec in zip(batch, vectors, strict=True):
            self.session.add(KnowledgeChunk(
                branch_id=self.branch_id, source_type=source_type, source_slug=slug,
                title=title, seq=seq, text=text, embedding=json.dumps(vec)))
        return len(batch)

    async def retrieve(
        self, query: str, k: int = _TOP_K, thread_id: int | None = None,
        exclude_slug: str | None = None,
    ) -> list[tuple[str, str]]:
        """Top-k (title, text) chunks most similar to `query`; [] if index/query empty.

        exclude_slug drops chunks from that product — used for the currently-FOCUSED
        product, whose card is already sent in full via the `[focus product=...]` block.
        Without this, a query about that exact product ranks its own chunks highest and
        sends near-duplicate content twice (the full card, then its own top-ranked pieces
        again) — pure wasted tokens on every turn about the product actually in play."""
        query = (query or "").strip()
        if not query:
            return []
        stmt = select(KnowledgeChunk).where(KnowledgeChunk.branch_id == self.branch_id)
        if exclude_slug:
            stmt = stmt.where(KnowledgeChunk.source_slug != exclude_slug)
        chunks = (await self.session.execute(stmt)).scalars().all()
        if not chunks:
            return []
        qv = (await self.llm.embed([query[:2000]], branch_id=self.branch_id,
                                   thread_id=thread_id, kind="embed:query"))[0]
        qn = _norm(qv)
        if qn == 0.0:
            return []
        scored: list[tuple[float, KnowledgeChunk]] = []
        for c in chunks:
            vec = json.loads(c.embedding or "[]")
            if len(vec) != len(qv):
                continue  # embedding model changed — skip stale chunk
            cn = _norm(vec)
            if cn == 0.0:
                continue
            scored.append((_dot(qv, vec) / (qn * cn), c))
        scored.sort(key=lambda s: s[0], reverse=True)
        return [(c.title, c.text) for _, c in scored[:k]]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))
