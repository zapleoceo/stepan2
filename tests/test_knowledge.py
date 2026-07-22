"""KnowledgeService — branch isolation, scoped by_slug, focused-card assembly."""
from app.adapters.db.models import Branch, KnowledgeDoc, Product
from app.modules.knowledge import KnowledgeRepo, KnowledgeService, ProductRepo


async def _branch(s, name: str, lang: str) -> int:
    b = Branch(name=name, lang=lang)
    s.add(b)
    await s.flush()
    return b.id


async def _seed(s, branch_id: int, persona: str, products: list[tuple[str, str, str]]) -> None:
    docs = KnowledgeRepo(s, branch_id)
    prods = ProductRepo(s, branch_id)
    await docs.add(KnowledgeDoc(slug="persona", content=persona, branch_id=branch_id))
    for slug, title, content in products:
        await prods.add(Product(slug=slug, title=title, content=content, branch_id=branch_id))


async def test_service_isolates_persona_and_products(db_session):
    s = db_session
    a = await _branch(s, "Jakarta", "id")
    b = await _branch(s, "Hanoi", "vi")
    await _seed(s, a, "persona-A", [("vibe", "Vibe A", "card-A")])
    await _seed(s, b, "persona-B", [("data", "Data B", "card-B")])

    svc_a = KnowledgeService(s, a)
    block_a = await svc_a.persona_block()  # all docs, each under a [slug] header
    assert "persona-A" in block_a and "persona-B" not in block_a
    assert [p.slug for p in await svc_a.products.active()] == ["vibe"]

    ctx_a = await svc_a.knowledge_context(None)
    assert "persona-A" in ctx_a
    assert "vibe" in ctx_a
    assert "persona-B" not in ctx_a
    assert "data" not in ctx_a
    assert "lang=id" in ctx_a  # branch lang, not hardcoded


async def test_by_slug_is_branch_scoped(db_session):
    s = db_session
    a = await _branch(s, "Jakarta", "id")
    b = await _branch(s, "Hanoi", "vi")
    await _seed(s, a, "persona-A", [("vibe", "Vibe A", "card-A")])
    await _seed(s, b, "persona-B", [("data", "Data B", "card-B")])

    repo_b = ProductRepo(s, b)
    assert await repo_b.by_slug("vibe") is None  # branch A's product invisible to B
    assert (await repo_b.by_slug("data")).title == "Data B"

    kdocs_b = KnowledgeRepo(s, b)
    assert (await kdocs_b.by_slug("persona")).content == "persona-B"


async def test_knowledge_context_includes_focused_card(db_session):
    s = db_session
    a = await _branch(s, "Jakarta", "id")
    await _seed(
        s, a, "persona-A",
        [("vibe", "Vibe Coding", "price 1.2M"), ("data", "Data Science", "price 2M")],
    )

    svc = KnowledgeService(s, a)
    ctx = await svc.knowledge_context("vibe")
    assert "focus product=vibe" in ctx
    assert "price 1.2M" in ctx  # focused card body present
    assert await svc.product_card("vibe") == "price 1.2M"
    assert await svc.product_card("missing") is None


async def test_payment_policy_is_always_in_context(db_session):
    """payment_policy (bank requisites + DP flow) must ride in EVERY context, not depend on RAG
    — the bot escalated a payment question the doc answers because RAG didn't surface it (2664)."""
    from app.adapters.db.models import KnowledgeDoc
    from app.modules.knowledge import KnowledgeRepo

    s = db_session
    a = await _branch(s, "Jakarta", "id")
    await _seed(s, a, "persona-A", [("vibe", "Vibe Coding", "price 1.2M")])
    await KnowledgeRepo(s, a).add(KnowledgeDoc(
        slug="payment_policy", branch_id=a,
        content="Bank BCA · No. Rekening 5245550101 · DP 500.000, sisanya sebelum kelas."))

    svc = KnowledgeService(s, a)  # no llm → no RAG at all
    ctx = await svc.knowledge_context("vibe")
    assert "5245550101" in ctx and "payment_policy" in ctx  # requisites present without RAG


async def test_persona_block_empty_when_absent(db_session):
    s = db_session
    a = await _branch(s, "Jakarta", "id")
    svc = KnowledgeService(s, a)
    assert await svc.persona_block() == ""
    assert await svc.knowledge_context(None) == ""  # nothing seeded → empty context


