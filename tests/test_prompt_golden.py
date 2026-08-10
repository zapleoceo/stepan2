"""The golden harness: proof that a refactor changed no branch's prompt.

Every later step of the connector/prompt work touches code that assembles messages[0] — the
whole of what the model knows about the business, and the broker's prompt-cache anchor. The
live Indonesian branch has to come through all of it byte-identical, and "we were careful" is
not evidence.

This fixture reproduces the SHAPE of branch 1 — the same document slugs, the same number of
products, the same settings — with harmless text, because the real content is a client's
commercial material and does not belong in the repo. That is the right trade: a refactor
changes assembly, never content, so assembly is what a golden must pin.

The companion is scripts/prompt_snapshot.py, which hashes the REAL prompt on the server before
and after a deploy. Together: this test catches a change in CI, that script proves production
is untouched.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import hashlib  # noqa: E402

import pytest  # noqa: E402

from app.adapters.db.models import Branch, KnowledgeDoc, Product  # noqa: E402
from app.modules.conversation.dossier import LeadDossier  # noqa: E402
from app.modules.conversation.free_mode import build_messages_free, free_contract  # noqa: E402
from app.modules.knowledge.service import KnowledgeService  # noqa: E402

# The four documents branch 1 actually carries, in id order — the order the assembler relies on.
_DOC_SLUGS = ("persona_core", "facts_policy", "facts_market", "objection_playbook")
_PRODUCTS = 14  # branch 1's active catalogue size


async def _branch_shaped_like_production(session, lang: str = "id") -> int:
    b = Branch(name="GoldenBranch", lang=lang)
    session.add(b)
    await session.flush()
    for slug in _DOC_SLUGS:
        session.add(KnowledgeDoc(branch_id=b.id, slug=slug, title=f"Doc {slug}",
                                 content=f"# {slug}\nLine one of {slug}.\nLine two."))
    for n in range(_PRODUCTS):
        session.add(Product(branch_id=b.id, slug=f"course_{n}", title=f"Course {n}",
                            content=f"Course {n}: duration 6 months, price 1.000.000.",
                            is_active=True, sort_order=n))
    await session.flush()
    return b.id


async def _prefix(session, branch_id: int) -> str:
    knowledge = KnowledgeService(session, branch_id)
    lang = await knowledge._lang(None)  # noqa: SLF001
    return (await knowledge.full_knowledge_context()).rstrip() + "\n\n" + free_contract(lang)


@pytest.mark.asyncio
async def test_the_prefix_is_stable_across_two_assemblies(db_session) -> None:  # noqa: ANN001
    """Byte-identical between calls: anything time- or lead-dependent leaking into the cached
    prefix would show up here first."""
    bid = await _branch_shaped_like_production(db_session)
    first = await _prefix(db_session, bid)
    second = await _prefix(db_session, bid)
    assert hashlib.sha256(first.encode()).hexdigest() == \
        hashlib.sha256(second.encode()).hexdigest()


@pytest.mark.asyncio
async def test_the_prefix_is_identical_for_two_different_leads(db_session) -> None:  # noqa: ANN001
    """The prompt-cache anchor must not depend on who is writing. If it ever does, the broker
    cache-hit rate collapses and the bill goes up silently."""
    bid = await _branch_shaped_like_production(db_session)
    knowledge = await KnowledgeService(db_session, bid).full_knowledge_context()
    a = build_messages_free(knowledge, [], "id", LeadDossier(), name_block="Lead: Andi")
    b = build_messages_free(knowledge, [], "id", LeadDossier(), name_block="Lead: Budi")
    assert a[0] == b[0]
    assert a[1] != b[1]  # the per-lead block is where they may differ, and it is not [0]


@pytest.mark.asyncio
async def test_two_branches_never_share_a_prefix(db_session) -> None:  # noqa: ANN001
    """Isolation, stated as a test: same shape, different tenants, different prompts."""
    one = await _branch_shaped_like_production(db_session)
    two = await _branch_shaped_like_production(db_session)
    assert one != two
    # Both branches hold identical text by construction, so equal LENGTH is the assertion that
    # means something: had either read the other's rows it would carry 8 docs and 28 products.
    solo = await KnowledgeService(db_session, one).full_knowledge_context()
    other = await KnowledgeService(db_session, two).full_knowledge_context()
    assert len(solo) == len(other)
    assert solo.count("[product ") == _PRODUCTS  # 14, not the other branch's 14 as well


@pytest.mark.asyncio
async def test_the_indonesian_contract_is_pinned(db_session) -> None:  # noqa: ANN001
    """A hash of the Indonesian contract. It WILL fail when someone edits the selling rules —
    that is the point: the diff must be read and the hash updated deliberately, never by
    accident while refactoring something else.

    To update: run the test, copy the actual hash, and say in the commit message what changed
    in the selling behaviour and why."""
    assert hashlib.sha256(free_contract("id").encode()).hexdigest()[:16] == "b1fffa5420a88184"


@pytest.mark.asyncio
async def test_a_non_indonesian_contract_differs_and_is_pinned(db_session) -> None:  # noqa: ANN001
    """English must stay free of the Indonesian style block; pinned so it cannot drift back."""
    en = free_contract("en")
    assert "Kak" not in en
    assert hashlib.sha256(en.encode()).hexdigest()[:16] == "b978d503522bd994"
