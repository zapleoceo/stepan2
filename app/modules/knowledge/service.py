"""Knowledge service — assembles the branch's persona + product + retrieved context.

RAG-only: the prompt gets persona (identity, direct) + the product catalog + a focused
product card + the chunks most relevant to the current dialog. The bulky playbooks/faq/
stories are NOT dumped in full — they reach the model only through retrieval. No branch_id
filtering lives here; all reads go through the BranchScoped repos."""
from __future__ import annotations

import logging

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Product
from app.config import settings
from app.ports.llm import LLMPort

from .rag import _TOP_K, PERSONA_SLUGS, RagService
from .repository import KnowledgeRepo, ProductRepo

logger = logging.getLogger(__name__)

PERSONA_SLUG = "persona"
# Persona identity is injected directly, persona_core first when present.
_PERSONA_ORDER = ("persona_core", "persona")
# Hard ceiling on the assembled context (chars) — see knowledge_context's docstring.
_CTX_CHAR_BUDGET = settings().knowledge_context_char_budget
# A follow-up nudge retrieves fewer chunks — it leans on the focus card, not broad recall.
_FOLLOWUP_RAG_K = 4


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
        self, product_slug: str | None, lang: str | None = None, query: str | None = None,
        thread_id: int | None = None, light: bool = False,
    ) -> str:
        """Persona (direct) + catalog + focused card + RAG-retrieved chunks for `query`.
        Chunks are added only when an llm is available and a query is given.

        light=True (follow-up nudges) retrieves fewer chunks — a re-engagement message
        leans on the focus card + persona, not broad KB recall. Either way the assembled
        context is capped at _CTX_CHAR_BUDGET by dropping the LOWEST-RANKED chunks first
        (never persona/focus/catalog): past ~30k chars the cheap JSON-mode providers stop
        returning valid JSON at all, so an oversized context doesn't buy recall — it buys
        empty responses and broker retries."""
        resolved_lang = await self._lang(lang)
        blocks = [_persona_block(await self._persona_text(), resolved_lang)]
        focused = await self._focused(product_slug)
        if focused is not None:
            blocks.append(_focus_block(focused, resolved_lang))
        blocks.append(_catalog_block(await self.products.active(), resolved_lang))
        if self.llm is not None and query:
            try:
                chunks = await RagService(self.session, self.branch_id, self.llm).retrieve(
                    query, k=_FOLLOWUP_RAG_K if light else _TOP_K,
                    thread_id=thread_id, exclude_slug=product_slug if focused else None)
            except Exception:
                # A transient broker embed failure must degrade to persona+catalog, NOT abort
                # the whole reply — otherwise one dead embed endpoint knocks out every thread's
                # reply that tick. RAG chunks are additive; losing them is a soft downgrade.
                logger.warning("RAG retrieve failed branch=%d — replying without chunks",
                               self.branch_id, exc_info=True)
            else:
                base_len = sum(len(b) + 2 for b in blocks if b)
                kept = _fit_chunks(chunks, _CTX_CHAR_BUDGET - base_len)
                if len(kept) < len(chunks):
                    logger.info(
                        "knowledge_context branch=%d: trimmed RAG %d→%d chunks to fit the "
                        "%d-char budget", self.branch_id, len(chunks), len(kept),
                        _CTX_CHAR_BUDGET)
                blocks.append(_rag_block(kept))
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


def _fit_chunks(chunks: list[tuple[str, str]], budget: int) -> list[tuple[str, str]]:
    """The highest-ranked prefix of `chunks` whose rendered size fits `budget` chars.
    retrieve() returns them best-first, so dropping from the tail sheds the least-relevant
    material first. A non-positive budget (persona+focus already past the cap) keeps zero
    chunks — the focus card and persona still carry the product facts."""
    kept: list[tuple[str, str]] = []
    used = 0
    for title, text in chunks:
        used += len(title) + len(text) + 12  # the "--- … ---\n" framing per chunk
        if used > budget:
            break
        kept.append((title, text))
    return kept


def _rag_block(chunks: list[tuple[str, str]]) -> str:
    if not chunks:
        return ""
    parts = [f"--- {title} ---\n{text}" for title, text in chunks]
    return "[relevant knowledge]\n" + "\n\n".join(parts)
