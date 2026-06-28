"""Pure prompt assembly — no I/O, no branch_id, no hardcoded language.

`build_messages` turns the branch's persona+KB block and the thread dialog into the
chat `messages` array. The model is told to answer in `lang`; nothing here is tied to
a specific language, so a new branch language needs no code change."""
from __future__ import annotations

from typing import Any

from app.adapters.db.models import Message

_DECISION_CONTRACT = (
    "Reply to the lead in language '{lang}'. Then return ONLY a JSON object: "
    '{{"reply": str, "stage": str, "product_slug": str|null, '
    '"ready": bool, "needs_manager": bool}}. No prose outside the JSON.'
)


def _role_of(message: Message) -> str:
    return "user" if message.direction == "in" else "assistant"


def build_messages(
    persona_and_kb: str, dialog: list[Message], lang: str
) -> list[dict[str, Any]]:
    """System (persona+KB+decision contract) followed by the dialog turns, oldest first."""
    system = persona_and_kb.rstrip()
    contract = _DECISION_CONTRACT.format(lang=lang)
    system_content = f"{system}\n\n{contract}" if system else contract

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_content}]
    messages.extend({"role": _role_of(m), "content": m.text} for m in dialog)
    return messages
