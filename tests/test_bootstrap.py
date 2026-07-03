"""User bootstrap reconcile — exactly one membership per user, fixes stale roles, no dupes."""
from __future__ import annotations

from sqlmodel import select

from app.adapters.db.models import Branch, Membership, User
from app.domain.enums import Role
from app.modules.auth.bootstrap import _set_only_membership, _upsert_user


async def _mships(s, user_id: int) -> list[Membership]:
    return list((await s.exec(select(Membership).where(Membership.user_id == user_id))).all())


async def test_reconcile_fixes_stale_and_dedupes(db_session) -> None:
    s = db_session
    b = Branch(name="Indonesia", lang="id")
    s.add(b)
    await s.flush()
    user = await _upsert_user(s, 42, "Viktor")
    # simulate a prior bad state: a stray super_admin + a duplicate
    s.add(Membership(user_id=user.id, branch_id=None, role=Role.SUPER_ADMIN))
    s.add(Membership(user_id=user.id, branch_id=b.id, role=Role.BRANCH_VIEWER))
    s.add(Membership(user_id=user.id, branch_id=b.id, role=Role.BRANCH_VIEWER))
    await s.flush()

    await _set_only_membership(s, user.id, b.id, Role.BRANCH_VIEWER)
    ms = await _mships(s, user.id)
    assert len(ms) == 1
    assert ms[0].branch_id == b.id and ms[0].role == Role.BRANCH_VIEWER  # stale super_admin gone


async def test_reconcile_is_idempotent(db_session) -> None:
    s = db_session
    b = Branch(name="Indonesia", lang="id")
    s.add(b)
    await s.flush()
    user = await _upsert_user(s, 7, "Citra")
    await _set_only_membership(s, user.id, b.id, Role.BRANCH_ADMIN)
    await _set_only_membership(s, user.id, b.id, Role.BRANCH_ADMIN)  # re-run
    ms = await _mships(s, user.id)
    assert len(ms) == 1 and ms[0].role == Role.BRANCH_ADMIN


async def test_upsert_user_is_idempotent(db_session) -> None:
    s = db_session
    a = await _upsert_user(s, 100, "X")
    b = await _upsert_user(s, 100, "X")
    assert a.id == b.id
    assert len((await s.exec(select(User).where(User.telegram_id == 100))).all()) == 1
