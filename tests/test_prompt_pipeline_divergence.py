"""What else differs between the two assemblers — the part branch 1's own rows hide.

test_prompt_branch1_equality pins the disagreements branch 1's data makes VISIBLE (the persona
header, the fact order, the catalogue order) and proves byte-equality is unreachable. Its
fixture copies production, so by construction it can only see a difference branch 1's four
documents already express.

The differences below exist today and branch 1 shows none of them, because its rows happen to
line up: exactly one persona-CATEGORY doc that happens to be NAMED persona_core, a playbook
whose sort_order happens to put it last, in_prompt on everywhere, a budget nobody changes at
runtime. Read off production 2026-08-04 those coincidences are real — and a coincidence is not
a guarantee. Each test states the rule the two assemblers actually follow and demonstrates it
on a branch that differs from branch 1 in exactly one row, so a later KB edit that breaks the
coincidence is a red test rather than a changed prompt.

Nothing here loads branch 1 or asserts about it; the fixtures are branch-1 SHAPED, carrying
harmless text, because the real content is a client's commercial material."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from typing import Any  # noqa: E402

import pytest  # noqa: E402

from app.adapters.db.models import Branch, KnowledgeDoc  # noqa: E402
from app.config import settings  # noqa: E402
from app.modules.conversation.guard import VERIFY_KB_CHARS, verify_grounding  # noqa: E402
from app.modules.knowledge.service import _FREE_CTX_CHAR_BUDGET, KnowledgeService  # noqa: E402
from app.modules.promptlib.composer import compose_context  # noqa: E402

# One row per document: (slug, category, sort_order, in_prompt). The default is branch 1's
# shape; every test below changes ONE field of ONE row and asserts what each assembler does.
_Doc = tuple[str, str | None, int, bool]
_BRANCH_ONE_DOCS: tuple[_Doc, ...] = (
    ("persona_core", "persona", 0, True),
    ("facts_policy", None, 0, True),
    ("facts_market", None, 0, True),
    ("objection_playbook", "playbook", 50, True),
)


async def _branch(session: Any, docs: tuple[_Doc, ...], body: str = "") -> int:
    b = Branch(name="Jakarta-shaped", lang="id")
    session.add(b)
    await session.flush()
    for slug, category, order, in_prompt in docs:
        session.add(KnowledgeDoc(
            branch_id=b.id, slug=slug, category=category, sort_order=order,
            in_prompt=in_prompt, title=f"Doc {slug}",
            content=f"# {slug}\nLine one of {slug}.{body}"))
    await session.flush()
    return b.id


async def _both(session: Any, branch_id: int) -> tuple[str, str]:
    legacy = await KnowledgeService(session, branch_id).full_knowledge_context()
    return legacy, await compose_context(session, branch_id, "id")


def _replaced(slug: str, **fields: Any) -> tuple[_Doc, ...]:
    """Branch 1's documents with one row's category/sort_order/in_prompt changed."""
    out: list[_Doc] = []
    for row in _BRANCH_ONE_DOCS:
        if row[0] != slug:
            out.append(row)
            continue
        out.append((row[0], fields.get("category", row[1]),
                    fields.get("sort_order", row[2]), fields.get("in_prompt", row[3])))
    return tuple(out)


@pytest.mark.asyncio
async def test_the_persona_is_chosen_by_slug_under_legacy_and_by_category_under_composer(
    db_session: Any,
) -> None:
    """Not a header difference — a SELECTION difference, and the sharper half of the two.

    Legacy walks a two-slug tuple (persona_core, persona) and takes the first that exists;
    category is never read. The composer takes every doc whose category is 'persona' and never
    reads the slug. On branch 1 they agree because its one persona-category doc is called
    persona_core — both halves of the coincidence are needed.

    Branches 9 and 10 already hold the row that breaks it: guard_verify, category='persona' —
    the grounding checker's OWN system prompt. Under the composer that document is injected
    into the sales prompt as identity. Legacy never sees it, at any branch."""
    bid = await _branch(db_session, (*_BRANCH_ONE_DOCS, ("guard_verify", "persona", 0, True)))
    legacy, composed = await _both(db_session, bid)
    assert "guard_verify" not in legacy
    assert "[persona guard_verify lang=id]" in composed
    assert composed.count("[persona ") == 2


@pytest.mark.asyncio
async def test_dropping_the_persona_category_keeps_it_persona_to_legacy_only(
    db_session: Any,
) -> None:
    """The other half of the same coincidence, in the direction an operator can reach.

    The KB editor exposes category as a free field. Clear it on persona_core — a filing
    decision, in an operator's mind — and legacy still injects the document as the model's
    identity while the composer files it under business facts, below the persona slot that is
    now empty. Same rows, same flag, two different answers to 'who are you'."""
    bid = await _branch(db_session, _replaced("persona_core", category=None))
    legacy, composed = await _both(db_session, bid)
    assert legacy.startswith("[persona lang=id]\n")
    assert "[persona " not in composed
    assert "[persona_core]" in composed


@pytest.mark.asyncio
async def test_in_prompt_switches_a_document_off_under_the_composer_only(
    db_session: Any,
) -> None:
    """in_prompt is the composer's selection rule and legacy's dead field.

    Legacy reaches every document by slug (KnowledgeRepo.by_slug applies no filter), so the
    KB panel's in-prompt switch does nothing on a legacy branch: the doc keeps riding in
    messages[0] after an operator has been shown it is off. The composer honours it. This is
    the one difference that runs the safe way round for branch 1 — flipping it can only ever
    REMOVE content the composer would send — but it is still a difference, and the panel tells
    the operator the same story on both."""
    bid = await _branch(db_session, _replaced("facts_policy", in_prompt=False))
    legacy, composed = await _both(db_session, bid)
    assert "[facts_policy]" in legacy
    assert "[facts_policy]" not in composed


@pytest.mark.asyncio
async def test_the_playbook_sits_last_by_structure_under_legacy_and_by_sort_order_under_composer(
    db_session: Any,
) -> None:
    """Branch 1's playbook lands last under BOTH assemblers for two unrelated reasons.

    Legacy appends it after the facts loop — a position in the code, unreachable from the KB
    panel. The composer has no notion of a playbook at all: category='playbook' is neither
    persona nor method, so it rides among the facts on (sort_order, slug), and lands last only
    because production gave it 50 against 0 and 0.

    So an operator reordering documents in the panel — here, pushing facts_market down to 99,
    an edit that touches no other row — moves the whole objection playbook above a facts
    document under the composer and moves nothing at all under legacy."""
    before = await _branch(db_session, _BRANCH_ONE_DOCS)
    after = await _branch(db_session, _replaced("facts_market", sort_order=99))
    legacy_before, composed_before = await _both(db_session, before)
    legacy_after, composed_after = await _both(db_session, after)
    assert _doc_order(legacy_before) == _doc_order(legacy_after)
    assert _doc_order(composed_before) != _doc_order(composed_after)
    assert _doc_order(composed_before)[-1] == "[objection_playbook]"
    assert _doc_order(composed_after)[-1] == "[facts_market]"


def _doc_order(text: str) -> list[str]:
    return [line for line in text.splitlines()
            if line.startswith("[") and line.endswith("]") and not line.startswith("[product ")]


@pytest.mark.asyncio
async def test_the_char_budget_is_read_at_import_by_legacy_and_per_call_by_the_composer(
    db_session: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two assemblers can fit to different ceilings within one process.

    knowledge.service binds _FREE_CTX_CHAR_BUDGET at MODULE IMPORT; composer calls settings()
    on every assembly. Identical today because nothing moves the value after start-up — but
    settings() is an lru_cache, and anything that clears it (the test suite's own autouse
    fixture does, every test) makes the composer follow the new value while legacy keeps the
    one it captured. The budget decides how much of the catalogue tail survives, so a
    divergence here silently removes product cards from one pipeline and not the other."""
    monkeypatch.setenv("STEPAN2_FREE_CONTEXT_CHAR_BUDGET", "600")
    settings.cache_clear()
    assert settings().free_context_char_budget != _FREE_CTX_CHAR_BUDGET
    bid = await _branch(db_session, _BRANCH_ONE_DOCS, body="\n" + "padding. " * 40)
    legacy, composed = await _both(db_session, bid)
    assert len(composed) <= 600
    assert len(legacy) > 600


