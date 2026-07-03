"""Per-message translation with a DB cache (message.tr_text) — never re-bill a bubble.

The first translate stores the result on the row; later requests return it for free."""
from __future__ import annotations

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.ports.llm import LLMPort


async def translate_text(llm: LLMPort, body: str, target: str = "Russian") -> str | None:
    """One-shot translation of raw text (no cache) — for unsent/queued content."""
    if not (body or "").strip():
        return None
    messages = [
        {"role": "system",
         "content": f"Translate the following message to {target}. Return ONLY the translation."},
        {"role": "user", "content": body[:800]},
    ]
    out, _ = await llm.chat(messages, capability="chat:fast", max_tokens=400)
    return out.strip() or None


async def translate_message(
    session: AsyncSession, mid: int, llm: LLMPort, target: str = "Russian"
) -> str | None:
    """Cached translation of message `mid`, or None if the message is missing/empty.

    Raises whatever the LLM transport raises on a miss — the caller decides the fallback."""
    row = (
        await session.execute(
            text("SELECT text, tr_text FROM message WHERE id=:mid"), {"mid": mid}
        )
    ).first()
    if not row or not row[0]:
        return None
    if row[1]:  # cache hit — no LLM call
        return row[1]
    clean = await translate_text(llm, row[0] or "", target)
    if clean:
        await session.execute(
            text("UPDATE message SET tr_text=:t WHERE id=:mid"), {"t": clean, "mid": mid}
        )
    return clean or None