async def test_knowledge_context_lang_override(db_session):
    s = db_session
    a = await _branch(s, "Jakarta", "id")
    await _seed(s, a, "persona-A", [("vibe", "Vibe", "card")])
    svc = KnowledgeService(s, a)
    ctx = await svc.knowledge_context(None, lang="en")
    assert "lang=en" in ctx  # explicit param wins over Branch.lang


async def test_focus_card_is_sent_in_full_no_rag(db_session):
    """No retrieval: the whole focus card (every section) is sent — the restructured cards are
    compact enough to send verbatim, so no section is deferred to a chunk index anymore."""
    s = db_session
    a = await _branch(s, "Jakarta", "id")
    card = (
        "# Vibe Coding\nQUICK FACTS: durasi 4 bulan | harga Rp 13.000.000 | DP Rp 500.000\n"
        "## Kurikulum\nHTML, CSS, JS, Python, Deploy\n## Success cases\nPieter Levels startup AI")
    await _seed(s, a, "persona-A", [("vibe", "Vibe Coding", card)])
    svc = KnowledgeService(s, a)  # no llm at all — no RAG
    ctx = await svc.knowledge_context("vibe")
    assert "focus product=vibe" in ctx
    assert "Kurikulum" in ctx and "Success cases" in ctx  # full card, nothing trimmed away
    assert "Rp 13.000.000" in ctx


async def test_catalog_shows_quick_facts_for_other_products(db_session):
    """A non-focus product is summarised by duration+price ONLY in the catalog (architecture
    review 2026-07-22: format/DP/outcome dropped — the catalog is a cross-reference anchor,
    the FULL card loads separately once the lead actually focuses on that product), so a
    cross-product question is answerable without dumping all 15 full cards."""
    s = db_session
    a = await _branch(s, "Jakarta", "id")
    data_card = ("# Data Analyst\nQUICK FACTS: durasi 9 bulan | harga Rp 15.030.000\n"
                 "## Kurikulum\nSQL, Python, Power BI")
    await _seed(s, a, "persona-A",
                [("vibe", "Vibe Coding", "QUICK FACTS: durasi 4 bulan | harga Rp 13.000.000"),
                 ("data", "Data Analyst", data_card)])
    svc = KnowledgeService(s, a)
    ctx = await svc.knowledge_context("vibe")
    assert "focus product=vibe" in ctx                 # vibe is the full focus card
    assert "- data: Data Analyst — durasi 9 bulan · harga Rp 15.030.000" in ctx  # data summarised
    assert "SQL, Python, Power BI" not in ctx           # the OTHER card's bulk is NOT dumped


async def test_catalog_drops_format_dp_outcome_keeps_duration_and_price(db_session):
    """The anchor keeps ONLY the durasi/harga segments — format, DP and outcome (present in the
    full QUICK FACTS line) are dropped from the OTHER-products catalog to save context; they're
    still fully available once that product becomes the focus card."""
    s = db_session
    a = await _branch(s, "Jakarta", "id")
    smm_card = (
        "# SMM Intensive\nQUICK FACTS: durasi 2 minggu | format hybrid (offline/online) | "
        "harga Rp 1.882.955 | DP Rp 500.000 | hasil Diploma IT STEP\n## Kurikulum\nX")
    await _seed(s, a, "persona-A",
                [("vibe", "Vibe Coding", "QUICK FACTS: durasi 4 bulan | harga Rp 13.000.000"),
                 ("smm", "SMM Intensive", smm_card)])
    svc = KnowledgeService(s, a)
    ctx = await svc.knowledge_context("vibe")
    assert "- smm: SMM Intensive — durasi 2 minggu · harga Rp 1.882.955" in ctx
    assert "format hybrid" not in ctx
    assert "DP Rp 500.000" not in ctx
    assert "Diploma IT STEP" not in ctx


