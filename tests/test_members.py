"""Member management routes: super_admin gate, CRUD flow, self-edit lockout guard."""
from __future__ import annotations

import contextlib

from starlette.requests import Request

from app.adapters.db.models import Branch, Membership, User
from app.api import _routes_members as rm
from app.domain.enums import Role


def _req(allowed=None, uid: int | None = None) -> Request:
    scope = {"type": "http", "headers": []}
    req = Request(scope)
    req.state.allowed_branch_ids = allowed
    req.state.user = {"uid": uid} if uid is not None else {}
    return req


def _patch_scope(monkeypatch, db_session) -> None:
    @contextlib.asynccontextmanager
    async def fake_scope():
        yield db_session

    monkeypatch.setattr(rm, "session_scope", fake_scope)


async def test_members_create_upserts_user_and_membership(db_session, monkeypatch) -> None:
    _patch_scope(monkeypatch, db_session)
    b = Branch(name="Jakarta", lang="id")
    db_session.add(b)
    await db_session.flush()

    html = (await rm.members_create(
        _req(allowed=None), telegram_id=555, name="Alice",
        role="branch_admin", branch_id=str(b.id),
    )).body.decode()
    assert "Alice" in html
    assert "tg:555" in html

    user = await rm.UserRepo(db_session).get_by_telegram_id(555)
    assert user is not None and user.name == "Alice"


async def test_members_create_reuses_existing_user(db_session, monkeypatch) -> None:
    _patch_scope(monkeypatch, db_session)
    user = User(telegram_id=777, name="Bob")
    db_session.add(user)
    await db_session.flush()

    await rm.members_create(
        _req(allowed=None), telegram_id=777, name="", role="branch_viewer", branch_id="",
    )
    memberships = await rm.MembershipRepo(db_session).memberships_for_user(user.id)
    assert len(memberships) == 1
    assert memberships[0].branch_id is None  # platform-wide (empty branch_id → None)


async def test_members_create_supports_mixed_roles_across_branches(
    db_session, monkeypatch,
) -> None:
    """A user can be branch_admin of one branch and branch_viewer of another — separate
    memberships, distinct roles."""
    _patch_scope(monkeypatch, db_session)
    b1 = Branch(name="B1", lang="id")
    b2 = Branch(name="B2", lang="id")
    db_session.add_all([b1, b2])
    await db_session.flush()

    await rm.members_create(
        _req(allowed=None), telegram_id=900, name="Mix",
        role="branch_admin", branch_id=str(b1.id))
    user = await rm.UserRepo(db_session).get_by_telegram_id(900)
    await rm.members_create(
        _req(allowed=None), telegram_id=900, name="",
        role="branch_viewer", branch_id=str(b2.id))

    ms = {m.branch_id: m.role for m in
          await rm.MembershipRepo(db_session).memberships_for_user(user.id)}
    assert ms[b1.id] == Role.BRANCH_ADMIN
    assert ms[b2.id] == Role.BRANCH_VIEWER


async def test_members_create_upserts_one_role_per_branch(db_session, monkeypatch) -> None:
    """Re-adding a user to the SAME branch re-assigns the role instead of creating a
    second, conflicting membership row."""
    _patch_scope(monkeypatch, db_session)
    b = Branch(name="B", lang="id")
    db_session.add(b)
    await db_session.flush()

    await rm.members_create(
        _req(allowed=None), telegram_id=901, name="U",
        role="branch_viewer", branch_id=str(b.id))
    await rm.members_create(
        _req(allowed=None), telegram_id=901, name="",
        role="branch_admin", branch_id=str(b.id))  # same branch again → upsert

    user = await rm.UserRepo(db_session).get_by_telegram_id(901)
    ms = await rm.MembershipRepo(db_session).memberships_for_user(user.id)
    assert len(ms) == 1                       # not duplicated
    assert ms[0].role == Role.BRANCH_ADMIN    # role re-assigned


async def test_members_set_role_updates_and_rerenders(db_session, monkeypatch) -> None:
    _patch_scope(monkeypatch, db_session)
    user = User(telegram_id=1, name="Carol")
    db_session.add(user)
    await db_session.flush()
    m = Membership(user_id=user.id, branch_id=None, role=Role.BRANCH_VIEWER)
    db_session.add(m)
    await db_session.flush()

    resp = await rm.members_set_role(m.id, _req(allowed=None, uid=999), role="branch_admin")
    assert resp.status_code == 200
    refreshed = await rm.MembershipRepo(db_session).get(m.id)
    assert refreshed.role == Role.BRANCH_ADMIN


async def test_members_set_role_rejects_invalid_role(db_session, monkeypatch) -> None:
    _patch_scope(monkeypatch, db_session)
    resp = await rm.members_set_role(1, _req(allowed=None), role="dictator")
    assert resp.status_code == 400


async def test_members_cannot_edit_or_delete_own_membership(db_session, monkeypatch) -> None:
    """The simplest guarantee against a super_admin locking themselves out: you may never
    change your own role/branch or remove your own membership through this UI."""
    _patch_scope(monkeypatch, db_session)
    user = User(telegram_id=42, name="Me")
    db_session.add(user)
    await db_session.flush()
    m = Membership(user_id=user.id, branch_id=None, role=Role.SUPER_ADMIN)
    db_session.add(m)
    await db_session.flush()

    me = _req(allowed=None, uid=user.id)
    role_resp = await rm.members_set_role(m.id, me, role="branch_viewer")
    assert role_resp.status_code == 400
    branch_resp = await rm.members_set_branch(m.id, me, branch_id="1")
    assert branch_resp.status_code == 400
    delete_resp = await rm.members_delete(m.id, me)
    assert delete_resp.status_code == 400

    unchanged = await rm.MembershipRepo(db_session).get(m.id)
    assert unchanged is not None and unchanged.role == Role.SUPER_ADMIN


async def test_members_delete_removes_others(db_session, monkeypatch) -> None:
    _patch_scope(monkeypatch, db_session)
    user = User(telegram_id=43, name="Other")
    db_session.add(user)
    await db_session.flush()
    m = Membership(user_id=user.id, branch_id=None, role=Role.BRANCH_VIEWER)
    db_session.add(m)
    await db_session.flush()

    resp = await rm.members_delete(m.id, _req(allowed=None, uid=999))
    assert resp.status_code == 200
    assert await rm.MembershipRepo(db_session).get(m.id) is None


def test_members_router_requires_super_admin() -> None:
    """The router-level Depends(require_super_admin) is the actual enforcement — this
    just confirms it's wired onto the router, not left off by accident."""
    from app.admin._branch import require_super_admin
    deps = [d.dependency for d in rm.router.dependencies]
    assert require_super_admin in deps
