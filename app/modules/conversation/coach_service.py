"""LLM-powered Knowledge Base editor for the coach mode.

propose_edit  → reads KB docs, asks LLM to produce old/new diff → stores CoachingEdit
apply_edit    → replaces old_text with new_text in the target doc
cancel_edit   → marks the edit as cancelled
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import CoachingEdit
from app.modules.knowledge.repository import KnowledgeRepo
from app.ports.llm import LLMPort

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


async def propose_edit(
    session: AsyncSession,
    branch_id: int,
    request: str,
    llm: LLMPort,
) -> CoachingEdit:
    """Coach turn: answer a question FROM the KB, propose a KB edit (confirm-before-write),
    or ask where to place new data. Persisted as a CoachingEdit row for the chat history.

    Latency isn't critical (a manager waits, not a live lead), so the WHOLE KB goes in uncut
    and chat:deep absorbs it — full context beats truncated snippets."""
    docs = await KnowledgeRepo(session, branch_id).list()
    docs_text = "\n\n".join(
        f"=== {d.slug} ({d.title or d.slug}) ===\n{d.content}" for d in docs
    )
    messages = [
        {"role": "system", "content": _SYSTEM.format(docs=docs_text)},
        {"role": "user", "content": request},
    ]
    try:
        raw, _meta = await llm.chat(
            messages, capability="chat:deep", max_tokens=8000, temperature=0.1,
            workflow="coach", branch_id=branch_id,
        )
        cleaned = (
            raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        )
        data: dict = json.loads(cleaned)
    except Exception as exc:  # noqa: BLE001
        data = {"intent": "clarify", "summary": f"Ошибка LLM: {exc}"}

    intent = data.get("intent") or ("edit" if data.get("old_text") else "clarify")
    if intent == "answer":
        status = "answered"
        summary = data.get("answer") or data.get("summary")
        slug = old_text = new_text = None
    elif intent == "edit" and data.get("old_text"):
        status = "proposed"
        summary, slug = data.get("summary"), data.get("slug")
        old_text, new_text = data.get("old_text"), data.get("new_text")
    else:  # clarify, or an edit that never named a target
        status = "clarify"
        summary = data.get("summary")
        slug = old_text = new_text = None

    edit = CoachingEdit(
        branch_id=branch_id, request=request, status=status,
        slug=slug, old_text=old_text, new_text=new_text, summary=summary,
    )
    session.add(edit)
    await session.flush()
    return edit


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
