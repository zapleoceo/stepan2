"""User bootstrap — seeds/reconciles platform users from env config (no PII in VCS).

Run inside the API container (idempotent + self-correcting — safe to re-run):
  docker exec -e PYTHONPATH=/app stepan2-api python -m app.modules.auth.bootstrap

The owner (SUPER_ADMIN) comes from STEPAN2_BOOTSTRAP_SUPER_ADMIN (a Telegram id).
Branch staff come from STEPAN2_BOOTSTRAP_STAFF_JSON — a JSON list of
{"tg": <id>, "name": "...", "role": "branch_admin|branch_viewer"} objects, all scoped to
the Indonesia branch. Real ids/names used to be hardcoded here; they now live only in the
server's env so employee contact ids aren't committed to git.
Reconcile ensures EXACTLY one membership per user (no duplicates, fixes stale roles)."""
from __future__ import annotations

import asyncio
import json
import logging

from sqlmodel import select

from app.adapters.db.models import Branch, Membership, User
from app.adapters.db.session import session_scope
from app.config import settings
from app.domain.enums import Role

log = logging.getLogger(__name__)


def _load_staff() -> list[tuple[int, str, Role]]:
    """Parse STEPAN2_BOOTSTRAP_STAFF_JSON → [(tg_id, name, role)]. Empty/invalid → []."""
    raw = settings().bootstrap_staff_json.strip()
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        log.error("STEPAN2_BOOTSTRAP_STAFF_JSON is not valid JSON — skipping staff")
        return []
    out: list[tuple[int, str, Role]] = []
    for it in items:
        try:
            role = Role(it.get("role", "branch_viewer"))
            out.append((int(it["tg"]), str(it.get("name", "")), role))
        except (KeyError, ValueError, TypeError):
            log.warning("skip malformed staff entry: %r", it)
    return out


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

        owner_id = settings().bootstrap_super_admin
        if owner_id:
            owner = await _upsert_user(s, owner_id, "owner")
            await _set_only_membership(s, owner.id, None, Role.SUPER_ADMIN)
            log.info("owner tg=%d → SUPER_ADMIN", owner_id)
        else:
            log.warning("STEPAN2_BOOTSTRAP_SUPER_ADMIN not set — no owner seeded")

        for tg_id, name, role in _load_staff():
            user = await _upsert_user(s, tg_id, name)
            await _set_only_membership(s, user.id, indonesia.id, role)
            log.info("  %-24s → %s @ Indonesia", name or tg_id, role)

    log.info("=== Done ===")


if __name__ == "__main__":
    asyncio.run(main())
