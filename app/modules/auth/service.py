"""AuthService — resolves users and answers access questions by combining the
membership repo with the pure rbac policy. super_admin (branch_id=NULL) bypasses the
branch check; branch_admin/viewer are confined to their own branch_id."""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import User
from app.domain.enums import Role
from app.modules.auth.rbac import Action, can
from app.modules.auth.repository import MembershipRepo, UserRepo


class AuthService:
    """Single entry point for identity + authorization decisions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepo(session)
        self.memberships = MembershipRepo(session)

    async def resolve(self, telegram_id: int) -> User | None:
        """Map a Telegram id to its platform user, or None if unknown."""
        return await self.users.get_by_telegram_id(telegram_id)

    async def can_access(self, user_id: int, branch_id: int, action: Action) -> bool:
        """True iff the user may perform action on branch_id — super_admin spans all
        branches; scoped roles only count within their own branch_id."""
        memberships = await self.memberships.memberships_for_user(user_id)
        if any(m.role == Role.SUPER_ADMIN for m in memberships):  # == не is: String-storage
            return True
        role = next(
            (m.role for m in memberships if m.branch_id == branch_id), None
        )
        return role is not None and can(role, action)