class _CapturingVerifier:
    """Records what the fabrication gate actually sends the model."""

    def __init__(self) -> None:
        self.seen = ""

    async def chat(self, messages: list[dict[str, Any]], **kw: Any) -> tuple[str, dict[str, Any]]:
        self.seen = str(messages[-1]["content"])
        return "CLEAN", {"model": "x", "cost_usd": 0.0}


@pytest.mark.asyncio
async def test_the_fabrication_gate_reads_a_prefix_so_the_fact_order_changes_what_it_checks(
    db_session: Any,
) -> None:
    """The order difference is NOT layout-only, and this is where it stops being layout.

    verify_grounding truncates the assembled surface at VERIFY_KB_CHARS before handing it to
    the checker. Branch 1 assembles 94 064 chars of knowledge, so the checker sees the first
    13% — and the two assemblers put a different document there. It is the gate that exists
    because a public price mistake screenshots (comments.service runs it on every risky public
    reply), and after a reorder it would be grounding the same draft against other facts.

    The fixture sizes each fact document at the window itself, so the cut is guaranteed to
    fall inside the first document and the second one is not in the window at all."""
    body = "\n" + "x" * VERIFY_KB_CHARS
    bid = await _branch(db_session, (("facts_policy", None, 0, True),
                                     ("facts_market", None, 0, True)), body=body)
    legacy, composed = await _both(db_session, bid)

    llm = _CapturingVerifier()
    await verify_grounding(llm, "Rp 1.000.000", legacy, branch_id=bid, thread_id=0, bill=False)
    assert "[facts_policy]" in llm.seen
    assert "[facts_market]" not in llm.seen

    await verify_grounding(llm, "Rp 1.000.000", composed, branch_id=bid, thread_id=0, bill=False)
    assert "[facts_market]" in llm.seen
    assert "[facts_policy]" not in llm.seen


def test_the_pipeline_switch_is_not_a_dropdown() -> None:
    """Switching a branch's pipeline must not be one click in the settings panel.

    The S7 gate's answer to "how does this hurt Jakarta if it ships tonight" was exactly this:
    a plain two-option select, no confirmation, no fingerprint check. One save on branch 1
    drops 5115 characters of its own selling layer, changes which fact document the public
    fabrication verifier grounds against, reorders the catalogue and cold-busts a prompt cache
    at a 91% hit rate. Recovery is a second click; the replies sent in between are already out.

    hidden=True keeps the key resolvable — the composer branches read it every turn — while
    taking it off the form. Moving a branch is a migration with scripts/prompt_snapshot.py run
    either side, not a setting."""
    from app.modules.settings.schema import SCHEMA

    field = next(f for section in SCHEMA for f in section.fields if f.key == "prompt_pipeline")
    assert field.hidden
    assert field.default == "legacy"  # and a branch that was never migrated stays where it is
