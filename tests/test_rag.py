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


class _DeadEmbedLLM(_BagLLM):
    """Every embed call fails (broker down) — every chunk gets dropped."""

    async def embed(self, texts, **k):  # noqa: ANN001, ANN003, ANN201
        raise RuntimeError("502 broker down")


async def test_incomplete_reindex_keeps_watermark_for_retry(db_session) -> None:
    """A transient embed failure that drops chunks must NOT advance the watermark, or a
    partial index locks in silently (the prod chat-1 156/278-chunk incident)."""
    bid = await _seed(db_session)
    stored = await reindex_branch(db_session, bid, _DeadEmbedLLM())
    assert stored == 0  # all chunks dropped
    assert await branch_needs_reindex(db_session, bid) is True  # not marked done → retries


async def test_retrieve_excludes_the_focused_products_own_chunks(db_session) -> None:
    """The focused product's card already goes out in full via the `[focus product=...]`
    block — its own chunks ranking again in the RAG results is pure duplicate content on
    every turn about the product actually in play. exclude_slug drops them so the k slots
    go to genuinely OTHER material instead."""
    bid = await _seed(db_session)
    await RagService(db_session, bid, _BagLLM()).reindex()
    hits_unfiltered = await RagService(db_session, bid, _BagLLM()).retrieve(
        "python career", k=5)
    assert any("py" in title.lower() or "python" in text.lower()
               for title, text in hits_unfiltered)  # the product's own chunk ranks in
    hits_filtered = await RagService(db_session, bid, _BagLLM()).retrieve(
        "python career", k=5, exclude_slug="py")
    assert all("python" not in text.lower() for _title, text in hits_filtered)


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


class _IntentLLM:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        return self._payload, {"cost_usd": 0.0}

    async def chat_deep(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        return self._payload, {"cost_usd": 0.0}

    async def embed(self, texts, **_k):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def test_coach_answers_a_question_without_editing(db_session) -> None:
    from app.modules.conversation.coach_service import propose_edit
    bid = await _seed(db_session)
    llm = _IntentLLM('{"intent":"answer","answer":"The price is 13M, per playbook_price."}')
    edit = await propose_edit(db_session, bid, "berapa harganya?", llm)
    assert edit.status == "answered"
    assert "13M" in edit.summary and edit.old_text is None  # an answer, not a KB write


async def test_coach_question_persisted_before_generation(db_session) -> None:
    """The question is saved as a 'thinking' row BEFORE the slow LLM call — so navigating
    away mid-generation never loses it. generate_into_edit then fills the same row."""
    from app.modules.conversation.coach_service import (
        create_pending_edit,
        generate_into_edit,
    )
    bid = await _seed(db_session)

    edit = await create_pending_edit(db_session, bid, "berapa harganya?")
    assert edit.id is not None
    assert edit.status == "thinking"        # question is durable immediately
    assert edit.request == "berapa harganya?"

    llm = _IntentLLM('{"intent":"answer","answer":"13M."}')
    filled = await generate_into_edit(db_session, bid, edit, llm)
    assert filled.id == edit.id             # same row, updated in place
    assert filled.status == "answered" and "13M" in filled.summary


def test_coach_thinking_bubble_self_polls() -> None:
    from app.api._i18n import _lang
    from app.api._ui_panels import _coach_response
    _lang.set("en")
    html = _coach_response(42, "hi?", "thinking", None, None, None, None, None)
    assert 'hx-get="/ui/coach/edit/42"' in html     # polls for the answer
    assert 'hx-trigger="every 2s"' in html
    assert 'id="ce-42"' in html
    # a finished edit does NOT poll
    done = _coach_response(42, "hi?", "answered", None, None, None, "13M", None)
    assert "hx-get=" not in done


async def test_coach_clarifies_before_ambiguous_edit(db_session) -> None:
    from app.modules.conversation.coach_service import propose_edit
    bid = await _seed(db_session)
    llm = _IntentLLM('{"intent":"clarify","summary":"Which doc should this go in?"}')
    edit = await propose_edit(db_session, bid, "add a note about parking", llm)
    assert edit.status == "clarify" and edit.old_text is None  # asks, doesn't write


async def test_coach_proposes_edit_that_needs_confirm(db_session) -> None:
    from app.modules.conversation.coach_service import propose_edit
    bid = await _seed(db_session)
    llm = _IntentLLM('{"intent":"edit","slug":"playbook_price","old_text":"The price and refund'
                     ' policy.","new_text":"The price is 13M.","summary":"set price"}')
    edit = await propose_edit(db_session, bid, "set the price to 13M", llm)
    assert edit.status == "proposed" and edit.new_text == "The price is 13M."  # awaits Apply


async def test_coach_analyze_chat_grades_against_kb(db_session) -> None:
    from app.adapters.db.models import Channel, ChannelThread, Lead, Message
    from app.domain.enums import ChannelKind
    from app.modules.conversation.coach_service import analyze_chat

    bid = await _seed(db_session)
    ch = Channel(branch_id=bid, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=bid)
    db_session.add_all([ch, lead])
    await db_session.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    db_session.add(th)
    await db_session.flush()
    db_session.add(Message(branch_id=bid, thread_id=th.id, channel_id=ch.id, external_id="m1",
                           direction="in", sent_by="lead", text="berapa harga?"))
    await db_session.flush()

    llm = _IntentLLM("✅ Что верно: ...\n⚠️ Ошибки: none")
    out = await analyze_chat(db_session, bid, th.id, llm)
    assert "Что верно" in out
    # empty thread → empty analysis, no crash
    empty_th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-2")
    db_session.add(empty_th)
    await db_session.flush()
    assert await analyze_chat(db_session, bid, empty_th.id, llm) == ""


async def test_coach_edit_makes_branch_need_reindex(db_session) -> None:
    """A Coach edit changes doc.content AND bumps updated_at, so the RAG watcher rebuilds —
    without the bump the index kept serving the OLD text (the reported gap)."""
    from app.modules.conversation.coach_service import apply_edit
    from app.modules.knowledge.repository import KnowledgeRepo

    bid = await _seed(db_session)
    await reindex_branch(db_session, bid, _BagLLM())
    assert await branch_needs_reindex(db_session, bid) is False  # fresh after index

    doc = await KnowledgeRepo(db_session, bid).by_slug("playbook_price")
    from app.adapters.db.models import CoachingEdit
    edit = CoachingEdit(branch_id=bid, request="fix price", status="proposed",
                        slug="playbook_price", old_text="The price and refund policy.",
                        new_text="The price is 13,000,000 and refund policy.", summary="x")
    db_session.add(edit)
    await db_session.flush()
    applied = await apply_edit(db_session, bid, edit.id)
    assert applied.status == "applied"
    doc = await KnowledgeRepo(db_session, bid).by_slug("playbook_price")
    assert "13,000,000" in doc.content                       # content changed
    assert await branch_needs_reindex(db_session, bid) is True  # → RAG rebuilds next tick
