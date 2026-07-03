"""Pure prompt assembly — no I/O, no branch_id, no hardcoded language.

`build_messages` turns the branch's persona+KB block, optional coaching notes,
and the thread dialog into the chat `messages` array. The model is told to
answer in `lang`; nothing here is tied to a specific language."""
from __future__ import annotations

from typing import Any

from app.adapters.db.models import Message

_DECISION_CONTRACT = (
    "Reply to the lead in language '{lang}'. Write like a human texting: split the reply "
    "into 1-3 SHORT chat bubbles separated by '|||' (not one wall of text). "
    "Then return ONLY a JSON object: "
    '{{"reply": str, "stage": str, "product_slug": str|null, "ready": bool, '
    '"ready_subtype": str|null, "needs_manager": bool, '
    '"manager_question": str|null, "kb_gap": str|null}}. '
    "reply: the message text, with '|||' between bubbles when you split it. "
    "stage: the lead's funnel stage, EXACTLY one of: "
    "new, nurturing, qualifying, presenting, objection, ready. "
    "ready_subtype: 'deal' (ready to enroll/pay) or 'openhouse' (wants the open-house/trial) "
    "— only when ready=true, else null. "
    "manager_question: the specific thing the lead needs a human to resolve (null if not needed). "
    "kb_gap: knowledge or info you lacked to answer (null if KB was sufficient). "
    "No prose outside the JSON."
)

_COACHING_HEADER = "MANDATORY RULES (from manager — follow strictly):"


def _role_of(message: Message) -> str:
    return "user" if message.direction == "in" else "assistant"


def build_messages(
    persona_and_kb: str,
    dialog: list[Message],
    lang: str,
    coaching_notes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """System (persona+KB+coaching+decision contract) followed by dialog, oldest first."""
    parts: list[str] = []
    if persona_and_kb.strip():
        parts.append(persona_and_kb.rstrip())
    if coaching_notes:
        notes_block = "\n".join(f"- {n}" for n in coaching_notes)
        parts.append(f"{_COACHING_HEADER}\n{notes_block}")
    parts.append(_DECISION_CONTRACT.format(lang=lang))

    messages: list[dict[str, Any]] = [{"role": "system", "content": "\n\n".join(parts)}]
    messages.extend({"role": _role_of(m), "content": m.text} for m in dialog)
    return messages
