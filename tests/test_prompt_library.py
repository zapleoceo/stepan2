"""The library and the clone: a branch takes a persona, a method and a catalogue, then owns
them.

The property under test is independence in both directions. The library must never reach into
a live branch's prompt (that is how one Indonesian constant became everybody's contract), and
a branch's edits must never travel back into the library.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import argparse  # noqa: E402
import json  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

import scripts.clone_prompt_library as cli  # noqa: E402
from app.adapters.db.models import (  # noqa: E402
    Branch,
    BranchPromptSource,
    KnowledgeDoc,
    Product,
    PromptLibraryItem,
)
from app.modules.knowledge.repository import KnowledgeRepo, ProductRepo  # noqa: E402
from app.modules.promptlib.clone import (  # noqa: E402
    CloneConflict,
    clone_into_branch,
    library_item,
)
from app.modules.promptlib.composer import compose_context  # noqa: E402
from app.modules.promptlib.library_seed import (  # noqa: E402
    CATALOGUE,
    METHOD,
    PERSONA,
    ensure_library,
)

_EX_CLIENT = "Former client: call the venue the Campus and quote in rupiah."


async def _branch(session, name: str = "Fresh") -> int:
    b = Branch(name=name, lang="en")
    session.add(b)
    await session.flush()
    return b.id


async def _occupied_branch(session) -> int:
    """A branch already carrying somebody else's prompt — branch 7's situation."""
    bid = await _branch(session, "TEST")
    session.add(KnowledgeDoc(branch_id=bid, slug="persona_core", category="persona",
                             title="Old persona", content=_EX_CLIENT))
    session.add(KnowledgeDoc(branch_id=bid, slug="sales_mastery", category="method",
                             title="Old method", content=_EX_CLIENT))
    session.add(Product(branch_id=bid, slug="stepan", title="Old product",
                        content=_EX_CLIENT, is_active=True))
    await session.flush()
    return bid


@pytest.mark.asyncio
async def test_the_library_seeds_three_separate_layers(db_session) -> None:  # noqa: ANN001
    """Persona and catalogue are separate entities, not one bundle — bundling them is how the
    Indonesian persona travelled with the Indonesian price list."""
    assert await ensure_library(db_session) == 3
    kinds = {i.kind for i in (await db_session.execute(
        select(PromptLibraryItem))).scalars()}
    assert kinds == {PERSONA, METHOD, CATALOGUE}


@pytest.mark.asyncio
async def test_seeding_twice_adds_nothing(db_session) -> None:  # noqa: ANN001
    await ensure_library(db_session)
    assert await ensure_library(db_session) == 0


@pytest.mark.asyncio
async def test_seeding_never_rewrites_a_row_somebody_cloned_from(db_session) -> None:  # noqa: ANN001
    """A library row is provenance for every branch that copied it; re-seeding must not edit
    it under them."""
    await ensure_library(db_session)
    item = await library_item(db_session, METHOD, "consultative_chat_sales")
    item.body = "edited by the author"
    db_session.add(item)
    await db_session.flush()
    await ensure_library(db_session)
    again = await library_item(db_session, METHOD, "consultative_chat_sales")
    assert again.body == "edited by the author"


@pytest.mark.asyncio
async def test_a_clone_lands_as_the_branchs_own_doc_with_provenance(db_session) -> None:  # noqa: ANN001
    await ensure_library(db_session)
    bid = await _branch(db_session)
    item = await library_item(db_session, METHOD, "consultative_chat_sales")
    result = await clone_into_branch(db_session, bid, item)
    assert result.created == 1
    doc = await KnowledgeRepo(db_session, bid).by_slug("consultative_chat_sales")
    assert doc is not None
    assert doc.category == "method"
    assert doc.in_prompt is True
    assert "objection ladder" in doc.content.lower()
    src = await db_session.get(BranchPromptSource, (bid, METHOD))
    assert src.library_slug == "consultative_chat_sales"
    assert src.library_version == item.version


@pytest.mark.asyncio
async def test_editing_a_clone_does_not_touch_the_library(db_session) -> None:  # noqa: ANN001
    """The branch's copy is the branch's. This is the requirement stated as a test."""
    await ensure_library(db_session)
    bid = await _branch(db_session)
    item = await library_item(db_session, METHOD, "consultative_chat_sales")
    original = item.body
    await clone_into_branch(db_session, bid, item)
    doc = await KnowledgeRepo(db_session, bid).by_slug("consultative_chat_sales")
    doc.content = "In this market, 'mahal' means the parent has not agreed yet."
    db_session.add(doc)
    await db_session.flush()
    db_session.expire_all()
    assert (await library_item(db_session, METHOD, "consultative_chat_sales")).body == original


@pytest.mark.asyncio
async def test_a_library_edit_does_not_reach_a_branch_that_cloned(db_session) -> None:  # noqa: ANN001
    """The direction that matters most: one edit in the library must not rewrite live prompts
    in every branch at once."""
    await ensure_library(db_session)
    bid = await _branch(db_session)
    item = await library_item(db_session, METHOD, "consultative_chat_sales")
    await clone_into_branch(db_session, bid, item)
    item.body = "REWRITTEN IN THE LIBRARY"
    db_session.add(item)
    await db_session.flush()
    doc = await KnowledgeRepo(db_session, bid).by_slug("consultative_chat_sales")
    assert "REWRITTEN IN THE LIBRARY" not in doc.content


@pytest.mark.asyncio
async def test_recloning_over_a_branch_edit_needs_saying_so(db_session) -> None:  # noqa: ANN001
    await ensure_library(db_session)
    bid = await _branch(db_session)
    item = await library_item(db_session, METHOD, "consultative_chat_sales")
    await clone_into_branch(db_session, bid, item)
    with pytest.raises(CloneConflict):
        await clone_into_branch(db_session, bid, item)
    await clone_into_branch(db_session, bid, item, overwrite=True)
    doc = await KnowledgeRepo(db_session, bid).by_slug("consultative_chat_sales")
    assert doc.content == item.body


@pytest.mark.asyncio
async def test_a_clone_never_reaches_another_branch(db_session) -> None:  # noqa: ANN001
    """Both halves: the copy lands only in the target, and a REPLACING clone retires only the
    target's rows. The second half is the one that can actually go wrong — retirement reads a
    doc list, and a list fetched one branch too wide would take another tenant's persona out
    of its prompt without anyone touching that branch."""
    await ensure_library(db_session)
    theirs = await _occupied_branch(db_session)
    mine = await _branch(db_session, "B")
    item = await library_item(db_session, PERSONA, "neutral_consultant")
    await clone_into_branch(db_session, mine, item, replace_existing=True)
    assert await KnowledgeRepo(db_session, theirs).by_slug("neutral_consultant") is None
    assert await db_session.get(BranchPromptSource, (theirs, PERSONA)) is None
    neighbour = await KnowledgeRepo(db_session, theirs).by_slug("persona_core")
    assert neighbour.in_prompt is True


@pytest.mark.asyncio
async def test_replacing_a_layer_retires_rows_without_deleting_them(db_session) -> None:  # noqa: ANN001
    """Reversibility: the previous occupant leaves the PROMPT, not the database, so putting
    it back is one flag."""
    await ensure_library(db_session)
    bid = await _occupied_branch(db_session)
    item = await library_item(db_session, PERSONA, "neutral_consultant")
    result = await clone_into_branch(db_session, bid, item, replace_existing=True)
    assert result.retired == 1
    old = await KnowledgeRepo(db_session, bid).by_slug("persona_core")
    assert old is not None and old.in_prompt is False
    assert old.content == _EX_CLIENT  # untouched, only out of scope


@pytest.mark.asyncio
async def test_cloning_a_catalogue_creates_cards_and_stands_down_the_rest(db_session) -> None:  # noqa: ANN001
    await ensure_library(db_session)
    bid = await _occupied_branch(db_session)
    item = await library_item(db_session, CATALOGUE, "starter_catalogue")
    result = await clone_into_branch(db_session, bid, item, replace_existing=True)
    expected = {c["slug"] for c in json.loads(item.body)}
    assert result.created == len(expected)
    active = {p.slug for p in await ProductRepo(db_session, bid).active()}
    assert active == expected
    old = await ProductRepo(db_session, bid).by_slug("stepan")
    assert old is not None and old.is_active is False


@pytest.mark.asyncio
async def test_a_rebuilt_branch_carries_nothing_from_the_previous_occupant(db_session) -> None:  # noqa: ANN001
    """The end-to-end shape of branch 7's fresh start: three layers cloned with replacement,
    and the composed prompt contains none of what the branch used to say."""
    await ensure_library(db_session)
    bid = await _occupied_branch(db_session)
    for kind, slug in ((PERSONA, "neutral_consultant"),
                       (METHOD, "consultative_chat_sales"),
                       (CATALOGUE, "starter_catalogue")):
        item = await library_item(db_session, kind, slug)
        await clone_into_branch(db_session, bid, item, replace_existing=True)
    composed = await compose_context(db_session, bid, "en")
    assert _EX_CLIENT not in composed
    assert "Old product" not in composed
    assert "objection ladder" in composed.lower()
    assert "[persona neutral_consultant lang=en]" in composed
    assert "[method consultative_chat_sales]" in composed
    assert "[product example_course lang=en]" in composed


@pytest.mark.asyncio
async def test_a_conflicting_clone_leaves_the_branch_exactly_as_it_was(db_session) -> None:  # noqa: ANN001
    """A refused clone must change NOTHING.

    The retirement loop used to run before the conflict check, so a --replace clone onto a
    slug the branch already held stood the incumbent persona down and then raised: the branch
    was left selling with no persona in its prompt at all, reported as a failure. Silent
    half-destruction is worse than either outcome the operator asked for."""
    await ensure_library(db_session)
    bid = await _occupied_branch(db_session)
    item = await library_item(db_session, PERSONA, "neutral_consultant")
    await clone_into_branch(db_session, bid, item)  # the branch now holds it, unretired
    incumbent = await KnowledgeRepo(db_session, bid).by_slug("persona_core")
    assert incumbent.in_prompt is True

    with pytest.raises(CloneConflict):
        await clone_into_branch(db_session, bid, item, replace_existing=True)

    db_session.expire_all()
    still = await KnowledgeRepo(db_session, bid).by_slug("persona_core")
    assert still.in_prompt is True, "the incumbent persona was retired by a clone that failed"
    composed = await compose_context(db_session, bid, "en")
    assert "[persona persona_core lang=en]" in composed


@pytest.mark.asyncio
async def test_a_catalogue_that_clashes_on_one_card_stands_nothing_down(db_session) -> None:  # noqa: ANN001
    """Same property one layer over, and the harder half: the clash is on the LAST thing the
    loop touches, so a per-card check that ran as it went would already have deactivated the
    branch's own cards by the time it fired."""
    await ensure_library(db_session)
    bid = await _branch(db_session, "Shop")
    item = await library_item(db_session, CATALOGUE, "starter_catalogue")
    clashing = json.loads(item.body)[0]["slug"]
    db_session.add(Product(branch_id=bid, slug="own_course", title="Ours",
                           content="Ours", is_active=True))
    db_session.add(Product(branch_id=bid, slug=clashing, title="Ours too",
                           content="Ours too", is_active=True))
    await db_session.flush()

    with pytest.raises(CloneConflict):
        await clone_into_branch(db_session, bid, item, replace_existing=True)

    db_session.expire_all()
    active = {p.slug for p in await ProductRepo(db_session, bid).active()}
    assert active == {"own_course", clashing}, "a failed clone deactivated the branch's cards"


