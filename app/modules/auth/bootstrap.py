"""One-time user bootstrap — seeds platform users from Stepan-1 admins.

Run inside the API container (idempotent — safe to re-run):
  docker exec -e PYTHONPATH=/app stepan2-api python -m app.modules.auth.bootstrap

Mapping from Stepan-1 `admins` table (snapshotted 2026-06-28):
  admin  → BRANCH_ADMIN  for Indonesia branch (id=1)
  viewer → SUPER_ADMIN   (platform-wide; only role with cross-branch access)
"""
from __future__ import annotations

import asyncio
import logging

from sqlmodel import select

from app.adapters.db.models import Branch, Membership, User
from app.adapters.db.session import session_scope
from app.domain.enums import Role

log = logging.getLogger(__name__)

# Stepan-1 `admin` role → BRANCH_ADMIN for Indonesia
_INDONESIA_ADMINS: list[tuple[int, str]] = [
    (6254765060, "Citra"),
    (7630115388, "Excel"),
    (7844258246, "Lisa"),
    (1084508593, "Maya"),
]

# Stepan-1 `viewer` role (HQ) → SUPER_ADMIN platform-wide
# No platform-viewer role exists yet; SUPER_ADMIN is the only cross-branch option.
_HQ_SUPER_ADMINS: list[tuple[int, str]] = [
    (382669163,  "@mastermiks Алексей"),
    (6376632328, "Александр CRM"),
    (192365781,  "Виктор CRM Team Lead"),
    (457022581,  "Шаболдас"),
    (169510539,  "Дима"),  # platform owner
]


async def _upsert_user(s, tg_id: int, name: str) -> User:
    user = (
        await s.exec(select(User).where(User.telegram_id == tg_id))
    ).scalar_one_or_none()
    if not user:
        user = User(telegram_id=tg_id, name=name)
        s.add(user)
        await s.flush()
        log.info("  created user %-30s tg=%d", name, tg_id)
    return user


async def _ensure_membership(
    s, user_id: int, branch_id: int | None, role: Role,
) -> None:
    exists = (
        await s.exec(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.branch_id == branch_id,
            )
        )
    ).scalar_one_or_none()
    if exists:
        return
    s.add(Membership(user_id=user_id, branch_id=branch_id, role=role))
    await s.flush()
    log.info("  membership user=%d branch=%s role=%s", user_id, branch_id, role)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log.info("=== Stepan-2 user bootstrap ===")

    async with session_scope() as s:
        indonesia = (
            await s.exec(select(Branch).where(Branch.name == "Indonesia"))
        ).scalar_one_or_none()
        if not indonesia:
            log.error("Indonesia branch not found — run seed first (see docs/deploy.md)")
            return

        log.info("Indonesia branch id=%d", indonesia.id)

        log.info("--- Indonesia sales staff (BRANCH_ADMIN) ---")
        for tg_id, name in _INDONESIA_ADMINS:
            user = await _upsert_user(s, tg_id, name)
            await _ensure_membership(s, user.id, indonesia.id, Role.BRANCH_ADMIN)

        log.info("--- HQ / platform staff (SUPER_ADMIN) ---")
        for tg_id, name in _HQ_SUPER_ADMINS:
            user = await _upsert_user(s, tg_id, name)
            await _ensure_membership(s, user.id, None, Role.SUPER_ADMIN)

    log.info("=== Done ===")


if __name__ == "__main__":
    asyncio.run(main())
