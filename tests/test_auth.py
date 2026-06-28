"""Auth/RBAC: super_admin spans all branches & can create branches; branch_admin
writes only in its own branch; branch_viewer reads but can't write; unknown user → no
access. rbac.can is exercised both directly (pure) and through AuthService (with DB)."""
from app.adapters.db.models import Branch, Membership, User
from app.domain.enums import Role
from app.modules.auth import Action, AuthService
from app.modules.auth.rbac import can


async def _branch(s, name: str) -> int:
    b = Branch(name=name)
    s.add(b)
    await s.flush()
    return b.id


async def _user_with(s, telegram_id: int, role: Role, branch_id: int | None) -> int:
    u = User(telegram_id=telegram_id)
    s.add(u)
    await s.flush()
    s.add(Membership(user_id=u.id, branch_id=branch_id, role=role))
    await s.flush()
    return u.id


def test_rbac_table_is_pure():
    assert can(Role.SUPER_ADMIN, Action.CREATE_BRANCH)
    assert can(Role.BRANCH_ADMIN, Action.WRITE)
    assert not can(Role.BRANCH_ADMIN, Action.CREATE_BRANCH)
    assert can(Role.BRANCH_VIEWER, Action.READ)
    assert not can(Role.BRANCH_VIEWER, Action.WRITE)


async def test_super_admin_spans_all_branches(db_session):
    s = db_session
    b1, b2 = await _branch(s, "ID"), await _branch(s, "VN")
    uid = await _user_with(s, 1, Role.SUPER_ADMIN, branch_id=None)
    auth = AuthService(s)
    assert await auth.can_access(uid, b1, Action.CREATE_BRANCH)
    assert await auth.can_access(uid, b2, Action.WRITE)
    assert await auth.can_access(uid, b1, Action.MANAGE_BRANCH)


async def test_branch_admin_confined_to_own_branch(db_session):
    s = db_session
    own, other = await _branch(s, "own"), await _branch(s, "other")
    uid = await _user_with(s, 2, Role.BRANCH_ADMIN, branch_id=own)
    auth = AuthService(s)
    assert await auth.can_access(uid, own, Action.WRITE)
    assert not await auth.can_access(uid, own, Action.CREATE_BRANCH)
    assert not await auth.can_access(uid, other, Action.READ)


async def test_branch_viewer_read_only(db_session):
    s = db_session
    bid = await _branch(s, "ID")
    uid = await _user_with(s, 3, Role.BRANCH_VIEWER, branch_id=bid)
    auth = AuthService(s)
    assert await auth.can_access(uid, bid, Action.READ)
    assert not await auth.can_access(uid, bid, Action.WRITE)


async def test_unknown_user_has_no_access(db_session):
    s = db_session
    bid = await _branch(s, "ID")
    auth = AuthService(s)
    assert await auth.resolve(telegram_id=999) is None
    assert not await auth.can_access(user_id=424242, branch_id=bid, action=Action.READ)


async def test_resolve_maps_telegram_id(db_session):
    s = db_session
    bid = await _branch(s, "ID")
    await _user_with(s, 555, Role.BRANCH_ADMIN, branch_id=bid)
    auth = AuthService(s)
    found = await auth.resolve(telegram_id=555)
    assert found is not None and found.telegram_id == 555