@pytest.mark.asyncio
async def test_a_named_version_is_taken_as_asked_a_browse_is_published_only(db_session) -> None:  # noqa: ANN001
    """A retired method must not walk into a live branch's prompt just because it is the only
    row under that slug. Naming the version is an operator saying so out loud; not naming one
    is a browse, and a browse gets published or nothing."""
    await ensure_library(db_session)
    item = await library_item(db_session, METHOD, "consultative_chat_sales")
    item.status = "retired"
    db_session.add(item)
    await db_session.flush()
    assert await library_item(db_session, METHOD, "consultative_chat_sales") is None
    named = await library_item(db_session, METHOD, "consultative_chat_sales", item.version)
    assert named is not None and named.status == "retired"


@pytest.mark.asyncio
async def test_a_newer_published_version_wins_a_browse(db_session) -> None:  # noqa: ANN001
    await ensure_library(db_session)
    one = await library_item(db_session, METHOD, "consultative_chat_sales")
    db_session.add(PromptLibraryItem(kind=METHOD, slug="consultative_chat_sales",
                                     version="1.10", title="Newer", body="newer body",
                                     status="published"))
    await db_session.flush()
    # 1.10 > 1.9 > 1.0 numerically; string order would put "1.10" before "1.9".
    db_session.add(PromptLibraryItem(kind=METHOD, slug="consultative_chat_sales",
                                     version="1.9", title="Older", body="older body",
                                     status="published"))
    await db_session.flush()
    assert one.version == "1.0"
    assert (await library_item(db_session, METHOD, "consultative_chat_sales")).version == "1.10"


