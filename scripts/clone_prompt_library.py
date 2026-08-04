"""Clone prompt-library entries into a branch.

    docker exec stepan2-api python -m scripts.clone_prompt_library --list
    docker exec stepan2-api python -m scripts.clone_prompt_library 7 \
        --persona neutral_consultant --method consultative_chat_sales --replace

--replace takes the branch's CURRENT rows of that layer out of the prompt (in_prompt=false /
is_active=false). Nothing is deleted, so it is undone by flipping those flags back — that is
what makes "this branch inherits nothing from whoever had it before" a safe operation.
--overwrite is needed to re-clone onto a slug the branch already holds, and discards whatever
the branch wrote on top of it.

Read the branch's settings before running this: the cloned method only reaches the model on
the composer pipeline. On legacy the documents are loaded by a hardcoded slug list and a
cloned method is invisible.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.adapters.db.models import PromptLibraryItem
from app.adapters.db.session import session_scope
from app.modules.promptlib.clone import CloneConflict, clone_into_branch, library_item
from app.modules.promptlib.library_seed import CATALOGUE, METHOD, PERSONA, ensure_library


async def _list() -> int:
    async with session_scope() as session:
        await ensure_library(session)
        rows = list((await session.execute(select(PromptLibraryItem).order_by(
            PromptLibraryItem.kind, PromptLibraryItem.slug,
            PromptLibraryItem.version))).scalars())
    for r in rows:
        print(f"{r.kind:10} {r.slug:28} {r.version:6} {r.status:10} {r.title}")
    return 0


async def _clone(args: argparse.Namespace) -> int:
    wanted = [(k, s) for k, s in ((PERSONA, args.persona), (METHOD, args.method),
                                  (CATALOGUE, args.catalogue)) if s]
    if not wanted:
        print("nothing to clone: pass at least one of --persona/--method/--catalogue")
        return 2
    async with session_scope() as session:
        await ensure_library(session)
        for kind, slug in wanted:
            item = await library_item(session, kind, slug, args.version)
            if item is None:
                print(f"no {kind} '{slug}' in the library")
                return 1
            try:
                result = await clone_into_branch(
                    session, args.branch_id, item,
                    replace_existing=args.replace, overwrite=args.overwrite)
            except CloneConflict as exc:
                print(f"{exc} (pass --overwrite to take the library version)")
                return 1
            print(f"branch {args.branch_id}: {kind} <- {item.slug}@{item.version} "
                  f"created={result.created} retired={result.retired}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("branch_id", nargs="?", type=int)
    p.add_argument("--list", action="store_true")
    p.add_argument("--persona")
    p.add_argument("--method")
    p.add_argument("--catalogue")
    p.add_argument("--version", default=None, help="default: highest published")
    p.add_argument("--replace", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    args = p.parse_args()
    if args.list:
        return asyncio.run(_list())
    if args.branch_id is None:
        p.print_help()
        return 2
    return asyncio.run(_clone(args))


if __name__ == "__main__":
    sys.exit(main())
