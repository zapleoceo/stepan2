"""Knowledge service — assembles the branch's persona + product context for prompts.

No branch_id filtering lives here; all reads go through the BranchScoped repos.
"""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Product

from .repository import KnowledgeRepo, ProductRepo

PERSONA_SLUG = "persona"
# Docs that carry the core identity/voice — always first in the assembled block.
_PERSONA_FIRST = ("persona", "persona_core")


class KnowledgeService:
    """Read-only knowledge access for one branch — the LLM prompt's context source."""

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        self.session = session
        self.branch_id = branch_id
        self.docs = KnowledgeRepo(session, branch_id)
        self.products = ProductRepo(session, branch_id)

    async def persona_block(self) -> str:
        """The branch's full ruleset for the prompt: every knowledge doc concatenated
        (persona/persona_core first, then playbooks/stories by slug), each under a [slug]
        header. "" when the branch has no docs. This is S1's 'direct' knowledge mode —
        all rules in-context; RAG chunk-selection is a separate future backend."""
        docs = await self.docs.all()
        docs.sort(key=lambda d: (d.slug not in _PERSONA_FIRST, d.slug))
        parts = [f"[{d.slug}]\n{d.content.strip()}" for d in docs if d.content.strip()]
        return "\n\n".join(parts)

    async def product_card(self, slug: str) -> str | None:
        """A single product's content within the branch, or None if absent."""
        product = await self.products.by_slug(slug)
        return product.content if product is not None else None

    async def _lang(self, lang: str | None) -> str:
        if lang is not None:
            return lang
        branch = await self.session.get(Branch, self.branch_id)
        return branch.lang if branch is not None else "id"

    async def knowledge_context(
        self, product_slug: str | None, lang: str | None = None
    ) -> str:
        """Persona + focused product card (if slug) + active products, in branch lang."""
        resolved_lang = await self._lang(lang)
        active = await self.products.active()
        focused = await self._focused(product_slug)

        blocks = [_persona_block(await self.persona_block(), resolved_lang)]
        if focused is not None:
            blocks.append(_focus_block(focused, resolved_lang))
        blocks.append(_catalog_block(active, resolved_lang))
        return "\n\n".join(b for b in blocks if b)

    async def _focused(self, product_slug: str | None) -> Product | None:
        if product_slug is None:
            return None
        return await self.products.by_slug(product_slug)


def _persona_block(content: str, lang: str) -> str:
    if not content:
        return ""
    return f"[persona lang={lang}]\n{content}"


def _focus_block(product: Product, lang: str) -> str:
    header = f"[focus product={product.slug} lang={lang}]"
    return f"{header}\n{product.title}\n{product.content}".rstrip()


def _catalog_block(products: list[Product], lang: str) -> str:
    if not products:
        return ""
    lines = [f"- {p.slug}: {p.title}" for p in products]
    return f"[catalog lang={lang}]\n" + "\n".join(lines)
