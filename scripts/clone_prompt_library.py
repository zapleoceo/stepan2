"""Clone prompt-library entries into a branch, and show what each branch cloned.

    docker exec stepan2-api python -m scripts.clone_prompt_library --list
    docker exec stepan2-api python -m scripts.clone_prompt_library --sources
    docker exec stepan2-api python -m scripts.clone_prompt_library 7 \
        --persona neutral_consultant --method consultative_chat_sales --replace

--replace takes the branch's CURRENT rows of that layer out of the prompt (in_prompt=false /
is_active=false). Nothing is deleted, so it is undone by flipping those flags back — that is
what makes "this branch inherits nothing from whoever had it before" a safe operation.
--overwrite is needed to re-clone onto a slug the branch already holds, and discards whatever
the branch wrote on top of it.

Read the branch's settings before running this: the cloned method only reaches the model on
the composer pipeline. On legacy the documents are loaded by a hardcoded slug list and a
cloned method is invisible — which is what --sources prints alongside each branch.

Every requested layer lands or none of them does. A failure has to leave the transaction by
raising, never by returning an exit code from inside session_scope: that scope commits on the
way out, so an early `return 1` used to persist the half-done clone it was reporting as a
failure — a branch left with its old persona retired and no new one written.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, BranchPromptSource, PromptLibraryItem
from app.adapters.db.session import session_scope
from app.modules.promptlib.clone import CloneConflict, clone_into_branch, library_item
from app.modules.promptlib.library_seed import CATALOGUE, METHOD, PERSONA, ensure_library
from app.modules.promptlib.pipeline import branch_pipeline


class MissingLibraryItem(LookupError):
    """Asked for a library entry that is not there. An exception rather than an exit code so
    it unwinds the transaction with everything else — see the module docstring."""


async def _list() -> int:
    async with session_scope() as session:
        await ensure_library(session)
        rows = list((await session.execute(select(PromptLibraryItem).order_by(
            PromptLibraryItem.kind, PromptLibraryItem.slug,
            PromptLibraryItem.version))).scalars())
    for r in rows:
        print(f"{r.kind:10} {r.slug:28} {r.version:6} {r.status:10} {r.title}")
    return 0


async def _sources() -> int:
    """Which branch is on which pipeline, and what it cloned — the answer to 'who is still on
    method 1.0', which branch_prompt_source existed to hold and nothing read back."""
    async with session_scope() as session:
        lines = await source_report(session)
    print("\n".join(lines) or "no branches")
    return 0


async def source_report(session: AsyncSession) -> list[str]:
    branches = list((await session.execute(
        select(Branch).order_by(Branch.id))).scalars())  # type: ignore[arg-type]
    sources = list((await session.execute(select(BranchPromptSource).order_by(
        BranchPromptSource.branch_id, BranchPromptSource.layer))).scalars())
    lines = []
    for b in branches:
        pipeline = await branch_pipeline(session, b.id)
        cloned = [f"{s.layer}={s.library_slug}@{s.library_version}"
                  for s in sources if s.branch_id == b.id]
        lines.append(f"{b.id:>4}  {b.name:20} {pipeline:9} "
                     f"{', '.join(cloned) or '(nothing cloned)'}")
    return lines


async def clone_layers(
    session: AsyncSession, branch_id: int, wanted: Sequence[tuple[str, str]], *,
    version: str | None = None, replace_existing: bool = False, overwrite: bool = False,
) -> list[str]:
    """Clone every requested layer, raising on the first one that cannot be done."""
    lines = []
    for kind, slug in wanted:
        item = await library_item(session, kind, slug, version)
        if item is None:
            at = f"@{version}" if version else " (published)"
            raise MissingLibraryItem(f"no {kind} '{slug}'{at} in the library")
        result = await clone_into_branch(
            session, branch_id, item,
            replace_existing=replace_existing, overwrite=overwrite)
        lines.append(f"branch {branch_id}: {kind} <- {item.slug}@{item.version} "
                     f"created={result.created} retired={result.retired}")
    return lines


async def _clone(args: argparse.Namespace) -> int:
    wanted = [(k, s) for k, s in ((PERSONA, args.persona), (METHOD, args.method),
                                  (CATALOGUE, args.catalogue)) if s]
    if not wanted:
        print("nothing to clone: pass at least one of --persona/--method/--catalogue")
        return 2
    try:
        async with session_scope() as session:
            await ensure_library(session)
            lines = await clone_layers(
                session, args.branch_id, wanted, version=args.version,
                replace_existing=args.replace, overwrite=args.overwrite)
    except CloneConflict as exc:
        print(f"{exc} (pass --overwrite to take the library version) — nothing was changed")
        return 1
    except MissingLibraryItem as exc:
        print(f"{exc} — nothing was changed")
        return 1
    print("\n".join(lines))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("branch_id", nargs="?", type=int)
    p.add_argument("--list", action="store_true")
    p.add_argument("--sources", action="store_true")
    p.add_argument("--persona")
    p.add_argument("--method")
    p.add_argument("--catalogue")
    p.add_argument("--version", default=None, help="default: highest published")
    p.add_argument("--replace", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    args = p.parse_args()
    if args.list:
        return asyncio.run(_list())
    if args.sources:
        return asyncio.run(_sources())
    if args.branch_id is None:
        p.print_help()
        return 2
    return asyncio.run(_clone(args))


if __name__ == "__main__":
    sys.exit(main())
