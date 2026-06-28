"""Миграция знаний Индонезии из Степан-1: seed создаёт филиал + грузит KB изолированно."""
from app.adapters.db.models import Branch
from app.modules.knowledge.seed import seed_indonesia
from app.modules.knowledge.service import KnowledgeService


async def test_seed_indonesia_loads_kb(db_session):
    s = db_session
    branch_id = await seed_indonesia(s)
    branch = await s.get(Branch, branch_id)
    assert branch.name == "Indonesia" and branch.lang == "id"

    svc = KnowledgeService(s, branch_id)
    assert (await svc.persona_block())                       # persona загружена
    assert await svc.product_card("vibe_coding") is not None  # продукт из Степан-1
    ctx = await svc.knowledge_context("vibe_coding")
    assert "vibe" in ctx.lower() or len(ctx) > 100            # контекст собрался


async def test_seed_indonesia_isolated(db_session):
    s = db_session
    id1 = await seed_indonesia(s)
    # второй филиал без знаний — не видит знания Индонезии
    other = Branch(name="Vietnam", lang="vi")
    s.add(other)
    await s.flush()
    svc_other = KnowledgeService(s, other.id)
    assert await svc_other.product_card("vibe_coding") is None  # изоляция
    assert id1 != other.id
