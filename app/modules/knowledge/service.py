"""Knowledge service — assembles the branch's persona + product + retrieved context.

RAG-only: the prompt gets persona (identity, direct) + the product catalog + a focused
product card + the chunks most relevant to the current dialog. The bulky playbooks/faq/
stories are NOT dumped in full — they reach the model only through retrieval. No branch_id
filtering lives here; all reads go through the BranchScoped repos."""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Product
from app.ports.llm import LLMPort

from .rag import PERSONA_SLUGS, RagService
from .repository import KnowledgeRepo, ProductRepo

PERSONA_SLUG = "persona"
# Persona identity is injected directly, persona_core first when present.
_PERSONA_ORDER = ("persona_core", "persona")


class KnowledgeService:
    """Knowledge access for one branch — the LLM prompt's context source. `llm` enables
    RAG retrieval; without it (unit tests) the context is persona + catalog + focus only."""

    def __init__(
        self, session: AsyncSession, branch_id: int, llm: LLMPort | None = None
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.llm = llm
        self.docs = KnowledgeRepo(session, branch_id)
        self.products = ProductRepo(session, branch_id)

    async def persona_block(self) -> str:
        """Every knowledge doc concatenated under [slug] headers (persona first). Retained
        for tooling/inspection; the prompt uses the RAG assembly, not this full dump."""
        docs = await self.docs.all()
        docs.sort(key=lambda d: (d.slug not in PERSONA_SLUGS, d.slug))
        parts = [f"[{d.slug}]\n{d.content.strip()}" for d in docs if d.content.strip()]
        return "\n\n".join(parts)

    async def _persona_text(self) -> str:
        """The identity block injected directly — persona_core when present, else persona."""
        for slug in _PERSONA_ORDER:
            doc = await self.docs.by_slug(slug)
            if doc is not None and doc.content.strip():
                return doc.content.strip()
        return ""

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
        self, product_slug: str | None, lang: str | None = None, query: str | None = None
    ) -> str:
        """Persona (direct) + catalog + focused card + RAG-retrieved chunks for `query`.
        Chunks are added only when an llm is available and a query is given."""
        resolved_lang = await self._lang(lang)
        blocks = [_persona_block(await self._persona_text(), resolved_lang)]
        focused = await self._focused(product_slug)
        if focused is not None:
            blocks.append(_focus_block(focused, resolved_lang))
        blocks.append(_catalog_block(await self.products.active(), resolved_lang))
        if self.llm is not None and query:
            chunks = await RagService(self.session, self.branch_id, self.llm).retrieve(query)
            blocks.append(_rag_block(chunks))
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


def _rag_block(chunks: list[tuple[str, str]]) -> str:
    if not chunks:
        return ""
    parts = [f"--- {title} ---\n{text}" for title, text in chunks]
    return "[relevant knowledge]\n" + "\n\n".join(parts)
