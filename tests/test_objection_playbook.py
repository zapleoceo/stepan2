"""Selective loading of the objection argument bank — never the whole playbook.

The whole point: at the exact moment a lead has an open objection, the KB + contract are
already over DeepSeek's 24k-char pricing threshold on their own (measured live, 2026-07-23:
19 551 + 5 770 = 25 321 chars with zero dialog). A library that loaded in full would only make
that worse. Loading ONLY the section matching what this lead actually raised keeps the cost
where it already is instead of adding to it.
"""
from __future__ import annotations

from app.adapters.db.models import Branch, KnowledgeDoc
from app.modules.knowledge.service import KnowledgeService

_PLAYBOOK = (
    "## price\nBahas nilai vs harga, cicilan tanpa bunga.\n\n"
    "## time\nSemua sesi direkam, bisa ditonton ulang.\n\n"
    "## trust\nJaringan internasional sejak 1999, kampus fisik nyata."
)


async def _branch_with_playbook(s) -> int:  # noqa: ANN001
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    s.add(KnowledgeDoc(branch_id=b.id, slug="objection_playbook", content=_PLAYBOOK))
    await s.flush()
    return b.id


async def test_only_the_matching_category_loads(db_session) -> None:  # noqa: ANN001
    bid = await _branch_with_playbook(db_session)
    out = await KnowledgeService(db_session, bid).objection_snippets(frozenset({"price"}))
    assert "cicilan tanpa bunga" in out
    assert "direkam" not in out
    assert "1999" not in out


async def test_two_categories_load_both_sections_and_nothing_else(db_session) -> None:  # noqa: ANN001
    bid = await _branch_with_playbook(db_session)
    out = await KnowledgeService(db_session, bid).objection_snippets(
        frozenset({"price", "trust"}))
    assert "cicilan tanpa bunga" in out
    assert "1999" in out
    assert "direkam" not in out


async def test_no_open_objection_loads_nothing(db_session) -> None:  # noqa: ANN001
    bid = await _branch_with_playbook(db_session)
    out = await KnowledgeService(db_session, bid).objection_snippets(frozenset())
    assert out == ""


async def test_a_category_with_no_matching_section_loads_nothing(db_session) -> None:  # noqa: ANN001
    bid = await _branch_with_playbook(db_session)
    out = await KnowledgeService(db_session, bid).objection_snippets(
        frozenset({"job_outcome"}))
    assert out == ""


async def test_a_missing_playbook_doc_is_harmless(db_session) -> None:  # noqa: ANN001
    b = Branch(name="NoPlaybook", lang="id")
    db_session.add(b)
    await db_session.flush()
    out = await KnowledgeService(db_session, b.id).objection_snippets(frozenset({"price"}))
    assert out == ""


async def test_the_whole_bank_never_loads_at_once(db_session) -> None:  # noqa: ANN001
    """The core guarantee: size scales with how many categories are OPEN, never with how many
    exist in the playbook."""
    bid = await _branch_with_playbook(db_session)
    svc = KnowledgeService(db_session, bid)
    one = await svc.objection_snippets(frozenset({"price"}))
    all_three = await svc.objection_snippets(frozenset({"price", "time", "trust"}))
    assert len(one) < len(all_three)
    assert len(all_three) < len(_PLAYBOOK) + 100  # roughly the source doc, not multiplied
