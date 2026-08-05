"""Operator-language copies of comment text, filled by the worker, read by the panel.

This used to happen while the panel rendered: every untranslated line meant a model call
before the first pixel, so opening the page the morning after a busy night sat there for
seconds. The work belongs to the hourly ingest — it already touches every new comment, and a
translation that lands a minute later costs nobody anything.

Cache lives in `post_comment.text_tr` / `reply_tr` as a JSON map of lang → text, so one
comment can carry Russian and English side by side without a second column each.
"""
from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.conversation.translate import target_for_lang, translate_text
from app.ports.llm import LLMPort

logger = logging.getLogger(__name__)

# At most this many translations in flight — the broker queues the rest. A branch coming back
# from a quiet week can have a hundred untranslated lines; firing them all at once is how the
# free tier turns into 429s.
_CONCURRENCY = 6

# Languages the panel can be read in, minus Indonesian — that is the source, and a comment
# already reads for an operator who set the UI to it. Deliberately not imported from
# app/api/_i18n.py: a module must not reach up into the API layer. test_comment_translate
# asserts the two stay in step, so adding a UI language fails loudly rather than silently
# leaving that operator looking at untranslated text.
OPERATOR_LANGS = ("en", "ru")

# One pass never chews through more than this. The rest waits for the next hour rather than
# holding the ingest transaction open.
_BATCH = 60


def cached(raw: str | None, lang: str) -> str | None:
    if not raw:
        return None
    try:
        return json.loads(raw).get(lang)
    except (ValueError, AttributeError):
        return None


def merge_cache(raw: str | None, lang: str, value: str) -> str:
    try:
        d = json.loads(raw) if raw else {}
    except ValueError:
        d = {}
    d[lang] = value
    return json.dumps(d, ensure_ascii=False)


async def translate_rows(
    session: AsyncSession, rows: list, lang: str, llm: LLMPort,
) -> dict:
    """Translate every question + reply in `rows` to `lang`, caching as it goes.

    Returns {comment_id: {'text': str|None, 'reply': str|None}}. Indonesian is the source
    language and is never translated — the raw text already reads for an operator who set
    the UI to Indonesian."""
    result: dict = {}
    if lang == "id":
        return result
    target = target_for_lang(lang)
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _one(body: str, cache_raw: str | None, col: str, cid: int):  # noqa: ANN202
        hit = cached(cache_raw, lang)
        if hit is not None:
            return hit
        if not (body or "").strip():
            return None
        async with sem:
            tr = await translate_text(llm, body, target)
        if tr:
            await session.execute(
                text(f"UPDATE post_comment SET {col}=:v WHERE id=:id"),  # noqa: S608
                {"v": merge_cache(cache_raw, lang, tr), "id": cid})
        return tr

    tasks = []
    for r in rows:
        result[r.id] = {"text": None, "reply": None}
        tasks.append(("text", r.id, _one(r.text, r.text_tr, "text_tr", r.id)))
        if r.reply_text:
            tasks.append(("reply", r.id, _one(r.reply_text, r.reply_tr, "reply_tr", r.id)))
    done = await asyncio.gather(*(c for _, _, c in tasks), return_exceptions=True)
    for (field, cid, _), out in zip(tasks, done, strict=True):
        if isinstance(out, Exception):
            logger.warning("comment translate failed id=%s: %s", cid, out)
            continue
        result[cid][field] = out
    return result


async def translate_pending(
    session: AsyncSession, branch_id: int, llm: LLMPort, langs: tuple[str, ...],
) -> int:
    """Fill the cache for one branch's recent comments. Returns rows looked at.

    Only the newest slice, because that is all the panel shows — an archive nobody opens is
    not worth a model call."""
    rows = list((await session.execute(text(
        "SELECT id, text, reply_text, text_tr, reply_tr FROM post_comment"
        " WHERE branch_id = :b ORDER BY occurred_at DESC LIMIT :n"),
        {"b": branch_id, "n": _BATCH})).all())
    if not rows:
        return 0
    for lang in langs:
        await translate_rows(session, rows, lang, llm)
    return len(rows)
