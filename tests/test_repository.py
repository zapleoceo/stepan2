"""BranchScoped — единая изоляция: филиал видит только своё, get чужого → None,
add принудительно проставляет branch_id."""
from app.adapters.db.models import Branch, Product
from app.adapters.db.repository import BranchScoped


async def _two_branches(s):
    b1, b2 = Branch(name="ID"), Branch(name="VN")
    s.add(b1)
    s.add(b2)
    await s.flush()
    return b1.id, b2.id


async def test_scoped_list_isolates(db_session):
    s = db_session
    id1, id2 = await _two_branches(s)
    r1, r2 = BranchScoped(s, id1, model=Product), BranchScoped(s, id2, model=Product)
    await r1.add(Product(slug="vibe", title="Vibe", branch_id=999))  # branch_id форсится → id1
    await r2.add(Product(slug="data", title="Data"))
    assert [p.slug for p in await r1.list()] == ["vibe"]
    assert [p.slug for p in await r2.list()] == ["data"]


async def test_add_forces_branch_id(db_session):
    s = db_session
    id1, _ = await _two_branches(s)
    r1 = BranchScoped(s, id1, model=Product)
    p = await r1.add(Product(slug="x", title="X", branch_id=12345))
    assert p.branch_id == id1   # переданный чужой branch_id перезаписан


async def test_get_cross_branch_returns_none(db_session):
    s = db_session
    id1, id2 = await _two_branches(s)
    r1, r2 = BranchScoped(s, id1, model=Product), BranchScoped(s, id2, model=Product)
    p = await r1.add(Product(slug="x", title="X"))
    assert await r2.get(p.id) is None   # чужой филиал не достать по id
    assert (await r1.get(p.id)).slug == "x"
