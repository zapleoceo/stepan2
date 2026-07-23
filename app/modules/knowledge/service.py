"""Knowledge service — assembles the branch's persona + facts + product cards for the prompt.

No retrieval. The KB was restructured to FACTS-ONLY (the tactic playbooks moved into the reply
prompt), so the whole fact surface — persona identity, the policy/market facts, the FULL focus
card, and a one-line facts summary of every other product — fits the char budget and is sent
on EVERY turn. That removes RAG's failure mode (a retrieval miss letting the model invent a
fact the right card would have grounded) and the reindex machinery entirely. No branch_id
filtering lives here; all reads go through the BranchScoped repos."""
from __future__ import annotations

import logging
import re

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Product
from app.config import settings
from app.ports.llm import LLMPort

from .repository import KnowledgeRepo, ProductRepo
from .sections import split_sections

logger = logging.getLogger(__name__)

PERSONA_SLUG = "persona"
# Persona identity is injected directly, persona_core first when present.
_PERSONA_ORDER = ("persona_core", "persona")
_PERSONA_SLUGS = frozenset(_PERSONA_ORDER)
# Facts docs injected on EVERY turn — the single source of truth for policy and market facts.
# facts_policy carries payment/discounts/certificates/referral/student rules + the cross-product
# NEVER-list; facts_market carries the institution facts, competitor contrasts, platform (Teams)
# and the success cases. Everything the model may need to state is here or on a product card.
# The trailing legacy slugs keep a branch that hasn't been migrated to facts_* yet working (and
# cover the migration window where both exist); whichever slugs are present load, the rest are
# skipped. Loading a slug that isn't there is a no-op, so there is no double-cost in steady state.
_ALWAYS_DOC_SLUGS = ("facts_policy", "facts_market", "payment_policy", "policy_prohibitions")
# Hard ceiling on the assembled context (chars) — the KB is authored to fit well under this;
# the cap is only a defensive backstop (past ~30k chars the cheap JSON-mode providers stop
# returning valid JSON at all).
_CTX_CHAR_BUDGET = settings().knowledge_context_char_budget

# The one-line headline every restructured card carries, shown for non-focus products so a
# cross-product question is answerable without dumping all 15 full cards.
_QUICK_FACTS_RE = re.compile(r"(?im)^\s*QUICK FACTS:\s*(.+)$")

# Event products are the UNIVERSAL low-friction step ('come see it live first' / free visit)
# offered in almost every objection handling, so their FULL cards ride in EVERY context — a
# terse catalog line isn't enough to ground a real offer, and the model offering them from
# memory got the critic to false-reject ('Demo Event not in KB') and cascade to a hand-off.
_ALWAYS_PRODUCT_SLUGS = ("vibe_coding_demo_event",)  # Open House retired 2026-07-21

# NOT in _ALWAYS_DOC_SLUGS on purpose — this doc never loads whole. Its `## price`,
# `## time`, ... sections are argument banks per objection category (dossier.
# OBJECTION_CATEGORIES); objection_snippets() below pulls out only the categories this
# lead actually raised, via the same split_sections the KB editor already uses.
OBJECTION_PLAYBOOK_SLUG = "objection_playbook"

# facts_market IS in _ALWAYS_DOC_SLUGS (via facts_policy/facts_market pair) but two of its
# sections are evidentiary support, not facts a reply needs by default: the competitor
# comparison table only earns its ~1.3k chars when the lead has raised a TRUST objection
# ("kenapa pilih IT STEP"), and the income ranges + success-case proof only earn theirs when
# a JOB_OUTCOME objection is open ("emang bisa dapet kerja/cuan"). Both used to ride in
# EVERY turn regardless of relevance — 2026-07-23 measurement showed this pair alone was
# ~2.5k of the ~10.2k always-loaded facts pair. Matched by heading PREFIX (not full text)
# so a copy-edit to the parenthetical doesn't silently stop the gate from matching.
_FACTS_MARKET_SLUG = "facts_market"
_GATED_MARKET_SECTIONS: tuple[tuple[str, str], ...] = (
    ("Сравнение с конкурентами", "trust"),
    ("Доход", "job_outcome"),
    ("Успешные кейсы", "job_outcome"),
)


