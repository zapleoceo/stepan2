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

_SB, _SR, _RB, _MR = ("[SUMMARY_BRANCH]", "[SUMMARY_RU]", "[REASON_BRANCH]",
                      "[LAST_MSG_RU]")


@dataclass(frozen=True)
class AlertBody:
    summary_branch: str
    summary_ru: str
    reason_branch: str
    # Последняя реплика лида дословно и её перевод. Пересказ отвечает на «что вообще
    # происходит», а менеджеру для решения нужно ещё и «что человек написал ИМЕННО сейчас» —
    # раньше за этим приходилось открывать чат, и алерт читался как повод сходить куда-то ещё.
    last_msg: str = ""
    last_msg_ru: str = ""


def lang_name(code: str) -> str:
    return _LANG_NAME.get((code or "").lower(), "English")


async def build_alert_body(
    session: AsyncSession, llm: LLMPort | None, thread_id: int | None,
    *, branch_lang: str, reason_en: str, reason_ru: str, branch_id: int | None = None,
) -> AlertBody:
    """(summary in branch lang, summary in Russian, reason in branch lang). Falls back to
    empty summaries + the English reason when there's no LLM, no dialog, or the call fails."""
    branch_name = lang_name(branch_lang)
    last_msg = await _last_inbound(session, thread_id) if thread_id is not None else ""
    if llm is None or thread_id is None:
        return AlertBody("", "", reason_en, last_msg, "")
    convo = await _dialog(session, thread_id)
    if not convo:
        # No dialog to summarise, but the one line we DO have is the line the manager has to
        # act on. Translating it is a sentence, not a summary — refusing to do it because the
        # bigger job is impossible left the Russian half of the alert quoting Indonesian.
        return AlertBody("", "", reason_en, last_msg,
                         await _translate_line(llm, last_msg, thread_id, branch_id))
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
                                workflow="alert", thread_id=thread_id, branch_id=branch_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("alert summary LLM failed thread=%s: %s", thread_id, exc)
        return AlertBody("", "", reason_en, last_msg, "")
    sb = _between(raw, _SB, _SR)
    sr = _between(raw, _SR, _RB)
    rb = _between(raw, _RB, _MR)
    mr = _between(raw, _MR, None)
    # The translation is the part a manager cannot do without, so it does not depend on the
    # model having remembered a marker: a dropped [LAST_MSG_RU] block used to leave the
    # Russian half quoting Indonesian, which is the one line the alert exists to deliver.
    if not mr and last_msg:
        mr = await _translate_line(llm, last_msg, thread_id, branch_id)
    return AlertBody(sb or "", sr or "", rb or reason_en, last_msg, mr or "")


async def _translate_line(
    llm: LLMPort, body: str, thread_id: int | None, branch_id: int | None,
) -> str:
    """Russian for one message. Empty on any failure — an alert without a translation is
    worse than one with, and an alert that never arrives is worse than both."""
    clean = (body or "").strip()
    if not clean:
        return ""
    try:
        out, _ = await llm.chat(
            [{"role": "system",
              "content": "Translate the user's message into Russian. Output only the "
                         "translation, no preamble, no quotes."},
             {"role": "user", "content": clean}],
            capability="chat:fast", max_tokens=300, workflow="alert",
            thread_id=thread_id, branch_id=branch_id)
    except Exception as exc:  # noqa: BLE001 — the ping is best-effort, never blocks
        logger.warning("alert line translation failed thread=%s: %s", thread_id, exc)
        return ""
    return (out or "").strip()


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


async def _last_inbound(session: AsyncSession, thread_id: int) -> str:
    """Что человек написал последним — дословно, на его языке."""
    row = (await session.execute(
        text("SELECT text FROM message WHERE thread_id = :tid AND direction = 'in'"
             " AND text <> '' ORDER BY occurred_at DESC, id DESC LIMIT 1"),
        {"tid": thread_id})).first()
    return (row[0] or "").strip()[:600] if row else ""


def _between(raw: str, start: str, end: str | None) -> str:
    i = raw.find(start)
    if i < 0:
        return ""
    i += len(start)
    j = raw.find(end, i) if end else len(raw)
    if j < 0:
        j = len(raw)
    return raw[i:j].strip()
