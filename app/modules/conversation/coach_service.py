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
    "You are a Knowledge Base editor for an AI sales assistant bot.\n"
    "The manager wants to modify the bot's behavior by editing the knowledge base.\n\n"
    "CURRENT KNOWLEDGE BASE:\n{docs}\n\n"
    "Propose a minimal targeted change. Reply with JSON ONLY (no markdown):\n"
    '{{"slug":"doc_slug","old_text":"exact verbatim text","new_text":"replacement",'
    '"summary":"one-line description"}}\n\n'
    "If clarification is needed:\n"
    '{{"slug":null,"old_text":null,"new_text":null,"summary":"your question"}}\n\n'
    "RULES:\n"
    "- old_text MUST be a verbatim substring of the named document\n"
    "- Keep changes minimal\n"
    "- Use the same language as the document"
)


async def propose_edit(
    session: AsyncSession,
    branch_id: int,
    request: str,
    llm: LLMPort,
) -> CoachingEdit:
    """Ask the LLM to propose a KB doc change and persist the result."""
    docs = await KnowledgeRepo(session, branch_id).list()
    # Coach isn't latency-critical (a manager waits a few seconds, not a live lead), so feed
    # the WHOLE KB uncut and let chat:deep absorb it — full context beats truncated snippets
    # when reasoning about which doc to change and how.
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
        data = {"slug": None, "old_text": None, "new_text": None, "summary": f"Ошибка LLM: {exc}"}

    status = "proposed" if data.get("old_text") else "clarify"
    edit = CoachingEdit(
        branch_id=branch_id,
        request=request,
        status=status,
        slug=data.get("slug"),
        old_text=data.get("old_text"),
        new_text=data.get("new_text"),
        summary=data.get("summary"),
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
