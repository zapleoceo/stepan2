"""Auth repositories — plain session repos, not branch-scoped: a super_admin's
membership has branch_id=NULL, so isolation here is by user, not by branch."""
from __future__ import annotations

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Membership, User
from app.domain.enums import Role


class UserRepo:
    """Lookup/create platform users by their Telegram identity."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        q = select(User).where(User.telegram_id == telegram_id)
        return (await self.session.exec(q)).first()

    async def create(self, telegram_id: int, name: str | None = None) -> User:
        user = User(telegram_id=telegram_id, name=name)
        self.session.add(user)
        await self.session.flush()
        return user


class MembershipRepo:
    """Read a user's branch memberships and their role within a given branch."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def memberships_for_user(self, user_id: int) -> list[Membership]:
        q = select(Membership).where(Membership.user_id == user_id)
        return list((await self.session.exec(q)).all())

    async def role_in_branch(self, user_id: int, branch_id: int) -> Role | None:
        q = select(Membership).where(
            Membership.user_id == user_id, Membership.branch_id == branch_id
        )
        m = (await self.session.exec(q)).first()
        return m.role if m is not None else None

    async def get(self, membership_id: int) -> Membership | None:
        return await self.session.get(Membership, membership_id)

    async def create(
        self, user_id: int, branch_id: int | None, role: Role,
    ) -> Membership:
        m = Membership(user_id=user_id, branch_id=branch_id, role=role)
        self.session.add(m)
        await self.session.flush()
        return m

    async def upsert(
        self, user_id: int, branch_id: int | None, role: Role,
    ) -> Membership:
        """One role per (user, branch): re-assign the role if a membership already exists,
        else create it. Keeps mixed-role users well-defined — a user has at most one role
        per branch, so `br`/`wr` (read/write sets) never see a duplicate/ambiguous branch."""
        q = select(Membership).where(
            Membership.user_id == user_id, Membership.branch_id == branch_id
        )
        existing = (await self.session.exec(q)).first()
        if existing is not None:
            existing.role = role
            self.session.add(existing)
            await self.session.flush()
            return existing
        return await self.create(user_id, branch_id, role)

    async def update_role(self, membership_id: int, role: Role) -> bool:
        m = await self.get(membership_id)
        if m is None:
            return False
        m.role = role
        self.session.add(m)
        await self.session.flush()
        return True

    async def update_branch(self, membership_id: int, branch_id: int | None) -> bool:
        m = await self.get(membership_id)
        if m is None:
            return False
        m.branch_id = branch_id
        self.session.add(m)
        await self.session.flush()
        return True

    async def delete(self, membership_id: int) -> bool:
        m = await self.get(membership_id)
        if m is None:
            return False
        await self.session.delete(m)
        await self.session.flush()
        return True
