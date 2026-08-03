"""Fingerprint a branch's cached prompt prefix, so a refactor can prove it changed nothing.

messages[0] is the broker's prompt-cache anchor and the whole of what the model knows about
the business. Every step of the connector/prompt refactor has to answer one question about the
live Indonesian branch: is it byte-identical to before? This prints a hash that answers it,
without ever putting the client's commercial text into the repo or a log.

    docker exec stepan2-api python -m scripts.prompt_snapshot 1
    docker exec stepan2-api python -m scripts.prompt_snapshot 1 --sections

Run it before a deploy and after; the hashes must match. --sections narrows a mismatch to the
block that moved (persona, docs, catalogue, contract) — still hashes only, no content.
"""
from __future__ import annotations

import asyncio
import hashlib
import sys

from app.adapters.db.session import session_scope
from app.modules.conversation.free_mode import free_contract
from app.modules.knowledge.service import KnowledgeService


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def _snapshot(branch_id: int, *, sections: bool) -> int:
    async with session_scope() as session:
        knowledge = KnowledgeService(session, branch_id)
        lang = await knowledge._lang(None)  # noqa: SLF001 — the resolver the prompt itself uses
        context = await knowledge.full_knowledge_context()
        contract = free_contract(lang)
        prefix = context.rstrip() + "\n\n" + contract

    print(f"branch={branch_id} lang={lang}")
    print(f"prefix   {_h(prefix)}  {len(prefix)} chars")
    if sections:
        print(f"  knowledge {_h(context)}  {len(context)} chars")
        print(f"  contract  {_h(contract)}  {len(contract)} chars")
        for block in context.split("\n\n"):
            head = block.strip().splitlines()[0][:48] if block.strip() else "(empty)"
            print(f"    {_h(block)}  {len(block):6}  {head}")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print(__doc__)
        return 2
    return asyncio.run(_snapshot(int(args[0]), sections="--sections" in sys.argv))


if __name__ == "__main__":
    raise SystemExit(main())
