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


# (result key, source attribute, cache column) for each table that carries translated text.
# Two tables, because the two comment directions are separate rows: what people wrote under
# our posts, and what we wrote under theirs.
_INBOUND_FIELDS = (("text", "text", "text_tr"), ("reply", "reply_text", "reply_tr"))
_OUTBOUND_FIELDS = (("text", "text", "text_tr"),)


async def translate_rows(
    session: AsyncSession, rows: list, lang: str, llm: LLMPort, *,
    table: str = "post_comment",
    fields: tuple[tuple[str, str, str], ...] = _INBOUND_FIELDS,
) -> dict:
    """Translate each row's text to `lang`, caching as it goes.

    Returns {row_id: {field: str|None}}. Indonesian is the source language and is never
    translated — the raw text already reads for an operator who set the UI to Indonesian."""
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
                text(f"UPDATE {table} SET {col}=:v WHERE id=:id"),  # noqa: S608
                {"v": merge_cache(cache_raw, lang, tr), "id": cid})
        return tr

    tasks = []
    for r in rows:
        result[r.id] = dict.fromkeys(k for k, _, _ in fields)
        for key, src, col in fields:
            body = getattr(r, src, None)
            if body:
                tasks.append((key, r.id, _one(body, getattr(r, col, None), col, r.id)))
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
    """Fill the cache for one branch's recent comments, both directions. Returns rows seen.

    Only the newest slice, because that is all the panel shows — an archive nobody opens is
    not worth a model call."""
    seen = 0
    for sql, table, fields in (
        ("SELECT id, text, reply_text, text_tr, reply_tr FROM post_comment"
         " WHERE branch_id = :b ORDER BY occurred_at DESC LIMIT :n",
         "post_comment", _INBOUND_FIELDS),
        ("SELECT id, text, text_tr FROM outbound_comment"
         " WHERE branch_id = :b AND status = 'sent'"
         " ORDER BY created_at DESC LIMIT :n",
         "outbound_comment", _OUTBOUND_FIELDS),
    ):
        rows = list((await session.execute(
            text(sql), {"b": branch_id, "n": _BATCH})).all())
        for lang in langs:
            await translate_rows(session, rows, lang, llm, table=table, fields=fields)
        seen += len(rows)
    return seen