async def test_catalog_falls_back_to_title_without_quick_facts(db_session):
    """A product card lacking a QUICK FACTS: line is summarised by its title alone in the
    catalog (never dropped)."""
    s = db_session
    a = await _branch(s, "Jakarta", "id")
    await _seed(s, a, "persona-A",
                [("vibe", "Vibe Coding", "QUICK FACTS: durasi 4 bulan | harga Rp 13.000.000"),
                 ("data", "Data Analyst", "# Data Analyst\nNo quick-facts headline here.")])
    svc = KnowledgeService(s, a)
    ctx = await svc.knowledge_context("vibe")
    assert "- data: Data Analyst" in ctx        # title fallback present
    assert "- data: Data Analyst —" not in ctx  # no ' — <facts>' suffix when line absent


async def test_event_card_is_full_when_relevant_to_this_turn(db_session):
    """Architecture review 2026-07-22: the Demo Event's full card no longer rides in EVERY
    context unconditionally — only when it's plausibly the next move (focus IS vibe_coding,
    an open objection, or a cold/no_budget lead). Focus=vibe_coding is one such case: its
    FULL card grounds the offer, not just a terse catalog line."""
    s = db_session
    a = await _branch(s, "Jakarta", "id")
    await _seed(s, a, "persona-A", [
        ("vibe_coding", "Vibe Coding", "QUICK FACTS: durasi 4 bulan | harga Rp 13.000.000"),
        ("vibe_coding_demo_event", "Demo Event",
         "QUICK FACTS: durasi 3 jam | harga Rp 100.000\n## Detail\nlihat AI bikin app live")])
    svc = KnowledgeService(s, a)
    ctx = await svc.knowledge_context("vibe_coding")
    assert "[event vibe_coding_demo_event]" in ctx and "lihat AI bikin app live" in ctx
    assert "- vibe_coding_demo_event:" not in ctx  # not duplicated in the compact catalog


async def test_event_card_is_just_a_catalog_anchor_when_not_relevant(db_session):
    """A lead deep in an UNRELATED product's enrolment, no open objection, not cold/no_budget
    — the Demo Event's full ~1.5-2.5k-char body is dead weight most turns. It should shrink to
    its cheap catalog anchor (still knowable, not fully invisible), never disappear outright."""
    s = db_session
    a = await _branch(s, "Jakarta", "id")
    await _seed(s, a, "persona-A", [
        ("cybersecurity", "Cybersecurity", "QUICK FACTS: durasi 8 bulan | harga Rp 13.360.000"),
        ("vibe_coding_demo_event", "Demo Event",
         "QUICK FACTS: durasi 3 jam | harga Rp 100.000\n## Detail\nlihat AI bikin app live")])
    svc = KnowledgeService(s, a)
    ctx = await svc.knowledge_context("cybersecurity")
    assert "[event vibe_coding_demo_event]" not in ctx  # no full card this turn
    assert "lihat AI bikin app live" not in ctx          # detail body genuinely dropped
    assert "- vibe_coding_demo_event: Demo Event — durasi 3 jam · harga Rp 100.000" in ctx


async def test_event_card_returns_to_full_on_an_open_objection(db_session):
    """An open objection makes the low-friction event plausibly the next move again, even on
    an unrelated product's focus card."""
    s = db_session
    a = await _branch(s, "Jakarta", "id")
    await _seed(s, a, "persona-A", [
        ("cybersecurity", "Cybersecurity", "QUICK FACTS: durasi 8 bulan | harga Rp 13.360.000"),
        ("vibe_coding_demo_event", "Demo Event",
         "QUICK FACTS: durasi 3 jam | harga Rp 100.000\n## Detail\nlihat AI bikin app live")])
    svc = KnowledgeService(s, a)
    ctx = await svc.knowledge_context("cybersecurity", has_open_objection=True)
    assert "[event vibe_coding_demo_event]" in ctx and "lihat AI bikin app live" in ctx


async def test_context_stays_within_the_char_budget(db_session):
    """Defensive cap: an over-large KB is truncated to the budget rather than shipped whole
    (cheap JSON-mode providers stop returning JSON past ~30k chars)."""
    from app.modules.knowledge import service as ksvc

    s = db_session
    a = await _branch(s, "Jakarta", "id")
    await _seed(s, a, "persona " * 200, [("vibe", "Vibe", "card " * 2000)])  # ~10k+ focus card
    svc = KnowledgeService(s, a)
    ctx = await svc.knowledge_context("vibe")
    assert len(ctx) <= ksvc._CTX_CHAR_BUDGET
