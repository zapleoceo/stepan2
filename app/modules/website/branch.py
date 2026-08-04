"""Finding — and, once, creating — the branch that serves the site chat.

Resolved through the CONNECTOR, never by id. A branch id is a deployment accident — whatever
the sequence happened to hand out on the install that ran the seed first — and `if branch_id
== N` in the demo route is exactly the special-casing S6 was asked to remove. The site branch
is "the active branch that owns an active website channel", a property anyone can read off the
channel list.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import AppSetting, Branch, Channel
from app.domain.enums import ChannelKind
from app.modules.promptlib.clone import clone_into_branch, library_item
from app.modules.promptlib.library_seed import ensure_library
from app.modules.promptlib.pipeline import COMPOSER
from app.modules.settings.service import invalidate

from .library import WEBSITE_ITEMS

logger = logging.getLogger(__name__)

BRANCH_NAME = "Website"
BRANCH_LANG = "en"

# One creator at a time WITHIN a process. The site chat is a public endpoint and the first two
# visitors after a fresh deploy are easily concurrent; both would find no branch and both would
# create one, leaving two Website branches.
#
# The lock alone is not the guarantee, and an earlier version of this comment claimed it was.
# It has to be held across the COMMIT, not just the flush: the caller's transaction stays open
# for the whole broker call (tens of seconds), so a lock released at flush time lets the next
# visitor read a table that still has nothing in it. And it is per-process — a second uvicorn
# worker never contends for it at all. The real guarantee is the partial unique index
# uq_channel_one_website; the lock only keeps the common case off that error path.
_seed_lock = asyncio.Lock()


async def website_branch_id(session: AsyncSession) -> int | None:
    """The active branch owning an active website channel, or None if the site has no branch.

    uq_channel_one_website makes two impossible from this build on; lowest id wins anyway, for
    a database that predates the index or was edited by hand. Arbitrary but STABLE, so the demo
    does not switch prompts between requests while the duplicate is being cleaned up."""
    rows = (await session.execute(
        select(Channel.branch_id)
        .join(Branch, Branch.id == Channel.branch_id)
        .where(Channel.kind == ChannelKind.WEBSITE,
               Channel.is_active.is_(True), Branch.is_active.is_(True))
        .order_by(Channel.branch_id))).scalars().all()
    if not rows:
        return None
    if len(set(rows)) > 1:
        logger.warning("more than one website branch (%s) — serving the lowest", sorted(set(rows)))
    return int(rows[0])


async def ensure_website_branch(session: AsyncSession) -> int:
    """The site branch, created and seeded from the prompt library if it is not there yet.

    Seeding is `ensure_library` + `clone_into_branch` — the S5 code path every other branch
    goes through, not a second one written for the site. That is the whole point: the demo's
    persona, method and catalogue are library rows a person can browse and version, and the
    contract wrapped around them is CRAFT, shared with the DM path.

    Called on every demo turn, so the existence check is the whole idempotency: once the
    branch is there this returns it and the library is not consulted again. That is what
    makes an edit safe — the prompt the site sells with is the branch's rows, and a later
    deploy must not push the library's wording back over what the owner rewrote."""
    async with _seed_lock:
        existing = await website_branch_id(session)
        if existing is not None:
            return existing
        try:
            branch_id = await _create(session)
            # Committed HERE, inside the lock, and not left to the caller's session_scope: the
            # caller holds its transaction open across the broker call that answers this turn,
            # so a branch that is only flushed stays invisible to the next visitor for as long
            # as that call takes — which is exactly how two Website branches got created.
            await session.commit()
        except IntegrityError:
            # Another worker won the unique index. Its row is committed by definition, so the
            # only thing to do is adopt it.
            await session.rollback()
            adopted = await website_branch_id(session)
            if adopted is None:
                raise
            logger.info("website branch %d created by another worker — adopted", adopted)
            return adopted
        invalidate(branch_id)
        logger.info("website branch %d created and seeded from the prompt library", branch_id)
        return branch_id


async def _create(session: AsyncSession) -> int:
    branch = Branch(name=BRANCH_NAME, lang=BRANCH_LANG, is_active=True)
    session.add(branch)
    await session.flush()
    branch_id = int(branch.id or 0)
    session.add(Channel(branch_id=branch_id, kind=ChannelKind.WEBSITE, handle=BRANCH_NAME))
    # The composer pipeline, explicitly. The default is legacy, whose hardcoded slug list
    # (facts_policy, objection_playbook, …) would load none of the cloned documents and
    # leave the site selling with an empty knowledge surface.
    session.add(AppSetting(branch_id=branch_id, channel_id=None,
                           key="prompt_pipeline", value=COMPOSER))
    await _clone_library(session, branch_id)
    await session.flush()
    return branch_id


async def _clone_library(session: AsyncSession, branch_id: int) -> None:
    """Every layer, into a branch created moments ago in this same transaction — so no clone
    can conflict, and a failure of any of them unwinds the whole branch rather than leaving a
    site selling with half a prompt."""
    await ensure_library(session, WEBSITE_ITEMS)
    for spec in WEBSITE_ITEMS:
        item = await library_item(session, spec["kind"], spec["slug"], spec["version"])
        if item is None:
            raise LookupError(f"website library entry {spec['kind']}/{spec['slug']} missing")
        await clone_into_branch(session, branch_id, item)
