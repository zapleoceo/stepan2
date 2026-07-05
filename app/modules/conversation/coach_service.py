"""LLM-powered Knowledge Base editor for the coach mode.

propose_edit  → answer a question / propose an edit / clarify (intent-based)
apply_edit    → replaces old_text with new_text in the target doc
cancel_edit   → marks the edit as cancelled
analyze_chat  → read a whole lead chat and grade it against the KB
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import CoachingEdit
from app.modules.knowledge.repository import KnowledgeRepo
from app.ports.llm import LLMPort

logger = logging.getLogger(__name__)

_ANALYZE_SYSTEM = (
    "You are a sales QA coach. Below is the branch's FULL knowledge base, then a lead chat "
    "(LEAD = customer, AGENT = our bot). Read both fully and grade the bot's handling AGAINST "
    "the knowledge base.\n\n"
    "KNOWLEDGE BASE:\n{docs}\n\n"
    "Answer concisely in {lang}, in these sections (use short bullet points):\n"
    "✅ Что верно — where the bot followed the KB / sold well.\n"
    "⚠️ Ошибки — anything the bot said that CONTRADICTS the KB, invented facts, wrong price/"
    "date, or a mishandled objection (quote the KB fact it broke).\n"
    "🕳 Пробелы в базе — questions the lead raised that the KB doesn't answer (so the bot "
    "couldn't).\n"
    "🎯 Что улучшить — 1-3 concrete next-step suggestions.\n"
    "Base every point ONLY on the KB and the chat; never invent."
)

_SYSTEM = (
    "You are the Knowledge Base assistant for an AI sales bot. The FULL knowledge base is "
    "below; read ALL of it before answering.\n\n"
    "CURRENT KNOWLEDGE BASE:\n{docs}\n\n"
    "Decide the manager's INTENT from their message and reply with JSON ONLY (no markdown):\n"
    "1) A QUESTION about the bot / KB / products → answer it FROM the knowledge base:\n"
    '   {{"intent":"answer","answer":"your answer, citing the relevant doc"}}\n'
    "2) A CLEAR command to CHANGE the KB where you know EXACTLY where → propose the edit "
    "(NOT applied until the manager confirms):\n"
    '   {{"intent":"edit","slug":"doc_slug","old_text":"exact verbatim substring",'
    '"new_text":"replacement","summary":"one-line description"}}\n'
    "3) A change whose target is unclear, OR new data you must place carefully → ASK where "
    "BEFORE touching anything:\n"
    '   {{"intent":"clarify","summary":"which document/section should this go in? ..."}}\n\n'
    "RULES:\n"
    "- NEVER invent facts; answers/edits use only what's in the KB (or the manager's explicit "
    "new data).\n"
    "- For an edit, old_text MUST be a verbatim substring of the named document; keep it "
    "minimal.\n"
    "- When unsure WHERE to add data, choose 'clarify' — never guess.\n"
    "- Use the same language as the manager."
)


async def create_pending_edit(
    session: AsyncSession, branch_id: int, request: str,
) -> CoachingEdit:
    """Persist the manager's question IMMEDIATELY as a 'thinking' row, before the slow
    chat:deep call — so the question is never lost if they navigate away mid-generation."""
    edit = CoachingEdit(branch_id=branch_id, request=request, status="thinking")
    session.add(edit)
    await session.flush()
    return edit


async def generate_into_edit(
    session: AsyncSession, branch_id: int, edit: CoachingEdit, llm: LLMPort,
) -> CoachingEdit:
    """Run the chat:deep coach turn and fill the (already-persisted) edit with the result.

    Latency isn't critical (a manager waits, not a live lead), so the WHOLE KB goes in uncut
    and chat:deep absorbs it — full context beats truncated snippets."""
    docs = await KnowledgeRepo(session, branch_id).list()
    docs_text = "\n\n".join(
        f"=== {d.slug} ({d.title or d.slug}) ===\n{d.content}" for d in docs
    )
    messages = [
        {"role": "system", "content": _SYSTEM.format(docs=docs_text)},
        {"role": "user", "content": edit.request},
    ]
    data: dict = {"intent": "clarify", "summary": "Ошибка LLM"}
    for attempt in range(3):  # reasoning model can return an empty/non-JSON body — retry
        try:
            raw, _meta = await llm.chat_deep(
                messages, require_json_schema=True,
                max_tokens=8000, temperature=0.1, workflow="coach", branch_id=branch_id,
            )
            cleaned = (
                raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            )
            data = json.loads(cleaned)
            break
        except Exception as exc:  # noqa: BLE001
            data = {"intent": "clarify", "summary": f"Ошибка LLM: {exc}"}
            if attempt < 2:
                await asyncio.sleep(1.5)

    intent = data.get("intent") or ("edit" if data.get("old_text") else "clarify")
    if intent == "answer":
        edit.status = "answered"
        edit.summary = data.get("answer") or data.get("summary")
        edit.slug = edit.old_text = edit.new_text = None
    elif intent == "edit" and data.get("old_text"):
        edit.status = "proposed"
        edit.summary, edit.slug = data.get("summary"), data.get("slug")
        edit.old_text, edit.new_text = data.get("old_text"), data.get("new_text")
    else:  # clarify, or an edit that never named a target
        edit.status = "clarify"
        edit.summary = data.get("summary")
        edit.slug = edit.old_text = edit.new_text = None

    session.add(edit)
    await session.flush()
    return edit


async def propose_edit(
    session: AsyncSession,
    branch_id: int,
    request: str,
    llm: LLMPort,
) -> CoachingEdit:
    """Synchronous coach turn (create the row + generate in one go). Kept for callers/tests
    that want the finished edit back; the interactive route uses the two split functions so
    generation can run in the background."""
    edit = await create_pending_edit(session, branch_id, request)
    return await generate_into_edit(session, branch_id, edit, llm)


async def analyze_chat(
    session: AsyncSession, branch_id: int, thread_id: int, llm: LLMPort, lang: str = "Russian",
) -> str:
    """Read a whole lead chat and grade the bot's handling against the KB (chat:deep). Returns
    the analysis text, or '' if the thread is empty / the call fails."""
    rows = (await session.execute(
        text("SELECT direction, text FROM message WHERE thread_id = :t AND text <> ''"
             " ORDER BY occurred_at, id LIMIT 300"),
        {"t": thread_id},
    )).all()
    convo = "\n".join(
        f"{'LEAD' if r[0] == 'in' else 'AGENT'}: {(r[1] or '').strip()}"
        for r in rows if (r[1] or "").strip()
    )[:12000]
    if not convo:
        return ""
    # RAG-retrieve the KB chunks this chat actually touches, not the whole 32k-token base:
    # the full KB + a full chat through the reasoning model overran the broker's ~100s
    # gateway (504). The relevant chunks are exactly the facts to grade the bot against.
    from app.modules.knowledge.rag import RagService  # noqa: PLC0415 (avoid import cycle)
    chunks = await RagService(session, branch_id, llm).retrieve(convo[-3000:], k=20,
                                                                thread_id=thread_id)
    if chunks:
        docs_text = "\n\n".join(f"=== {title} ===\n{txt}" for title, txt in chunks)
    else:  # index empty (never reindexed) → fall back to the raw docs
        docs = await KnowledgeRepo(session, branch_id).list()
        docs_text = "\n\n".join(f"=== {d.slug} ===\n{d.content}" for d in docs)[:20000]
    messages = [
        {"role": "system", "content": _ANALYZE_SYSTEM.format(docs=docs_text, lang=lang)},
        {"role": "user", "content": convo},
    ]
    for attempt in range(3):
        try:
            # chat:smart (fast large-context model), NOT chat:deep: a full chat + full KB
            # through the slow reasoning model overran the broker's ~100s gateway (504). One
            # branch's KB + one chat fits chat:smart's 128k window fine and answers in time.
            raw, _ = await llm.chat(messages, capability="chat:smart", max_tokens=4000,
                                    temperature=0.2, workflow="coach", thread_id=thread_id,
                                    branch_id=branch_id)
            if raw and raw.strip():
                return raw.strip()
        except Exception as exc:  # noqa: BLE001, PERF203
            logger.warning("analyze_chat failed thread=%s: %s", thread_id, exc)
        if attempt < 2:
            await asyncio.sleep(1.5)
    return ""


async def apply_edit(
    session: AsyncSession, branch_id: int, edit_id: int
) -> CoachingEdit | None:
    """Apply the proposed text replacement to the KB doc; returns None when not applicable."""
    edit = await session.get(CoachingEdit, edit_id)
    if not edit or edit.branch_id != branch_id or edit.status != "proposed":
        return None
    if edit.slug and edit.old_text is not None and edit.new_text is not None:
        doc = await KnowledgeRepo(session, branch_id).by_slug(edit.slug)
        if doc and edit.old_text in doc.content:
            doc.content = doc.content.replace(edit.old_text, edit.new_text, 1)
            doc.updated_at = datetime.now(UTC).replace(tzinfo=None)  # → RAG watcher reindexes
            session.add(doc)
            edit.status = "applied"
            edit.applied_at = datetime.now(UTC).replace(tzinfo=None)
        else:
            edit.status = "failed"
            edit.summary = (edit.summary or "") + " [текст не найден в документе]"
    else:
        edit.status = "cancelled"
    session.add(edit)
    await session.flush()
    return edit


async def cancel_edit(
    session: AsyncSession, branch_id: int, edit_id: int
) -> CoachingEdit | None:
    """Mark a proposed edit as cancelled."""
    edit = await session.get(CoachingEdit, edit_id)
    if not edit or edit.branch_id != branch_id:
        return None
    edit.status = "cancelled"
    session.add(edit)
    await session.flush()
    return edit


async def revert_edit(
    session: AsyncSession, branch_id: int, edit_id: int
) -> CoachingEdit | None:
    """Undo an applied edit by the INVERSE substring swap (new_text → old_text).

    NOT a whole-document restore: replacing the doc with the stored old_text would wipe
    everything else and any later edits. If new_text is no longer present (doc changed
    since), the revert is refused rather than guessing."""
    edit = await session.get(CoachingEdit, edit_id)
    if not edit or edit.branch_id != branch_id or edit.status != "applied":
        return None
    if edit.slug and edit.old_text is not None and edit.new_text is not None:
        doc = await KnowledgeRepo(session, branch_id).by_slug(edit.slug)
        if doc and edit.new_text in doc.content:
            doc.content = doc.content.replace(edit.new_text, edit.old_text, 1)
            session.add(doc)
            edit.status = "reverted"
        else:
            edit.status = "revert_failed"
            edit.summary = (edit.summary or "") + " [новый текст не найден — откат невозможен]"
    session.add(edit)
    await session.flush()
    return edit
