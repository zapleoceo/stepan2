"""Branch 1 through both pipelines, byte for byte — the gate for moving it off legacy.

Branch 1 is the live Indonesian tenant: 37 000 messages, the revenue, and a prompt pinned at
a63a2a39f44e5339 / 105 846 chars. The owner's condition for the move is "the same answers, the
same answer logic — the architecture is what changes". So the move is allowed exactly when the
composer reproduces the legacy assembler's bytes on branch 1's own data, and not before.

The fixture below is branch 1's SHAPE read off production 2026-08-04 — the same four document
slugs with the same categories and sort_order values, the same fourteen product slugs with the
same sort_order values and kinds — carrying harmless text, because the real content is a
client's commercial material and does not belong in the repo. Assembly is what a refactor
changes, so assembly is what this pins.

Today the two pipelines do NOT agree, and this file states each disagreement as its own
assertion so the work list is readable rather than a wall of diff. When one of them starts
passing the wrong way round, the tests below fail — that is the intended alarm, not a nuisance:
each is a claim about production that stopped being true.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.adapters.db.models import Branch, KnowledgeDoc, Product  # noqa: E402
from app.modules.conversation.free_mode import free_contract, language_name  # noqa: E402
from app.modules.knowledge.service import KnowledgeService  # noqa: E402
from app.modules.promptlib.composer import compose_context  # noqa: E402
from app.modules.promptlib.craft import craft_contract  # noqa: E402

# Branch 1's documents as production holds them: (slug, category, sort_order). facts_policy and
# facts_market carry NO category and the SAME sort_order — which is why the composer's
# (sort_order, slug) key puts market before policy while the legacy slug list puts policy first.
_DOCS: tuple[tuple[str, str | None, int], ...] = (
    ("persona_core", "persona", 0),
    ("facts_policy", None, 0),
    ("facts_market", None, 0),
    ("objection_playbook", "playbook", 50),
)

# Branch 1's catalogue as production holds it: (slug, sort_order, kind). The sort_order values
# are a deliberate merchandising order (vibe_coding first, the Skill Boosters last on 100) and
# bear no relation to alphabetical, which is the order the legacy assembler emits.
_PRODUCTS: tuple[tuple[str, int, str], ...] = (
    ("vibe_coding", 10, "course"),
    ("python_backend", 20, "course"),
    ("data_analyst", 30, "course"),
    ("cybersecurity", 40, "course"),
    ("uiux_design", 50, "course"),
    ("vibe_coding_demo_event", 50, "event"),
    ("smm_intensive", 60, "course"),
    ("graphic_design", 70, "course"),
    ("social_media_bootcamp", 80, "course"),
    ("cybersecurity_skillbooster", 100, "course"),
    ("data_analyst_skillbooster", 100, "course"),
    ("graphic_design_skillbooster", 100, "course"),
    ("python_skillbooster", 100, "course"),
    ("uiux_design_skillbooster", 100, "course"),
)


async def _branch_one(session) -> int:  # noqa: ANN001
    b = Branch(name="Jakarta-shaped", lang="id")
    session.add(b)
    await session.flush()
    for slug, category, order in _DOCS:
        session.add(KnowledgeDoc(branch_id=b.id, slug=slug, category=category,
                                 sort_order=order, title=f"Doc {slug}",
                                 content=f"# {slug}\nLine one of {slug}.\nLine two."))
    for slug, order, kind in _PRODUCTS:
        session.add(Product(branch_id=b.id, slug=slug, title=f"Title {slug}", kind=kind,
                            sort_order=order, is_active=True,
                            content=f"QUICK FACTS: 6 bulan | harga 1.000.000\nBody of {slug}."))
    await session.flush()
    return b.id


async def _legacy_prefix(session, branch_id: int) -> str:  # noqa: ANN001
    knowledge = await KnowledgeService(session, branch_id).full_knowledge_context()
    return knowledge.rstrip() + "\n\n" + free_contract("id")


async def _composed_prefix(session, branch_id: int) -> str:  # noqa: ANN001
    knowledge = await compose_context(session, branch_id, "id")
    return knowledge.rstrip() + "\n\n" + craft_contract(language_name("id"))


@pytest.mark.xfail(strict=True, reason="branch 1 is not movable yet — see the four tests below")
@pytest.mark.asyncio
async def test_branch_one_assembles_to_the_same_bytes_both_ways(db_session) -> None:  # noqa: ANN001
    """The acceptance gate for the move, written as the thing that must become true.

    It fails today, and the expectation is NOT adjusted to make it pass: the four tests below
    name each disagreement instead. strict=True makes this a gate in both directions — the day
    the composer does reproduce branch 1, this test reports an unexpected pass and the suite
    goes red, which is the prompt to delete the marker and flip the branch deliberately rather
    than discover the equality months later by accident.

    Until then branch 1 stays on legacy: flipping it would move messages[0] — the broker's
    prompt-cache anchor and the whole of what the model knows about the business — on the
    tenant that carries the revenue."""
    bid = await _branch_one(db_session)
    assert await _legacy_prefix(db_session, bid) == await _composed_prefix(db_session, bid)


@pytest.mark.asyncio
async def test_the_persona_header_names_the_slug_only_under_the_composer(db_session) -> None:  # noqa: ANN001
    """Difference 1, in the header line. Legacy injects ONE identity doc under a header that
    names no slug; the composer emits every persona-category doc and names each. Purely
    mechanical, and the only one of the four that composer code alone could close."""
    bid = await _branch_one(db_session)
    legacy = await KnowledgeService(db_session, bid).full_knowledge_context()
    composed = await compose_context(db_session, bid, "id")
    assert legacy.startswith("[persona lang=id]\n")
    assert composed.startswith("[persona persona_core lang=id]\n")


@pytest.mark.asyncio
async def test_the_fact_documents_come_out_in_a_different_order(db_session) -> None:  # noqa: ANN001
    """Difference 2, and it is branch 1's DATA, not the composer's code. The legacy order is a
    tuple of slugs in knowledge.service; the composer orders by (sort_order, slug). Branch 1
    gave facts_policy and facts_market the same sort_order — 0 — so the slug decides, and it
    decides the other way round. No content-independent rule in the composer can reorder this
    without reading the branch's rows differently from every other branch's."""
    bid = await _branch_one(db_session)
    legacy = await KnowledgeService(db_session, bid).full_knowledge_context()
    composed = await compose_context(db_session, bid, "id")
    assert legacy.index("[facts_policy]") < legacy.index("[facts_market]")
    assert composed.index("[facts_market]") < composed.index("[facts_policy]")


@pytest.mark.asyncio
async def test_the_catalogue_comes_out_in_a_different_order(db_session) -> None:  # noqa: ANN001
    """Difference 3, also branch 1's data. Legacy sorts the catalogue by slug; the composer by
    (sort_order, slug) — which S5 made deliberate, because the catalogue is the drop tail and
    sort_order is the only lever an operator has over what survives an overrun. Branch 1's
    sort_order values are a merchandising order, so the two sequences share only their length."""
    bid = await _branch_one(db_session)
    legacy = await KnowledgeService(db_session, bid).full_knowledge_context()
    composed = await compose_context(db_session, bid, "id")
    by_slug = [slug for slug, _, _ in sorted(_PRODUCTS)]
    by_order = [slug for slug, _, _ in sorted(_PRODUCTS, key=lambda p: (p[1], p[0]))]
    assert _catalogue_order(legacy) == by_slug
    assert _catalogue_order(composed) == by_order
    assert by_slug != by_order


def _catalogue_order(prefix: str) -> list[str]:
    return [line.removeprefix("[product ").split(" ", 1)[0]
            for line in prefix.splitlines() if line.startswith("[product ")]


def test_no_assembly_upstream_can_make_the_two_prefixes_equal() -> None:
    """Difference 4, and the one that closes the question — as an impossibility, not a diff.

    Each prefix is `knowledge + "\\n\\n" + contract`, so the composed prefix ends with CRAFT and
    the legacy one ends with the fused contract. Equal strings have equal suffixes, so equality
    would require CRAFT to be a suffix of the legacy contract — the knowledge half absorbing
    everything above it. It is not: the two say the same closing advice in two voices ("say you
    don't know" against "say you do not know"), and they diverge again far above that.

    So no ordering, header, budget or sort change in the composer can bring branch 1's two
    prefixes together. Byte-equality needs an edit to CRAFT — which four other branches now
    read — or to branch 1's own selling rules. Both are the owner's call, not a refactor's.

    The shared tail is asserted too, because it is the part that is deliberately identical:
    REPLY_JSON_SCHEMA lives in craft.py precisely so the two contracts ask for one JSON shape."""
    legacy = free_contract("id")
    craft = craft_contract(language_name("id"))
    assert legacy.splitlines()[-1] == craft.splitlines()[-1]  # the shared machine contract
    assert len(craft) < len(legacy)
    assert not legacy.endswith(craft)


def test_the_indonesian_style_block_exists_only_in_the_legacy_contract() -> None:
    """The concrete cost of flipping branch 1 today, in one line of evidence. "Kak"/"aku" and
    the ban on calling the place a "kampus" are Indonesian-only rules S4 deliberately withheld
    from CRAFT. Under the composer branch 1 would lose all three at once — the forms of address
    it has used across 37 000 messages, and a word choice the owner treats as a compliance
    matter. That is a behaviour change, not an architecture change."""
    legacy = free_contract("id")
    craft = craft_contract(language_name("id"))
    for token in ("Kak/Kakak", "kampus", "aku"):
        assert token in legacy
        assert token not in craft