class KnowledgeService:
    """Knowledge access for one branch — the LLM prompt's context source. `llm` is accepted for
    call-site compatibility (retrieval no longer needs it) and unused here."""

    def __init__(
        self, session: AsyncSession, branch_id: int, llm: LLMPort | None = None
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.llm = llm
        self.docs = KnowledgeRepo(session, branch_id)
        self.products = ProductRepo(session, branch_id)

    async def persona_block(self) -> str:
        """Every knowledge doc concatenated under [slug] headers (persona first). Retained for
        tooling/inspection; the prompt uses knowledge_context, not this full dump."""
        docs = await self.docs.all()
        docs.sort(key=lambda d: (d.slug not in _PERSONA_SLUGS, d.slug))
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
        lead_type: str | None = None, has_open_objection: bool = False,
    ) -> str:
        """Persona + policy/market facts + the FULL focus card + a compact facts catalog of the
        other products. Deterministic and complete every turn — no retrieval. `query`/`thread_id`
        are accepted for call-site compatibility and ignored; `light` is unused (kept so the
        follow-up caller's signature is unchanged).

        `lead_type`/`has_open_objection` gate the low-friction EVENT card (architecture review
        2026-07-22): its full ~1.5-2.5k-char body used to ride in EVERY turn regardless of
        relevance. The event is the answer to price-sensitivity/objections/postponing and is
        already named (without its full card) in the base contract, so a lead deep in an
        unrelated product's enrolment discussion doesn't need its full body every turn — only
        when it's plausibly the next move: price-sensitive/no-budget/cold, an open objection,
        or the focus product itself is the event's own course (vibe_coding)."""
        resolved_lang = await self._lang(lang)
        blocks = [_persona_block(await self._persona_text(), resolved_lang)]
        focused = await self._focused(product_slug)
        if focused is not None:
            blocks.append(_focus_block(focused, resolved_lang))
        event_relevant = (
            has_open_objection or lead_type in ("cold", "no_budget")
            or product_slug in ("vibe_coding", *_ALWAYS_PRODUCT_SLUGS))
        events = await self._always_products_block(exclude=product_slug) \
            if event_relevant else ""
        if events:
            blocks.append(events)
        always = await self._always_docs_block()
        if always:
            blocks.append(always)
        # The other-products catalog (~6-7k chars: one anchor line per course) exists so the
        # model knows OTHER products exist while the lead is still deciding — cross-reference,
        # never for presenting (the FULL card loads separately once a product becomes focus).
        # Once a focus IS set, most of that need is gone: the lead settled on ONE product, and
        # every turn from here on was paying for anchors it never used. 2026-07-23 measurement:
        # this was the single largest untouched chunk of the prompt, bigger than facts_policy.
        # The event stays reachable at its cheap anchor even while focused — its "never
        # disappear outright" rule (below) predates this change and still applies.
        catalog_exclude = {product_slug, *(_ALWAYS_PRODUCT_SLUGS if event_relevant else ())}
        catalog_pool = await self.products.active() if product_slug is None else \
            [p for p in await self.products.active() if p.slug in _ALWAYS_PRODUCT_SLUGS]
        catalog = _catalog_block(catalog_pool, resolved_lang, exclude=catalog_exclude)
        if catalog:
            blocks.append(catalog)
        text = "\n\n".join(b for b in blocks if b)
        if len(text) > _CTX_CHAR_BUDGET:
            logger.warning("knowledge_context branch=%d assembled %d chars > %d budget — the KB "
                           "has grown past the fits-in-context assumption; consider trimming",
                           self.branch_id, len(text), _CTX_CHAR_BUDGET)
            text = text[:_CTX_CHAR_BUDGET]
        return text

    async def _always_docs_block(self) -> str:
        parts = []
        for slug in _ALWAYS_DOC_SLUGS:
            doc = await self.docs.by_slug(slug)
            if doc is None or not doc.content.strip():
                continue
            content = doc.content.strip()
            if slug == _FACTS_MARKET_SLUG:
                content = _strip_gated_sections(content)
            parts.append(f"[{slug}]\n{content}")
        return "\n\n".join(parts)

    async def objection_snippets(self, categories: frozenset[str]) -> str:
        """Argument bank entries for only the objection categories this lead actually raised
        — never the whole playbook. Empty categories or a missing doc return ''."""
        if not categories:
            return ""
        doc = await self.docs.by_slug(OBJECTION_PLAYBOOK_SLUG)
        if doc is None or not doc.content.strip():
            return ""
        matched = [f"[objection:{heading}]\n{body}"
                  for heading, body in split_sections(doc.content) if heading in categories]
        return "\n\n".join(matched)

    async def market_snippets(self, categories: frozenset[str]) -> str:
        """The facts_market sections deferred out of _always_docs_block (see
        _GATED_MARKET_SECTIONS) — only the ones matching a category this lead actually raised.
        Same structural-gate pattern as objection_snippets: no model self-selection."""
        if not categories:
            return ""
        doc = await self.docs.by_slug(_FACTS_MARKET_SLUG)
        if doc is None or not doc.content.strip():
            return ""
        wanted = {cat for _, cat in _GATED_MARKET_SECTIONS if cat in categories}
        if not wanted:
            return ""
        matched = [f"[facts_market:{heading}]\n{body}"
                  for heading, body in split_sections(doc.content)
                  if _gated_category(heading) in wanted]
        return "\n\n".join(matched)

    async def _always_products_block(self, exclude: str | None) -> str:
        """Full cards for the universal low-friction event products, skipping the focus one."""
        parts = []
        for slug in _ALWAYS_PRODUCT_SLUGS:
            if slug == exclude:
                continue
            p = await self.products.by_slug(slug)
            if p is not None and (p.content or "").strip():
                parts.append(f"[event {p.slug}]\n{p.title}\n{p.content.strip()}")
        return "\n\n".join(parts)

    async def _focused(self, product_slug: str | None) -> Product | None:
        if product_slug is None:
            return None
        return await self.products.by_slug(product_slug)


