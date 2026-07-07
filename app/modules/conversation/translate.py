"""Per-message translation with a DB cache (message.tr_text) — never re-bill a bubble.

The first translate stores the result on the row; later requests return it for free."""
from __future__ import annotations

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.ports.llm import LLMPort

_TARGET_BY_LANG = {"ru": "Russian", "en": "English", "id": "Indonesian"}


def target_for_lang(lang_code: str) -> str:
    """UI language code (ru/en/id) → the target language name the translate prompt needs.
    Defaults to Russian for an unknown code, matching this app's primary operator base."""
    return _TARGET_BY_LANG.get(lang_code, "Russian")


def _system_prompt(target: str) -> str:
    # A bare "translate this to {target}" let a cheap model (chat:fast) badly misfire on
    # short/informal chat text — real cases: 'sok' (Indonesian slang) was "translated" as
    # the LOOKALIKE Russian word "сок" (juice) instead of its actual meaning, because the
    # model wasn't told the source is never already the target language; 'kasih tau'
    # (common Indonesian for "let me know") got an outright refusal ("this isn't Russian,
    # I can't translate it"). Both are addressed below.
    return (
        f"You translate short, informal chat messages into {target}. The source is often "
        "Indonesian/Malay slang or broken English, sometimes mixed — detect it automatically. "
        "The input is NEVER already written in the target language, even if a word happens to "
        f"look like a {target} word — never mistake a lookalike for a real match; find its "
        "actual meaning in the source language. For slang, shorthand, or a single word/fragment, "
        "give the most natural everyday meaning a native chat reader would understand — never "
        "refuse and never claim you can't identify the language. "
        f"Return ONLY the {target} translation — no preamble, no explanation, no quotes."
    )


async def translate_text(llm: LLMPort, body: str, target: str = "Russian") -> str | None:
    """One-shot translation of raw text (no cache) — for unsent/queued content."""
    if not (body or "").strip():
        return None
    messages = [
        {"role": "system", "content": _system_prompt(target)},
        {"role": "user", "content": body[:800]},
    ]
    # Cyrillic/Indonesian output is token-heavy (mistral encodes Cyrillic at ~2-3x the
    # char count); 400 truncated real translations mid-sentence. Cap input at 800 chars,
    # so ~1500 output tokens comfortably covers a full translation.
    out, _ = await llm.chat(messages, capability="chat:fast",
                            max_tokens=settings().translate_max_tokens,
                            workflow="translate")
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
