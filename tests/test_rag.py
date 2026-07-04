"""RAG: section chunking, reindex → store, cosine retrieval ranks the relevant chunk,
persona docs stay out of the index, and the watcher's staleness check."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from sqlalchemy import func, select  # noqa: E402

from app.adapters.db.models import Branch, KnowledgeChunk, KnowledgeDoc, Product  # noqa: E402
from app.modules.knowledge.chunking import chunk_sections  # noqa: E402
from app.modules.knowledge.rag import RagService  # noqa: E402
from app.modules.knowledge.reindex import branch_needs_reindex, reindex_branch  # noqa: E402

_VOCAB = ["price", "refund", "schedule", "python", "design", "career"]


class _BagLLM:
    """Deterministic embedder: bag-of-words counts over a tiny vocab. A query sharing words
    with a chunk gets a high cosine, so retrieval order is predictable in tests."""

    async def chat(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
        raise NotImplementedError

    async def embed(self, texts, **_k):  # noqa: ANN001, ANN003, ANN201
        return [[float(t.lower().count(w)) for w in _VOCAB] for t in texts]


# ─── chunking ─────────────────────────────────────────────────────────────────

def test_chunk_sections_splits_and_keeps_heading() -> None:
    content = "## Price\nCosts 1M.\n\n## Refund\nNo refund after start."
    chunks = chunk_sections(content)
    assert any(c.startswith("## Price") and "Costs 1M" in c for c in chunks)
    assert any(c.startswith("## Refund") for c in chunks)


def test_chunk_sections_packs_within_limit() -> None:
    body = "\n\n".join(f"para {i} " + "x" * 300 for i in range(6))
    chunks = chunk_sections(f"## Big\n{body}", max_chars=700)
    assert len(chunks) > 1
    assert all(len(c) <= 900 for c in chunks)  # heading + packed paras, roughly bounded


# ─── reindex + retrieve ─────────────────────────────────────────────────────────

async def _seed(s) -> int:
    b = Branch(name="T", lang="en")
    s.add(b)
    await s.flush()
    s.add(KnowledgeDoc(branch_id=b.id, slug="persona_core", content="You are a bot. Be kind."))
    s.add(KnowledgeDoc(branch_id=b.id, slug="playbook_price",
                       content="## Price\nThe price and refund policy.\n\n"
                               "## Schedule\nThe schedule and dates."))
    s.add(Product(branch_id=b.id, slug="py", title="Python course",
                  content="Learn python for a career.", is_active=True))
    await s.flush()
    return b.id


async def test_reindex_excludes_persona_and_retrieves_relevant(db_session) -> None:
    bid = await _seed(db_session)
    stored = await RagService(db_session, bid, _BagLLM()).reindex()
    assert stored >= 3  # 2 price/schedule chunks + 1 product

    slugs = set((await db_session.execute(
        select(KnowledgeChunk.source_slug).where(KnowledgeChunk.branch_id == bid)
    )).scalars().all())
    assert "persona_core" not in slugs  # persona never indexed
    assert {"playbook_price", "py"} <= slugs

    hits = await RagService(db_session, bid, _BagLLM()).retrieve("what is the price and refund")
    assert hits and "refund" in hits[0][1].lower()  # price/refund chunk ranks first


async def test_reindex_is_a_full_rebuild(db_session) -> None:
    bid = await _seed(db_session)
    llm = _BagLLM()
    first = await RagService(db_session, bid, llm).reindex()
    await RagService(db_session, bid, llm).reindex()  # again — must not duplicate
    total = (await db_session.execute(
        select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.branch_id == bid)
    )).scalar()
    assert total == first


class _PickyLLM(_BagLLM):
    """Rejects embed batches larger than 2 — forces RagService to split and retry."""

    async def embed(self, texts, **k):  # noqa: ANN001, ANN003, ANN201
        if len(texts) > 2:
            raise RuntimeError("502 batch too large")
        return await super().embed(texts, **k)


async def test_reindex_splits_oversized_embed_batches(db_session) -> None:
    bid = await _seed(db_session)
    stored = await RagService(db_session, bid, _PickyLLM()).reindex()
    total = (await db_session.execute(
        select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.branch_id == bid)
    )).scalar()
    assert stored == total and total >= 3  # every chunk embedded despite the batch cap


async def test_retrieve_empty_when_no_index(db_session) -> None:
    bid = await _seed(db_session)
    assert await RagService(db_session, bid, _BagLLM()).retrieve("price") == []


async def test_retrieve_labels_embed_as_query_with_thread(db_session) -> None:
    """The per-reply retrieval embedding is tagged embed:query + carries the chat id, so
    the broker log distinguishes it from KB-reindex embeddings (embed:index)."""
    class _RecordingLLM(_BagLLM):
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def embed(self, texts, **k):  # noqa: ANN001, ANN003, ANN201
            self.calls.append(k)
            return await super().embed(texts, **k)

    bid = await _seed(db_session)
    llm = _RecordingLLM()
    await reindex_branch(db_session, bid, _BagLLM())
    await RagService(db_session, bid, llm).retrieve("price", thread_id=1761)
    assert llm.calls and llm.calls[-1]["kind"] == "embed:query"
    assert llm.calls[-1]["thread_id"] == 1761


# ─── watcher staleness ──────────────────────────────────────────────────────────

async def test_watcher_detects_edits(db_session) -> None:
    bid = await _seed(db_session)
    assert await branch_needs_reindex(db_session, bid) is True  # never indexed
    await reindex_branch(db_session, bid, _BagLLM())
    assert await branch_needs_reindex(db_session, bid) is False  # fresh

    doc = (await db_session.execute(
        select(KnowledgeDoc).where(KnowledgeDoc.slug == "playbook_price"))).scalars().first()
    from datetime import timedelta

    from app.adapters.db.models import _utcnow
    doc.updated_at = _utcnow() + timedelta(seconds=5)  # simulate a later edit
    db_session.add(doc)
    await db_session.flush()
    assert await branch_needs_reindex(db_session, bid) is True