def _gated_category(heading: str) -> str:
    """The objection category a facts_market heading is deferred behind, or '' if it's core
    (always loaded) — prefix match so a wording tweak to the parenthetical doesn't break it."""
    for prefix, category in _GATED_MARKET_SECTIONS:
        if heading.startswith(prefix):
            return category
    return ""


def _strip_gated_sections(content: str) -> str:
    """facts_market with the gated sections (competitor comparison, income/success-case proof)
    removed — those load separately via market_snippets() only when the matching objection
    category is open. Preamble and every other section (institution facts, format/platform)
    stay, since they're needed on effectively every turn."""
    core = [(h, b) for h, b in split_sections(content) if not _gated_category(h)]
    parts = [f"## {h}\n{b}" if h else b for h, b in core]
    return "\n\n".join(parts)


def _persona_block(content: str, lang: str) -> str:
    if not content:
        return ""
    return f"[persona lang={lang}]\n{content}"


def _focus_block(product: Product, lang: str) -> str:
    """The FULL focus card — the restructured cards are compact (~2.5k), so the whole card is
    sent when the lead is on this product; no section trimming needed anymore."""
    header = f"[focus product={product.slug} lang={lang}]"
    return f"{header}\n{product.title}\n{(product.content or '').strip()}".rstrip()


def _quick_facts(product: Product) -> str:
    """The card's one-line QUICK FACTS headline for the catalog; falls back to the title."""
    m = _QUICK_FACTS_RE.search(product.content or "")
    return f"{product.title} — {m.group(1).strip()}" if m else product.title


def _catalog_anchor(product: Product) -> str:
    """ONE-LINE anchor for the OTHER-products catalog: just duration + price, not the full
    QUICK FACTS headline (format/DP/outcome too). Architecture review 2026-07-22: this catalog
    exists so the model KNOWS a product exists and roughly what it costs/how long — for cross-
    reference and product-switching, never for presenting (that needs the FULL card, which
    loads separately once the lead actually focuses on it). Every other detail is duplicated
    work most turns never use.

    Every QUICK FACTS line's FIRST segment is duration (no consistent label word survives
    translation — Bahasa said 'durasi 37 sesi', Russian just says '37 сессий'), and exactly one
    segment starts with a price label ('цена' in the live Russian KB, 'harga' in Bahasa —
    kept for older/test content) — matched by keyword since its position varies. Falls back to
    the full quick_facts line if a card has no price segment, so no product silently loses its
    anchor."""
    m = _QUICK_FACTS_RE.search(product.content or "")
    if not m:
        return product.title
    segments = [s.strip() for s in m.group(1).split("|")]
    if not segments:
        return _quick_facts(product)
    price = next((s for s in segments if s.lower().startswith(("цена", "harga"))), None)
    if price is None:
        return _quick_facts(product)
    anchor = [segments[0], price] if segments[0] != price else [price]
    return f"{product.title} — {' · '.join(anchor)}"


def _catalog_block(products: list[Product], lang: str,
                   exclude: str | set[str] | None = None) -> str:
    ex = {exclude} if isinstance(exclude, str) else set(exclude or ())
    lines = [f"- {p.slug}: {_catalog_anchor(p)}" for p in products if p.slug not in ex]
    if not lines:
        return ""
    return (f"[catalog lang={lang}] (ringkasan produk lain — durasi & harga saja; kalau lead "
            "fokus ke salah satu, kartu LENGKAPNYA akan tampil terpisah)\n" + "\n".join(lines))
