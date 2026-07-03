"""User bootstrap — seeds/reconciles platform users from the Stepan-1 admin list.

Run inside the API container (idempotent + self-correcting — safe to re-run):
  docker exec -e PYTHONPATH=/app stepan2-api python -m app.modules.auth.bootstrap

Everyone except the owner is scoped to the Indonesia branch for now (more branches can be
granted later). Mapping from Stepan-1 `admins` (2026-07-04): admin → BRANCH_ADMIN,
viewer → BRANCH_VIEWER, both on Indonesia; the owner is the only SUPER_ADMIN.
Reconcile ensures EXACTLY one membership per user (no duplicates, fixes stale roles)."""
from __future__ import annotations

import asyncio
import logging

from sqlmodel import select

from app.adapters.db.models import Branch, Membership, User
from app.adapters.db.session import session_scope
from app.domain.enums import Role

log = logging.getLogger(__name__)

_OWNER: tuple[int, str] = (169510539, "Дима")  # platform owner → SUPER_ADMIN

# All Indonesia staff: (telegram_id, name, role) — scoped to the Indonesia branch.
_INDONESIA_STAFF: list[tuple[int, str, Role]] = [
    (6254765060, "Citra", Role.BRANCH_ADMIN),
    (7630115388, "Excel", Role.BRANCH_ADMIN),
    (7844258246, "Lisa", Role.BRANCH_ADMIN),
    (1084508593, "Maya", Role.BRANCH_ADMIN),
    (382669163, "@mastermiks Алексей", Role.BRANCH_VIEWER),
    (6376632328, "Александр CRM", Role.BRANCH_VIEWER),
    (192365781, "Виктор CRM Team Lead", Role.BRANCH_VIEWER),
    (457022581, "Шаболдас", Role.BRANCH_VIEWER),
]


async def _upsert_user(s, tg_id: int, name: str) -> User:
    user = (await s.exec(select(User).where(User.telegram_id == tg_id))).one_or_none()
    if not user:
        user = User(telegram_id=tg_id, name=name)
        s.add(user)
        await s.flush()
        log.info("  created user %-24s tg=%d", name, tg_id)
    return user


async def _set_only_membership(s, user_id: int, branch_id: int | None, role: Role) -> None:
    """Make the user's memberships be EXACTLY {(branch_id, role)} — drop the rest."""
    rows = (await s.exec(select(Membership).where(Membership.user_id == user_id))).all()
    keep = None
    for m in rows:
        if keep is None and m.branch_id == branch_id and m.role == role:
            keep = m
        else:
            await s.delete(m)  # duplicate or stale (e.g. an old super_admin grant)
    if keep is None:
        s.add(Membership(user_id=user_id, branch_id=branch_id, role=role))
    await s.flush()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log.info("=== Stepan-2 user bootstrap ===")

    async with session_scope() as s:
        indonesia = (
            await s.exec(select(Branch).where(Branch.name == "Indonesia"))
        ).one_or_none()
        if not indonesia:
            log.error("Indonesia branch not found — run seed first (see docs/deploy.md)")
            return
        log.info("Indonesia branch id=%d", indonesia.id)

        owner = await _upsert_user(s, *_OWNER)
        await _set_only_membership(s, owner.id, None, Role.SUPER_ADMIN)
        log.info("owner %s → SUPER_ADMIN", _OWNER[1])

        for tg_id, name, role in _INDONESIA_STAFF:
            user = await _upsert_user(s, tg_id, name)
            await _set_only_membership(s, user.id, indonesia.id, role)
            log.info("  %-24s → %s @ Indonesia", name, role)

    log.info("=== Done ===")


if __name__ == "__main__":
    asyncio.run(main())
