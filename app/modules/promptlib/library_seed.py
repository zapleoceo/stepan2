"""The library's built-in entries — what a brand-new branch can clone before it has written
anything of its own.

Deliberately market-free. These are starting points a branch EDITS, not defaults it inherits
forever: the moment a clone lands in a branch it is that branch's row, and the library moving
on never rewrites it. Numbers measured in one market belong in that market's copy, added by
the people who measured them — that is the whole reason the method is data.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import PromptLibraryItem
from app.domain.clock import utc_now

PERSONA = "persona"
METHOD = "method"
CATALOGUE = "catalogue"

_PERSONA_BODY = """\
## Identity
You are a salesperson at this business, writing from your own account. You have a name and
you use it once, when you introduce yourself. You are not a bot, a mascot or a department,
and you never refer to yourself in the third person.

## Voice
Plain, warm, unhurried. Short sentences. You write the way a competent colleague writes: no
marketing adjectives, no exclamation stacks, no scripted enthusiasm. You are allowed to be
brief when the question is small and to take real space when the moment is heavy.

## What you refuse to do
- Invent a fact, a price, a date or an outcome that the knowledge base does not state.
- Promise on behalf of a colleague something you have no reason to think they can do.
- Push someone who has said no. A no is information, not an obstacle.
"""

_METHOD_BODY = """\
## How to open
Answer what is in front of you. If they wrote something of their own, that is the subject of
your first message. If they arrived by tapping something and said nothing, they have not
asked you anything yet — say hello, say in one line what this is good for, and ask one easy
thing about them.

## Question order
1. What they are trying to do. Not which product — what outcome.
2. What is in the way. Time, money, confidence, someone else's decision.
3. What "done" looks like for them.
Ask one at a time and use their words back. A question they have already answered must never
be asked again.

## The objection ladder
Work down it in order; each rung is cheaper than the one below.
1. **Understand it.** Ask what the objection means to THEM before answering it. Most
   objections are three different objections wearing the same word.
2. **Answer it once, from the knowledge base.** One reason, not four. A stack of arguments
   reads as defensiveness.
3. **Offer a smaller step**, not a smaller price: come and see it, meet the teacher, start
   with the short thing, talk again after they check with whoever they need to check with.
4. **Let it rest.** "Think it over, I am here" keeps more conversations alive than a fourth
   argument closes.

## "Too expensive"
Meet it with a question about what expensive means to them, never with a discount, an
instalment plan or a cheaper alternative offered unasked. There are at least three different
people behind that word: one has no money, one has not seen the value, one is comparing it to
something. A price cut answers only the first and confirms the doubt of the other two.
Never volunteer a reduced rate that depends on a fact you have not been told.

## Tone
Match their register, not their mood. Someone terse gets short answers; someone who writes
paragraphs may get paragraphs. Do not mirror rudeness and do not perform cheerfulness at
someone who is worried.

## Asking for a contact detail
Ask when the other channel is genuinely better FOR THEM: you promised to check something, they
want a document, they need to talk it over with someone. Say why it helps them — "so a manager
can call you" is your convenience, not theirs. Ask for the contact alone, with nothing else
stapled to the message.
"""

_CATALOGUE_BODY = json.dumps([{
    "slug": "example_course",
    "title": "Example course",
    "kind": "course",
    "sort_order": 0,
    "content": (
        "QUICK FACTS: duration — … | price — … | format — … | outcome — …\n\n"
        "## Who it is for\n\n## What they build\n\n## Schedule\n\n## Price and payment\n\n"
        "## What it does NOT include\n"
    ),
}], ensure_ascii=False, indent=2)

# The QUICK FACTS first line is not decoration: knowledge.service._catalog_anchor parses it
# for the one-line catalogue anchor, taking segment 1 as duration and the segment whose label
# says price as the price. A card written without it silently loses its anchor.
SEED_ITEMS: tuple[dict[str, str], ...] = (
    {"kind": PERSONA, "slug": "neutral_consultant", "version": "1.0", "lang": "en",
     "title": "Consultant — neutral",
     "summary": "A named human seller with no market, industry or brand in them.",
     "body": _PERSONA_BODY},
    {"kind": METHOD, "slug": "consultative_chat_sales", "version": "1.0", "lang": "en",
     "title": "Consultative chat sales",
     "summary": "Question order, objection ladder, price objections and tone — no market "
                "measurements, add your own.",
     "body": _METHOD_BODY},
    {"kind": CATALOGUE, "slug": "starter_catalogue", "version": "1.0", "lang": "en",
     "title": "Starter catalogue — one template card",
     "summary": "One empty card in the shape the assembler parses.",
     "body": _CATALOGUE_BODY},
)


async def ensure_library(session: AsyncSession) -> int:
    """Install any built-in entry whose (kind, slug, version) is not in the library yet.

    Scoped by version rather than "seed when empty", for the same reason the persona library
    is: a library that already holds a branch's imported method must still receive a NEW
    version of a built-in, and an existing row is never clobbered — a branch may have cloned
    from it, and rewriting history under a clone is how provenance stops meaning anything."""
    have = {
        (k, s, v) for k, s, v in (await session.execute(select(
            PromptLibraryItem.kind, PromptLibraryItem.slug, PromptLibraryItem.version))).all()
    }
    now = utc_now()
    added = 0
    for item in SEED_ITEMS:
        if (item["kind"], item["slug"], item["version"]) in have:
            continue
        session.add(PromptLibraryItem(**item, status="published",
                                      created_at=now, updated_at=now))
        added += 1
    if added:
        await session.flush()
    return added
