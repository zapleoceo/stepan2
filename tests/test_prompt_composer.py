"""The composer pipeline: a branch's prompt built from the branch's own documents.

The bug this closes, measured on production 2026-08-03: knowledge.service loads documents by
a HARDCODED tuple of slugs, so branch 7's eight documents and ~35 000 characters of its own
material — faq, playbook_close, sales_mastery, … — never reached the model. Its prompt
fingerprinted at 16 810 characters against branch 1's 105 846, and nothing said so.

Branch 1 must not move. Every test here builds its own branch and asserts the DEFAULT is the
legacy assembler, byte for byte.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pathlib  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

import pytest  # noqa: E402

from app.adapters.db.models import AppSetting, Branch, KnowledgeDoc, Product  # noqa: E402
from app.config import settings  # noqa: E402
from app.modules.conversation.dossier import LeadDossier  # noqa: E402
from app.modules.conversation.free_mode import (  # noqa: E402
    build_messages_free,
    free_contract,
)
from app.modules.knowledge.service import KnowledgeService  # noqa: E402
from app.modules.promptlib.composer import compose_context  # noqa: E402
from app.modules.promptlib.craft import craft_contract  # noqa: E402
from app.modules.promptlib.fit import fit_blocks  # noqa: E402
from app.modules.promptlib.pipeline import (  # noqa: E402
    branch_pipeline,
    prompt_contract,
    prompt_knowledge,
)

# Branch 7's real document names, read off production. Not one of them is a slug the legacy
# assembler knows, which is the whole point.
_BRANCH_7_SLUGS = ("faq", "playbook_close", "playbook_discovery", "sales_mastery")


async def _branch(session, lang: str = "en", *, pipeline: str | None = None,
                  marker: str = "AAAAA") -> int:
    """`marker` is stamped into every block so a cross-branch read is visible, not merely
    improbable: two branches built from the same shape would otherwise compare equal however
    badly the scoping leaked."""
    b = Branch(name="Fresh", lang=lang)
    session.add(b)
    await session.flush()
    session.add(KnowledgeDoc(branch_id=b.id, slug="persona_core", category="persona",
                             title="Persona", content=f"I am the seller of {marker}."))
    for i, slug in enumerate(_BRANCH_7_SLUGS):
        session.add(KnowledgeDoc(branch_id=b.id, slug=slug, category="playbook",
                                 sort_order=i, title=slug,
                                 content=f"Body of {slug} — property of {marker}."))
    session.add(Product(branch_id=b.id, slug="course_a", title="Course A",
                        content=f"QUICK FACTS: 6 months | price 1.000.000 | {marker}",
                        is_active=True))
    if pipeline is not None:
        session.add(AppSetting(branch_id=b.id, key="prompt_pipeline", value=pipeline))
    await session.flush()
    return b.id


@pytest.mark.asyncio
async def test_legacy_cannot_see_the_branchs_own_documents(db_session) -> None:  # noqa: ANN001
    """The failure as it stands in production, stated as a test so it cannot come back."""
    bid = await _branch(db_session)
    legacy = await KnowledgeService(db_session, bid).full_knowledge_context()
    for slug in _BRANCH_7_SLUGS:
        assert f"Body of {slug}" not in legacy


@pytest.mark.asyncio
async def test_the_composer_reads_every_document_the_branch_marked(db_session) -> None:  # noqa: ANN001
    bid = await _branch(db_session)
    composed = await compose_context(db_session, bid, "en")
    for slug in _BRANCH_7_SLUGS:
        assert f"Body of {slug}" in composed
    assert "I am the seller of" in composed
    assert "Course A" in composed


@pytest.mark.asyncio
async def test_in_prompt_false_takes_a_document_out(db_session) -> None:  # noqa: ANN001
    """The lever that replaces the slug list: a branch decides, per document, not the code."""
    bid = await _branch(db_session)
    doc = (await db_session.get(KnowledgeDoc, 2))
    assert doc.slug == _BRANCH_7_SLUGS[0]
    doc.in_prompt = False
    db_session.add(doc)
    await db_session.flush()
    composed = await compose_context(db_session, bid, "en")
    assert f"Body of {_BRANCH_7_SLUGS[0]}" not in composed
    assert f"Body of {_BRANCH_7_SLUGS[1]}" in composed


@pytest.mark.asyncio
async def test_the_composed_prefix_is_byte_stable(db_session) -> None:  # noqa: ANN001
    """Two assemblies, same bytes — the broker's prompt-cache anchor. Anything ordered by a
    dict, a set or a timestamp would show up here first."""
    bid = await _branch(db_session)
    assert await compose_context(db_session, bid, "en") == \
        await compose_context(db_session, bid, "en")


@pytest.mark.asyncio
async def test_two_branches_never_share_a_composed_prefix(db_session) -> None:  # noqa: ANN001
    one = await _branch(db_session, marker="ALPHA")
    two = await _branch(db_session, marker="OMEGA")
    assert one != two
    solo = await compose_context(db_session, one, "en")
    other = await compose_context(db_session, two, "en")
    assert "ALPHA" in solo and "OMEGA" not in solo
    assert "OMEGA" in other and "ALPHA" not in other
    # Same shape, same marker length: equal size means neither picked up the other's rows.
    assert len(solo) == len(other)
    assert solo.count("[product ") == 1


@pytest.mark.asyncio
async def test_a_branch_nobody_configured_stays_on_legacy(db_session) -> None:  # noqa: ANN001
    """The safety property the whole step rests on: default = today's behaviour, byte for
    byte. Branch 1 is never given a prompt_pipeline row by the migration."""
    bid = await _branch(db_session, lang="id")
    assert await branch_pipeline(db_session, bid) == "legacy"
    knowledge = KnowledgeService(db_session, bid)
    assert await prompt_knowledge(db_session, bid, knowledge) == \
        await knowledge.full_knowledge_context()
    assert await prompt_contract(db_session, bid, "id") == free_contract("id")


@pytest.mark.asyncio
async def test_the_flag_switches_both_halves_of_the_prefix(db_session) -> None:  # noqa: ANN001
    bid = await _branch(db_session, pipeline="composer")
    assert await branch_pipeline(db_session, bid) == "composer"
    knowledge = KnowledgeService(db_session, bid)
    assert await prompt_knowledge(db_session, bid, knowledge) == \
        await compose_context(db_session, bid, "en")
    assert await prompt_contract(db_session, bid, "en") == craft_contract("English")


@pytest.mark.asyncio
async def test_an_unknown_pipeline_value_reads_as_legacy(db_session) -> None:  # noqa: ANN001
    """An operator typo must not silently hand a live branch a different prompt."""
    bid = await _branch(db_session, pipeline="composr")
    assert await branch_pipeline(db_session, bid) == "legacy"


@pytest.mark.asyncio
async def test_build_messages_uses_the_contract_it_is_given(db_session) -> None:  # noqa: ANN001
    """Passing a contract must actually replace the fused one — not sit unused beside it."""
    given = "CONTRACT UNDER TEST"
    msgs = build_messages_free("KB", [], "id", LeadDossier(), contract=given)
    assert msgs[0]["content"].endswith(given)
    assert "Kak/Kakak" not in msgs[0]["content"]  # the Indonesian style block is gone with it
    default = build_messages_free("KB", [], "id", LeadDossier())
    assert "Kak/Kakak" in default[0]["content"]


@pytest.mark.asyncio
async def test_craft_carries_no_measurement_from_another_market(db_session) -> None:  # noqa: ANN001
    """CRAFT is shared by every branch, so anything a market measured must not be in it —
    percentages, thread ids, local hours, local forms of address, local currency."""
    craft = craft_contract("English")
    for token in ("%", "WIB", "thread ", "Kak", "kampus", "Rp", "WhatsApp", "Instagram",
                  "09.00", "18-55", "Menara"):
        assert token not in craft, f"CRAFT still carries {token!r} from a specific market"
    assert not any(ch.isdigit() and ch not in "123" for ch in craft), \
        "CRAFT carries a figure; figures are a market's, not the craft's"


def test_a_block_that_does_not_fit_is_dropped_whole_and_named() -> None:
    """Both pipelines now share this. On 30.07.2026 the budget was a character slice that fell
    inside a product card: the prompt ended '## PRI' and the price section was never read, on
    every reply, silently. A truncated block is strictly worse than an absent one — the model
    cannot tell "not here" from "cut off", so it fills in the rest itself."""
    fitted = fit_blocks(["A" * 40, "B" * 40, "CARD\nprice section"], budget=90)
    assert fitted.text == "A" * 40 + "\n\n" + "B" * 40
    assert fitted.dropped == ["CARD"]
    assert fitted.full_chars == 40 + 2 + 40 + 2 + len("CARD\nprice section")
    assert "price section" not in fitted.text  # no half-card ever reaches the model


def test_everything_that_fits_is_returned_untouched() -> None:
    fitted = fit_blocks(["one", "", "two"], budget=1000)
    assert fitted.text == "one\n\ntwo"  # empty blocks never become blank separators
    assert fitted.dropped == []


@pytest.mark.asyncio
async def test_craft_still_states_the_machine_contract(db_session) -> None:  # noqa: ANN001
    """Stripping the market must not strip the rules the CODE depends on: the JSON shape the
    parser expects, the bubble cap that protects the account, the hand-off condition."""
    craft = craft_contract("Bahasa Melayu")
    assert '"reply": str, "needs_human": bool, "human_reason": str|null' in craft
    assert "At most 3 bubbles, split with '|||'" in craft
    assert "needs_human=true ONLY when" in craft
    assert "Bahasa Melayu" in craft  # the fallback language, named as a person would name it


@pytest.mark.asyncio
async def test_the_catalogue_follows_the_branchs_own_order(db_session) -> None:  # noqa: ANN001
    """sort_order decides which card survives an overrun, so it has to reach the assembly.

    The catalogue is the composer's declared drop tail: whatever sits last is what a branch
    loses first. Ordering it by slug alone made alphabetical position the operator's only
    lever — a flagship called zzz_* fell out before a minor course called aaa_*, and the
    sort_order the KB editor exposes did nothing at all."""
    bid = await _branch(db_session)
    db_session.add(Product(branch_id=bid, slug="zzz_flagship", title="Flagship",
                           content="the one that pays for the branch", sort_order=0,
                           is_active=True))
    db_session.add(Product(branch_id=bid, slug="aaa_minor", title="Minor",
                           content="a small evening course", sort_order=99, is_active=True))
    await db_session.flush()
    composed = await compose_context(db_session, bid, "en")
    assert composed.index("[product zzz_flagship") < composed.index("[product aaa_minor")


@pytest.mark.asyncio
async def test_the_legacy_assembler_drops_a_whole_card_not_a_slice(db_session) -> None:  # noqa: ANN001
    """The path the golden cannot see, and the one branch 1 is actually on.

    tests/test_prompt_golden.py pins the CONTRACT hashes; it never hashes an assembled KB, and
    its fixture is four tiny docs, so full_knowledge_context's over-budget branch never runs
    there. That branch is the one the 30.07.2026 incident lived in: the cap was a character
    slice that landed inside a product card, the prompt ended '## PRI', and the price section
    was never read on any reply. So it gets its own test with real over-budget content."""
    budget = settings().free_context_char_budget
    b = Branch(name="Big", lang="id")
    db_session.add(b)
    await db_session.flush()
    db_session.add(KnowledgeDoc(branch_id=b.id, slug="persona_core", category="persona",
                                title="Persona", content="I sell here."))
    db_session.add(KnowledgeDoc(branch_id=b.id, slug="facts_policy", title="Policy",
                                content="P" * (budget - 10_000)))
    for slug in ("card_a", "card_b", "card_c"):
        db_session.add(Product(branch_id=b.id, slug=slug, title=slug.upper(), is_active=True,
                               content=f"QUICK FACTS: {slug}\n" + "C" * 4_000))
    await db_session.flush()

    assembled = await KnowledgeService(db_session, b.id).full_knowledge_context()

    assert len(assembled) <= budget
    assert "[facts_policy]" in assembled
    assert "[product card_a" in assembled and "[product card_b" in assembled
    # Gone entirely — header, title and body. A slice would have left the header behind and
    # the model would read a card that stops mid-sentence as a card that says nothing more.
    assert "card_c" not in assembled and "CARD_C" not in assembled


def test_promptlib_can_be_imported_before_anything_else() -> None:
    """app.modules.conversation.__init__ imports ReplyService -> delivery -> engine, and
    engine imports promptlib.pipeline. At module scope pipeline importing conversation closed
    that ring, and it only ever worked because every existing entry point happened to import
    conversation first. A script or a test that reached pipeline first died on a partially
    initialised module — so import it first, in a clean interpreter, and see."""
    root = pathlib.Path(__file__).parents[1]
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c",
         "import app.modules.promptlib.pipeline as p; print(p.COMPOSER)"],
        capture_output=True, text=True, cwd=str(root),
        env={**os.environ, "PYTHONPATH": str(root),
             "STEPAN2_DATABASE_URL": "sqlite+aiosqlite://",
             "STEPAN2_SECRET_KEY": Fernet.generate_key().decode()})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "composer"
