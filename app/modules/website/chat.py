"""One turn of the site chat, assembled by the branch prompt pipeline.

Nothing about the PROMPT is special here. The stable prefix comes out of DecisionEngine — the
same object the DM reply and the follow-up build theirs with — so `messages[0]` for the site
branch is the branch's own documents through the composer followed by CRAFT, byte for byte
what a DM turn on this branch would carry. That is the point of S6: the selling contract is
the shared one, and what is peculiar to a web page lives in the branch's method document where
a person can read it and edit it.

The GENERATION loop below is this module's own, and smaller than reply.py's. It shares the
prompt, not the pipeline: no `chat:sales -> chat:smart` capability fallback, and none of
guard.py's grounding or link verification, so an invented figure on the public page is caught
by the persona's own rules and nothing else. Same as before S6 — the old hardcoded route had
neither — and the reason it is not wired here is that both hang off a thread, a lead and a
dossier, none of which a demo turn has. Written down in docs/website-branch.md rather than
left to be rediscovered.

No thread, no lead, no dossier: the dialog is the in-memory transcript (see session.py) and
the LeadDossier is empty, so build_messages_free emits the shape it does on turn one of any
conversation.
"""
from __future__ import annotations

import logging

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Message
from app.adapters.llm.broker import BrokerLLM
from app.modules.conversation.decision import parse_turn_decision
from app.modules.conversation.dossier import LeadDossier
from app.modules.conversation.engine import DecisionEngine
from app.modules.conversation.free_mode import build_messages_free
from app.modules.knowledge.service import KnowledgeService
from app.modules.settings.service import get_settings

logger = logging.getLogger(__name__)

_CAPABILITY = "chat:sales"
_ATTEMPTS = 2  # one retry: a stuck provider fails fast and the retry lands on a fast one
# Per-attempt read cap, well under the 90s broker ceiling — an interactive page that waits
# ninety seconds reads as hung.
_ATTEMPT_TIMEOUT_S = 45.0
_MAX_TOKENS = 700
_BUBBLE_SEP = "|||"  # CRAFT's multi-bubble marker; a web chat shows one block


def _as_dialog(branch_id: int, turns: list[tuple[str, str]]) -> list[Message]:
    """The transcript in the shape the prompt assembler reads. Unsaved rows, never added to
    the session: the owner's decision is that an anonymous visitor leaves no rows behind."""
    return [Message(branch_id=branch_id, thread_id=0, channel_id=0, external_id=f"web-{i}",
                    direction="in" if role == "user" else "out", text=text)
            for i, (role, text) in enumerate(turns)]


def engine_for(
    session: AsyncSession, branch_id: int, llm: BrokerLLM | None = None,
) -> DecisionEngine:
    """The branch's prompt assembler — the DM path's own class, not a subclass of it."""
    return DecisionEngine(session, branch_id, llm, KnowledgeService(session, branch_id, llm))


async def build_messages(
    engine: DecisionEngine, branch_id: int, turns: list[tuple[str, str]],
) -> list[dict]:
    """The chat `messages` for this turn, through the branch's own prompt pipeline."""
    lang = await engine.branch_lang()
    return build_messages_free(
        await engine.free_kb_context(), _as_dialog(branch_id, turns), lang, LeadDossier(),
        contract=await engine.reply_contract(lang),
        now_block=await engine._now_block())  # noqa: SLF001 — engine owns the clock


async def generate_reply(
    session: AsyncSession, branch_id: int, turns: list[tuple[str, str]],
    llm: BrokerLLM | None = None,
) -> str:
    """Stepan's answer for this turn, or "" when the broker could not produce one.

    Empty rather than a canned apology, so the caller decides what a failed turn looks like
    and a failed turn never enters the transcript as something Stepan said."""
    broker = llm or BrokerLLM()
    engine = engine_for(session, branch_id, broker)
    messages = await build_messages(engine, branch_id, turns)
    lang = await engine.branch_lang()
    cfg = await get_settings(session, branch_id)
    for attempt in range(_ATTEMPTS):
        try:
            raw, _meta = await broker.chat(
                messages, capability=_CAPABILITY, max_tokens=_MAX_TOKENS,
                require_json_schema=True, workflow="website_demo", branch_id=branch_id,
                read_timeout_s=_ATTEMPT_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — any transport failure earns the one retry
            logger.warning("website demo attempt %d/%d failed: %s",
                           attempt + 1, _ATTEMPTS, type(exc).__name__)
            continue
        try:
            # The branch's own country code and language, not the Indonesian defaults:
            # clean_reply deletes invented phone numbers and only knows their shape once it
            # knows the country, and the price reader needs the market's currency words.
            decision = parse_turn_decision(
                raw or "", country_code=cfg.phone_country_code, money_lang=lang)
        except ValueError:
            logger.warning("website demo attempt %d/%d: unparseable decision",
                           attempt + 1, _ATTEMPTS)
            continue
        reply = decision.reply.replace(_BUBBLE_SEP, "\n\n").strip()
        if reply:
            return reply
    return ""
