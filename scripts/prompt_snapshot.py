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
from app.modules.knowledge.service import KnowledgeService
from app.modules.promptlib.pipeline import branch_pipeline, prompt_contract, prompt_knowledge


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def _snapshot(branch_id: int, *, sections: bool) -> int:
    async with session_scope() as session:
        knowledge = KnowledgeService(session, branch_id)
        lang = await knowledge._lang(None)  # noqa: SLF001 — the resolver the prompt itself uses
        # Through the pipeline, not around it: a snapshot that always read the legacy
        # assembler would print a hash of a prompt no lead on a composer branch ever sees.
        pipeline = await branch_pipeline(session, branch_id)
        context = await prompt_knowledge(session, branch_id, knowledge)
        contract = await prompt_contract(session, branch_id, lang)
        prefix = context.rstrip() + "\n\n" + contract

    print(f"branch={branch_id} lang={lang} pipeline={pipeline}")
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
