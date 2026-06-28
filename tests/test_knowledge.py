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
    assert await svc_a.persona_block() == "persona-A"
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
