"""Pure RBAC policy — no I/O, so it's trivially testable and swappable for a Casbin
adapter later. The grant table is the single auditable source of who-may-do-what."""
from __future__ import annotations

from enum import StrEnum

from app.domain.enums import Role


class Action(StrEnum):
    READ = "read"
    WRITE = "write"
    MANAGE_BRANCH = "manage_branch"
    CREATE_BRANCH = "create_branch"


# Per-role grants. super_admin holds every action (incl. platform-level CREATE_BRANCH);
# branch_admin manages its own branch; branch_viewer is read-only.
_GRANTS: dict[Role, frozenset[Action]] = {
    Role.SUPER_ADMIN: frozenset(Action),
    Role.BRANCH_ADMIN: frozenset({Action.READ, Action.WRITE, Action.MANAGE_BRANCH}),
    Role.BRANCH_VIEWER: frozenset({Action.READ}),
}


def can(role: Role, action: Action) -> bool:
    """True iff the role's grant set contains the action — deny by default."""
    return action in _GRANTS.get(role, frozenset())
