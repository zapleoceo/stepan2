"""Which prompt pipeline a branch runs, and the two pieces that differ between them.

legacy   — knowledge.service.full_knowledge_context (hardcoded slug list) + free_mode's
           fused contract. Branch 1 is on this and stays on it: 37 000 live messages and a
           pinned fingerprint, migrated in its own step, not as a side effect of this one.
composer — the branch's own documents (composer.compose_context) + CRAFT, with the selling
           method living in the branch's knowledge where a market can edit it.

Defaulting to legacy is the whole safety property: a branch nobody has touched keeps exactly
today's prompt, byte for byte.
"""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.knowledge.service import KnowledgeService
from app.modules.settings.service import get_settings

from .composer import compose_context
from .craft import craft_contract

LEGACY = "legacy"
COMPOSER = "composer"


async def branch_pipeline(session: AsyncSession, branch_id: int) -> str:
    """The branch's pipeline; anything unrecognised reads as legacy — an operator typo must
    not silently swap a live branch's prompt for a different one."""
    cfg = await get_settings(session, branch_id)
    return COMPOSER if cfg.prompt_pipeline == COMPOSER else LEGACY


async def prompt_knowledge(
    session: AsyncSession, branch_id: int, knowledge: KnowledgeService,
) -> str:
    """The fact surface for this branch's pipeline.

    `knowledge` is already scoped to the branch the KB is READ from (which may be another
    branch — see knowledge.source.effective_kb_branch), while the pipeline flag belongs to
    the branch that is doing the selling. Composing from the KB branch keeps that alias
    working: a branch borrowing a KB borrows the documents, not the flag."""
    if await branch_pipeline(session, branch_id) == COMPOSER:
        lang = await knowledge._lang(None)  # noqa: SLF001 — the resolver the prompt itself uses
        return await compose_context(session, knowledge.branch_id, lang)
    return await knowledge.full_knowledge_context()


async def prompt_contract(session: AsyncSession, branch_id: int, lang: str) -> str:
    """The selling contract for this branch's pipeline. `lang` is the language to fall back
    to when the lead's is unreadable, not the language to write the contract in."""
    # Imported here, not at module scope: app.modules.conversation.__init__ pulls in
    # ReplyService -> delivery -> engine, and engine imports THIS module. At module scope that
    # cycle only survives because every existing entry point happens to import conversation
    # first; anything that reaches pipeline first (a script, a test) died on a partially
    # initialised module. Deferring the leaf import removes the ordering dependency entirely.
    from app.modules.conversation.free_mode import free_contract, language_name
    if await branch_pipeline(session, branch_id) == COMPOSER:
        return craft_contract(language_name(lang))
    return free_contract(lang)
