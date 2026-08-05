"""A branch that sells must know who it is.

The demo branch introduced itself as "Kirill from the consulting team" and offered an
"Example course". Nothing was broken in the code: the fresh-start migration switched off
every document and product the branch owned and switched on the neutral library ones, and the
neutral persona says "you have a name and you use it once when you introduce yourself" —
without saying which name. Told to introduce itself and given no identity, the model invented
one, and the catalogue it quoted was the library placeholder.

The money gate cannot catch this. It checks prices, dates and promises against the knowledge
base; a fabricated NAME is not a fact it knows to look for. So the check belongs here: a
branch whose prompt names neither the seller nor the business is misconfigured, and on the
branch Meta reviews that is a rejected submission.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.adapters.db.models import AppSetting, Branch, KnowledgeDoc  # noqa: E402
from app.modules.knowledge.service import KnowledgeService  # noqa: E402
from app.modules.promptlib.pipeline import prompt_knowledge  # noqa: E402
from app.modules.settings.service import invalidate  # noqa: E402

# The library persona, as it ships: correct about voice and refusals, deliberately silent
# about who the seller is — that belongs to the branch, not the library.
_NEUTRAL = """## Identity
You are a salesperson at this business, writing from your own account. You have a name and
you use it once, when you introduce yourself.
"""

_OWN = """# You are Stepan

You are an AI sales agent working the account of Zapleo Soft.
"""


async def _branch_with(session, docs: dict[str, str]) -> int:  # noqa: ANN001
    """A branch shaped like the demo one: on the composer, so WHICH documents reach the model
    is the branch's own in_prompt flags rather than a hardcoded slug list."""
    b = Branch(name="Demo", lang="en")
    session.add(b)
    await session.flush()
    session.add(AppSetting(branch_id=b.id, key="prompt_pipeline", value="composer"))
    for slug, content in docs.items():
        session.add(KnowledgeDoc(branch_id=b.id, slug=slug, title=slug,
                                 content=content, in_prompt=True))
    await session.flush()
    invalidate(b.id)  # the settings cache would otherwise answer with the default pipeline
    return b.id


@pytest.mark.asyncio
async def test_a_prompt_naming_nobody_is_the_bug_that_shipped(db_session) -> None:  # noqa: ANN001
    """Reproduces the live failure: only the neutral library persona in the prompt.

    It tells the model to introduce itself by name and never supplies one — which is exactly
    how "Kirill from the consulting team" reached a real conversation."""
    bid = await _branch_with(db_session, {"neutral_consultant": _NEUTRAL})

    text = await prompt_knowledge(db_session, bid, KnowledgeService(db_session, bid))

    assert "introduce yourself" in text          # the instruction is there
    assert "Stepan" not in text                  # the answer to it is not
    assert "Zapleo" not in text


@pytest.mark.asyncio
async def test_the_branch_own_persona_carries_the_identity(db_session) -> None:  # noqa: ANN001
    """The fix: the branch's own persona is in the prompt, so the name is given, not invented."""
    bid = await _branch_with(db_session, {"persona_core": _OWN})

    text = await prompt_knowledge(db_session, bid, KnowledgeService(db_session, bid))

    assert "Stepan" in text
    assert "Zapleo Soft" in text


@pytest.mark.asyncio
async def test_a_document_out_of_the_prompt_cannot_supply_the_name(db_session) -> None:  # noqa: ANN001
    """The identity was never deleted — it was flagged out of the prompt, which reads the same
    from the database and completely different to the model."""
    bid = await _branch_with(db_session, {"neutral_consultant": _NEUTRAL})
    off = KnowledgeDoc(branch_id=bid, slug="persona_core", title="persona_core",
                       content=_OWN, in_prompt=False)
    db_session.add(off)
    await db_session.flush()

    text = await prompt_knowledge(db_session, bid, KnowledgeService(db_session, bid))

    assert "Stepan" not in text