class _RecordingScope:
    """A stand-in for db.session_scope with its real semantics: commit on the way out,
    rollback and re-raise on an exception. A fake that swallowed the exception would prove
    nothing about the property under test — which is exactly that one escapes."""

    def __init__(self, session) -> None:  # noqa: ANN001
        self.session = session
        self.committed = False
        self.rolled_back = False

    @asynccontextmanager
    async def __call__(self):  # noqa: ANN202
        try:
            yield self.session
            self.committed = True
        except Exception:
            self.rolled_back = True
            raise


@pytest.mark.asyncio
async def test_the_script_unwinds_the_whole_clone_when_one_layer_fails(  # noqa: ANN201
    db_session, monkeypatch,  # noqa: ANN001
):
    """Every layer or none.

    session_scope COMMITS on the way out, so a failure reported with `return 1` from inside it
    persisted the layers that had already succeeded — the persona clone landing for real while
    the command printed an error about the method. The failure has to leave by raising."""
    await ensure_library(db_session)
    bid = await _branch(db_session, "Fresh")
    scope = _RecordingScope(db_session)
    monkeypatch.setattr(cli, "session_scope", scope)

    args = argparse.Namespace(branch_id=bid, persona="neutral_consultant",
                              method="no_such_method", catalogue=None, version=None,
                              replace=True, overwrite=False)
    assert await cli._clone(args) == 1  # noqa: SLF001

    assert scope.rolled_back and not scope.committed
    # And the first layer really had landed in the session the scope was asked to unwind:
    # without that, "nothing committed" would be true for an uninteresting reason.
    assert await KnowledgeRepo(db_session, bid).by_slug("neutral_consultant") is not None


@pytest.mark.asyncio
async def test_the_source_report_says_which_branch_cloned_what(db_session) -> None:  # noqa: ANN001
    """branch_prompt_source is only worth writing if something reads it back — the question
    it was created for ('who is still on method 1.0') needs an answer inside the product."""
    await ensure_library(db_session)
    plain = await _branch(db_session, "Untouched")
    cloned = await _branch(db_session, "Rebuilt")
    item = await library_item(db_session, METHOD, "consultative_chat_sales")
    await clone_into_branch(db_session, cloned, item)

    lines = await cli.source_report(db_session)
    assert len(lines) == 2
    assert f"consultative_chat_sales@{item.version}" in lines[1]
    assert "legacy" in lines[1]  # cloned, but nobody switched the pipeline — say so
    assert str(plain) in lines[0] and "(nothing cloned)" in lines[0]
