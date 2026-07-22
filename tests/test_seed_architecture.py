"""A newly seeded branch must get the CURRENT knowledge architecture, not the legacy one.

The seeds shipped the pre-restructure KB long after branch 1 had moved on: a 40 855-char
`persona` plus faq/market_facts/stories, and no facts_policy/facts_market at all. Any new
branch was therefore born with the exact shape the v3 rebuild exists to undo — and under a
small contract an oversized KB is what would eat the context budget back up.

The seeds are now a straight export of the live, working branch-1 knowledge base, so these
tests assert the architecture rather than the wording (which the owner edits freely).
"""
from __future__ import annotations

import json
from pathlib import Path

from app.modules.knowledge.seed import seed_indonesia
from app.modules.knowledge.service import _ALWAYS_DOC_SLUGS, KnowledgeService

_SEEDS = Path(__file__).resolve().parents[1] / "app" / "modules" / "knowledge" / "seeds"
# The assembled prompt has to leave room for the dialog and the contract; the KB is the
# larger half, so it gets a ceiling of its own.
_KB_CEILING = 22_000
_PERSONA_CEILING = 6_000


def _docs() -> list[dict]:
    return json.loads((_SEEDS / "indonesia_docs.json").read_text(encoding="utf-8"))


def _products() -> list[dict]:
    return json.loads((_SEEDS / "indonesia_products.json").read_text(encoding="utf-8"))


def test_seeds_ship_the_facts_docs_the_prompt_actually_loads() -> None:
    """knowledge_context only ever reads _ALWAYS_DOC_SLUGS — a doc outside that set is dead
    weight the model never sees."""
    slugs = {d["slug"] for d in _docs()}
    assert slugs & set(_ALWAYS_DOC_SLUGS)
    assert "persona_core" in slugs


def test_the_retired_pre_restructure_docs_are_gone() -> None:
    slugs = {d["slug"] for d in _docs()}
    assert not slugs & {"faq", "market_facts", "stories", "use_cases_tech", "student_policy"}


def test_the_seeded_persona_is_an_identity_not_a_playbook() -> None:
    """It was 40 855 chars — a whole tactics manual injected on every single turn."""
    persona = next(d for d in _docs() if d["slug"] == "persona_core")
    assert len(persona["content"]) < _PERSONA_CEILING


def test_every_seeded_product_carries_the_quick_facts_line() -> None:
    """The compact catalog anchor (duration + price) is parsed out of this line; a card
    without it falls back to just its title and the model loses the price."""
    for product in _products():
        assert "Quick facts" in product["content"] or "QUICK FACTS" in product["content"], \
            product["slug"]


def test_seeded_products_are_compact_enough_to_send_in_full() -> None:
    """The focus card ships whole, so an oversized card is an oversized prompt."""
    for product in _products():
        assert len(product["content"]) < 4_000, product["slug"]


async def test_a_freshly_seeded_branch_assembles_a_usable_context(db_session) -> None:  # noqa: ANN001
    """The end-to-end guarantee: seed a branch, ask for a turn's knowledge, get real facts
    within budget. Catches a canonical-doc pass overwriting a seeded doc with an empty one."""
    branch_id = await seed_indonesia(db_session)
    service = KnowledgeService(db_session, branch_id)

    context = await service.knowledge_context("vibe_coding")
    assert 5_000 < len(context) < _KB_CEILING
    assert "persona" in context
    assert "vibe_coding" in context


async def test_a_seeded_branch_can_still_answer_about_money(db_session) -> None:  # noqa: ANN001
    """The money gate blocks any figure absent from this context, so a seed that loses prices
    would make the bot unable to quote at all."""
    from app.modules.conversation.guard import canonical_prices

    branch_id = await seed_indonesia(db_session)
    context = await KnowledgeService(db_session, branch_id).knowledge_context("vibe_coding")
    assert canonical_prices(context, liberal=True)
