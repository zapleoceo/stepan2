"""Comment text gets operator-language copies, cached so a translation is never re-billed
(mirroring message.tr_text). The work runs in the hourly ingest, not while the panel renders:
translating on render meant one model call per untranslated line before the first pixel."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from app.adapters.db.models import Branch, Channel, PostComment
from app.domain.enums import ChannelKind
from app.modules.comments.translate import (
    OPERATOR_LANGS,
    cached,
    merge_cache,
    translate_pending,
    translate_rows,
)


class _CountingLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003
        self.calls += 1
        return "переведено", {"model": "x", "cost_usd": 0.0}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003
        return [[0.0] for _ in texts]


def test_cache_helpers_roundtrip() -> None:
    raw = merge_cache(None, "ru", "привет")
    raw = merge_cache(raw, "en", "hi")
    assert cached(raw, "ru") == "привет"
    assert cached(raw, "en") == "hi"
    assert cached(raw, "id") is None
    assert cached(None, "ru") is None


async def _seed(s) -> tuple[int, int]:
    b = Branch(name="ID", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM, handle="x")
    s.add(ch)
    await s.flush()
    pc = PostComment(branch_id=b.id, channel_id=ch.id, external_id="c1", media_id="m1",
                     text="berapa harganya?", reply_text="Rp 1.882.955",
                     occurred_at=datetime(2026, 7, 20, 10, 0), status="replied")
    s.add(pc)
    await s.flush()
    return b.id, pc.id


async def _rows(session, bid):  # noqa: ANN001, ANN201
    return list((await session.execute(text(
        "SELECT id, text, reply_text, text_tr, reply_tr FROM post_comment"
        " WHERE branch_id=:b"), {"b": bid})).all())


async def test_translates_and_caches(db_session) -> None:
    bid, cid = await _seed(db_session)
    llm = _CountingLLM()
    trs = await translate_rows(db_session, await _rows(db_session, bid), "ru", llm)
    assert trs[cid]["text"] == "переведено"
    assert trs[cid]["reply"] == "переведено"
    assert llm.calls == 2  # question + reply

    # second render: cache hit, no new LLM calls
    llm2 = _CountingLLM()
    trs2 = await translate_rows(db_session, await _rows(db_session, bid), "ru", llm2)
    assert trs2[cid]["text"] == "переведено"
    assert llm2.calls == 0  # served from post_comment.text_tr / reply_tr


async def test_indonesian_ui_skips_translation(db_session) -> None:
    bid, _cid = await _seed(db_session)
    llm = _CountingLLM()
    trs = await translate_rows(db_session, await _rows(db_session, bid), "id", llm)
    assert trs == {} and llm.calls == 0  # source is already Indonesian


def test_every_ui_language_gets_a_copy() -> None:
    """The worker fills the cache ahead of any request, so it cannot ask what language the
    reader wants — it has to cover all of them. A UI language added to LANGS and not here
    would leave that operator staring at untranslated Indonesian with no error anywhere."""
    from app.api._i18n import LANGS

    assert OPERATOR_LANGS == tuple(x for x in LANGS if x != "id")


async def test_the_panel_never_calls_the_model(db_session) -> None:
    """The whole point of moving this: an untranslated line renders instantly in the original
    rather than holding the page open for a model round-trip."""
    from app.api._routes_comments import _cached_translations

    bid, cid = await _seed(db_session)
    trs = _cached_translations(await _rows(db_session, bid), "ru")

    assert trs[cid] == {"text": None, "reply": None}


async def test_pending_fills_the_cache_for_the_whole_branch(db_session) -> None:
    bid, cid = await _seed(db_session)
    llm = _CountingLLM()
    looked_at = await translate_pending(db_session, bid, llm, ("ru",))

    assert looked_at == 1
    rows = {r.id: r for r in await _rows(db_session, bid)}
    assert cached(rows[cid].text_tr, "ru") == "переведено"
