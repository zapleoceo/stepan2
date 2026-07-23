"""Selective loading of facts_market's evidentiary sections (competitor comparison, income /
success-case proof) — deferred out of the always-loaded block, pulled back in only when the
matching objection category is open. Same structural gate as test_objection_playbook.py.

2026-07-23 measurement: facts_policy + facts_market together were ~10.2k chars of the ~15.2k
knowledge_context baseline (no focus product, no open objection) — the single largest chunk.
Of facts_market's 5 524 chars, "Perbandingan kompetitor" + "Penghasilan" + "Success cases"
alone were ~3 950 — evidence a reply only needs once a lead has actually raised trust or
job_outcome doubt, not on every turn."""
from __future__ import annotations

from app.adapters.db.models import Branch, KnowledgeDoc
from app.modules.knowledge.service import KnowledgeService

_MARKET = (
    "## О IT STEP\nSejak 1999, 24 negara.\n\n"
    "## Формат и платформа\nMicrosoft Teams, sesi direkam.\n\n"
    "## Сравнение с конкурентами (jujur, pakai RANGE)\nPurwadhika Rp 18-47M.\n\n"
    "## Доход (kira-kira, bukan jaminan)\nJunior developer Rp 5-9 juta/bulan.\n\n"
    "## Успешные кейсы / bukti\nMinStep dibuat alumnus Vibe Coding."
)


async def _branch_with_market(s) -> int:  # noqa: ANN001
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    s.add(KnowledgeDoc(branch_id=b.id, slug="facts_market", content=_MARKET))
    await s.flush()
    return b.id


async def test_always_docs_block_keeps_core_sections_only(db_session) -> None:  # noqa: ANN001
    bid = await _branch_with_market(db_session)
    out = await KnowledgeService(db_session, bid)._always_docs_block()
    assert "Sejak 1999" in out
    assert "Microsoft Teams" in out
    assert "Purwadhika" not in out
    assert "Rp 5-9 juta" not in out
    assert "MinStep" not in out


async def test_trust_objection_pulls_in_only_competitor_section(db_session) -> None:  # noqa: ANN001
    bid = await _branch_with_market(db_session)
    out = await KnowledgeService(db_session, bid).market_snippets(frozenset({"trust"}))
    assert "Purwadhika" in out
    assert "Rp 5-9 juta" not in out
    assert "MinStep" not in out


async def test_job_outcome_objection_pulls_in_income_and_success_cases(db_session) -> None:  # noqa: ANN001
    bid = await _branch_with_market(db_session)
    out = await KnowledgeService(db_session, bid).market_snippets(frozenset({"job_outcome"}))
    assert "Rp 5-9 juta" in out
    assert "MinStep" in out
    assert "Purwadhika" not in out


async def test_no_open_objection_loads_no_market_snippets(db_session) -> None:  # noqa: ANN001
    bid = await _branch_with_market(db_session)
    out = await KnowledgeService(db_session, bid).market_snippets(frozenset())
    assert out == ""


async def test_a_category_with_no_gated_section_loads_nothing(db_session) -> None:  # noqa: ANN001
    bid = await _branch_with_market(db_session)
    out = await KnowledgeService(db_session, bid).market_snippets(frozenset({"price"}))
    assert out == ""


async def test_gating_survives_a_missing_market_doc(db_session) -> None:  # noqa: ANN001
    b = Branch(name="NoMarket", lang="id")
    db_session.add(b)
    await db_session.flush()
    svc = KnowledgeService(db_session, b.id)
    assert await svc._always_docs_block() == ""
    assert await svc.market_snippets(frozenset({"trust"})) == ""
