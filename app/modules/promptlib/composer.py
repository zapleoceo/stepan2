"""Composer — a branch's prompt assembled out of what the branch actually holds.

The legacy assembler (knowledge.service.full_knowledge_context) picks documents by a
HARDCODED TUPLE of slugs: facts_policy, facts_market, payment_policy, policy_prohibitions,
objection_playbook. A branch that names its documents anything else is silently invisible.
Branch 7 held eight documents and 35 000 characters of its own material under names that
tuple had never heard of; its prompt fingerprinted at 16 810 characters against branch 1's
105 846, and nothing in the product said so. That is not a knowledge base, it is a filter
written in Jakarta.

Here the selection is DATA: every doc the branch marked in_prompt, in the branch's own order.
Adding a document is enough to have the model read it — which is what an operator already
believed was happening.

Order is fixed and content-independent so the assembled prefix stays byte-stable across turns
and leads; the broker's prompt cache is the only reason this is worth the discipline.
"""
from __future__ import annotations

import logging

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import KnowledgeDoc, Product
from app.config import settings
from app.modules.knowledge.repository import KnowledgeRepo, ProductRepo

from .fit import fit_blocks

logger = logging.getLogger(__name__)

# The two categories the composer treats specially — everything else is BUSINESS fact and
# rides in the middle. Persona first because it is who the model is before it is told
# anything; method last of the docs because it reads as instruction over facts.
PERSONA_CATEGORY = "persona"
METHOD_CATEGORY = "method"


def _doc_key(doc: KnowledgeDoc) -> tuple[int, str]:
    return (doc.sort_order, doc.slug)


def _persona_block(docs: list[KnowledgeDoc], lang: str) -> list[str]:
    return [f"[persona {d.slug} lang={lang}]\n{d.content.strip()}" for d in docs]


def _method_block(docs: list[KnowledgeDoc]) -> list[str]:
    return [f"[method {d.slug}]\n{d.content.strip()}" for d in docs]


def _fact_block(docs: list[KnowledgeDoc]) -> list[str]:
    return [f"[{d.slug}]\n{d.content.strip()}" for d in docs]


def _product_block(products: list[Product], lang: str) -> list[str]:
    return [f"[product {p.slug} lang={lang}]\n{p.title}\n{(p.content or '').strip()}"
            for p in products if (p.content or "").strip()]


async def compose_context(session: AsyncSession, branch_id: int, lang: str) -> str:
    """Persona → business facts → method → catalogue, every block the branch put in scope.

    No per-lead or per-turn input, on purpose: the result must be byte-identical across turns
    and leads or the broker's prompt cache dies and the Sonnet bill triples.

    Order is also drop priority — fit_blocks cuts from the tail — and the tail is the
    catalogue on purpose. A branch that outgrows the budget loses a product card, which costs
    the leads asking about that one course; losing the payment policy or the prohibitions
    instead would cost every lead. The real answer to an overrun is an operator turning
    in_prompt off, which is why the drop is logged with the names of what fell out.

    Because the catalogue IS the drop tail, its order decides which card survives an overrun.
    So it follows the branch's own sort_order, the same field the KB editor already exposes,
    with the slug only as the tie-break that keeps the bytes stable. Sorting by slug alone —
    what this did first — made alphabetical position the operator's only lever, and a
    flagship card called zzz_* the first thing to fall out."""
    docs = [d for d in await KnowledgeRepo(session, branch_id).all()
            if d.in_prompt and d.content.strip()]
    persona = sorted((d for d in docs if d.category == PERSONA_CATEGORY), key=_doc_key)
    method = sorted((d for d in docs if d.category == METHOD_CATEGORY), key=_doc_key)
    special = {PERSONA_CATEGORY, METHOD_CATEGORY}
    facts = sorted((d for d in docs if d.category not in special), key=_doc_key)
    products = sorted(await ProductRepo(session, branch_id).active(),
                      key=lambda p: (p.sort_order, p.slug))

    blocks = [
        *_persona_block(persona, lang),
        *_fact_block(facts),
        *_method_block(method),
        *_product_block(products, lang),
    ]
    fitted = fit_blocks(blocks, settings().free_context_char_budget)
    if fitted.dropped:
        logger.warning(
            "compose_context branch=%d: %d chars > %d budget — dropped %d block(s): %s",
            branch_id, fitted.full_chars, settings().free_context_char_budget,
            len(fitted.dropped), "; ".join(fitted.dropped))
    return fitted.text
