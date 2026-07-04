"""Build the bilingual body of a group alert: a chat summary in the branch language and
in Russian, plus the reason translated into the branch language. One LLM call, parsed by
markers; any failure degrades to empty summaries + the untranslated reason so the alert
still goes out (the ping is best-effort, never blocks the hand-off)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.ports.llm import LLMPort

logger = logging.getLogger(__name__)

_MAX_MSGS = 40
_LANG_NAME = {"ru": "Russian", "en": "English", "id": "Indonesian", "ms": "Malay"}

_SB, _SR, _RB = "[SUMMARY_BRANCH]", "[SUMMARY_RU]", "[REASON_BRANCH]"


@dataclass(frozen=True)
class AlertBody:
    summary_branch: str
    summary_ru: str
    reason_branch: str


def lang_name(code: str) -> str:
    return _LANG_NAME.get((code or "").lower(), "English")


async def build_alert_body(
    session: AsyncSession, llm: LLMPort | None, thread_id: int | None,
    *, branch_lang: str, reason_en: str, reason_ru: str,
) -> AlertBody:
    """(summary in branch lang, summary in Russian, reason in branch lang). Falls back to
    empty summaries + the English reason when there's no LLM, no dialog, or the call fails."""
    branch_name = lang_name(branch_lang)
    if llm is None or thread_id is None:
        return AlertBody("", "", reason_en)
    convo = await _dialog(session, thread_id)
    if not convo:
        return AlertBody("", "", reason_en)
    reason_clean = reason_en.replace("'", "")
    system = (
        "You summarize a sales conversation for a manager and translate a short reason "
        "line. Output EXACTLY three blocks, each preceded by its marker on its own line:\n"
        f"{_SB}\n<3-6 sentence summary of the chat in {branch_name}: what the lead wants, "
        "key objections, current state>\n"
        f"{_SR}\n<the same summary in Russian>\n"
        f"{_RB}\n<this reason line translated into {branch_name}: '{reason_clean}'>\n"
        "No preamble, no extra text."
    )
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": convo}]
    try:
        raw, _ = await llm.chat(msgs, capability="chat:fast", max_tokens=700,
                                workflow="alert", thread_id=thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("alert summary LLM failed thread=%s: %s", thread_id, exc)
        return AlertBody("", "", reason_en)
    sb = _between(raw, _SB, _SR)
    sr = _between(raw, _SR, _RB)
    rb = _between(raw, _RB, None)
    return AlertBody(sb or "", sr or "", rb or reason_en)


async def _dialog(session: AsyncSession, thread_id: int) -> str:
    rows = (
        await session.execute(
            text("SELECT direction, text FROM message WHERE thread_id = :tid AND text <> ''"
                 " ORDER BY occurred_at DESC, id DESC LIMIT :lim"),
            {"tid": thread_id, "lim": _MAX_MSGS},
        )
    ).all()
    return "\n".join(
        f"{'Lead' if r[0] == 'in' else 'Agent'}: {(r[1] or '').strip()}"
        for r in reversed(rows) if (r[1] or "").strip()
    )[:6000]


def _between(raw: str, start: str, end: str | None) -> str:
    i = raw.find(start)
    if i < 0:
        return ""
    i += len(start)
    j = raw.find(end, i) if end else len(raw)
    if j < 0:
        j = len(raw)
    return raw[i:j].strip()
